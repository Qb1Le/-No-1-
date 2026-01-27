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


class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)

    player1_id = db.Column(db.Integer, db.ForeignKey("auth_user.id"), nullable=False)
    player2_id = db.Column(db.Integer, db.ForeignKey("auth_user.id"), nullable=False)

    player1_name = db.Column(db.String(32), nullable=False)
    player2_name = db.Column(db.String(32), nullable=False)

    # Снимок рейтингов на старте (для прозрачности)
    player1_rating = db.Column(db.Integer, nullable=False)
    player2_rating = db.Column(db.Integer, nullable=False)

    duration_sec = db.Column(db.Integer, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)

    winner_user_id = db.Column(db.Integer, nullable=True)  # null = ничья/время
    reason = db.Column(db.String(32), nullable=True)       # solve/time/surrender/disconnect

    status = db.Column(db.String(16), nullable=False, default="pending")  # pending|started|ended
