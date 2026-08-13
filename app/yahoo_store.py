"""Read/write the hand-curated Yahoo league data in the database.

One module owns both directions so the JSON->DB migration and the
repository read path can never drift into disagreeing about the shape.
Every function here returns exactly what the old JSON loaders returned
(app/draft_history.py, app/draft_picks.py, app/standings.py, and
YahooJsonRepository.rosters()) -- scripts/compare_yahoo_backends.py asserts
that equality against the real files.

Fidelity rules worth knowing before changing a read function:
- standings rows OMIT optional fields that are None. No standings row in the
  source JSON has ever held a real null, and standings.current_team_names()
  does row.get('note', '') then regexes it, so an emitted None crashes where
  an absent key does not.
- roster player dicts emit ALL fields including nulls: every one of the 15
  keys is present on every source row (several explicitly null).
- load_draft_pick_origins() returns None, not {}, when no team had origin
  data -- callers branch on that.
"""
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, selectinload

from .db import SessionLocal
from .yahoo_models import (
    YahooDraftPick,
    YahooDraftPickOwnership,
    YahooRosterPlayerRow,
    YahooRosterTeam,
    YahooStanding,
)

# The optional standings fields, in the order the source JSON writes them,
# mapped to their column name. Order only matters for readability; dict
# equality ignores it.
_STANDINGS_OPTIONAL_FIELDS = (
    ('wins', 'wins'),
    ('losses', 'losses'),
    ('ties', 'ties'),
    ('pointsFor', 'points_for'),
    ('pointsAgainst', 'points_against'),
    ('streak', 'streak'),
    ('waiverBudget', 'waiver_budget'),
    ('waiverPriority', 'waiver_priority'),
    ('moves', 'moves'),
    ('madePlayoffs', 'made_playoffs'),
    ('note', 'note'),
)


def _session(session: Optional[Session] = None):
    return session if session is not None else SessionLocal()


# --------------------------------------------------------------------------
# draft history
# --------------------------------------------------------------------------

def load_draft_years(platform: str, platform_league_id: str,
                     session: Optional[Session] = None) -> Dict[int, List[dict]]:
    """{year: [{'round','pick','playerName','team'}, ...]} -- the shape
    draft_history.load_draft_years() returns."""
    owns = session is None
    sess = _session(session)
    try:
        rows = (
            sess.query(YahooDraftPick)
            .filter_by(platform=platform, platform_league_id=platform_league_id)
            .order_by(YahooDraftPick.year, YahooDraftPick.round, YahooDraftPick.pick)
            .all()
        )
        years: Dict[int, List[dict]] = {}
        for row in rows:
            years.setdefault(row.year, []).append({
                'round': row.round,
                'pick': row.pick,
                'playerName': row.player_name,
                'team': row.team,
            })
        return years
    finally:
        if owns:
            sess.close()


def save_draft_year(platform: str, platform_league_id: str, year: int, picks: List[dict],
                    session: Optional[Session] = None) -> int:
    """Replace one season's picks. Returns rows written."""
    owns = session is None
    sess = _session(session)
    try:
        sess.query(YahooDraftPick).filter_by(
            platform=platform, platform_league_id=platform_league_id, year=year,
        ).delete()
        for pick in picks:
            sess.add(YahooDraftPick(
                platform=platform,
                platform_league_id=platform_league_id,
                year=year,
                round=pick['round'],
                pick=pick['pick'],
                player_name=pick['playerName'],
                team=pick['team'],
            ))
        if owns:
            sess.commit()
        return len(picks)
    finally:
        if owns:
            sess.close()


# --------------------------------------------------------------------------
# draft pick ownership
# --------------------------------------------------------------------------

def load_draft_picks(platform: str, platform_league_id: str, year: int,
                     session: Optional[Session] = None) -> Optional[Dict[str, Dict[int, int]]]:
    """{teamName: {round: pickCount}} or None when that year was never saved."""
    owns = session is None
    sess = _session(session)
    try:
        rows = (
            sess.query(YahooDraftPickOwnership)
            .filter_by(platform=platform, platform_league_id=platform_league_id, year=year)
            .order_by(YahooDraftPickOwnership.id)
            .all()
        )
        if not rows:
            return None
        result: Dict[str, Dict[int, int]] = {}
        for row in rows:
            result.setdefault(row.team_name, {})[row.round] = row.pick_count
        return result
    finally:
        if owns:
            sess.close()


