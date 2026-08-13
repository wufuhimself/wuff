"""Cross-platform player identity — the join key wuff never had.

Phase 5 step 1 of docs/roadmap.md. Until now a "player" was a name string,
normalized by ten slightly-different functions across strategy.py,
board_service.py, adp_manager.py, ranking_history.py, outcome_log.py,
keeper_history.py, rankings_aggregator.py, draft_history.py and
rankings_manager.py. Nothing mapped Sleeper's opaque numeric ids to Yahoo's,
ESPN's, or nflverse's, so nothing could join a roster to a stat line, an
injury status, or a bye week. This module is that map.

Sources, both already on disk and both free:

- `data/raw/sleeper/players_cache.json` — the spine. ~12k players, each row
  carrying `espn_id`, `yahoo_id`, `gsis_id` and `sportradar_id` alongside
  Sleeper's own id, plus `status`/`injury_status`. Refreshed by the
  scheduler, so a deployed container gets it for free after the first sync.
- `data/raw/nfl_stats/rosters/{season}.csv` — nflverse, which carries the
  same crosswalk plus `sleeper_id` and `pfr_id`. Local-only (the CSVs live
  under the gitignored data/raw/), so this source *enriches* the spine and
  cross-checks it; it is never required.

Canonical ids are `sleeper:{player_id}` wherever Sleeper knows the player,
falling back to `nfl:{gsis_id}` and then `name:{search_name}`. Deliberately
NOT "prefer gsis_id when present": a rookie can be in Sleeper before
nflverse assigns a gsis id, so a gsis-first scheme would silently *change*
that player's canonical id on the next rebuild — which is exactly the
orphaned-key failure this whole phase exists to remove. Sleeper ids are
stable, cover team defenses (32 DEF rows nflverse has no equivalent for),
and are present in production.

Resolution never guesses. `resolve()` returns None for an ambiguous name
rather than picking one, because picking one is precisely the bug
`nfl_stats.fantasy_position_map()` documents: Josh Allen is a BUF QB and a
JAX LB, Lamar Jackson is a BAL QB and a CAR/ATL DB, and a lookup that keeps
whichever row came last silently dropped this league's round-1 rushing QBs
from its own draft analysis. Callers that cannot disambiguate get nothing
and must say so.
"""
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .paths import PLAYER_ALIASES_FILE, RAW_NFL_ROSTERS_DIR, SLEEPER_PLAYERS_CACHE_FILE

logger = logging.getLogger(__name__)

# Name suffixes dropped from the match key. "Michael Pittman Jr." and
# "Michael Pittman" are one player to every ranking source that spells them
# differently; a genuine father/son collision is disambiguated by position
# and team like any other.
_NAME_SUFFIXES = frozenset({'jr', 'sr', 'ii', 'iii', 'iv', 'v'})

# Trailing words that mark a team defense rather than a person.
_DEFENSE_WORDS = frozenset({'defense', 'dst', 'def', 'ds'})

# Team abbreviations that moved or that sources spell differently. Team is
# only ever a tiebreak here (see resolve()), so this map is a convenience,
# not a correctness requirement.
_TEAM_ALIASES = {
    'JAC': 'JAX', 'WSH': 'WAS', 'WFT': 'WAS', 'ARZ': 'ARI', 'BLT': 'BAL',
    'CLV': 'CLE', 'HST': 'HOU', 'LA': 'LAR', 'STL': 'LAR', 'SD': 'LAC',
    'OAK': 'LV', 'SL': 'LAR',
}

# Ranking sources say DEF, mock_draft.py keys its limits on DST, Yahoo says
# D/ST. Canonical here is DEF -- it matches Sleeper, strategy.py's
# _normalize_position, and LeagueFormat.starters. mock_draft.py keeps its own
# DST spelling for now; unifying that is step 3 work, not step 1.
_POSITION_ALIASES = {'DST': 'DEF', 'D/ST': 'DEF', 'DEFENSE': 'DEF', 'PK': 'K'}

_FANTASY_POSITIONS = frozenset({'QB', 'RB', 'WR', 'TE', 'K', 'DEF'})


