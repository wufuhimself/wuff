from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / 'data'
AUTH_DIR = DATA_DIR / 'auth'
CONFIG_DIR = DATA_DIR / 'config'
RAW_DIR = DATA_DIR / 'raw'
PROCESSED_DIR = DATA_DIR / 'processed'
RAW_RANKINGS_DIR = RAW_DIR / 'rankings'
RAW_ADP_DIR = RAW_DIR / 'adp'
RAW_ROSTERS_DIR = RAW_DIR / 'rosters'
RAW_DRAFT_HISTORY_DIR = RAW_DIR / 'draft_history'
RAW_DRAFT_PICKS_DIR = RAW_DIR / 'draft_picks'
RAW_STANDINGS_DIR = RAW_DIR / 'standings'
RAW_NFL_STATS_DIR = RAW_DIR / 'nfl_stats'
RAW_NFL_WEEKLY_STATS_DIR = RAW_NFL_STATS_DIR / 'weekly'
RAW_NFL_SEASONAL_STATS_DIR = RAW_NFL_STATS_DIR / 'seasonal'
RAW_NFL_ROSTERS_DIR = RAW_NFL_STATS_DIR / 'rosters'
PROCESSED_ROSTERS_DIR = PROCESSED_DIR / 'rosters'
PROCESSED_DRAFT_ANALYSIS_DIR = PROCESSED_DIR / 'draft_analysis'
RAW_SLEEPER_DIR = RAW_DIR / 'sleeper'

YAHOO_TOKEN_FILE = AUTH_DIR / 'yahoo_token.json'
LEAGUE_SETTINGS_FILE = CONFIG_DIR / 'league_settings.json'
LEAGUES_CONFIG_FILE = CONFIG_DIR / 'leagues.json'
SLEEPER_LEAGUES_CONFIG_FILE = CONFIG_DIR / 'sleeper_leagues.json'
SLEEPER_PLAYERS_CACHE_FILE = RAW_SLEEPER_DIR / 'players_cache.json'
# Committed (data/config/ is versioned, data/raw/ is not) so a deployed
# container can resolve player positions without the nflverse CSVs, which it
# has no copy of. Regenerate with `python3 -m app snapshot-position-map`.
NFL_POSITION_MAP_FILE = CONFIG_DIR / 'nfl_position_map.json'
# Hand-authored name -> real name map for the players no algorithm can link:
# nicknames ("Hollywood Brown" is Marquise Brown) and short forms ("Josh
# Palmer" is Joshua Palmer). Committed, because it is curated data.
PLAYER_ALIASES_FILE = CONFIG_DIR / 'player_aliases.json'


RAW_ESPN_DIR = RAW_DIR / 'espn'


def sleeper_league_dir(league_id: str) -> Path:
    return RAW_SLEEPER_DIR / league_id


def espn_league_dir(league_id: str) -> Path:
    return RAW_ESPN_DIR / league_id
YAHOO_RANKINGS_FILE = RAW_RANKINGS_DIR / 'yahoo_rankings.json'
RANKINGS_COMBINED_FILE = RAW_RANKINGS_DIR / 'rankings_combined.json'
YAHOO_ROSTER_FILE = RAW_ROSTERS_DIR / 'yahoo_roster.json'
YAHOO_LEAGUE_ROSTERS_JSON = RAW_ROSTERS_DIR / 'yahoo_league_rosters.json'
YAHOO_LEAGUE_ROSTERS_CSV = PROCESSED_ROSTERS_DIR / 'yahoo_league_rosters.csv'


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