def load_draft_pick_origins(platform: str, platform_league_id: str, year: int,
                            session: Optional[Session] = None) -> Optional[Dict[str, Dict[int, list]]]:
    """{teamName: {round: [originTeam, ...]}}, or None when no team has origin
    data -- matching draft_picks.load_draft_pick_origins()'s `result or None`."""
    owns = session is None
    sess = _session(session)
    try:
        rows = (
            sess.query(YahooDraftPickOwnership)
            .filter_by(platform=platform, platform_league_id=platform_league_id, year=year)
            .order_by(YahooDraftPickOwnership.id)
            .all()
        )
        result: Dict[str, Dict[int, list]] = {}
        for row in rows:
            if row.origins_json is None:
                continue
            result.setdefault(row.team_name, {})[row.round] = json.loads(row.origins_json)
        return result or None
    finally:
        if owns:
            sess.close()


def save_draft_pick_ownership(platform: str, platform_league_id: str, year: int, teams: List[dict],
                              session: Optional[Session] = None) -> int:
    """Replace one year's pick ownership. `teams` is the source shape:
    [{'teamName', 'picksByRound': {round: count}, 'picksByRoundOrigins': {round: [team,...]}}]."""
    owns = session is None
    sess = _session(session)
    try:
        sess.query(YahooDraftPickOwnership).filter_by(
            platform=platform, platform_league_id=platform_league_id, year=year,
        ).delete()
        written = 0
        for team in teams:
            name = team.get('teamName')
            if not name:
                continue
            picks_by_round = team.get('picksByRound') or {}
            origins = team.get('picksByRoundOrigins')
            for round_str, count in picks_by_round.items():
                round_number = int(round_str)
                origin_list = None
                if origins is not None and str(round_number) in {str(k) for k in origins}:
                    raw = origins.get(str(round_number), origins.get(round_number))
                    origin_list = json.dumps(list(raw)) if raw is not None else None
                sess.add(YahooDraftPickOwnership(
                    platform=platform,
                    platform_league_id=platform_league_id,
                    year=year,
                    team_name=name,
                    round=round_number,
                    pick_count=int(count),
                    origins_json=origin_list,
                ))
                written += 1
        if owns:
            sess.commit()
        return written
    finally:
        if owns:
            sess.close()


# --------------------------------------------------------------------------
# standings
# --------------------------------------------------------------------------

def standings_years(platform: str, platform_league_id: str,
                    session: Optional[Session] = None) -> List[int]:
    """Seasons with saved standings, newest first (YahooJsonRepository sorted
    its directory glob reverse=True)."""
    owns = session is None
    sess = _session(session)
    try:
        rows = (
            sess.query(YahooStanding.year)
            .filter_by(platform=platform, platform_league_id=platform_league_id)
            .distinct()
            .all()
        )
        return sorted({row[0] for row in rows}, reverse=True)
    finally:
        if owns:
            sess.close()


def load_standings(platform: str, platform_league_id: str, year: int,
                   session: Optional[Session] = None) -> Optional[List[Dict[str, Any]]]:
    """Standings sorted by rank ascending, or None when that year is unsaved."""
    owns = session is None
    sess = _session(session)
    try:
        rows = (
            sess.query(YahooStanding)
            .filter_by(platform=platform, platform_league_id=platform_league_id, year=year)
            .order_by(YahooStanding.rank)
            .all()
        )
        if not rows:
            return None
        result = []
        for row in rows:
            entry: Dict[str, Any] = {'rank': row.rank, 'team': row.team_name}
            for json_key, column in _STANDINGS_OPTIONAL_FIELDS:
                value = getattr(row, column)
                if value is not None:
                    entry[json_key] = value
            result.append(entry)
        return result
    finally:
        if owns:
            sess.close()


