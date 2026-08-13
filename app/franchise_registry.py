"""Franchise identity — the stable team key wuff never had (Phase 5 step 2).

A "team" has been a display-name string everywhere: `KeeperMark.team_name`,
standings `'team'`, draft-pick `'team'`, mock-draft ordering. Managers rename
their team constantly -- this league's own standings show 12 slots wearing 47
different names across 5 seasons -- so a rename orphans that manager's data,
and cross-season aggregation counts one person as several. That is exactly
why `manager_report.py` reports 24 "managers" for a 12-team league.

How identity is established, per platform, in descending order of trust:

- **Sleeper / ESPN: solved, and the data was already there.** Every synced
  roster carries `rosterId` and `ownerId`; `SnapshotJsonRepository.standings()`
  simply dropped them and keyed on the display name instead. `ownerId` is the
  strongest key (it survives a manager taking over a different roster slot),
  `rosterId` is the fallback.
- **Yahoo: not solvable algorithmically, and this is settled.** The standings
  snapshots hold team name and season stats, no owner id, and re-fetching one
  is blocked on API approval. Yahoo's own rename note fires for exactly one
  of the ~47 name changes in this league's history. `manager_report.py`
  already concluded a hand-authored alias file is the only real fix; this
  module reads that file (`data/config/franchise_aliases.json`) and falls
  back to name-as-identity for anything it doesn't cover, which is the
  current behaviour rather than a guess.

Nothing here infers identity from name *similarity*. The only name-based
links it will make are exact ones: two names whose slugs are identical
("Balls Deep" / "BALLS DEEP" — same string modulo case and punctuation), and
a roster name whose tail after " - " exactly equals a known team name (the
Yahoo paste prefixes every roster with the league name). Anything softer than
exact-after-normalization is the algorithmic-manager-identity mistake this
project already made once, and stays out.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .paths import FRANCHISE_ALIASES_FILE, RAW_MANAGERS_DIR
from .standings import current_team_names

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r'[^a-z0-9]+')


def slugify(value: str) -> str:
    return _SLUG_RE.sub('-', str(value).strip().lower()).strip('-') or 'unknown'


@dataclass
class Franchise:
    """One team slot in one league, across every name it has ever worn."""
    franchise_id: str
    platform: str
    platform_league_id: str
    name: str
    manager_display_name: Optional[str] = None
    owner_id: Optional[str] = None
    roster_id: Optional[str] = None
    names: List[str] = field(default_factory=list)
    source: str = 'name'

    def summary(self) -> str:
        extra = f' ({len(self.names)} names)' if len(self.names) > 1 else ''
        return f'{self.name}{extra} [{self.franchise_id}]'


def manager_alias_groups(directory=RAW_MANAGERS_DIR) -> Dict[str, List[str]]:
    """{franchise key: [team names]} grouped by manager EMAIL, from the local
    manager archive (`data/raw/managers/{year}.json`).

    This is the persistent owner id `manager_report.py` said did not exist --
    it does, just not in the standings snapshots: the archive carries one row
    per manager per season with their email, and those team names match the
    standings names exactly (verified 12/12 for every saved season). So Yahoo
    identity is resolvable after all, and without anyone hand-authoring it.

    The archive lives under the gitignored `data/raw/` and is local-only, so
    this is a *generator* for the committed alias file rather than something
    the app reads at runtime -- same shape as `snapshot-position-map`. That
    also keeps the emails on this machine: the file it produces contains only
    team names and a slug derived from one, never an address or a real name.
    """
    by_email: Dict[str, Dict[int, str]] = {}
    try:
        files = sorted(directory.glob('*.json'))
    except OSError:
        return {}
    for path in files:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        year = payload.get('year')
        for manager in payload.get('managers') or []:
            email = str(manager.get('email') or '').strip().lower()
            team = manager.get('team_name')
            if email and team and year is not None:
                by_email.setdefault(email, {})[int(year)] = str(team)

    groups: Dict[str, List[str]] = {}
    for seasons in by_email.values():
        if not seasons:
            continue
        # Key off the manager's most recent team name, so the file reads as
        # "this is who that is today" without carrying who they are.
        key = slugify(seasons[max(seasons)])
        while key in groups:
            key = f'{key}-2'
        groups[key] = sorted(set(seasons.values()))
    return groups


def load_alias_file(path=FRANCHISE_ALIASES_FILE) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    """{platform: {platform_league_id: {franchise_key: [team names]}}}.

    Hand-authored, committed, and the ONLY way a Yahoo league gets real
    cross-season identity. Empty/missing is a supported state -- every
    franchise then falls back to its own name, i.e. today's behaviour.
    """
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _alias_lookup(platform: str, platform_league_id: str,
                  aliases: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """({team name: franchise key}, {franchise key: [team names]}) for one league."""
    league_aliases = (aliases.get(platform) or {}).get(str(platform_league_id)) or {}
    by_name: Dict[str, str] = {}
    by_key: Dict[str, List[str]] = {}
    for key, names in league_aliases.items():
        if not isinstance(names, list):
            continue
        by_key[key] = [str(n) for n in names]
        for name in names:
            by_name[str(name)] = key
    return by_name, by_key


def _snapshot_franchises(repo, platform: str, platform_league_id: str) -> List[Franchise]:
    """Sleeper/ESPN: identity comes straight from the platform's own ids."""
    franchises: List[Franchise] = []
    for roster in repo.rosters():
        owner_id = roster.get('ownerId')
        roster_id = roster.get('rosterId')
        # ownerId first: it follows the manager even if they end up on a
        # different roster slot. rosterId is the fallback for a league whose
        # snapshot predates an owner being set (co-managed or orphaned teams).
        if owner_id:
            key, source = f'owner:{owner_id}', 'owner_id'
        elif roster_id is not None:
            key, source = f'roster:{roster_id}', 'roster_id'
        else:
            continue
        name = roster.get('teamName') or f'Team {roster_id}'
        franchises.append(Franchise(
            franchise_id=f'{platform}:{platform_league_id}:{key}',
            platform=platform,
            platform_league_id=str(platform_league_id),
            name=name,
            manager_display_name=roster.get('managerDisplayName'),
            owner_id=str(owner_id) if owner_id else None,
            roster_id=str(roster_id) if roster_id is not None else None,
            names=[name],
            source=source,
        ))
    return franchises


