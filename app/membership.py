"""Which leagues a user may see, and which one is theirs by default.

Until 2026-08-12 every logged-in user landed on the Yahoo league: `/`,
`/keepers-board`, `/mock-draft`, `/standings` and `/draft-history` all
resolved to `league_registry.default_league_id()`, a single global constant.
Login stopped strangers reading it, but a second real user still got my
league instead of their own. This module replaces that global default with a
per-user one, and makes league access a membership question.

Membership is a `user_leagues` row, created by onboarding (Sleeper/ESPN) or
by `python3 -m app grant-league` (the registry leagues in
data/config/leagues.json, which nobody "imported" and which therefore have no
follower until granted one). Matching is on (platform, platform_league_id),
the same key KeeperMark/BoardAdjustment use -- not the wuff slug, so a league
that exists in both the registry and the DB can't read as two leagues.

There is deliberately no fallback to the registry default: a user who follows
no league has no default league, and the callers send them to onboarding.
Falling back would silently re-open the exact leak this closes.
"""
from typing import List, Optional, Set, Tuple

from .db import SessionLocal
from .league_registry import League, load_leagues
from .league_service import resolve_league
from .models import DbLeague, User, UserLeague


def followed_league_rows(user_id: int) -> List[DbLeague]:
    """This user's league rows, by display name."""
    with SessionLocal() as session:
        return (
            session.query(DbLeague)
            .join(UserLeague, UserLeague.league_id == DbLeague.id)
            .filter(UserLeague.user_id == user_id)
            .order_by(DbLeague.name)
            .all()
        )


def followed_platform_ids(user_id: int) -> Set[Tuple[str, str]]:
    """{(platform, platform_league_id)} this user follows."""
    return {(row.platform, row.platform_league_id) for row in followed_league_rows(user_id)}


def followed_leagues(user_id: int) -> List[League]:
    """Fully resolved Leagues (registry format + saved rules) this user follows."""
    leagues = []
    for row in followed_league_rows(user_id):
        league = resolve_league(row.slug)
        if league is not None:
            leagues.append(league)
    return leagues


def user_follows(user_id: int, league: League) -> bool:
    return (league.platform, league.platform_league_id) in followed_platform_ids(user_id)


def user_follows_platform_league(user_id: int, platform: str, platform_league_id: str) -> bool:
    return (platform, str(platform_league_id)) in followed_platform_ids(user_id)


def default_league_for_user(user_id: int) -> Optional[League]:
    """The league un-scoped pages resolve to for this user.

    Their stored choice when it is still one of their leagues, otherwise their
    first followed league, otherwise None (follows nothing -- send them to
    onboarding rather than to somebody else's league).
    """
    with SessionLocal() as session:
        user = session.get(User, user_id)
        stored = user.default_league_slug if user is not None else None

    if stored:
        league = resolve_league(stored)
        # A stored slug the user no longer follows (or that vanished from the
        # registry) falls through rather than being served.
        if league is not None and user_follows(user_id, league):
            return league

    for row in followed_league_rows(user_id):
        league = resolve_league(row.slug)
        if league is not None:
            return league
    return None


def set_default_league(user_id: int, slug: str) -> bool:
    """Store this user's default league. False when the league is unknown or
    not one of theirs -- the picker must not be usable to claim a league."""
    league = resolve_league(slug)
    if league is None or not user_follows(user_id, league):
        return False
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return False
        user.default_league_slug = slug
        session.commit()
    return True


def ensure_db_league(league: League) -> int:
    """DbLeague row id for a resolved league, created from the registry entry
    if it only exists in leagues.json so far. Same shape save_league_rules()
    creates, so the two can't produce competing rows."""
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
            session.commit()
        return row.id


def grant_league(email: str, slug: str, *, make_default: bool = False) -> str:
    """Give an existing user access to a league (CLI `grant-league`).

    This is how the registry leagues -- the Yahoo league above all -- reach an
    account: nothing "imports" them, so without this they have no follower and
    are visible to nobody. Deliberately not a web action: a claim button on a
    public deploy means the first stranger to click it gets my league.

    Returns a human-readable result line. Raises LookupError for an unknown
    email or slug.
    """
    league = resolve_league(slug)
    if league is None:
        known = ', '.join(sorted(load_leagues())) or '(none)'
        raise LookupError(f"Unknown league '{slug}'. Known registry leagues: {known}")

    with SessionLocal() as session:
        user = session.query(User).filter_by(email=email.strip().lower()).one_or_none()
        if user is None:
            raise LookupError(f"No account for '{email}'. Log in once at /login to create it.")
        user_id = user.id

    league_row_id = ensure_db_league(league)

    with SessionLocal() as session:
        link = session.query(UserLeague).filter_by(user_id=user_id, league_id=league_row_id).one_or_none()
        created = link is None
        if created:
            session.add(UserLeague(user_id=user_id, league_id=league_row_id))
            session.commit()

    if make_default:
        set_default_league(user_id, league.league_id)

    verb = 'granted' if created else 'already had'
    suffix = ' (now their default)' if make_default else ''
    return f'{email} {verb} access to {league.name} [{league.league_id}]{suffix}'
