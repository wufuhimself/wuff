"""Login plumbing (Flask-Login) with a dev email-only login.

No password and no email verification yet — this is the local-development
account system so the multi-user flows can be built and exercised. A real
login transport (magic-link email or Google sign-in) replaces the dev form
before any public deploy; see docs/roadmap.md Phase 1/4.
"""
from typing import Optional

from flask import Flask
from flask_login import LoginManager

from .db import SessionLocal
from .models import User

login_manager = LoginManager()


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
