from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class AuthUser(db.Model):
    __tablename__ = "auth_user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    rating = db.Column(db.Integer, nullable=False, default=1000)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)



class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)

    player1_id = db.Column(db.Integer, db.ForeignKey("auth_user.id"), nullable=False)
    player2_id = db.Column(db.Integer, db.ForeignKey("auth_user.id"), nullable=False)

    player1_name = db.Column(db.String(32), nullable=False)
    player2_name = db.Column(db.String(32), nullable=False)

    player1_rating = db.Column(db.Integer, nullable=False)
    player2_rating = db.Column(db.Integer, nullable=False)

    duration_sec = db.Column(db.Integer, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)

    winner_user_id = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.String(32), nullable=True)

    status = db.Column(db.String(16), nullable=False, default="pending")

    from datetime import datetime


class Task(db.Model):
    __tablename__ = "task"

    id = db.Column(db.Integer, primary_key=True)

    prompt = db.Column(db.Text, nullable=False)
    answer = db.Column(db.String(200), nullable=False)

    kind = db.Column(db.String(20), nullable=False, default="text")

    topic = db.Column(db.String(64), nullable=False, default="Общее")
    difficulty = db.Column(db.String(64), nullable=False, default="Средняя")
    subject = db.Column(db.String(64), nullable=False, default="Математика")

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
