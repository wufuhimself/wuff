"""LeagueFormat dataclass: teams, starter counts, keeper rules.

Persisted to data/config/league_settings.json.
Used by keeper scoring for positional scarcity (superflex, WR count, etc.).
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .paths import LEAGUE_SETTINGS_FILE, ensure_parent_dir


@dataclass
class LeagueFormat:
    teams: int = 12
    league_id: Optional[str] = None
    league_name: Optional[str] = None
    draft_type: Optional[str] = None
    scoring_type: Optional[str] = None
    keeper_cost_uses_draft_round: bool = False
    keeper_slots_at_draft_end: bool = True
    keeper_max_consecutive_seasons: int = 2
    roster_positions: List[str] = field(default_factory=list)
    scoring: Dict[str, float] = field(
        default_factory=lambda: {
            'pass_td': 4.0,
            'pass_yd_per_point': 25.0,
            'rush_yd_per_point': 10.0,
            'rec_yd_per_point': 10.0,
            'receptions': 0.5,
            'rush_td': 6.0,
            'rec_td': 6.0,
            'interception': -1.0,
            'fumble_lost': -2.0,
        }
    )
    starters: Dict[str, int] = field(
        default_factory=lambda: {
            'QB': 1,
            'RB': 2,
            'WR': 2,
            'TE': 1,
            'FLEX': 0,
            'SUPERFLEX': 0,
            'DEF': 1,
            'K': 1,
        }
    )

    def slot_count(self, slot: str) -> int:
        return int(self.starters.get(slot.upper(), 0))

    def summary(self) -> str:
        parts = [f'{self.teams}-team']
        for slot in ('QB', 'RB', 'WR', 'TE', 'FLEX', 'SUPERFLEX', 'DEF', 'K'):
            count = self.slot_count(slot)
            if count:
                parts.append(f'{count} {slot.lower()}')
        receptions = self.scoring.get('receptions')
        if receptions is not None:
            parts.append(f'{receptions:g} PPR')
        return ', '.join(parts)


def default_league_format() -> LeagueFormat:
    return LeagueFormat()


def load_league_format(path: Path = LEAGUE_SETTINGS_FILE) -> LeagueFormat:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default_league_format()

    if not isinstance(payload, dict):
        return default_league_format()

    starters = payload.get('starters')
    if not isinstance(starters, dict):
        starters = default_league_format().starters

    return LeagueFormat(
        teams=int(payload.get('teams', 12)),
        league_id=payload.get('league_id'),
        league_name=payload.get('league_name'),
        draft_type=payload.get('draft_type'),
        scoring_type=payload.get('scoring_type'),
        keeper_cost_uses_draft_round=bool(payload.get('keeper_cost_uses_draft_round', False)),
        keeper_slots_at_draft_end=bool(payload.get('keeper_slots_at_draft_end', True)),
        keeper_max_consecutive_seasons=int(payload.get('keeper_max_consecutive_seasons', 2)),
        roster_positions=list(payload.get('roster_positions', [])),
        scoring={key: float(value) for key, value in payload.get('scoring', default_league_format().scoring).items()},
        starters={key.upper(): int(value) for key, value in starters.items()},
    )


def save_league_format(league_format: LeagueFormat, path: Path = LEAGUE_SETTINGS_FILE) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(asdict(league_format), indent=2))
