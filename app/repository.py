"""Per-league data access layer (Phase 0 storage seam of docs/roadmap.md).

League-scoped reads go through a LeagueDataRepository obtained from
get_repository(league_id). Today every backend reads the existing JSON
snapshots (the Yahoo league's data/raw/ files, data/raw/sleeper/{id}/ for
Sleeper leagues); when a database arrives it becomes another backend behind
the same interface and call sites don't change.

Normalized shapes every backend serves:
- rosters(): [{'teamName': str, 'players': [{'playerId','playerName','position','team',...}]}]
- draft_years(): {year: [{'round','pick','playerName','team',...}, ...]}
- standings(year): [{'team','wins','losses',...}] or None when unsaved
- rankings(): market rankings [{'playerName','position','team','ranking',...}]
"""
import json
from typing import Any, Dict, List, Optional

from . import espn_manager, sleeper_manager
from .draft_history import load_draft_years
from .draft_picks import load_draft_pick_origins, load_draft_picks
from .league_registry import League, get_league
from .paths import RANKINGS_COMBINED_FILE, RAW_STANDINGS_DIR, YAHOO_LEAGUE_ROSTERS_JSON
from .standings import load_standings
from .strategy import load_yahoo_rankings


class LeagueDataRepository:
    """Interface. draft_picks/draft_pick_origins (traded-pick ownership) may
    legitimately be None for platforms that don't track them."""

    def __init__(self, league: League):
        self.league = league

    def rosters(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def draft_years(self) -> Dict[int, List[dict]]:
        raise NotImplementedError

    def standings_years(self) -> List[int]:
        raise NotImplementedError

    def standings(self, year: int) -> Optional[List[Dict[str, Any]]]:
        raise NotImplementedError

    def rankings(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def draft_picks(self, year: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def draft_pick_origins(self, year: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError


class YahooJsonRepository(LeagueDataRepository):
    """The original single-league data layout under data/raw/ — valid only for
    the league league_settings.json describes (frank-gore)."""

    def rosters(self) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(YAHOO_LEAGUE_ROSTERS_JSON.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def draft_years(self) -> Dict[int, List[dict]]:
        return load_draft_years()

    def standings_years(self) -> List[int]:
        if not RAW_STANDINGS_DIR.exists():
            return []
        return sorted((int(p.stem) for p in RAW_STANDINGS_DIR.glob('*.json')), reverse=True)

    def standings(self, year: int) -> Optional[List[Dict[str, Any]]]:
        return load_standings(year)

    def rankings(self) -> List[Dict[str, Any]]:
        return load_yahoo_rankings()

    def draft_picks(self, year: int) -> Optional[Dict[str, Any]]:
        return load_draft_picks(year)

    def draft_pick_origins(self, year: int) -> Optional[Dict[str, Any]]:
        return load_draft_pick_origins(year)


class SnapshotJsonRepository(LeagueDataRepository):
    """Shared backend over the snapshot layout sleeper_manager defines
    (rosters.json / draft_*.json / league.json under a per-league dir).
    Subclasses point `snapshots` at the platform's manager module."""

    snapshots = sleeper_manager

    @property
    def _platform_id(self) -> str:
        return self.league.platform_league_id

    def rosters(self) -> List[Dict[str, Any]]:
        return self.snapshots.load_synced_rosters(self._platform_id)

    def draft_years(self) -> Dict[int, List[dict]]:
        team_by_roster_id = {
            roster.get('rosterId'): roster.get('teamName')
            for roster in self.snapshots.load_synced_rosters(self._platform_id)
        }
        years: Dict[int, List[dict]] = {}
        for draft in self.snapshots.load_synced_drafts(self._platform_id):
            try:
                season = int(draft.get('season'))
            except (TypeError, ValueError):
                continue
            picks = [
                {**pick, 'team': team_by_roster_id.get(pick.get('rosterId'))}
                for pick in draft.get('picks') or []
            ]
            years[season] = picks
        return years

    def standings_years(self) -> List[int]:
        league = self.snapshots.load_synced_league(self._platform_id)
        if league is None:
            return []
        try:
            return [int(league.get('season'))]
        except (TypeError, ValueError):
            return []

    def standings(self, year: int) -> Optional[List[Dict[str, Any]]]:
        if year not in self.standings_years():
            return None
        rosters = sorted(
            self.snapshots.load_synced_rosters(self._platform_id),
            key=lambda r: (-(r.get('wins') or 0), r.get('losses') or 0, -(r.get('fpts') or 0)),
        )
        return [
            {
                'team': roster.get('teamName'),
                'wins': roster.get('wins'),
                'losses': roster.get('losses'),
                'ties': roster.get('ties'),
                'pointsFor': roster.get('fpts'),
                'pointsAgainst': roster.get('fptsAgainst'),
            }
            for roster in rosters
        ]

    def rankings(self) -> List[Dict[str, Any]]:
        # The shared consensus board, NOT the Yahoo league's QB-adjusted one —
        # that adjustment is derived from frank-gore's own draft history.
        try:
            payload = json.loads(RANKINGS_COMBINED_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def draft_picks(self, year: int) -> Optional[Dict[str, Any]]:
        return {}  # snapshot platforms don't expose traded-pick ownership by round

    def draft_pick_origins(self, year: int) -> Optional[Dict[str, Any]]:
        return {}


class SleeperJsonRepository(SnapshotJsonRepository):
    snapshots = sleeper_manager


class EspnJsonRepository(SnapshotJsonRepository):
    snapshots = espn_manager


def get_repository(league_id: Optional[str] = None) -> LeagueDataRepository:
    league = get_league(league_id)
    if league.platform == 'sleeper':
        return SleeperJsonRepository(league)
    if league.platform == 'espn':
        return EspnJsonRepository(league)
    if league.platform == 'yahoo':
        return YahooJsonRepository(league)
    raise ValueError(f"No repository backend for platform '{league.platform}' (league '{league.league_id}').")
