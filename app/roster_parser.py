import re
import json
from typing import Optional, List, Dict, Any
from .strategy import load_yahoo_rankings
from .paths import RANKINGS_COMBINED_FILE


def _clean_player_name(raw_name: str) -> str:
    """Remove 'Video Forecast', the 'player Notes' link label Yahoo's pasted
    text appends after (almost) every name, and an injury-status letter
    (Q/O/D/IR/PUP/DTD/...) that sometimes sits between the name and that
    label -- e.g. 'Zach Charbonnet Q player Notes' -> 'Zach Charbonnet'.
    Left unstripped, both leak into playerName and break every downstream
    rankings/ADP name match for that player."""
    cleaned = re.sub(r'\s*Video Forecast\s*', '', raw_name)
    cleaned = re.sub(r'\s+(?:Q|O|D|IR|PUP|DTD|SUSP)?\s*player Notes\s*$', '', cleaned)
    return cleaned.strip()


def _extract_nfl_team(player_name: str, rankings: List[Dict[str, Any]]) -> str:
    """Look up player in rankings to find NFL team, fallback to 'UNK'."""
    normalized_name = ' '.join(player_name.strip().lower().split())
    for item in rankings:
        ranking_name = ' '.join(str(item.get('playerName', '')).strip().lower().split())
        if ranking_name == normalized_name:
            team = item.get('team', 'UNK')
            return str(team).upper() if team else 'UNK'
    return 'UNK'


def parse_yahoo_text_rosters(text: str, rankings: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Parse raw Yahoo Fantasy text (copy-pasted from browser) into league roster structure.

    Returns list of team dicts with players in YahooRosterPlayer format.
    """
    if rankings is None:
        # Prefer combined rankings if available
        if RANKINGS_COMBINED_FILE.exists():
            rankings = json.loads(RANKINGS_COMBINED_FILE.read_text())
        if not rankings:
            rankings = load_yahoo_rankings()

    teams = []
    current_team_name = None
    current_players = []

    lines = text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines
        if not line:
            i += 1
            continue

        # Detect team header (lines that look like team names, not "Pos" or "Player")
        skip_prefixes = ('Pos', 'Player', 'Cost', 'Final')
        if line and not line.startswith(skip_prefixes):
            # Check if this might be a team name (next non-empty line should be "Pos")
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1

            if j < len(lines) and 'Pos' in lines[j]:
                # This is a team name
                if current_team_name and current_players:
                    teams.append({
                        'teamName': current_team_name,
                        'players': current_players
                    })

                current_team_name = line
                current_players = []
                i = j + 1
                continue

        # Parse position/player lines
        if line.startswith('Pos') or line.startswith('Player') or line.startswith('Cost'):
            # Header row, skip
            i += 1
            continue

        # Match position lines: "QB", "WR", "RB", "TE", "K", "DEF", "BN", "IR", "Q/W/R/T"
        pos_match = re.match(r'^(QB|WR|RB|TE|K|DEF|BN|IR|Q/W/R/T)\s+(.+)$', line)
        if pos_match:
            position = pos_match.group(1)
            player_line = pos_match.group(2)

            # Clean player name (remove game results, video forecast, etc)
            player_name = _clean_player_name(player_line)
            if not player_name:
                i += 1
                continue

            # Normalize position (BN -> roster player, IR -> roster player)
            if position in ('BN', 'IR'):
                display_pos = 'BN'
            else:
                display_pos = position

            # Look up NFL team
            nfl_team = _extract_nfl_team(player_name, rankings)

            current_players.append({
                'playerId': player_name,
                'playerName': player_name,
                'position': display_pos,
                'team': nfl_team,
                'status': None,
                'selectedPosition': None,
                'eligibleSlots': None,
                'draftRound': None,
                'draftPick': None,
                'draftSlot': None,
                'keeperEligibleOverride': None,
                'keeperLockedOverride': None,
                'marketRoundOverride': None,
                'valueNote': None,
                'keeperNote': None,
            })

        i += 1

    # Don't forget last team
    if current_team_name and current_players:
        teams.append({
            'teamName': current_team_name,
            'players': current_players
        })

    return teams


def format_roster_preview(teams: List[Dict[str, Any]]) -> str:
    """Format parsed teams for user preview."""
    lines = []
    for team in teams:
        team_name = team.get('teamName', 'Unknown')
        players = team.get('players', [])
        lines.append(f"\n{team_name} ({len(players)} players)")
        for p in players[:3]:
            lines.append(f"  - {p.get('playerName')} ({p.get('position')}, {p.get('team')})")
        if len(players) > 3:
            lines.append(f"  ... and {len(players) - 3} more")

    return '\n'.join(lines)