def normalize_name(value: str, strip_suffix: bool = True) -> str:
    """The one name key. Everything else in the codebase should end up calling
    this (step 1b of the Phase 5 plan).

    Aggressive on purpose -- accents folded, punctuation and whitespace
    dropped -- because it is only ever a lookup key, never displayed. The
    display name is kept verbatim on PlayerIdentity.full_name.

    `strip_suffix=False` keeps Jr/Sr/III, which is what separates Frank Gore
    from Frank Gore Jr. The registry indexes both forms and tries the exact
    one first, so a source that spells a player "Michael Pittman" still
    matches "Michael Pittman Jr." without the two Gores collapsing into one
    ambiguous blob.
    """
    if not value:
        return ''
    text = str(value)
    # Yahoo's roster paste appends a "Player Notes" link label to nearly every
    # name; a bye week or team can arrive in parentheses from ranking CSVs.
    text = re.sub(r'\bplayer\s+notes\b', ' ', text, flags=re.IGNORECASE)
    text = text.split('(')[0]
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(char for char in text if not unicodedata.combining(char))
    parts = [re.sub(r'[^a-z0-9]', '', part) for part in text.lower().split()]
    parts = [part for part in parts if part]
    # Ranking boards spell team defenses "Denver Defense" / "NY Jets DST".
    # No human player's name ends in these, so dropping the tail is safe and
    # lets one defense alias cover every source's spelling.
    while len(parts) > 1 and parts[-1] in _DEFENSE_WORDS:
        parts.pop()
    if strip_suffix:
        while len(parts) > 1 and parts[-1] in _NAME_SUFFIXES:
            parts.pop()
    return ''.join(parts)


def index_rows_by_name(rows: Iterable[Dict[str, Any]], name_key: str = 'playerName',
                       rank_key: Optional[str] = 'ranking') -> Dict[str, Dict[str, Any]]:
    """{name key: row} for a list of rows, collision-safe.

    Two things make a plain `{normalize_name(r[name_key]): r for r in rows}`
    comprehension wrong, and both are silent:

    1. Sources duplicate a player under two spellings. The live board carries
       "Aaron Jones Sr." at rank 93 *and* "Aaron Jones" at 268, "Deebo Samuel
       Sr." at 98 and "Deebo Samuel" at 299. A comprehension keeps whichever
       came last, so a top-100 RB silently reads as a rank-268 afterthought.
       When `rank_key` is set, the better (lower) value wins instead; ties and
       missing ranks fall back to first-writer-wins.
    2. Suffix-stripped keys collide across generations (Frank Gore vs Frank
       Gore Jr.). The suffix-preserving key is indexed too and wins on exact
       match, same two-level scheme resolve() uses.

    This is the same class of bug as nfl_stats.fantasy_position_map's
    last-row-wins collision -- see CLAUDE.md.
    """
    index: Dict[str, Dict[str, Any]] = {}

    def better(incumbent: Optional[Dict[str, Any]], challenger: Dict[str, Any]) -> bool:
        if incumbent is None:
            return True
        if not rank_key:
            return False
        current, new = incumbent.get(rank_key), challenger.get(rank_key)
        if new is None:
            return False
        if current is None:
            return True
        return new < current

    for row in rows:
        name = str(row.get(name_key) or '')
        if not name:
            continue
        # Both keys go into ONE dict under the same better() rule. Keeping two
        # dicts and unioning them looked tidier but was wrong: for an
        # unsuffixed name the two keys are identical, so the "exact" copy
        # clobbered a better-ranked base entry -- which is the duplicate bug
        # again, one layer up.
        for key in (normalize_name(name), normalize_name(name, strip_suffix=False)):
            # The two keys are identical for unsuffixed names; the repeat write
            # is a no-op because a row is never "better" than itself.
            if key and better(index.get(key), row):
                index[key] = row

    return index


def dedupe_rows_by_name(rows: Iterable[Dict[str, Any]], name_key: str = 'playerName',
                        rank_key: Optional[str] = 'ranking') -> List[Dict[str, Any]]:
    """Drop rows that are the same player under a second spelling, keeping the
    better-ranked one and the original order.

    The free-rankings board carries these: "James Cook III" at 20 and "James
    Cook" at 257, "Travis Etienne Jr." at 45 and "Travis Etienne" at 259. They
    are one player, and treating them as two is not cosmetic -- the mock draft
    drafted the same human twice (once early, once in round 15), and the
    week-over-week trend column rendered a confident "down 237" for a player
    who had not moved.

    `_sleeper_tail()` stops new duplicates being written now that its dedupe
    check shares this module's name key, but a board already on disk keeps
    them until the next daily refresh -- and until then every consumer would
    see the phantom. Cheap enough to apply on read.
    """
    rows = list(rows)  # iterated twice; must not be a generator
    best = index_rows_by_name(rows, name_key=name_key, rank_key=rank_key)
    keep = {id(row) for row in best.values()}
    return [row for row in rows if id(row) in keep]


