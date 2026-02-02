import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Dict, Optional, List, Tuple

from sqlalchemy import func
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    abort,
    session,
    Response,
)
from flask_socketio import SocketIO, join_room, emit
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from models import db, AuthUser, Match, Task


# ----------------------------
# App init
# ----------------------------
app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")


# ----------------------------
# Difficulty / Topic helpers
# ----------------------------
# В БД храним строкой ровно: "Легкая" / "Средняя" / "Сложная"
DIFFICULTIES = ("Легкая", "Средняя", "Сложная")
DEFAULT_DIFFICULTY = "Средняя"
DEFAULT_TOPIC = "Общее"
DEFAULT_SUBJECT = "Математика"


def normalize_difficulty(val: str) -> str:
    v = (val or "").strip()
    return v if v in DIFFICULTIES else DEFAULT_DIFFICULTY


def normalize_subject(val: str) -> str:
    v = (val or "").strip()
    return v if v else DEFAULT_SUBJECT


def normalize_topic(val: str) -> str:
    v = (val or "").strip()
    return v if v else DEFAULT_TOPIC


# ----------------------------
# Auth helpers
# ----------------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        user = db.session.get(AuthUser, session["user_id"])
        if not user or not getattr(user, "is_admin", False):
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


def ensure_user():
    uid = session.get("user_id")
    uname = session.get("username")
    if not uid or not uname:
        return None, None
    return int(uid), str(uname)


# ----------------------------
# Elo helpers
# ----------------------------
def elo_expected(r_a: int, r_b: int) -> float:
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))


def elo_apply(r_a: int, r_b: int, score_a: float, k: int) -> int:
    exp_a = elo_expected(r_a, r_b)
    return int(round(r_a + k * (score_a - exp_a)))


# ----------------------------
# Matchmaking queue (in-memory)
# ----------------------------
@dataclass
class QueueEntry:
    user_id: int
    username: str
    rating: int
    sid: str
    joined_at: float


WAITING: List[QueueEntry] = []
LIVE_MATCHES: Dict[int, Dict] = {}


def match_room(match_id: int) -> str:
    return f"match:{match_id}"


def remove_from_queue_by_user(user_id: int):
    global WAITING
    WAITING = [e for e in WAITING if e.user_id != user_id]


def remove_from_queue_by_sid(sid: str):
    global WAITING
    WAITING = [e for e in WAITING if e.sid != sid]


def find_best_opponent(entry: QueueEntry) -> Optional[QueueEntry]:
    best = None
    best_diff = None
    for e in WAITING:
        if e.user_id == entry.user_id:
            continue
        diff = abs(e.rating - entry.rating)
        if best is None or diff < best_diff:
            best = e
            best_diff = diff
    return best


# ----------------------------
# Training (in-memory)
# ----------------------------
LIVE_TRAININGS: Dict[int, Dict] = {}


def training_room(user_id: int) -> str:
    return f"training:{user_id}"


def training_seconds_default() -> int:
    # можно добавить DEFAULT_TRAINING_SECONDS в Config
    return int(app.config.get("DEFAULT_TRAINING_SECONDS", 60))


def normalize_filter_value(val: str, any_value: str) -> str:
    v = (val or "").strip()
    return v if v else any_value


# ----------------------------
# Tasks logic (server-side)
# ----------------------------
def normalize_answer(s: str) -> str:
    return (s or "").strip().replace(",", ".").lower()


def is_correct(submitted: str, correct: str) -> bool:
    return normalize_answer(submitted) == normalize_answer(correct)


def pick_task() -> Dict[str, str]:
    """
    Берём активную задачу из БД. Если задач нет — возвращаем демо.
    ВАЖНО: сервер хранит correct answer, клиенту его не отдаем.
    """
    t = Task.query.filter_by(is_active=True).order_by(func.random()).first()

    if not t:
        return {
            "id": None,
            "subject": DEFAULT_SUBJECT,
            "topic": "Демо-задача",
            "prompt": "Сколько будет 17 + 25 ? (введите число)",
            "answer": "42",
            "kind": "number",
            "difficulty": DEFAULT_DIFFICULTY,
        }

    return {
        "id": t.id,
        "subject": getattr(t, "subject", DEFAULT_SUBJECT),
        "topic": t.topic,
        "prompt": t.prompt,
        "answer": t.answer,
        "kind": t.kind,
        "difficulty": t.difficulty,
    }


