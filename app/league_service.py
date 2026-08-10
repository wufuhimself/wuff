"""League resolution with web-configured rules applied.

The registry (leagues.json) and the DB (web-imported leagues) both hold
leagues; keeper/format rules configured through the web settings page live
in DbLeague.rules_json. resolve_league() is the one place that merges all
of it into a League object — analysis routes should use it instead of
touching the registry or DB directly.
"""
import json
from dataclasses import asdict
from typing import Any, Dict, Optional

from .db import SessionLocal
from .league_context import league_format_from_payload
from .league_registry import League, load_leagues
from .models import DbLeague

# The only LeagueFormat fields the settings page may override.
EDITABLE_RULE_FIELDS = (
    'teams',
    'keeper_slots',
    'keeper_ineligible_rounds',
    'keeper_slot_rounds',
    'keeper_max_consecutive_seasons',
)


def resolve_league(league_id: str) -> Optional[League]:
    """League by wuff slug, from the registry or the DB, with any stored
    rules overrides merged into its format. None when unknown everywhere."""
    league = load_leagues().get(league_id)

    with SessionLocal() as session:
        row = session.query(DbLeague).filter_by(slug=league_id).one_or_none()

    if league is None:
        if row is None:
            return None
        league = row.to_registry_league()

    if row is not None and row.rules_json:
        try:
            overrides = json.loads(row.rules_json)
        except json.JSONDecodeError:
            overrides = {}
        if isinstance(overrides, dict) and overrides:
            merged = {**asdict(league.format), **overrides}
            league.format = league_format_from_payload(merged)
    return league


def save_league_rules(league: League, rules: Dict[str, Any]) -> None:
    """Persist rule overrides for a league, creating its DB row if it only
    exists in the registry so far."""
    cleaned = {key: value for key, value in rules.items() if key in EDITABLE_RULE_FIELDS}
    with SessionLocal() as session:
        row = (
            session.query(DbLeague)
            .filter_by(platform=league.platform, platform_league_id=league.platform_league_id)
            .one_or_none()
        )
        if row is None:
            row = DbLeague(
                slug=league.league_id,
                platform=league.platform,
                platform_league_id=league.platform_league_id,
                name=league.name,
                season=league.season,
                total_teams=league.format.teams,
            )
            session.add(row)
        row.rules_json = json.dumps(cleaned)
        session.commit()
