import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///examarena.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # матч по умолчанию (сек)
    DEFAULT_MATCH_SECONDS = int(os.environ.get("MATCH_SECONDS", "600"))  # 10 минут
