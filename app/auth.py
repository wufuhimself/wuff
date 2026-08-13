"""Login plumbing (Flask-Login) with magic-link email as the login transport.

A login token is a signed, expiring token (itsdangerous) that encodes an
email address — not a session, not a password. /login POSTs an email and
gets one mailed via app/mailer.py; /login/verify/<token> checks the
signature + expiry and only then creates/logs in the User row. Proves
ownership of the inbox, which the earlier dev-only "type any email in" form
never did (see docs/roadmap.md Phase 1).
"""
import threading
import time
from typing import Optional

from flask import Flask
from flask_login import LoginManager
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .db import SessionLocal
from .models import User

login_manager = LoginManager()

LOGIN_TOKEN_MAX_AGE_SECONDS = 15 * 60
_LOGIN_TOKEN_SALT = 'wuff-magic-link'

# Per-email send cooldown, separate from app/rate_limit.py's RateLimiter:
# that one blocks-and-waits for a shared budget (right for outbound API
# calls), this one rejects outright so repeated clicks/refreshes on the
# login form can't be used to spam one inbox or burn Resend quota.
LOGIN_SEND_COOLDOWN_SECONDS = 60
_last_sent_at: dict = {}
_last_sent_lock = threading.Lock()


def login_send_allowed(email: str) -> bool:
    """True (and records the attempt) if a magic link may be sent to this
    email now; False if one was sent too recently."""
    normalized = email.strip().lower()
    now = time.monotonic()
    with _last_sent_lock:
        last = _last_sent_at.get(normalized)
        if last is not None and now - last < LOGIN_SEND_COOLDOWN_SECONDS:
            return False
        _last_sent_at[normalized] = now
        return True


def _serializer(app_secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app_secret_key, salt=_LOGIN_TOKEN_SALT)


def generate_login_token(app_secret_key: str, email: str) -> str:
    return _serializer(app_secret_key).dumps(email.strip().lower())


def verify_login_token(app_secret_key: str, token: str) -> Optional[str]:
    """Returns the email the token was issued for, or None if the token is
    expired, tampered with, or otherwise invalid. Never raises."""
    try:
        return _serializer(app_secret_key).loads(token, max_age=LOGIN_TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def init_auth(app: Flask) -> None:
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    login_manager.login_message = 'Log in to see your leagues.'


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    with SessionLocal() as session:
        return session.get(User, int(user_id))


def get_or_create_user(email: str) -> User:
    normalized = email.strip().lower()
    with SessionLocal() as session:
        user = session.query(User).filter_by(email=normalized).one_or_none()
        if user is None:
            user = User(email=normalized)
            session.add(user)
            session.commit()
        return user
