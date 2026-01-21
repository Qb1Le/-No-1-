import random
import string
import time
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, abort
from flask_socketio import SocketIO, join_room, leave_room, emit
from sqlalchemy import desc

from config import Config
from models import db, Lobby, Match

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# -----------------------
# In-memory runtime state
# -----------------------
# match_room -> dict(state)
# state: {
#   "lobby_id": int,
#   "host_sid": str,
#   "guest_sid": str,
#   "host_name": str,
#   "guest_name": str,
#   "seconds_left": int,
#   "running": bool,
#   "started_at": datetime
# }
LIVE_MATCHES = {}

def gen_code(n=8):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))

def lobby_room(lobby_id: int) -> str:
    return f"lobby:{lobby_id}"

def match_room(lobby_id: int) -> str:
    return f"match:{lobby_id}"

# -----------------------
# HTTP routes
# -----------------------
@app.route("/", methods=["GET"])
def index():
    # показываем только open и public
    lobbies = (
        Lobby.query
        .filter(Lobby.status == "open", Lobby.is_private == False)  # noqa: E712
        .order_by(desc(Lobby.created_at))
        .limit(50)
        .all()
    )
    return render_template("index.html", lobbies=lobbies)

@app.route("/lobby/create", methods=["POST"])
def create_lobby():
    name = (request.form.get("name") or "").strip()[:32]
    private = request.form.get("private") == "on"
    if not name:
        abort(400, "Name required")

    code = gen_code(8) if private else gen_code(6)

    lobby = Lobby(code=code, is_private=private, host_name=name, status="open")
    db.session.add(lobby)
    db.session.commit()

    return redirect(url_for("lobby_page", lobby_id=lobby.id, name=name, code=code))

@app.route("/lobby/join", methods=["POST"])
def join_private():
    name = (request.form.get("name") or "").strip()[:32]
    code = (request.form.get("code") or "").strip().upper()

    if not name or not code:
        abort(400, "Name and code required")

    lobby = Lobby.query.filter_by(code=code).first()
    if not lobby or lobby.status != "open":
        abort(404, "Lobby not found or not open")

    # гость резервируется тут же, чтобы не было гонки в UI
    lobby.guest_name = name
    lobby.status = "full"
    db.session.commit()

    return redirect(url_for("lobby_page", lobby_id=lobby.id, name=name, code=code))

@app.route("/lobby/<int:lobby_id>", methods=["GET"])
def lobby_page(lobby_id):
    name = (request.args.get("name") or "").strip()[:32]
    if not name:
        # базовая защита: без имени не пускаем (можно заменить на auth)
        return redirect(url_for("index"))

    lobby = Lobby.query.get_or_404(lobby_id)
    return render_template("lobby.html", lobby=lobby, name=name)

@app.route("/match/<int:lobby_id>", methods=["GET"])
def match_page(lobby_id):
    name = (request.args.get("name") or "").strip()[:32]
    if not name:
        return redirect(url_for("index"))

    lobby = Lobby.query.get_or_404(lobby_id)
    if lobby.status not in ("full", "started"):
        return redirect(url_for("lobby_page", lobby_id=lobby_id, name=name))

    return render_template("match.html", lobby=lobby, name=name, match_seconds=app.config["DEFAULT_MATCH_SECONDS"])

# -----------------------
# Socket.IO handlers
# -----------------------
@socketio.on("lobby:join")
def on_lobby_join(data):
    lobby_id = int(data["lobby_id"])
    name = (data["name"] or "").strip()[:32]
    role = data.get("role")  # "host"|"guest"
    if not name:
        return

    lobby = Lobby.query.get(lobby_id)
    if not lobby:
        emit("toast", {"type": "danger", "text": "Лобби не найдено"})
        return

    # Присоединяем к комнате лобби
    join_room(lobby_room(lobby_id))

    # Если это публичное лобби и гость пришёл через список (HTTP не резервировал)
    if role == "guest" and lobby.status == "open":
        lobby.guest_name = name
        lobby.status = "full"
        db.session.commit()

    emit("lobby:state", lobby_state(lobby), to=lobby_room(lobby_id))

    # если лобби уже full — предлагаем перейти в матч
    if lobby.status == "full":
        emit("lobby:ready", {"lobby_id": lobby_id}, to=lobby_room(lobby_id))

def lobby_state(lobby: Lobby):
    return {
        "id": lobby.id,
        "is_private": lobby.is_private,
        "code": lobby.code if lobby.is_private else None,
        "host_name": lobby.host_name,
        "guest_name": lobby.guest_name,
        "status": lobby.status,
    }