def normalize_position(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.match(r'[A-Za-z/]+', str(value).strip())
    position = (match.group(0) if match else str(value).strip()).upper()
    return _POSITION_ALIASES.get(position, position)


def normalize_team(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    team = str(value).strip().upper()
    if not team or team == 'UNK':
        return None
    return _TEAM_ALIASES.get(team, team)


@dataclass(frozen=True)
class PlayerIdentity:
    """One real player (or one team defense), and every id that points at them."""
    canonical_id: str
    full_name: str
    search_name: str
    position: Optional[str] = None
    team: Optional[str] = None
    status: Optional[str] = None
    injury_status: Optional[str] = None
    active: bool = False
    sleeper_id: Optional[str] = None
    yahoo_id: Optional[str] = None
    espn_id: Optional[str] = None
    gsis_id: Optional[str] = None
    sources: Tuple[str, ...] = ()

    def platform_id(self, platform: str) -> Optional[str]:
        return {
            'sleeper': self.sleeper_id,
            'yahoo': self.yahoo_id,
            'espn': self.espn_id,
            'nfl': self.gsis_id,
        }.get(platform)


# Why a resolution failed, so callers and the gate script can report the
# difference instead of lumping everything into "not found".
UNRESOLVED_UNKNOWN = 'unknown'
UNRESOLVED_AMBIGUOUS = 'ambiguous'
RESOLVED = 'resolved'


def _clean_id(value: Any) -> Optional[str]:
    """Sleeper ships at least one id with leading whitespace (gsis_id is
    ' 00-0035057'), which silently breaks equality joins."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _aliases_for(full_name: str, first: str, last: str, position: Optional[str],
                 team: Optional[str]) -> List[str]:
    aliases = {normalize_name(full_name), normalize_name(f'{first} {last}')}
    if position == 'DEF':
        # Team defenses are spelled every possible way across sources: "HOU",
        # "Houston Texans", "Texans", "Houston". Sleeper stores them with
        # first=city, last=nickname and no full_name at all.
        aliases.update({normalize_name(team or ''), normalize_name(last), normalize_name(first)})
    return [alias for alias in aliases if alias]


def _identities_from_sleeper(cache: Dict[str, Any]) -> Dict[str, PlayerIdentity]:
    identities: Dict[str, PlayerIdentity] = {}
    for player_id, info in cache.items():
        if not isinstance(info, dict):
            continue
        first = str(info.get('first_name') or '').strip()
        last = str(info.get('last_name') or '').strip()
        full_name = str(info.get('full_name') or f'{first} {last}').strip()
        if not full_name:
            continue
        position = normalize_position(info.get('position'))
        team = normalize_team(info.get('team'))
        identities[f'sleeper:{player_id}'] = PlayerIdentity(
            canonical_id=f'sleeper:{player_id}',
            full_name=full_name,
            search_name=normalize_name(full_name),
            position=position,
            team=team,
            status=_clean_id(info.get('status')),
            injury_status=_clean_id(info.get('injury_status')),
            active=bool(info.get('active')),
            sleeper_id=str(player_id),
            yahoo_id=_clean_id(info.get('yahoo_id')),
            espn_id=_clean_id(info.get('espn_id')),
            gsis_id=_clean_id(info.get('gsis_id')),
            sources=('sleeper',),
        )
    return identities


def _load_nflverse_rows(directory: Path, seasons: Optional[List[int]]) -> List[Dict[str, str]]:
    """Newest season last, so later rows win on team/position."""
    import csv  # pylint: disable=import-outside-toplevel

    if not directory.exists():
        return []
    rows: List[Dict[str, str]] = []
    for path in sorted(directory.glob('*.csv')):
        if seasons is not None and path.stem.isdigit() and int(path.stem) not in seasons:
            continue
        try:
            with open(path, encoding='utf-8') as handle:
                rows.extend(list(csv.DictReader(handle)))
        except (OSError, csv.Error):
            logger.warning('player registry: could not read %s', path)
    return rows


def _merge_nflverse(identities: Dict[str, PlayerIdentity],
                    rows: Iterable[Dict[str, str]]) -> Tuple[Dict[str, PlayerIdentity], int]:
    """Fill in ids the Sleeper spine is missing, and add nflverse-only players.

    Never overwrites an id the spine already has -- a disagreement is logged,
    not silently resolved, because two sources disagreeing about which yahoo_id
    belongs to a name is a data problem worth seeing.
    """
    by_sleeper = {p.sleeper_id: cid for cid, p in identities.items() if p.sleeper_id}
    by_gsis = {p.gsis_id: cid for cid, p in identities.items() if p.gsis_id}
    conflicts = 0

    rows = list(rows)
    # Pre-pass: learn every gsis<->sleeper link nflverse knows before creating
    # anything. Without it, a player whose Sleeper row carries no gsis_id gets
    # a duplicate `nfl:` identity from the first season row that omits
    # sleeper_id, and a later season row then quietly enriches the *other*
    # copy -- two identities, one player, one silently ambiguous name.
    # (Found exactly this way: Jake Bates, Cade York, Mike Washington.)
    gsis_to_sleeper = {
        _clean_id(row.get('gsis_id')): _clean_id(row.get('sleeper_id'))
        for row in rows
        if _clean_id(row.get('gsis_id')) and _clean_id(row.get('sleeper_id'))
    }
    # Spine rows with no gsis id yet, indexed for the name+position fallback
    # below. Only these are eligible, so a row that already has a real id link
    # can never be reassigned by a name match.
    unlinked_by_name: Dict[Tuple[str, Optional[str]], List[str]] = {}
    for cid, existing in identities.items():
        if existing.gsis_id is None:
            unlinked_by_name.setdefault((existing.search_name, existing.position), []).append(cid)

    for row in rows:
        gsis_id = _clean_id(row.get('gsis_id'))
        sleeper_id = _clean_id(row.get('sleeper_id')) or gsis_to_sleeper.get(gsis_id)
        full_name = str(row.get('full_name') or '').strip()
        if not full_name:
            continue
        canonical_id = by_sleeper.get(sleeper_id) or by_gsis.get(gsis_id)

        if canonical_id is None:
            # No id link at all -- nflverse carries no sleeper_id for this
            # player in any season (common for rookies). Fall back to an exact
            # name+position match, but only onto a spine row that has no gsis
            # id yet, so this fills a gap rather than overwriting a link.
            # Creating a second identity instead would make the name
            # permanently ambiguous, which is strictly worse than a rare
            # wrong link between two same-named players at one position.
            key = (normalize_name(full_name), normalize_position(row.get('position')))
            matches = unlinked_by_name.get(key)
            if matches and len(matches) == 1:
                canonical_id = matches[0]
                # Claimed -- a second nflverse row must not link here too.
                unlinked_by_name.pop(key, None)

        if canonical_id is None:
            if gsis_id is None:
                continue  # nothing stable to key on
            canonical_id = f'nfl:{gsis_id}'
            identities[canonical_id] = PlayerIdentity(
                canonical_id=canonical_id,
                full_name=full_name,
                search_name=normalize_name(full_name),
                position=normalize_position(row.get('position')),
                team=normalize_team(row.get('team')),
                status=_clean_id(row.get('status')),
                active=str(row.get('status') or '').upper() == 'ACT',
                sleeper_id=sleeper_id,
                yahoo_id=_clean_id(row.get('yahoo_id')),
                espn_id=_clean_id(row.get('espn_id')),
                gsis_id=gsis_id,
                sources=('nflverse',),
            )
            by_gsis[gsis_id] = canonical_id
            if sleeper_id:
                by_sleeper[sleeper_id] = canonical_id
            continue

        existing = identities[canonical_id]
        updates: Dict[str, Any] = {}
        for field, incoming in (
            ('sleeper_id', sleeper_id),
            ('yahoo_id', _clean_id(row.get('yahoo_id'))),
            ('espn_id', _clean_id(row.get('espn_id'))),
            ('gsis_id', gsis_id),
        ):
            current = getattr(existing, field)
            if incoming is None:
                continue
            if current is None:
                updates[field] = incoming
            elif current != incoming:
                conflicts += 1
                logger.debug('player registry: %s %s mismatch (sleeper=%s nflverse=%s)',
                             existing.full_name, field, current, incoming)
        if 'nflverse' not in existing.sources:
            updates['sources'] = existing.sources + ('nflverse',)
        if updates:
            identities[canonical_id] = replace(existing, **updates)
        if gsis_id:
            by_gsis.setdefault(gsis_id, canonical_id)

    return identities, conflicts


def build_identities(seasons: Optional[List[int]] = None) -> Tuple[List[PlayerIdentity], Dict[str, int]]:
    """Build the registry from whatever sources are on disk.

    The Sleeper cache alone is enough (and is all a deployed container has);
    the nflverse CSVs only add coverage. Returns (identities, stats) so the
    caller can report what each source contributed instead of guessing.
    """
    try:
        cache = json.loads(SLEEPER_PLAYERS_CACHE_FILE.read_text()).get('players', {})
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    identities = _identities_from_sleeper(cache)
    sleeper_count = len(identities)

    rows = _load_nflverse_rows(RAW_NFL_ROSTERS_DIR, seasons)
    identities, conflicts = _merge_nflverse(identities, rows)

    stats = {
        'sleeper': sleeper_count,
        'nflverse_rows': len(rows),
        'nflverse_only': len(identities) - sleeper_count,
        'id_conflicts': conflicts,
        'total': len(identities),
    }
    return list(identities.values()), stats


def load_curated_aliases(path: Path = PLAYER_ALIASES_FILE) -> Dict[str, str]:
    """{alias name: real name} for the links no algorithm can make.

    Nicknames ("Hollywood Brown" is Marquise Brown) and short forms ("Josh
    Palmer" is Joshua Palmer) are not derivable from any field in either
    source -- they are knowledge. Kept hand-authored and committed, which is
    the same conclusion manager_report.py reached about manager identity.
    scripts/check_player_resolution.py prints the candidates to add.
    """
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(alias): str(target) for alias, target in payload.items() if alias and target}


class PlayerRegistry:
    """In-memory lookup over a built registry. ~15k rows; build once, reuse."""

    def __init__(self, identities: Iterable[PlayerIdentity],
                 curated_aliases: Optional[Dict[str, str]] = None):
        self.players: Dict[str, PlayerIdentity] = {}
        self._by_platform: Dict[Tuple[str, str], str] = {}
        # Suffix-preserving keys, tried first so "Frank Gore" does not collide
        # with "Frank Gore Jr.".
        self._by_exact: Dict[str, List[str]] = {}
        self._by_alias: Dict[str, List[str]] = {}
        for identity in identities:
            self.players[identity.canonical_id] = identity
            for platform in ('sleeper', 'yahoo', 'espn', 'nfl'):
                platform_id = identity.platform_id(platform)
                if platform_id:
                    self._by_platform.setdefault((platform, platform_id), identity.canonical_id)
            exact = normalize_name(identity.full_name, strip_suffix=False)
            if exact:
                self._by_exact.setdefault(exact, []).append(identity.canonical_id)
            for alias in self.aliases_for(identity):
                self._by_alias.setdefault(alias, []).append(identity.canonical_id)
        self._apply_curated(load_curated_aliases() if curated_aliases is None else curated_aliases)

    def _apply_curated(self, aliases: Dict[str, str]) -> None:
        for alias_name, real_name in aliases.items():
            target, reason = self.resolve_detail(real_name, use_curated=False)
            if target is None:
                logger.warning('player registry: curated alias %r -> %r did not resolve (%s)',
                               alias_name, real_name, reason)
                continue
            for key in (normalize_name(alias_name), normalize_name(alias_name, strip_suffix=False)):
                if not key:
                    continue
                # Curated links are authoritative: they replace whatever that
                # key pointed at rather than adding a second candidate, or the
                # alias would just resolve as ambiguous and change nothing.
                self._by_alias[key] = [target.canonical_id]
                self._by_exact[key] = [target.canonical_id]

    @staticmethod
    def aliases_for(identity: PlayerIdentity) -> List[str]:
        parts = identity.full_name.split()
        first = parts[0] if parts else ''
        last = parts[-1] if len(parts) > 1 else ''
        aliases = set(_aliases_for(identity.full_name, first, last, identity.position, identity.team))
        aliases.add(normalize_name(identity.full_name, strip_suffix=False))
        return [alias for alias in aliases if alias]

    def alias_pairs(self) -> List[Tuple[str, str]]:
        """(alias, canonical_id) for persistence — curated links included, so
        the stored index can never disagree with this one."""
        pairs = {
            (alias, canonical_id)
            for index in (self._by_alias, self._by_exact)
            for alias, canonical_ids in index.items()
            for canonical_id in canonical_ids
        }
        return sorted(pairs)

    def __len__(self) -> int:
        return len(self.players)

    def by_platform_id(self, platform: str, platform_id: Any) -> Optional[PlayerIdentity]:
        """The cheap, exact path. Prefer it over resolve() wherever a caller
        actually has the platform's own id."""
        key = _clean_id(platform_id)
        if not key:
            return None
        canonical_id = self._by_platform.get((platform, key))
        return self.players.get(canonical_id) if canonical_id else None

    def resolve_detail(  # pylint: disable=too-many-return-statements
            self, name: str, team: Optional[str] = None,
            position: Optional[str] = None,
            prefer_fantasy: bool = True,
            use_curated: bool = True) -> Tuple[Optional[PlayerIdentity], str]:
        """(player, reason) — reason is one of RESOLVED / UNRESOLVED_UNKNOWN /
        UNRESOLVED_AMBIGUOUS.

        Narrowing order: position, then fantasy-relevance, then active, then
        team. Team is applied last and only as a tiebreak that can never empty
        the candidate set, because roster snapshots go stale on trades while
        the name and position stay right.

        `use_curated` exists only so _apply_curated() can resolve a curated
        alias's *target* without consulting the half-built curated index.
        """
        del use_curated  # the curated index is merged into the alias index
        wanted_position = normalize_position(position)
        wanted_team = normalize_team(team)

        # A team defense is identified by its team, not its name: sources
        # spell one four different ways ("DEN", "Denver", "Broncos", "Denver
        # Defense") and Sleeper stores none of them as a full name.
        if wanted_position == 'DEF' and wanted_team:
            for cid in self._by_alias.get(normalize_name(wanted_team), []):
                found = self.players[cid]
                if found.position == 'DEF':
                    return found, RESOLVED

        exact_key = normalize_name(name, strip_suffix=False)
        exact = [self.players[cid] for cid in self._by_exact.get(exact_key, [])]
        if len(exact) == 1:
            return exact[0], RESOLVED

        key = normalize_name(name)
        if not key:
            return None, UNRESOLVED_UNKNOWN
        candidates = [self.players[cid] for cid in self._by_alias.get(key, [])]
        if not candidates:
            return None, UNRESOLVED_UNKNOWN
        if len(candidates) == 1:
            return candidates[0], RESOLVED

        if wanted_position:
            narrowed = [c for c in candidates if c.position == wanted_position]
            if narrowed:
                candidates = narrowed
            elif wanted_position not in _FANTASY_POSITIONS:
                # The caller asked for a defender and this name only belongs to
                # fantasy players. Saying "unknown" is right; falling through
                # would hand back a QB for a request that said LB, which is the
                # Josh Allen bug wearing different clothes.
                return None, UNRESOLVED_UNKNOWN
        if prefer_fantasy and len(candidates) > 1:
            # The Josh Allen / Lamar Jackson case: a QB and a defender share a
            # name, and only one of them can be on a fantasy roster.
            narrowed = [c for c in candidates if c.position in _FANTASY_POSITIONS]
            if narrowed:
                candidates = narrowed
        if len(candidates) > 1:
            narrowed = [c for c in candidates if c.active]
            if narrowed:
                candidates = narrowed
        wanted_team = normalize_team(team)
        if wanted_team and len(candidates) > 1:
            narrowed = [c for c in candidates if c.team == wanted_team]
            if narrowed:
                candidates = narrowed

        if len(candidates) == 1:
            return candidates[0], RESOLVED
        return None, UNRESOLVED_AMBIGUOUS

    def resolve(self, name: str, team: Optional[str] = None,
                position: Optional[str] = None) -> Optional[PlayerIdentity]:
        return self.resolve_detail(name, team=team, position=position)[0]
