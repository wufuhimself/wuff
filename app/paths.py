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
RAW_MANAGERS_DIR = RAW_DIR / 'managers'
RAW_NFL_STATS_DIR = RAW_DIR / 'nfl_stats'
RAW_NFL_WEEKLY_STATS_DIR = RAW_NFL_STATS_DIR / 'weekly'
RAW_NFL_SEASONAL_STATS_DIR = RAW_NFL_STATS_DIR / 'seasonal'
RAW_NFL_ROSTERS_DIR = RAW_NFL_STATS_DIR / 'rosters'
RAW_NFL_SCHEDULES_DIR = RAW_NFL_STATS_DIR / 'schedules'
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
# Committed for the same reason as NFL_POSITION_MAP_FILE: a deployed
# container has no copy of the nflverse schedule CSVs. Regenerate with
# `python3 -m app snapshot-byes`.
NFL_BYE_WEEKS_FILE = CONFIG_DIR / 'nfl_bye_weeks.json'
# Hand-authored name -> real name map for the players no algorithm can link:
# nicknames ("Hollywood Brown" is Marquise Brown) and short forms ("Josh
# Palmer" is Joshua Palmer). Committed, because it is curated data.
PLAYER_ALIASES_FILE = CONFIG_DIR / 'player_aliases.json'
# Hand-authored {platform: {league_id: {franchise_key: [team names]}}}. The
# only way a Yahoo league gets cross-season manager identity -- its standings
# carry no owner id and Yahoo's rename note almost never fires. Committed,
# because it is knowledge no algorithm can recover. See app/franchise_registry.py.
FRANCHISE_ALIASES_FILE = CONFIG_DIR / 'franchise_aliases.json'


YAHOO_RANKINGS_FILE = RAW_RANKINGS_DIR / 'yahoo_rankings.json'
RANKINGS_COMBINED_FILE = RAW_RANKINGS_DIR / 'rankings_combined.json'
YAHOO_ROSTER_FILE = RAW_ROSTERS_DIR / 'yahoo_roster.json'
YAHOO_LEAGUE_ROSTERS_JSON = RAW_ROSTERS_DIR / 'yahoo_league_rosters.json'
YAHOO_LEAGUE_ROSTERS_CSV = PROCESSED_ROSTERS_DIR / 'yahoo_league_rosters.csv'


def ensure_parent_dir(path: Path) -> None:
    """Make the directory a FILE will be written into.

    Takes the path of the file itself, not its directory -- pass a directory
    and this creates its parent and leaves the directory itself missing, so
    the write fails with FileNotFoundError. Use ensure_dir() for that case.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_dir(path: Path) -> None:
    """Make a directory that several files will be written into.

    The counterpart to ensure_parent_dir: that one takes a file path and makes
    the directory above it, this one takes the directory path itself.
    """
    path.mkdir(parents=True, exist_ok=True)