def pick_task_filtered(subject: str, topic: str, difficulty: str) -> Dict[str, str]:
    """
    subject/topic/difficulty могут быть 'Любой/Любая'.
    """
    q = Task.query.filter_by(is_active=True)

    if subject and subject != "Любой":
        # если поля subject нет в модели/БД — здесь упадёт; это ожидаемо (нужно добавить колонку)
        q = q.filter(Task.subject == subject)

    if topic and topic != "Любая":
        q = q.filter(Task.topic == topic)

    if difficulty and difficulty != "Любая":
        q = q.filter(Task.difficulty == difficulty)

    t = q.order_by(func.random()).first()
    if not t:
        return {
            "id": None,
            "subject": subject if subject != "Любой" else DEFAULT_SUBJECT,
            "topic": topic if topic != "Любая" else "Нет задач",
            "prompt": "Нет задач под выбранные фильтры. Убери фильтры или добавь задачи в админке.",
            "answer": "",
            "kind": "text",
            "difficulty": difficulty if difficulty != "Любая" else DEFAULT_DIFFICULTY,
        }

    return {
        "id": t.id,
        "subject": getattr(t, "subject", DEFAULT_SUBJECT),
        "topic": t.topic,
        "prompt": t.prompt,
        "answer": t.answer,
        "kind": t.kind,
        "difficulty": t.difficulty,
    }


def training_options() -> Dict:
    """
    Возвращаем списки для селектов тренировки.
    """
    # subjects
    try:
        subjects_rows = db.session.query(Task.subject).distinct().order_by(Task.subject).all()
        subjects = [r[0] for r in subjects_rows if r and r[0]]
    except Exception:
        # если subject ещё не добавили
        subjects = [DEFAULT_SUBJECT]

    topics_rows = db.session.query(Task.topic).distinct().order_by(Task.topic).all()
    topics = [r[0] for r in topics_rows if r and r[0]]

    return {
        "subjects": ["Любой"] + subjects,
        "topics": ["Любая"] + topics,
        "difficulties": ["Любая", "Легкая", "Средняя", "Сложная"],
    }


# ----------------------------
# DB bootstrap
# ----------------------------
def ensure_db():
    with app.app_context():
        db.create_all()


# ----------------------------
# HTTP routes: main
# ----------------------------
@app.route("/", methods=["GET"])
@login_required
def index():
    user = db.session.get(AuthUser, session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))
    return render_template("index.html", user=user)


@app.route("/match/<int:match_id>", methods=["GET"])
@login_required
def match_page(match_id: int):
    uid, uname = ensure_user()
    m = db.session.get(Match, match_id)
    if not m:
        abort(404)
    if uid not in (m.player1_id, m.player2_id):
        abort(403)

    opponent_name = m.player2_name if uid == m.player1_id else m.player1_name
    opponent_rating = m.player2_rating if uid == m.player1_id else m.player1_rating

    return render_template(
        "match.html",
        match=m,
        me_name=uname,
        opponent_name=opponent_name,
        opponent_rating=opponent_rating,
        match_seconds=app.config.get("DEFAULT_MATCH_SECONDS", 600),
    )


@app.route("/training", methods=["GET"])
@login_required
def training_page():
    return render_template("training.html", training_seconds=training_seconds_default())


# ----------------------------
# HTTP routes: auth
# ----------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", error=None)

    username = (request.form.get("username") or "").strip()[:32]
    password = request.form.get("password") or ""
    password2 = request.form.get("password2") or ""

    if len(username) < 3:
        return render_template("register.html", error="Логин должен быть от 3 символов.")
    if len(password) < 6:
        return render_template("register.html", error="Пароль должен быть минимум 6 символов.")
    if password != password2:
        return render_template("register.html", error="Пароли не совпадают.")

    exists = AuthUser.query.filter_by(username=username).first()
    if exists:
        return render_template("register.html", error="Такой логин уже занят.")

    user = AuthUser(
        username=username,
        password_hash=generate_password_hash(password),
        rating=1000,
    )
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    session["username"] = user.username
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", error=None)

    username = (request.form.get("username") or "").strip()[:32]
    password = request.form.get("password") or ""

    user = AuthUser.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return render_template("login.html", error="Неверный логин или пароль.")

    if hasattr(user, "last_login_at"):
        user.last_login_at = datetime.utcnow()
        db.session.commit()

    session["user_id"] = user.id
    session["username"] = user.username
    return redirect(url_for("index"))