def save_standings(platform: str, platform_league_id: str, year: int, standings: List[dict],
                   session: Optional[Session] = None) -> int:
    """Replace one season's standings."""
    owns = session is None
    sess = _session(session)
    try:
        sess.query(YahooStanding).filter_by(
            platform=platform, platform_league_id=platform_league_id, year=year,
        ).delete()
        for row in standings:
            sess.add(YahooStanding(
                platform=platform,
                platform_league_id=platform_league_id,
                year=year,
                rank=row.get('rank'),
                team_name=row.get('team'),
                wins=row.get('wins'),
                losses=row.get('losses'),
                ties=row.get('ties'),
                points_for=row.get('pointsFor'),
                points_against=row.get('pointsAgainst'),
                streak=row.get('streak'),
                waiver_budget=row.get('waiverBudget'),
                waiver_priority=row.get('waiverPriority'),
                moves=row.get('moves'),
                made_playoffs=row.get('madePlayoffs'),
                note=row.get('note'),
            ))
        if owns:
            sess.commit()
        return len(standings)
    finally:
        if owns:
            sess.close()


# --------------------------------------------------------------------------
# current-season rosters
# --------------------------------------------------------------------------

def load_rosters(platform: str, platform_league_id: str,
                 session: Optional[Session] = None) -> List[Dict[str, Any]]:
    """The yahoo_league_rosters.json shape: a list of team dicts each holding
    a `players` list. Emits every player field including nulls."""
    owns = session is None
    sess = _session(session)
    try:
        teams = (
            sess.query(YahooRosterTeam)
            .filter_by(platform=platform, platform_league_id=platform_league_id)
            .options(selectinload(YahooRosterTeam.players))
            .order_by(YahooRosterTeam.sort_order)
            .all()
        )
        result = []
        for team in teams:
            result.append({
                'teamId': team.team_id,
                'ownerName': team.owner_name,
                'teamName': team.team_name,
                'playerCount': team.player_count,
                'players': [
                    {
                        'playerId': player.player_id,
                        'playerName': player.player_name,
                        'position': player.player_position,
                        'team': player.team,
                        'status': player.status,
                        'selectedPosition': player.selected_position,
                        'eligibleSlots': (
                            json.loads(player.eligible_slots_json)
                            if player.eligible_slots_json is not None else None
                        ),
                        'draftRound': player.draft_round,
                        'draftPick': player.draft_pick,
                        'draftSlot': player.draft_slot,
                        'keeperEligibleOverride': player.keeper_eligible_override,
                        'keeperLockedOverride': player.keeper_locked_override,
                        'marketRoundOverride': player.market_round_override,
                        'valueNote': player.value_note,
                        'keeperNote': player.keeper_note,
                    }
                    for player in team.players
                ],
            })
        return result
    finally:
        if owns:
            sess.close()


def save_rosters(platform: str, platform_league_id: str, teams: List[dict],
                 session: Optional[Session] = None) -> int:
    """Replace the whole league roster snapshot -- parse-rosters always
    rewrote the entire file, so a partial merge would be a behaviour change."""
    owns = session is None
    sess = _session(session)
    try:
        existing = (
            sess.query(YahooRosterTeam)
            .filter_by(platform=platform, platform_league_id=platform_league_id)
            .all()
        )
        for team in existing:
            sess.delete(team)  # cascade removes its players
        sess.flush()

        players_written = 0
        for team_index, team in enumerate(teams):
            row = YahooRosterTeam(
                platform=platform,
                platform_league_id=platform_league_id,
                team_id=team.get('teamId'),
                owner_name=team.get('ownerName'),
                team_name=team.get('teamName'),
                player_count=team.get('playerCount'),
                sort_order=team_index,
            )
            sess.add(row)
            sess.flush()
            for player_index, player in enumerate(team.get('players') or []):
                slots = player.get('eligibleSlots')
                sess.add(YahooRosterPlayerRow(
                    roster_team_id=row.id,
                    sort_order=player_index,
                    player_id=player.get('playerId'),
                    player_name=player.get('playerName'),
                    player_position=player.get('position'),
                    team=player.get('team'),
                    status=player.get('status'),
                    selected_position=player.get('selectedPosition'),
                    eligible_slots_json=json.dumps(slots) if slots is not None else None,
                    draft_round=player.get('draftRound'),
                    draft_pick=player.get('draftPick'),
                    draft_slot=player.get('draftSlot'),
                    keeper_eligible_override=player.get('keeperEligibleOverride'),
                    keeper_locked_override=player.get('keeperLockedOverride'),
                    market_round_override=player.get('marketRoundOverride'),
                    value_note=player.get('valueNote'),
                    keeper_note=player.get('keeperNote'),
                ))
                players_written += 1
        if owns:
            sess.commit()
        return players_written
    finally:
        if owns:
            sess.close()