def _name_franchises(repo, platform: str, platform_league_id: str,
                     aliases: Dict[str, Any]) -> List[Franchise]:
    """Yahoo: every team name this league's standings have ever recorded,
    folded by the alias file and by Yahoo's own rename note where it fired.

    A name the alias file doesn't cover becomes its own franchise, which is
    exactly today's behaviour -- no worse, and visibly incomplete rather than
    silently merged.
    """
    alias_by_name, alias_by_key = _alias_lookup(platform, platform_league_id, aliases)

    rename_map: Dict[str, str] = {}
    seasons: Dict[str, List[int]] = {}
    for year in repo.standings_years():
        standings = repo.standings(year) or []
        rename_map.update(current_team_names(standings))
        for row in standings:
            name = row.get('team')
            if name:
                seasons.setdefault(name, []).append(year)

    # A rename target only ever appears inside the note ("now displayed as
    # 'Wuf'"), never as a standings row of its own, so without this it is not
    # a known name and nothing can link to it -- which is how the one team in
    # this league that actually renamed ended up with orphaned keeper marks.
    for target in set(rename_map.values()):
        seasons.setdefault(target, []).append(max(repo.standings_years(), default=0))

    # Current roster team names, which are NOT always spelled the way the
    # standings spell them: the Yahoo roster paste prefixes every team with
    # the league name ("Frank Gore Memorial League y15 - BALLS DEEP"), so the
    # keeper board and the standings pages have never been able to join on
    # team at all. Linked only when the tail matches a standings name
    # EXACTLY -- that is evidence, not a similarity guess.
    # Rosters are "now", so they sort after every saved season and win the
    # display-name pick below -- the keeper board keys teams by roster name,
    # so that is the name a keeper mark has to come back under.
    latest_season = max((y for years in seasons.values() for y in years), default=0) + 1
    for roster in repo.rosters():
        name = roster.get('teamName')
        if not name or name in seasons:
            continue
        _, separator, tail = name.partition(' - ')
        if separator and tail in seasons:
            rename_map[name] = tail
        seasons.setdefault(name, []).append(latest_season)

    def canonical(name: str) -> str:
        seen = set()
        while name in rename_map and name not in seen:
            seen.add(name)
            name = rename_map[name]
        return name

    grouped: Dict[str, List[str]] = {}
    for name in seasons:
        key = alias_by_name.get(name) or slugify(canonical(name))
        grouped.setdefault(key, []).append(name)
    # Alias-file groups win outright, including names not present in any saved
    # season, so an entry stays authoritative even before its season is loaded.
    for key, names in alias_by_key.items():
        grouped[key] = sorted(set(grouped.get(key, [])) | set(names))

    franchises = []
    for key, names in sorted(grouped.items()):
        # Display name: the most recent name, resolved THROUGH the rename map.
        # Taking the raw most-recent name instead would hand back the roster
        # paste's prefixed spelling ("Frank Gore Memorial League y15 - BALLS
        # DEEP"), and since the keeper board keys teams by the bare name, every
        # mark would stop matching -- silently, since an unmatched mark just
        # doesn't apply. Caught by diffing the board before/after.
        latest = canonical(max(names, key=lambda n: (max(seasons.get(n, [0])), n)))
        franchises.append(Franchise(
            franchise_id=f'{platform}:{platform_league_id}:{key}',
            platform=platform,
            platform_league_id=str(platform_league_id),
            name=latest,
            names=sorted(names),
            source='alias_file' if key in alias_by_key else 'name',
        ))
    return franchises


