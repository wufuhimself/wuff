import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path('.') / '.env')

REQUIRED_ENV_VARS = [
    'YAHOO_CLIENT_ID',
    'YAHOO_CLIENT_SECRET',
    'YAHOO_REDIRECT_URI',
    'YAHOO_LEAGUE_ID',
    'YAHOO_TEAM_KEY',
]

for key in REQUIRED_ENV_VARS:
    if not os.getenv(key):
        raise EnvironmentError(f"Missing required environment variable: {key}")


class Config:
    yahoo_client_id: str = os.environ['YAHOO_CLIENT_ID']
    yahoo_client_secret: str = os.environ['YAHOO_CLIENT_SECRET']
    yahoo_redirect_uri: str = os.environ['YAHOO_REDIRECT_URI']
    yahoo_league_id: str = os.environ['YAHOO_LEAGUE_ID']
    yahoo_team_key: str = os.environ['YAHOO_TEAM_KEY']
    yahoo_ssl_key_path: str | None = os.environ.get('YAHOO_SSL_KEY_PATH')
    yahoo_ssl_cert_path: str | None = os.environ.get('YAHOO_SSL_CERT_PATH')


config = Config()
