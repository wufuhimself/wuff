"""Per-user manual adjustments layered on top of the data-derived draft board.

The board itself comes from the league's own data (free rankings + the
historical QB adjustment, see app/free_rankings.py). This module is the
"what I actually think" overlay on top of it: a signed offset per player,
owned by one user for one league.

Offsets, not pinned ranks, on purpose -- the base board is regenerated daily,
so an absolute position would freeze a stale opinion in place and quietly stop
reflecting new information. `adjusted = base_rank - offset` (a positive offset
moves a player UP, toward pick 1, matching which arrow was clicked), then the
board is re-sorted and renumbered.
"""
from typing import Any, Dict, List, Optional

from .db import SessionLocal
from .models import BoardAdjustment


def _normalize(name: str) -> str:
    return ' '.join(str(name).strip().lower().split())


def load_adjustments(user_id: int, platform: str, platform_league_id: str) -> Dict[str, int]:
    """{normalized_player_name: offset} for one user on one league."""
    if not user_id:
        return {}
    with SessionLocal() as session:
        rows = (
            session.query(BoardAdjustment)
            .filter_by(user_id=user_id, platform=platform, platform_league_id=platform_league_id)
            .all()
        )
    return {_normalize(row.player_name): row.offset for row in rows if row.offset}


def bump_adjustment(
    user_id: int, platform: str, platform_league_id: str, player_name: str, delta: int,
) -> int:
    """Move a player `delta` spots (positive = up). Returns the new offset.

    Upserts, and deletes the row when the offset lands back on 0 -- "no row"
    and "offset 0" would otherwise be two spellings of the same state.
    """
    with SessionLocal() as session:
        row = (
            session.query(BoardAdjustment)
            .filter_by(user_id=user_id, platform=platform,
                       platform_league_id=platform_league_id, player_name=player_name)
            .one_or_none()
        )
        new_offset = (row.offset if row else 0) + delta
        if new_offset == 0:
            if row is not None:
                session.delete(row)
            session.commit()
            return 0
        if row is None:
            session.add(BoardAdjustment(
                user_id=user_id, platform=platform, platform_league_id=platform_league_id,
                player_name=player_name, offset=new_offset,
            ))
        else:
            row.offset = new_offset
        session.commit()
        return new_offset


def clear_adjustment(user_id: int, platform: str, platform_league_id: str, player_name: str) -> None:
    """Reset one player back to whatever the board says."""
    with SessionLocal() as session:
        session.query(BoardAdjustment).filter_by(
            user_id=user_id, platform=platform,
            platform_league_id=platform_league_id, player_name=player_name,
        ).delete()
        session.commit()


def clear_all_adjustments(user_id: int, platform: str, platform_league_id: str) -> int:
    """Reset this user's whole board for a league. Returns rows removed."""
    with SessionLocal() as session:
        removed = session.query(BoardAdjustment).filter_by(
            user_id=user_id, platform=platform, platform_league_id=platform_league_id,
        ).delete()
        session.commit()
    return removed


def apply_adjustments(
    board: List[Dict[str, Any]], adjustments: Optional[Dict[str, int]],
    rank_key: str = 'ranking', order_key: str = 'draftOrder',
) -> List[Dict[str, Any]]:
    """Re-sort a board by base rank minus each player's offset.

    Adds `userOffset` (the signed nudge, 0 when untouched) and `baseOrder`
    (where the data alone would have put them) to every row, then renumbers
    `order_key` gap-free so downstream pick math still lines up.

    Apply this BEFORE truncating to a top-N: a player sitting at 150 who's
    been moved up 60 spots has to be able to reach the visible board.
    """
    ordered = sorted(
        board,
        key=lambda row: (
            row.get(order_key) if row.get(order_key) is not None else (row.get(rank_key) or 9999)
        ),
    )
    for position, row in enumerate(ordered, start=1):
        row['baseOrder'] = position

    if not adjustments:
        for row in ordered:
            row['userOffset'] = 0
            row[order_key] = row['baseOrder']
        return ordered

    for row in ordered:
        row['userOffset'] = adjustments.get(_normalize(row.get('playerName', '')), 0)

    # Sort by adjusted position. The tiebreak has to respect which direction a
    # player was nudged, because moving up one spot lands you exactly on top of
    # the player above you: base 10 with +1 ties base 9 at adjusted 9. Breaking
    # that tie by base order alone kept the incumbent ahead, so the first press
    # of ▲ appeared to do nothing and it took two presses to move one spot.
    # Higher offset wins a tie, so a moved-up player passes the untouched one
    # and a moved-down player falls behind it. Base order still separates two
    # untouched players.
    ordered.sort(key=lambda row: (row['baseOrder'] - row['userOffset'], -row['userOffset'], row['baseOrder']))
    for position, row in enumerate(ordered, start=1):
        row[order_key] = position
    return ordered