@app.route("/logout", methods=["GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------------
# Admin routes
# ----------------------------
@app.route("/admin")
@admin_required
def admin_index():
    return render_template("admin/index.html")


@app.route("/admin/users")
@admin_required
def admin_users():
    users = AuthUser.query.order_by(AuthUser.rating.desc()).all()
    stats = {}

    for u in users:
        ended = (
            Match.query.filter(Match.status == "ended")
            .filter((Match.player1_id == u.id) | (Match.player2_id == u.id))
            .count()
        )
        wins = Match.query.filter(Match.status == "ended", Match.winner_user_id == u.id).count()
        draws = (
            Match.query.filter(Match.status == "ended", Match.winner_user_id.is_(None))
            .filter((Match.player1_id == u.id) | (Match.player2_id == u.id))
            .count()
        )
        losses = max(0, ended - wins - draws)
        stats[u.id] = {"ended": ended, "wins": wins, "losses": losses, "draws": draws}

    return render_template("admin/users_list.html", users=users, stats=stats)


@app.route("/admin/tasks")
@admin_required
def admin_tasks():
    tasks = Task.query.order_by(Task.id.desc()).all()
    return render_template("admin/tasks_list.html", tasks=tasks)


@app.route("/admin/tasks/new", methods=["GET", "POST"])
@admin_required
def admin_task_new():
    if request.method == "GET":
        return render_template("admin/task_form.html", task=None, error=None)

    subject = normalize_subject(request.form.get("subject") or DEFAULT_SUBJECT)
    topic = normalize_topic(request.form.get("topic") or DEFAULT_TOPIC)
    prompt = (request.form.get("prompt") or "").strip()
    answer = (request.form.get("answer") or "").strip()
    kind = (request.form.get("kind") or "text").strip()
    is_active = bool(request.form.get("is_active"))
    difficulty = normalize_difficulty(request.form.get("difficulty") or DEFAULT_DIFFICULTY)

    if not prompt or not answer:
        return render_template("admin/task_form.html", task=None, error="Заполни prompt / answer")

    t = Task(
        subject=subject,
        topic=topic,
        prompt=prompt,
        answer=answer,
        kind=kind,
        difficulty=difficulty,
        is_active=is_active,
    )
    db.session.add(t)
    db.session.commit()
    return redirect(url_for("admin_tasks"))


@app.route("/admin/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_task_edit(task_id: int):
    t = db.session.get(Task, task_id)
    if not t:
        abort(404)

    if request.method == "GET":
        return render_template("admin/task_form.html", task=t, error=None)

    t.subject = normalize_subject(request.form.get("subject") or getattr(t, "subject", DEFAULT_SUBJECT))
    t.topic = normalize_topic(request.form.get("topic") or t.topic)
    t.prompt = (request.form.get("prompt") or "").strip()
    t.answer = (request.form.get("answer") or "").strip()
    t.kind = (request.form.get("kind") or "text").strip()
    t.is_active = bool(request.form.get("is_active"))
    t.difficulty = normalize_difficulty(request.form.get("difficulty") or DEFAULT_DIFFICULTY)

    if not t.prompt or not t.answer:
        return render_template("admin/task_form.html", task=t, error="Заполни prompt / answer")

    db.session.commit()
    return redirect(url_for("admin_tasks"))


@app.route("/admin/tasks/<int:task_id>/toggle", methods=["POST"])
@admin_required
def admin_task_toggle(task_id: int):
    t = db.session.get(Task, task_id)
    if not t:
        abort(404)
    t.is_active = not t.is_active
    db.session.commit()
    return redirect(url_for("admin_tasks"))


@app.route("/admin/tasks/<int:task_id>/delete", methods=["POST"])
@admin_required
def admin_task_delete(task_id: int):
    t = db.session.get(Task, task_id)
    if not t:
        abort(404)
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for("admin_tasks"))


@app.route("/admin/tasks/export.json")
@admin_required
def admin_tasks_export_json():
    tasks = Task.query.order_by(Task.id.asc()).all()
    data = []
    for t in tasks:
        data.append(
            {
                "id": t.id,
                "subject": getattr(t, "subject", DEFAULT_SUBJECT),
                "topic": t.topic,
                "prompt": t.prompt,
                "answer": t.answer,
                "kind": t.kind,
                "difficulty": t.difficulty,
                "is_active": t.is_active,
            }
        )
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=tasks.json"},
    )


@app.route("/admin/tasks/export.csv")
@admin_required
def admin_tasks_export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "subject", "topic", "prompt", "answer", "kind", "difficulty", "is_active"])
    for t in Task.query.order_by(Task.id.asc()).all():
        w.writerow(
            [
                t.id,
                getattr(t, "subject", DEFAULT_SUBJECT),
                t.topic,
                t.prompt,
                t.answer,
                t.kind,
                t.difficulty,
                int(t.is_active),
            ]
        )

    return Response(
        out.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=tasks.csv"},
    )