def build_franchises(repo, league, aliases: Optional[Dict[str, Any]] = None) -> List[Franchise]:
    """Every franchise in one league. Platform ids where they exist, the
    hand-authored alias file plus rename notes where they don't."""
    aliases = load_alias_file() if aliases is None else aliases
    platform = league.platform
    platform_league_id = str(league.platform_league_id)
    if platform in ('sleeper', 'espn'):
        franchises = _snapshot_franchises(repo, platform, platform_league_id)
        if franchises:
            return franchises
        # An un-synced snapshot league has no rosters yet; fall through rather
        # than returning nothing, so its standings still resolve to something.
        logger.info('franchises: %s league %s has no synced rosters, falling back to names',
                    platform, platform_league_id)
    return _name_franchises(repo, platform, platform_league_id, aliases)


class FranchiseRegistry:
    """In-memory franchise lookup for ONE league."""

    def __init__(self, franchises: List[Franchise]):
        self.franchises: Dict[str, Franchise] = {f.franchise_id: f for f in franchises}
        self._by_name: Dict[str, str] = {}
        self._by_owner: Dict[str, str] = {}
        self._by_roster: Dict[str, str] = {}
        for franchise in franchises:
            for name in franchise.names or [franchise.name]:
                self._by_name.setdefault(name, franchise.franchise_id)
            if franchise.owner_id:
                self._by_owner[str(franchise.owner_id)] = franchise.franchise_id
            if franchise.roster_id is not None:
                self._by_roster[str(franchise.roster_id)] = franchise.franchise_id

    def __len__(self) -> int:
        return len(self.franchises)

    def by_name(self, team_name: Optional[str]) -> Optional[Franchise]:
        if not team_name:
            return None
        franchise_id = self._by_name.get(team_name)
        return self.franchises.get(franchise_id) if franchise_id else None

    def by_roster_id(self, roster_id: Any) -> Optional[Franchise]:
        franchise_id = self._by_roster.get(str(roster_id)) if roster_id is not None else None
        return self.franchises.get(franchise_id) if franchise_id else None

    def by_owner_id(self, owner_id: Any) -> Optional[Franchise]:
        franchise_id = self._by_owner.get(str(owner_id)) if owner_id else None
        return self.franchises.get(franchise_id) if franchise_id else None

    def id_for_name(self, team_name: Optional[str]) -> Optional[str]:
        franchise = self.by_name(team_name)
        return franchise.franchise_id if franchise else None
