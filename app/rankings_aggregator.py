import json
from typing import Any, Dict, List

Keeper = Dict[str, str]
PlayerRanking = Dict[str, Any]
AggregatedRanking = Dict[str, Any]


def normalize_name(name: str) -> str:
    return name.strip().lower()


def is_keeper(ranking: PlayerRanking, keepers: List[Keeper]) -> bool:
    normalized_name = normalize_name(str(ranking.get('playerName', '')))

    for keeper in keepers:
        player_id = keeper.get('playerId')
        player_name = keeper.get('playerName')

        if player_id and player_id == ranking.get('playerId'):
            return True
        if player_name and normalize_name(player_name) == normalized_name:
            return True

    return False


def fetch_rankings_from_site(url: str, source: str) -> List[PlayerRanking]:
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    rankings: List[PlayerRanking] = []

    for index, row in enumerate(soup.select('.player-row')):
        player_name = row.select_one('.player-name')
        position = row.select_one('.player-position')
        team = row.select_one('.player-team')
        rank = row.select_one('.player-rank')

        if not player_name or not rank:
            continue

        player_name_text = player_name.get_text(strip=True)
        ranking_value = None
        try:
            ranking_value = int(rank.get_text(strip=True))
        except ValueError:
            continue

        player_id = row.get('data-player-id') or f"{source}-{index}"
        rankings.append(
            {
                'source': source,
                'playerId': player_id,
                'playerName': player_name_text,
                'position': position.get_text(strip=True) if position else '',
                'team': team.get_text(strip=True) if team else '',
                'ranking': ranking_value,
            }
        )

    return rankings


def aggregate_rankings(rankings: List[PlayerRanking], keepers: List[Keeper] = []) -> List[AggregatedRanking]:
    filtered_rankings = [r for r in rankings if not is_keeper(r, keepers)] if keepers else rankings
    grouped: Dict[str, AggregatedRanking] = {}

    for ranking in filtered_rankings:
        key = f"{ranking['playerId']}::{ranking['playerName']}"
        if key not in grouped:
            grouped[key] = {
                'playerId': ranking['playerId'],
                'playerName': ranking['playerName'],
                'position': ranking['position'],
                'team': ranking['team'],
                'sourceRanks': {},
                'averageRank': 0.0,
            }

        grouped[key]['sourceRanks'][ranking['source']] = ranking['ranking']

    aggregated: List[AggregatedRanking] = []
    for group in grouped.values():
        source_ranks = list(group['sourceRanks'].values())
        group['averageRank'] = sum(source_ranks) / len(source_ranks) if source_ranks else 0.0
        aggregated.append(group)

    return aggregated


if __name__ == '__main__':
    print(json.dumps(aggregate_rankings([], []), indent=2))