@app.route("/admin/tasks/import", methods=["GET", "POST"])
@admin_required
def admin_tasks_import():
    if request.method == "GET":
        return render_template("admin/tasks_import.html", error=None)

    f = request.files.get("file")
    if not f or not f.filename:
        return render_template("admin/tasks_import.html", error="Выбери файл")

    name = f.filename.lower()
    raw = f.read()

    created = 0
    updated = 0

    if name.endswith(".json"):
        items = json.loads(raw.decode("utf-8"))
        if not isinstance(items, list):
            return render_template("admin/tasks_import.html", error="JSON должен быть массивом объектов.")

        for it in items:
            if not isinstance(it, dict):
                continue

            t_id = it.get("id")
            subject = normalize_subject(it.get("subject") or DEFAULT_SUBJECT)
            topic = normalize_topic(it.get("topic") or DEFAULT_TOPIC)
            prompt = (it.get("prompt") or "").strip()
            answer = (it.get("answer") or "").strip()
            kind = (it.get("kind") or "text").strip()
            difficulty = normalize_difficulty(it.get("difficulty") or DEFAULT_DIFFICULTY)
            is_active = bool(it.get("is_active", True))

            if not prompt or not answer:
                continue

            t = db.session.get(Task, int(t_id)) if t_id else None
            if t:
                t.subject = subject
                t.topic = topic
                t.prompt = prompt
                t.answer = answer
                t.kind = kind
                t.difficulty = difficulty
                t.is_active = is_active
                updated += 1
            else:
                db.session.add(
                    Task(
                        subject=subject,
                        topic=topic,
                        prompt=prompt,
                        answer=answer,
                        kind=kind,
                        difficulty=difficulty,
                        is_active=is_active,
                    )
                )
                created += 1

    elif name.endswith(".csv"):
        text = raw.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            t_id = (row.get("id") or "").strip()
            subject = normalize_subject(row.get("subject") or DEFAULT_SUBJECT)
            topic = normalize_topic(row.get("topic") or DEFAULT_TOPIC)
            prompt = (row.get("prompt") or "").strip()
            answer = (row.get("answer") or "").strip()
            kind = (row.get("kind") or "text").strip()
            difficulty = normalize_difficulty(row.get("difficulty") or DEFAULT_DIFFICULTY)
            is_active = (row.get("is_active") or "1").strip() in ("1", "true", "True", "yes", "YES")

            if not prompt or not answer:
                continue

            t = db.session.get(Task, int(t_id)) if t_id.isdigit() else None
            if t:
                t.subject = subject
                t.topic = topic
                t.prompt = prompt
                t.answer = answer
                t.kind = kind
                t.difficulty = difficulty
                t.is_active = is_active
                updated += 1
            else:
                db.session.add(
                    Task(
                        subject=subject,
                        topic=topic,
                        prompt=prompt,
                        answer=answer,
                        kind=kind,
                        difficulty=difficulty,
                        is_active=is_active,
                    )
                )
                created += 1
    else:
        return render_template("admin/tasks_import.html", error="Нужен .csv или .json")

    db.session.commit()
    return redirect(url_for("admin_tasks"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id: int):
    user = db.session.get(AuthUser, user_id)
    if not user:
        abort(404)

    # Защита от удаления самого себя
    if user.id == session.get("user_id"):
        abort(400, "Нельзя удалить самого себя")

    # Удаляем связанные матчи
    Match.query.filter((Match.player1_id == user.id) | (Match.player2_id == user.id)).delete(
        synchronize_session=False
    )

    db.session.delete(user)
    db.session.commit()

    return redirect(url_for("admin_users"))


# ----------------------------
# Socket.IO: matchmaking
# ----------------------------
@socketio.on("queue:join")
def on_queue_join(_data):
    uid, _ = ensure_user()
    if not uid:
        emit("toast", {"type": "danger", "text": "Нужно войти."})
        return

    remove_from_queue_by_user(uid)

    user = db.session.get(AuthUser, uid)
    if not user:
        emit("toast", {"type": "danger", "text": "Пользователь не найден."})
        return

    entry = QueueEntry(
        user_id=uid,
        username=user.username,
        rating=int(user.rating),
        sid=request.sid,
        joined_at=time.time(),
    )

    opponent = find_best_opponent(entry)
    if opponent:
        remove_from_queue_by_user(opponent.user_id)

        m = Match(
            player1_id=entry.user_id,
            player2_id=opponent.user_id,
            player1_name=entry.username,
            player2_name=opponent.username,
            player1_rating=entry.rating,
            player2_rating=opponent.rating,
            duration_sec=int(app.config.get("DEFAULT_MATCH_SECONDS", 600)),
            status="pending",
        )
        db.session.add(m)
        db.session.commit()

        LIVE_MATCHES[m.id] = {
            "p1_sid": None,
            "p2_sid": None,
            "p1_id": m.player1_id,
            "p2_id": m.player2_id,
            "seconds_left": m.duration_sec,
            "running": False,
            "task": pick_task(),  # сервер-only хранит answer
            "submissions": {},
        }

        emit(
            "match:found",
            {"match_id": m.id, "opponent_name": opponent.username, "opponent_rating": opponent.rating},
            to=entry.sid,
        )
        emit(
            "match:found",
            {"match_id": m.id, "opponent_name": entry.username, "opponent_rating": entry.rating},
            to=opponent.sid,
        )
        return

    WAITING.append(entry)
    emit("queue:status", {"status": "searching", "rating": entry.rating})


@socketio.on("queue:leave")
def on_queue_leave(_data):
    uid, _ = ensure_user()
    if uid:
        remove_from_queue_by_user(uid)
    emit("queue:status", {"status": "idle"})


# ----------------------------
# Socket.IO: training
# ----------------------------
@socketio.on("training:join")
def on_training_join(_data=None):
    uid, uname = ensure_user()
    if not uid:
        emit("toast", {"type": "danger", "text": "Нужно войти."})
        return

    room = training_room(uid)
    join_room(room)

    secs = training_seconds_default()

    state = LIVE_TRAININGS.get(uid)
    if not state:
        # дефолтные фильтры
        filters = {"subject": "Любой", "topic": "Любая", "difficulty": "Любая"}

        task = pick_task_filtered(filters["subject"], filters["topic"], filters["difficulty"])
        state = {
            "user_id": uid,
            "username": uname,
            "room": room,
            "sid": request.sid,
            "running": True,
            "seconds_left": secs,
            "task": task,
            "stats": {"total": 0, "solved": 0},
            "filters": filters,
            "generation": 0,  # защита от дубля таймеров
        }
        LIVE_TRAININGS[uid] = state
    else:
        state["sid"] = request.sid
        state["room"] = room
        state["running"] = True
        if not state.get("filters"):
            state["filters"] = {"subject": "Любой", "topic": "Любая", "difficulty": "Любая"}
        if not state.get("task"):
            f = state["filters"]
            state["task"] = pick_task_filtered(f["subject"], f["topic"], f["difficulty"])
        if not state.get("seconds_left"):
            state["seconds_left"] = secs

    # options для селектов
    emit("training:options", training_options(), to=room)

    # отдадим текущую задачу
    task = state["task"]
    emit(
        "training:task",
        {
            "subject": task.get("subject", DEFAULT_SUBJECT),
            "topic": task.get("topic", DEFAULT_TOPIC),
            "difficulty": task.get("difficulty", DEFAULT_DIFFICULTY),
            "prompt": task.get("prompt", ""),
            "seconds_left": int(state["seconds_left"]),
            "stats": state["stats"],
            "filters": state["filters"],
        },
        to=room,
    )

    # старт/рестарт таймера (одно поколение на задачу)
    state["generation"] += 1
    gen = state["generation"]
    socketio.start_background_task(training_timer_task, uid, gen)


@socketio.on("training:set_filters")
def on_training_set_filters(data):
    uid, _ = ensure_user()
    if not uid:
        return

    state = LIVE_TRAININGS.get(uid)
    if not state:
        return

    subject = normalize_filter_value((data or {}).get("subject"), "Любой")
    topic = normalize_filter_value((data or {}).get("topic"), "Любая")
    difficulty = normalize_filter_value((data or {}).get("difficulty"), "Любая")

    # нормализуем difficulty под нашу БД
    if difficulty not in ("Любая",) + DIFFICULTIES:
        difficulty = "Любая"

    state["filters"] = {"subject": subject, "topic": topic, "difficulty": difficulty}
    training_next_task(uid)


def training_timer_task(user_id: int, generation: int):
    with app.app_context():
        while True:
            state = LIVE_TRAININGS.get(user_id)
            if not state:
                return
            if state.get("generation") != generation:
                return
            if not state.get("running"):
                return

            if state["seconds_left"] <= 0:
                # таймаут
                task = state["task"]
                correct = task.get("answer", "")
                socketio.emit(
                    "training:result",
                    {
                        "correct": False,
                        "reason": "timeout",
                        "correct_answer": correct,
                        "stats": state["stats"],
                    },
                    to=state["room"],
                )
                socketio.sleep(1)
                training_next_task(user_id, generation)
                return

            socketio.sleep(1)
            state["seconds_left"] -= 1
            socketio.emit(
                "training:tick",
                {"seconds_left": int(state["seconds_left"])},
                to=state["room"],
            )


def training_next_task(user_id: int, generation: Optional[int] = None):
    state = LIVE_TRAININGS.get(user_id)
    if not state:
        return
    if generation is not None and state.get("generation") != generation:
        return

    secs = training_seconds_default()
    f = state.get("filters") or {"subject": "Любой", "topic": "Любая", "difficulty": "Любая"}

    state["task"] = pick_task_filtered(f["subject"], f["topic"], f["difficulty"])
    state["seconds_left"] = secs
    state["running"] = True

    task = state["task"]
    socketio.emit(
        "training:task",
        {
            "subject": task.get("subject", DEFAULT_SUBJECT),
            "topic": task.get("topic", DEFAULT_TOPIC),
            "difficulty": task.get("difficulty", DEFAULT_DIFFICULTY),
            "prompt": task.get("prompt", ""),
            "seconds_left": int(state["seconds_left"]),
            "stats": state["stats"],
            "filters": f,
        },
        to=state["room"],
    )

    # рестарт таймера
    state["generation"] += 1
    gen = state["generation"]
    socketio.start_background_task(training_timer_task, user_id, gen)


@socketio.on("training:submit_answer")
def on_training_submit_answer(data):
    uid, _ = ensure_user()
    if not uid:
        return

    state = LIVE_TRAININGS.get(uid)
    if not state or not state.get("running"):
        return

    ans = ((data or {}).get("answer") or "").strip()
    if not ans:
        emit("toast", {"type": "warning", "text": "Введи ответ."})
        return

    task = state["task"]
    correct = task.get("answer", "")

    # не считаем попытку, если демо/нет ответа
    if correct:
        state["stats"]["total"] += 1
        ok = is_correct(ans, correct)
        if ok:
            state["stats"]["solved"] += 1
    else:
        ok = False

    socketio.emit(
        "training:result",
        {
            "correct": ok,
            "reason": "answer",
            "correct_answer": correct if correct else "—",
            "stats": state["stats"],
        },
        to=state["room"],
    )

    socketio.sleep(1)
    training_next_task(uid)


@socketio.on("training:leave")
def on_training_leave(_data=None):
    uid, _ = ensure_user()
    if not uid:
        return
    state = LIVE_TRAININGS.get(uid)
    if state:
        state["running"] = False
    emit("toast", {"type": "secondary", "text": "Тренировка остановлена."})


# ----------------------------
# Socket.IO: match
# ----------------------------
@socketio.on("match:join")
def on_match_join(data):
    uid, uname = ensure_user()
    if not uid:
        emit("toast", {"type": "danger", "text": "Нужно войти."})
        return

    match_id = int((data or {}).get("match_id", 0))
    if not match_id:
        emit("toast", {"type": "danger", "text": "Некорректный match_id."})
        return

    m = db.session.get(Match, match_id)
    if not m:
        emit("toast", {"type": "danger", "text": "Матч не найден."})
        return
    if uid not in (m.player1_id, m.player2_id):
        emit("toast", {"type": "danger", "text": "Вы не участник этого матча."})
        return

    room = match_room(match_id)
    join_room(room)

    state = LIVE_MATCHES.get(match_id)
    if not state:
        state = {
            "p1_sid": None,
            "p2_sid": None,
            "p1_id": m.player1_id,
            "p2_id": m.player2_id,
            "seconds_left": m.duration_sec,
            "running": False,
            "task": pick_task(),
            "submissions": {},
        }
        LIVE_MATCHES[match_id] = state

    if uid == state["p1_id"]:
        state["p1_sid"] = request.sid
    else:
        state["p2_sid"] = request.sid

    task = state["task"]
    emit(
        "match:task",
        {
            "topic": task.get("topic", DEFAULT_TOPIC),
            "difficulty": task.get("difficulty", DEFAULT_DIFFICULTY),
            "prompt": task["prompt"],
        },
        to=room,
    )

    emit(
        "match:state",
        {
            "running": state["running"],
            "seconds_left": state["seconds_left"],
            "me": uname,
            "p1": m.player1_name,
            "p2": m.player2_name,
        },
        to=room,
    )

    if state["p1_sid"] and state["p2_sid"] and not state["running"] and m.status != "ended":
        start_match(match_id)


def start_match(match_id: int):
    m = db.session.get(Match, match_id)
    state = LIVE_MATCHES.get(match_id)
    if not m or not state:
        return

    state["running"] = True
    m.status = "started"
    if hasattr(m, "started_at"):
        m.started_at = datetime.utcnow()
    db.session.commit()

    socketio.emit("match:started", {"seconds_left": state["seconds_left"]}, to=match_room(match_id))
    socketio.start_background_task(timer_task, match_id)


def timer_task(match_id: int):
    with app.app_context():
        room = match_room(match_id)
        state = LIVE_MATCHES.get(match_id)
        if not state:
            return

        while state["running"] and state["seconds_left"] > 0:
            socketio.sleep(1)
            state["seconds_left"] -= 1
            socketio.emit("match:tick", {"seconds_left": state["seconds_left"]}, to=room)

        if not state["running"]:
            return

        finish_match(match_id, reason="time")
        db.session.remove()


@socketio.on("match:submit_answer")
def on_match_submit_answer(data):
    uid, _ = ensure_user()
    if not uid:
        return

    match_id = int((data or {}).get("match_id", 0))
    ans = ((data or {}).get("answer") or "").strip()

    m = db.session.get(Match, match_id)
    if not m or m.status == "ended":
        return
    if uid not in (m.player1_id, m.player2_id):
        return

    state = LIVE_MATCHES.get(match_id)
    if not state or not state.get("running"):
        return

    now = time.time()
    task = state["task"]
    correct = task["answer"]

    sub = state["submissions"].get(uid)
    if not sub:
        sub = {"first_ts": now, "first_correct_ts": None}
        state["submissions"][uid] = sub

    sub["answer"] = ans
    sub["ts"] = now

    if sub["first_correct_ts"] is None and is_correct(ans, correct):
        sub["first_correct_ts"] = now

    socketio.emit("match:submitted", {"user_id": uid}, to=match_room(match_id))

    p1_id = state["p1_id"]
    p2_id = state["p2_id"]
    if p1_id in state["submissions"] and p2_id in state["submissions"]:
        finish_match(match_id, reason="both_submitted")


@socketio.on("match:surrender")
def on_match_surrender(data):
    uid, _ = ensure_user()
    if not uid:
        return
    match_id = int((data or {}).get("match_id", 0))

    m = db.session.get(Match, match_id)
    if not m or m.status == "ended":
        return
    if uid not in (m.player1_id, m.player2_id):
        return

    winner_id = m.player2_id if uid == m.player1_id else m.player1_id
    finish_match(match_id, winner_user_id=winner_id, reason="surrender")


def finish_match(match_id: int, winner_user_id: Optional[int] = None, reason: str = "time"):
    m = db.session.get(Match, match_id)
    state = LIVE_MATCHES.get(match_id)
    if not m or not state:
        return
    if m.status == "ended":
        return
    if not state.get("running"):
        return

    state["running"] = False

    if reason != "surrender":
        task = state["task"]
        correct = task["answer"]

        sub1 = state["submissions"].get(m.player1_id)
        sub2 = state["submissions"].get(m.player2_id)

        p1_ok = is_correct(sub1["answer"], correct) if sub1 else False
        p2_ok = is_correct(sub2["answer"], correct) if sub2 else False

        if p1_ok and not p2_ok:
            winner_user_id = m.player1_id
        elif p2_ok and not p1_ok:
            winner_user_id = m.player2_id
        elif p1_ok and p2_ok:
            t1 = sub1.get("first_correct_ts") if sub1 else None
            t2 = sub2.get("first_correct_ts") if sub2 else None
            if t1 is None or t2 is None:
                winner_user_id = None
            elif t1 < t2:
                winner_user_id = m.player1_id
            elif t2 < t1:
                winner_user_id = m.player2_id
            else:
                winner_user_id = None
        else:
            winner_user_id = None

    m.status = "ended"
    if hasattr(m, "ended_at"):
        m.ended_at = datetime.utcnow()
    if hasattr(m, "winner_user_id"):
        m.winner_user_id = winner_user_id
    if hasattr(m, "reason"):
        m.reason = reason

    p1 = db.session.get(AuthUser, m.player1_id)
    p2 = db.session.get(AuthUser, m.player2_id)
    if p1 and p2:
        r1, r2 = int(p1.rating), int(p2.rating)
        k = int(app.config.get("ELO_K", 32))

        if winner_user_id == m.player1_id:
            s1, s2 = 1.0, 0.0
        elif winner_user_id == m.player2_id:
            s1, s2 = 0.0, 1.0
        else:
            s1, s2 = 0.5, 0.5

        p1.rating = elo_apply(r1, r2, s1, k)
        p2.rating = elo_apply(r2, r1, s2, k)

    db.session.commit()

    task = state["task"]
    correct = task["answer"]
    sub1 = state["submissions"].get(m.player1_id)
    sub2 = state["submissions"].get(m.player2_id)

    payload = {
        "winner_user_id": winner_user_id,
        "reason": reason,
        "p1_id": m.player1_id,
        "p2_id": m.player2_id,
        "p1_name": m.player1_name,
        "p2_name": m.player2_name,
        "correct_answer": correct,
        "p1_answer": sub1["answer"] if sub1 else None,
        "p2_answer": sub2["answer"] if sub2 else None,
        "p1_correct": is_correct(sub1["answer"], correct) if sub1 else False,
        "p2_correct": is_correct(sub2["answer"], correct) if sub2 else False,
    }

    socketio.emit("match:ended", payload, to=match_room(match_id))
    LIVE_MATCHES.pop(match_id, None)


@socketio.on("disconnect")
def on_disconnect():
    remove_from_queue_by_sid(request.sid)


# ----------------------------
if __name__ == "__main__":
    ensure_db()
    socketio.run(app, host="127.0.0.1", port=5000, debug=True)
