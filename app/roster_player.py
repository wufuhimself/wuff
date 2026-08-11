"""Platform-neutral roster-player shape.

Originally lived in yahoo_client.py as YahooRosterPlayer. strategy.py's
keeper engine (league_keeper_board(), roster_keeper_insight(), etc.) now
builds these from any platform's repository dict (Yahoo/Sleeper/ESPN — see
repository.py), not just Yahoo, so the type moved to a neutral home.
yahoo_client.YahooRosterPlayer is kept as an alias — Yahoo-specific call
sites (roster_store.py, mcp_client.py, cli.py) can keep using that name.
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RosterPlayer:  # pylint: disable=invalid-name
    playerId: str
    playerName: str
    position: str
    team: str
    status: Optional[str] = None
    selectedPosition: Optional[str] = None
    eligibleSlots: Optional[List[str]] = None
    draftRound: Optional[int] = None
    draftPick: Optional[int] = None
    draftSlot: Optional[int] = None
    keeperEligibleOverride: Optional[bool] = None
    keeperLockedOverride: Optional[bool] = None
    marketRoundOverride: Optional[int] = None
    valueNote: Optional[str] = None
    keeperNote: Optional[str] = None
