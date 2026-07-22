import csv
import re
from pathlib import Path
from typing import Any, Dict, List, TextIO


PlayerRanking = Dict[str, Any]

_PLAYER_NAME_COLUMNS = ('playername', 'player', 'name')
_PLAYER_ID_COLUMNS = ('playerid', 'id', 'yahooplayerid')
_POSITION_COLUMNS = ('position', 'pos')
_POSITION_RANK_COLUMNS = ('posrank', 'position_rank', 'pos_rank')
_TEAM_COLUMNS = ('team', 'nflteam')
_RANKING_COLUMNS = ('ranking', 'rank', 'rk', 'overall', 'overallrank')
_SOURCE_COLUMNS = ('source', 'site')


def _normalize_header(value: str) -> str:
    return ''.join(ch for ch in value.strip().lower() if ch.isalnum())


def _get_value(row: Dict[str, str], columns: tuple[str, ...]) -> str:
    for header, value in row.items():
        if _normalize_header(header) in columns:
            return (value or '').strip()
    return ''


def _normalize_position(value: str) -> str:
    if not value:
        return ''
    match = re.match(r'[A-Za-z/]+', value.strip())
    return match.group(0).upper() if match else value.strip().upper()


def _extract_pos_rank(value: str) -> str:
    if not value:
        return ''
    value = value.strip()
    match = re.match(r'([A-Za-z/]+)(\d+)', value)
    if match:
        return match.group(0).upper()
    return ''


def parse_rankings_csv(file_obj: TextIO, default_source: str) -> List[PlayerRanking]:
    reader = csv.DictReader(file_obj)
    if not reader.fieldnames:
        raise ValueError('CSV file must include a header row.')

    rankings: List[PlayerRanking] = []

    for row_number, row in enumerate(reader, start=2):
        player_name = _get_value(row, _PLAYER_NAME_COLUMNS)
        ranking_value = _get_value(row, _RANKING_COLUMNS)
        if not player_name and not ranking_value:
            continue
        if not player_name:
            raise ValueError(f'Row {row_number} is missing a player name column.')
        if not ranking_value:
            raise ValueError(f'Row {row_number} is missing a ranking column.')

        try:
            ranking = int(ranking_value)
        except ValueError as exc:
            raise ValueError(f'Row {row_number} has a non-numeric ranking: {ranking_value!r}.') from exc

        player_id = _get_value(row, _PLAYER_ID_COLUMNS) or player_name
        source = _get_value(row, _SOURCE_COLUMNS) or default_source
        position_value = _get_value(row, _POSITION_COLUMNS)
        pos_rank_value = _get_value(row, _POSITION_RANK_COLUMNS) or position_value

        entry = {
            'playerId': player_id,
            'playerName': player_name,
            'position': _normalize_position(position_value) or 'UNK',
            'team': _get_value(row, _TEAM_COLUMNS) or 'UNK',
            'ranking': ranking,
            'source': source,
        }

        pos_rank = _extract_pos_rank(pos_rank_value)
        if pos_rank:
            entry['posRank'] = pos_rank

        rankings.append(entry)

    if not rankings:
        raise ValueError('CSV file did not contain any rankings rows.')

    return rankings


def load_rankings_csv(path: Path, source: str | None = None) -> List[PlayerRanking]:
    default_source = source or path.stem
    with path.open(newline='', encoding='utf-8-sig') as file_obj:
        return parse_rankings_csv(file_obj, default_source)
