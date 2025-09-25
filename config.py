import os
from dotenv import load_dotenv


load_dotenv()


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret')
    SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///community.db')
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    INVITE_CODE = os.getenv('INVITE_CODE', 'invite')
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', '')

    # Optional: Cookies härten, wenn du über Nginx per HTTPS gehst
    SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    SESSION_COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')