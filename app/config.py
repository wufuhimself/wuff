import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path('.') / '.env')


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return value


class Config:
    """Yahoo OAuth config, read from the environment lazily -- on attribute
    access, not on import.

    This used to fail at import time (a module-level loop over the 5
    required Yahoo vars that raised immediately), which meant importing
    yahoo_client.py -- and transitively, anything that imports it, including
    every command in cli.py, since it imports yahoo_client/roster_store/
    mcp_client at module level -- crashed the whole process on any host with
    no Yahoo vars set. Caught live: Railway's sync-sweep cron service has
    none (it has no reason to touch Yahoo at all) and `python3 -m app
    sync-sweep` never got past the import line. Properties defer the
    failure to the first Yahoo-specific call that actually needs a value,
    which for a non-Yahoo command is never.
    """

    @property
    def yahoo_client_id(self) -> str:
        return _require('YAHOO_CLIENT_ID')

    @property
    def yahoo_client_secret(self) -> str:
        return _require('YAHOO_CLIENT_SECRET')

    @property
    def yahoo_redirect_uri(self) -> str:
        return _require('YAHOO_REDIRECT_URI')

    @property
    def yahoo_league_id(self) -> str:
        return _require('YAHOO_LEAGUE_ID')

    @property
    def yahoo_team_key(self) -> str:
        return _require('YAHOO_TEAM_KEY')

    @property
    def yahoo_ssl_key_path(self) -> str | None:
        return os.environ.get('YAHOO_SSL_KEY_PATH')

    @property
    def yahoo_ssl_cert_path(self) -> str | None:
        return os.environ.get('YAHOO_SSL_CERT_PATH')


config = Config()