@socketio.on("match:join")
def on_match_join(data):
    lobby_id = int(data["lobby_id"])
    name = (data["name"] or "").strip()[:32]
    if not name:
        return

    lobby = Lobby.query.get(lobby_id)
    if not lobby:
        emit("toast", {"type": "danger", "text": "Лобби не найдено"})
        return

    # Проверяем, что это один из игроков
    if name not in (lobby.host_name, lobby.guest_name):
        emit("toast", {"type": "danger", "text": "Вы не участник этого матча"})
        return

    room = match_room(lobby_id)
    join_room(room)

    # Создаём LIVE состояние, когда оба реально подключились
    state = LIVE_MATCHES.get(room)
    if not state:
        state = {
            "lobby_id": lobby_id,
            "host_sid": None,
            "guest_sid": None,
            "host_name": lobby.host_name,
            "guest_name": lobby.guest_name,
            "seconds_left": app.config["DEFAULT_MATCH_SECONDS"],
            "running": False,
            "started_at": None,
        }
        LIVE_MATCHES[room] = state

    if name == state["host_name"]:
        state["host_sid"] = request.sid
    elif name == state["guest_name"]:
        state["guest_sid"] = request.sid

    emit("match:state", public_match_state(state), to=room)

    # если оба на месте — стартуем (один раз)
    if state["host_sid"] and state["guest_sid"] and not state["running"]:
        start_match(lobby)

def public_match_state(state):
    return {
        "host_name": state["host_name"],
        "guest_name": state["guest_name"],
        "seconds_left": state["seconds_left"],
        "running": state["running"],
    }

def start_match(lobby: Lobby):
    room = match_room(lobby.id)
    state = LIVE_MATCHES[room]

    lobby.status = "started"
    db.session.commit()

    state["running"] = True
    state["started_at"] = datetime.utcnow()

    # фиксируем матч в БД
    m = Match(
        lobby_id=lobby.id,
        host_name=state["host_name"],
        guest_name=state["guest_name"],
        duration_sec=state["seconds_left"],
        started_at=state["started_at"],
    )
    db.session.add(m)
    db.session.commit()

    socketio.emit("match:started", {"seconds_left": state["seconds_left"]}, to=room)

    # фоновый таймер
    socketio.start_background_task(timer_task, lobby.id)

def timer_task(lobby_id: int):
    room = match_room(lobby_id)
    state = LIVE_MATCHES.get(room)
    if not state:
        return

    while state["running"] and state["seconds_left"] > 0:
        time.sleep(1)
        state["seconds_left"] -= 1
        socketio.emit("match:tick", {"seconds_left": state["seconds_left"]}, to=room)

    if not state["running"]:
        return

    # время вышло
    finish_match(lobby_id, winner=None, reason="time")

@socketio.on("match:finish")
def on_match_finish(data):
    lobby_id = int(data["lobby_id"])
    name = (data["name"] or "").strip()[:32]
    winner = (data.get("winner") or "").strip()[:32]
    reason = (data.get("reason") or "solve").strip()[:32]

    # минимальная валидация: закончить может только участник
    lobby = Lobby.query.get(lobby_id)
    if not lobby or name not in (lobby.host_name, lobby.guest_name):
        return

    # winner может быть либо один из игроков, либо пусто
    if winner and winner not in (lobby.host_name, lobby.guest_name):
        winner = None

    finish_match(lobby_id, winner=winner, reason=reason)

def finish_match(lobby_id: int, winner, reason: str):
    room = match_room(lobby_id)
    state = LIVE_MATCHES.get(room)
    if not state:
        return

    state["running"] = False

    # обновляем БД
    lobby = Lobby.query.get(lobby_id)
    if lobby:
        lobby.status = "closed"
    match = Match.query.filter_by(lobby_id=lobby_id).order_by(desc(Match.id)).first()
    if match and match.ended_at is None:
        match.ended_at = datetime.utcnow()
        match.winner = winner
        match.reason = reason

    db.session.commit()

    socketio.emit("match:ended", {"winner": winner, "reason": reason}, to=room)
    # очищаем runtime state (можно держать ещё N минут)
    LIVE_MATCHES.pop(room, None)

@socketio.on("disconnect")
def on_disconnect():
    # можно расширить: если один из игроков вышел — засчитать победу оставшемуся
    pass

# -----------------------
# Bootstrap DB
# -----------------------
def ensure_db():
    with app.app_context():
        db.create_all()

if __name__ == "__main__":
    ensure_db()
    socketio.run(app, host="127.0.0.1", port=5000, debug=True)
