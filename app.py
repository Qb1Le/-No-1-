import time
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Dict, Optional, List

from flask import Flask, render_template, request, redirect, url_for, abort, session
from flask_socketio import SocketIO, join_room, emit
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from models import db, AuthUser, Match

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

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
    # expected score for A
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))

def elo_apply(r_a: int, r_b: int, score_a: float, k: int) -> int:
    # returns new rating for A
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

# One global queue for demo (for real prod you'd shard/lock/etc.)
WAITING: List[QueueEntry] = []

# Live match runtime state
# match_id -> state
# state = {
#   "p1_sid": str|None,
#   "p2_sid": str|None,
#   "p1_id": int,
#   "p2_id": int,
#   "seconds_left": int,
#   "running": bool,
# }
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
    """
    Find opponent in WAITING with minimal rating diff.
    """
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

def create_match(p1: QueueEntry, p2: QueueEntry) -> Match:
    # For determinism: p1 is the one who clicked now, but doesn't matter.
    m = Match(
        player1_id=p1.user_id,
        player2_id=p2.user_id,
        player1_name=p1.username,
        player2_name=p2.username,
        player1_rating=p1.rating,
        player2_rating=p2.rating,
        duration_sec=int(app.config["DEFAULT_MATCH_SECONDS"]),
        status="pending",
    )
    db.session.add(m)
    db.session.commit()
    return m

def ensure_db():
    with app.app_context():
        db.create_all()

# ----------------------------
# HTTP routes
# ----------------------------
@app.route("/", methods=["GET"])
@login_required
def index():
    user = AuthUser.query.get(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))
    return render_template("index.html", user=user)

@app.route("/match/<int:match_id>", methods=["GET"])
@login_required
def match_page(match_id: int):
    uid, uname = ensure_user()
    m = Match.query.get_or_404(match_id)
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
        match_seconds=app.config["DEFAULT_MATCH_SECONDS"],
    )

# ----------------------------
# Auth routes
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
# Socket.IO: matchmaking
# ----------------------------
@socketio.on("queue:join")
def on_queue_join(_data):
    uid, uname = ensure_user()
    if not uid:
        emit("toast", {"type": "danger", "text": "Нужно войти."})
        return

    # prevent duplicates
    remove_from_queue_by_user(uid)

    user = AuthUser.query.get(uid)
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

    # Try to match immediately against someone already waiting
    opponent = find_best_opponent(entry)
    if opponent:
        # remove opponent from queue
        remove_from_queue_by_user(opponent.user_id)

        # create match in DB
        m = create_match(entry, opponent)

        # init live state
        LIVE_MATCHES[m.id] = {
            "p1_sid": None,
            "p2_sid": None,
            "p1_id": m.player1_id,
            "p2_id": m.player2_id,
            "seconds_left": m.duration_sec,
            "running": False,
        }

        # notify both clients to navigate
        emit("match:found", {
            "match_id": m.id,
            "opponent_name": opponent.username,
            "opponent_rating": opponent.rating
        }, to=entry.sid)

        emit("match:found", {
            "match_id": m.id,
            "opponent_name": entry.username,
            "opponent_rating": entry.rating
        }, to=opponent.sid)

        return

    # No opponent -> add to queue
    WAITING.append(entry)
    emit("queue:status", {"status": "searching", "rating": entry.rating})

@socketio.on("queue:leave")
def on_queue_leave(_data):
    uid, _ = ensure_user()
    if uid:
        remove_from_queue_by_user(uid)
    emit("queue:status", {"status": "idle"})

@socketio.on("match:join")
def on_match_join(data):
    uid, uname = ensure_user()
    if not uid:
        emit("toast", {"type": "danger", "text": "Нужно войти."})
        return

    match_id = int(data.get("match_id", 0))
    if not match_id:
        emit("toast", {"type": "danger", "text": "Некорректный match_id."})
        return

    m = Match.query.get(match_id)
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
        # Could happen after server restart. Recreate minimal live state.
        state = {
            "p1_sid": None,
            "p2_sid": None,
            "p1_id": m.player1_id,
            "p2_id": m.player2_id,
            "seconds_left": m.duration_sec,
            "running": False,
        }
        LIVE_MATCHES[match_id] = state

    if uid == state["p1_id"]:
        state["p1_sid"] = request.sid
    else:
        state["p2_sid"] = request.sid

    emit("match:state", {
        "running": state["running"],
        "seconds_left": state["seconds_left"],
        "me": uname,
        "p1": m.player1_name,
        "p2": m.player2_name,
    }, to=room)

    # start when both connected and match not started
    if state["p1_sid"] and state["p2_sid"] and not state["running"] and m.status != "ended":
        start_match(match_id)

def start_match(match_id: int):
    m = Match.query.get(match_id)
    if not m:
        return

    state = LIVE_MATCHES.get(match_id)
    if not state:
        return

    state["running"] = True
    m.status = "started"
    m.started_at = datetime.utcnow()
    db.session.commit()

    socketio.emit("match:started", {"seconds_left": state["seconds_left"]}, to=match_room(match_id))
    socketio.start_background_task(timer_task, match_id)

def timer_task(match_id: int):
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

    finish_match(match_id, winner_user_id=None, reason="time")

@socketio.on("match:finish")
def on_match_finish(data):
    uid, _ = ensure_user()
    if not uid:
        return

    match_id = int(data.get("match_id", 0))
    reason = (data.get("reason") or "solve").strip()[:32]

    m = Match.query.get(match_id)
    if not m or m.status == "ended":
        return
    if uid not in (m.player1_id, m.player2_id):
        return

    if reason == "solve":
        winner_user_id = uid
    elif reason == "surrender":
        winner_user_id = m.player2_id if uid == m.player1_id else m.player1_id
    else:
        winner_user_id = None

    finish_match(match_id, winner_user_id=winner_user_id, reason=reason)

def finish_match(match_id: int, winner_user_id: Optional[int], reason: str):
    m = Match.query.get(match_id)
    if not m or m.status == "ended":
        return

    state = LIVE_MATCHES.get(match_id)
    if state:
        if not state["running"]:
            return
        state["running"] = False

    m.status = "ended"
    m.ended_at = datetime.utcnow()
    m.winner_user_id = winner_user_id
    m.reason = reason

    # Elo update (only if there is a winner; time -> no changes)
    if winner_user_id in (m.player1_id, m.player2_id):
        p1 = AuthUser.query.get(m.player1_id)
        p2 = AuthUser.query.get(m.player2_id)
        if p1 and p2:
            r1, r2 = int(p1.rating), int(p2.rating)
            k = int(app.config["ELO_K"])

            if winner_user_id == m.player1_id:
                s1, s2 = 1.0, 0.0
            else:
                s1, s2 = 0.0, 1.0

            p1.rating = elo_apply(r1, r2, s1, k)
            p2.rating = elo_apply(r2, r1, s2, k)

    db.session.commit()

    # notify clients
    socketio.emit("match:ended", {
        "winner_user_id": winner_user_id,
        "reason": reason,
        "p1_id": m.player1_id,
        "p2_id": m.player2_id,
        "p1_name": m.player1_name,
        "p2_name": m.player2_name
    }, to=match_room(match_id))

    # cleanup
    LIVE_MATCHES.pop(match_id, None)

@socketio.on("disconnect")
def on_disconnect():
    # If user was searching -> remove from queue
    remove_from_queue_by_sid(request.sid)

# ----------------------------
if __name__ == "__main__":
    ensure_db()
    socketio.run(app, host="127.0.0.1", port=5000, debug=True)
