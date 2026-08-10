"""League registry: every league wuff observes, across platforms.

data/config/leagues.json is the canonical registry — one entry per league
with a wuff-internal id, platform (yahoo/sleeper/espn), the platform's own
league id, and the league's format/rules. If the file doesn't exist yet the
registry is synthesized in memory from the legacy configs
(league_settings.json for the Yahoo league, sleeper_leagues.json for the
Sleeper leagues) so existing single-league flows keep working;
`python3 -m app leagues-init` persists that synthesis.

A registry entry's format lives either inline ("format": {...}) or in a
separate file ("formatFile": "data/config/league_settings.json"). The Yahoo
league uses formatFile so set-league-format keeps editing a single source
of truth.
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .league_context import LeagueFormat, league_format_from_payload, load_league_format
from .paths import (
    LEAGUE_SETTINGS_FILE,
    LEAGUES_CONFIG_FILE,
    REPO_ROOT,
    SLEEPER_LEAGUES_CONFIG_FILE,
    ensure_parent_dir,
)

PLATFORMS = ('yahoo', 'sleeper', 'espn')
YAHOO_LEAGUE_SLUG = 'frank-gore'


@dataclass
class League:
    league_id: str
    platform: str
    platform_league_id: str
    name: str
    season: Optional[str] = None
    format: LeagueFormat = field(default_factory=LeagueFormat)
    format_file: Optional[str] = None

    def summary(self) -> str:
        season = f' ({self.season})' if self.season else ''
        return f'[{self.platform}] {self.name}{season} — {self.format.teams} teams'


def _league_from_entry(entry: dict) -> Optional[League]:
    league_id = str(entry.get('leagueId', '')).strip()
    platform = str(entry.get('platform', '')).strip().lower()
    if not league_id or platform not in PLATFORMS:
        return None
    format_file = entry.get('formatFile')
    if format_file:
        league_format = load_league_format(REPO_ROOT / format_file)
    else:
        league_format = league_format_from_payload(entry.get('format'))
    return League(
        league_id=league_id,
        platform=platform,
        platform_league_id=str(entry.get('platformLeagueId', '')),
        name=str(entry.get('name', league_id)),
        season=entry.get('season'),
        format=league_format,
        format_file=format_file,
    )


def _synthesize_leagues() -> Tuple[str, List[League]]:
    """Build the registry from the legacy configs when leagues.json doesn't exist."""
    leagues: List[League] = []

    yahoo_format = load_league_format()
    leagues.append(
        League(
            league_id=YAHOO_LEAGUE_SLUG,
            platform='yahoo',
            platform_league_id=str(yahoo_format.league_id or ''),
            name=yahoo_format.league_name or 'Yahoo league',
            format=yahoo_format,
            format_file=str(LEAGUE_SETTINGS_FILE.relative_to(REPO_ROOT)),
        )
    )

    try:
        sleeper_config = json.loads(SLEEPER_LEAGUES_CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        sleeper_config = {}
    for entry in sleeper_config.get('leagues', []):
        if not isinstance(entry, dict) or not entry.get('leagueId'):
            continue
        # Visibility-only for now: team count is known, keeper/roster rules are not,
        # so keeper logic stays off (keeper_slots=0) until rules are configured.
        sleeper_format = LeagueFormat(
            teams=int(entry.get('totalRosters', 12)),
            league_id=str(entry['leagueId']),
            league_name=entry.get('name'),
            keeper_slots=0,
            keeper_ineligible_rounds=[],
            keeper_slot_rounds=[],
        )
        leagues.append(
            League(
                league_id=f"sleeper-{entry['leagueId']}",
                platform='sleeper',
                platform_league_id=str(entry['leagueId']),
                name=str(entry.get('name', entry['leagueId'])),
                season=entry.get('season'),
                format=sleeper_format,
            )
        )

    return YAHOO_LEAGUE_SLUG, leagues


def _load_registry_payload() -> Optional[dict]:
    try:
        payload = json.loads(LEAGUES_CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_leagues() -> Dict[str, League]:
    """All registered leagues keyed by wuff-internal league_id, registry order preserved."""
    payload = _load_registry_payload()
    if payload is not None:
        leagues: Dict[str, League] = {}
        for entry in payload.get('leagues', []):
            if isinstance(entry, dict):
                league = _league_from_entry(entry)
                if league is not None:
                    leagues[league.league_id] = league
        if leagues:
            return leagues
    _, synthesized = _synthesize_leagues()
    return {league.league_id: league for league in synthesized}


def default_league_id() -> str:
    payload = _load_registry_payload()
    if payload is not None:
        configured = str(payload.get('defaultLeagueId', '')).strip()
        if configured:
            return configured
    leagues = load_leagues()
    if YAHOO_LEAGUE_SLUG in leagues:
        return YAHOO_LEAGUE_SLUG
    return next(iter(leagues), YAHOO_LEAGUE_SLUG)


def get_league(league_id: Optional[str] = None) -> League:
    leagues = load_leagues()
    resolved_id = league_id or default_league_id()
    if resolved_id not in leagues:
        known = ', '.join(leagues) or '(none)'
        raise KeyError(f"Unknown league '{resolved_id}'. Known leagues: {known}. Run `leagues-init` to (re)build the registry.")
    return leagues[resolved_id]


def save_leagues_config(default_id: str, leagues: List[League]) -> Path:
    entries = []
    for league in leagues:
        entry: dict = {
            'leagueId': league.league_id,
            'platform': league.platform,
            'platformLeagueId': league.platform_league_id,
            'name': league.name,
            'season': league.season,
        }
        if league.format_file:
            entry['formatFile'] = league.format_file
        else:
            entry['format'] = asdict(league.format)
        entries.append(entry)
    ensure_parent_dir(LEAGUES_CONFIG_FILE)
    LEAGUES_CONFIG_FILE.write_text(json.dumps({'defaultLeagueId': default_id, 'leagues': entries}, indent=2))
    return LEAGUES_CONFIG_FILE


def init_leagues_config(force: bool = False) -> Tuple[Path, bool]:
    """Write leagues.json from the legacy configs. Returns (path, whether it was written)."""
    if LEAGUES_CONFIG_FILE.exists() and not force:
        return LEAGUES_CONFIG_FILE, False
    default_id, leagues = _synthesize_leagues()
    return save_leagues_config(default_id, leagues), True
