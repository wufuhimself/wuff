import re
from pathlib import Path
from typing import Any, BinaryIO, Dict, List

import pdfplumber

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


def _parse_ranking_entries(text: str, default_source: str) -> List[PlayerRanking]:
    """Parse rankings from text. Handles format: 'N. (POS#) PlayerName, TEAM $price etc'
    Extracts all rankings including multi-column layouts."""
    entries = []
    seen_rankings = set()

    pattern = r'(\d+)\.\s*\(([A-Z/0-9]+)\)\s*([^,]+?)\s*,\s*([A-Z]{2,3}(?:/[A-Z]{2,3})?)'
    for match in re.finditer(pattern, text):
        ranking_str, pos_rank_str, player_name, team = match.groups()

        try:
            ranking = int(ranking_str)
        except ValueError:
            continue

        player_name = player_name.strip()
        if not player_name or len(player_name) > 100:
            continue

        pos_rank = pos_rank_str.strip()
        position = _normalize_position(pos_rank.rstrip('0123456789'))

        key = (ranking, player_name)
        if key in seen_rankings:
            continue
        seen_rankings.add(key)

        entries.append({
            'playerId': player_name,
            'playerName': player_name,
            'position': position or 'UNK',
            'team': team.strip() or 'UNK',
            'ranking': ranking,
            'source': default_source,
            'posRank': pos_rank.upper() if pos_rank else None,
        })

    return entries


def parse_rankings_pdf(file_obj: BinaryIO, default_source: str) -> List[PlayerRanking]:
    """Extract rankings from PDF file. Supports both table and text-based layouts."""
    rankings: List[PlayerRanking] = []

    try:
        pdf = pdfplumber.open(file_obj)
    except Exception as exc:
        raise ValueError(f'Failed to parse PDF: {exc}') from exc

    if not pdf.pages:
        raise ValueError('PDF file contains no pages.')

    for page_num, page in enumerate(pdf.pages, start=1):
        tables = page.extract_tables()
        page_rankings = 0

        if tables:
            for table in tables:
                if not table or len(table) < 1:
                    continue

                headers = table[0]
                if not headers:
                    continue

                for row_number, row in enumerate(table[1:], start=2):
                    row_dict = {headers[i]: (row[i] if i < len(row) else '') for i in range(len(headers))}

                    player_name = _get_value(row_dict, _PLAYER_NAME_COLUMNS)
                    ranking_value = _get_value(row_dict, _RANKING_COLUMNS)

                    if not player_name or not ranking_value:
                        continue

                    try:
                        ranking = int(ranking_value)
                    except ValueError:
                        continue

                    player_id = _get_value(row_dict, _PLAYER_ID_COLUMNS) or player_name
                    source = _get_value(row_dict, _SOURCE_COLUMNS) or default_source
                    position_value = _get_value(row_dict, _POSITION_COLUMNS)
                    pos_rank_value = _get_value(row_dict, _POSITION_RANK_COLUMNS) or position_value

                    entry = {
                        'playerId': player_id,
                        'playerName': player_name,
                        'position': _normalize_position(position_value) or 'UNK',
                        'team': _get_value(row_dict, _TEAM_COLUMNS) or 'UNK',
                        'ranking': ranking,
                        'source': source,
                    }

                    pos_rank = _extract_pos_rank(pos_rank_value)
                    if pos_rank:
                        entry['posRank'] = pos_rank

                    rankings.append(entry)
                    page_rankings += 1

        if page_rankings == 0:
            text = page.extract_text()
            if text:
                page_entries = _parse_ranking_entries(text, default_source)
                rankings.extend(page_entries)
                page_rankings = len(page_entries)

    pdf.close()

    if not rankings:
        raise ValueError('PDF file did not contain any rankings data.')

    return rankings


def load_rankings_pdf(path: Path, source: str | None = None) -> List[PlayerRanking]:
    default_source = source or path.stem
    with path.open('rb') as file_obj:
        return parse_rankings_pdf(file_obj, default_source)
