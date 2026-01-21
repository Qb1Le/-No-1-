from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Lobby(db.Model):
    __tablename__ = "lobbies"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), unique=True, nullable=False)  # для закрытых
    is_private = db.Column(db.Boolean, default=False, nullable=False)

    host_name = db.Column(db.String(32), nullable=False)
    guest_name = db.Column(db.String(32), nullable=True)

    status = db.Column(db.String(16), nullable=False, default="open")  # open|full|started|closed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey("lobbies.id"), nullable=False)

    host_name = db.Column(db.String(32), nullable=False)
    guest_name = db.Column(db.String(32), nullable=False)

    duration_sec = db.Column(db.Integer, nullable=False)
    started_at = db.Column(db.DateTime, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=True)

    winner = db.Column(db.String(32), nullable=True)  # имя победителя или None
    reason = db.Column(db.String(32), nullable=True)  # "time", "surrender", ...
