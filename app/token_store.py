import json
import time
from pathlib import Path
from typing import Optional

import requests

from .paths import YAHOO_TOKEN_FILE, ensure_parent_dir
from .yahoo_client import YahooToken, refresh_token

DEFAULT_TOKEN_FILE = YAHOO_TOKEN_FILE


def load_token(token_file: Path = DEFAULT_TOKEN_FILE) -> Optional[YahooToken]:
    try:
        data = json.loads(token_file.read_text())
        return YahooToken(**data)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return None


def save_token(token: YahooToken, token_file: Path = DEFAULT_TOKEN_FILE) -> None:
    ensure_parent_dir(token_file)
    token_file.write_text(json.dumps(token.__dict__, indent=2))


def token_needs_refresh(token: YahooToken) -> bool:
    return token.expires_at <= int(time.time()) + 60


def get_valid_token(token_file: Path = DEFAULT_TOKEN_FILE) -> Optional[YahooToken]:
    token = load_token(token_file)
    if token is None:
        return None

    if token_needs_refresh(token):
        if not token.refresh_token:
            return None
        try:
            token = refresh_token(token.refresh_token)
        except (requests.exceptions.RequestException, ValueError, TypeError):
            # Yahoo rejected the refresh (expired/revoked refresh token, network issue, etc.)
            # Treat this the same as "no saved token" instead of crashing the caller -- the
            # user just needs to re-run auth-server.
            return None
        save_token(token, token_file)

    return token
