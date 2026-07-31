import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from .config import config

BASE_URL = 'https://fantasysports.yahooapis.com/fantasy/v2'


@dataclass
class YahooToken:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str
    expires_at: int


@dataclass
class YahooRosterPlayer:  # pylint: disable=invalid-name
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


@dataclass
class LineupSlot:  # pylint: disable=invalid-name
    position: str
    playerId: str
    playerName: str


def get_field(node: Any, field: str) -> Any:
    if node is None:
        return None
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and field in item:
                return item[field]
        return None
    if isinstance(node, dict):
        return node.get(field)
    return None


def find_players_array(node: Any) -> Optional[List[Any]]:
    if node is None:
        return None

    if isinstance(node, list):
        if all(isinstance(item, dict) and ('player_key' in item or 'player' in item) for item in node):
            return node
        for item in node:
            result = find_players_array(item)
            if result is not None:
                return result
    elif isinstance(node, dict):
        for value in node.values():
            result = find_players_array(value)
            if result is not None:
                return result

    return None


def _parse_numeric_from_dict(node: Dict[Any, Any], field_names: List[str]) -> Optional[int]:
    for key, value in node.items():
        normalized = key.lower()
        if any(field_name in normalized for field_name in field_names):
            candidate = parse_numeric_field(value, field_names)
            if candidate is not None:
                return candidate
        nested = parse_numeric_field(value, field_names)
        if nested is not None:
            return nested
    return None


def parse_numeric_field(node: Any, field_names: List[str]) -> Optional[int]:
    if node is None:
        return None

    if isinstance(node, int):
        return node
    if isinstance(node, str) and node.isdigit():
        return int(node)
    if isinstance(node, list):
        for item in node:
            result = parse_numeric_field(item, field_names)
            if result is not None:
                return result
    if isinstance(node, dict):
        return _parse_numeric_from_dict(node, field_names)

    return None


def parse_yahoo_roster_players(data: Any) -> List[YahooRosterPlayer]:
    players_container = find_players_array(data)
    if players_container is None:
        return []

    players: List[YahooRosterPlayer] = []
    for entry in players_container:
        player = entry.get('player') if isinstance(entry, dict) and 'player' in entry else entry
        player_id = get_field(player, 'player_key')
        player_name = get_field(get_field(player, 'name'), 'full')
        position = get_field(player, 'primary_position') or get_field(player, 'display_position')
        team = get_field(player, 'editorial_team_abbr')
        status = get_field(player, 'status')
        selected_position = get_field(player, 'selected_position')
        eligible = get_field(player, 'eligible_positions')
        eligible_slots = [item.get('position') for item in eligible] if isinstance(eligible, list) else None
        draft_round = parse_numeric_field(player, ['draft_round', 'round', 'keeper_round', 'keeperround'])
        draft_pick = parse_numeric_field(player, ['pick', 'draft_pick', 'draftpick', 'pick_number'])
        draft_slot = parse_numeric_field(player, ['draft_slot', 'slot', 'draftslot'])

        if not player_id or not player_name:
            continue

        players.append(
            YahooRosterPlayer(
                playerId=player_id,
                playerName=player_name,
                position=position or 'UNK',
                team=team or 'UNK',
                status=status,
                selectedPosition=selected_position,
                eligibleSlots=eligible_slots,
                draftRound=draft_round,
                draftPick=draft_pick,
                draftSlot=draft_slot,
            )
        )

    return players


def find_keepers_from_players(players: List[YahooRosterPlayer]) -> List[YahooRosterPlayer]:
    players_with_rounds = [player for player in players if player.draftRound is not None]
    if not players_with_rounds:
        return []

    candidates = [player for player in players_with_rounds if player.draftRound is not None and player.draftRound > 2]
    if not candidates:
        return []

    unique_rounds = sorted({player.draftRound for player in candidates if player.draftRound is not None}, reverse=True)
    keeper_rounds = unique_rounds[:2]
    return [player for player in candidates if player.draftRound in keeper_rounds]


def parse_yahoo_players(data: Any) -> List[Dict[str, Any]]:
    players_container = data.get('fantasy_content', {}).get('league', [None, {}]).get(1)
    if not isinstance(players_container, list) or len(players_container) < 2 or not isinstance(players_container[1], list):
        return []

    players = players_container[1]
    parsed: List[Dict[str, Any]] = []

    for entry in players:
        player = entry.get('player') if isinstance(entry, dict) and 'player' in entry else entry
        player_id = get_field(player, 'player_key')
        name = get_field(get_field(player, 'name'), 'full')
        position = get_field(player, 'primary_position') or get_field(player, 'display_position')
        team = get_field(player, 'editorial_team_abbr')

        if not player_id or not name:
            continue

        parsed.append(
            {
                'playerId': player_id,
                'playerName': name,
                'position': position or 'UNK',
                'team': team or 'UNK',
            }
        )

    return parsed


def _auth_headers(access_token: str) -> Dict[str, str]:
    return {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}


def get_yahoo_auth_url() -> str:
    params = {
        'client_id': config.yahoo_client_id,
        'redirect_uri': config.yahoo_redirect_uri,
        'response_type': 'code',
        'language': 'en-us',
    }
    return f"https://api.login.yahoo.com/oauth2/request_auth?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(code: str) -> YahooToken:
    payload = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': config.yahoo_redirect_uri,
        'client_id': config.yahoo_client_id,
        'client_secret': config.yahoo_client_secret,
    }
    response = requests.post(
        'https://api.login.yahoo.com/oauth2/get_token',
        data=payload,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    token_data['expires_at'] = int(time.time()) + int(token_data.get('expires_in', 0))
    return YahooToken(
        access_token=token_data['access_token'],
        refresh_token=token_data['refresh_token'],
        expires_in=token_data['expires_in'],
        token_type=token_data['token_type'],
        expires_at=token_data['expires_at'],
    )


def refresh_token(refresh_token_value: str) -> YahooToken:
    payload = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token_value,
        'client_id': config.yahoo_client_id,
        'client_secret': config.yahoo_client_secret,
    }
    response = requests.post(
        'https://api.login.yahoo.com/oauth2/get_token',
        data=payload,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    token_data['expires_at'] = int(time.time()) + int(token_data.get('expires_in', 0))
    return YahooToken(
        access_token=token_data['access_token'],
        refresh_token=token_data['refresh_token'],
        expires_in=token_data['expires_in'],
        token_type=token_data['token_type'],
        expires_at=token_data['expires_at'],
    )


def fetch_yahoo_rankings(access_token: str, count: int = 200) -> List[Dict[str, Any]]:
    request_url = f"{BASE_URL}/league/{config.yahoo_league_id}/players;status=ALL;sort=rank;count={count}?format=json"
    response = requests.get(request_url, headers=_auth_headers(access_token), timeout=30)
    response.raise_for_status()
    players = parse_yahoo_players(response.json())
    return [
        {
            'source': 'Yahoo',
            'ranking': index + 1,
            **player,
        }
        for index, player in enumerate(players)
    ]


def get_roster(access_token: str) -> Any:
    response = requests.get(f"{BASE_URL}/team/{config.yahoo_team_key}/roster?format=json", headers=_auth_headers(access_token), timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_yahoo_roster_players(access_token: str) -> List[YahooRosterPlayer]:
    return parse_yahoo_roster_players(get_roster(access_token))


def fetch_yahoo_keepers(access_token: str) -> List[YahooRosterPlayer]:
    return find_keepers_from_players(fetch_yahoo_roster_players(access_token))


def set_lineup(access_token: str, lineup: List[Dict[str, str]]) -> Any:
    values = []
    for slot in lineup:
        values.append(('position[]', slot['position']))
        values.append(('player_ids[]', slot['playerId']))

    encoded = urllib.parse.urlencode(values)
    url = f"{BASE_URL}/team/{config.yahoo_team_key}/roster;{encoded}"
    response = requests.put(
        url,
        data=None,
        headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/xml'},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_games(access_token: str, seasons: List[int]) -> Dict[int, str]:
    """Fetch game_key mapping for given seasons.
    Returns: {season: game_key}"""
    seasons_str = ','.join(str(s) for s in seasons)
    request_url = f"{BASE_URL}/games;seasons={seasons_str}?format=json"
    response = requests.get(request_url, headers=_auth_headers(access_token), timeout=30)
    response.raise_for_status()
    data = response.json()

    result = {}
    games_list = data.get('fantasy_content', {}).get('games', [])
    if isinstance(games_list, list):
        for item in games_list:
            if isinstance(item, dict) and 'game' in item:
                game = item['game']
                season = int(game.get('season', 0))
                game_key = game.get('game_key')
                if season and game_key:
                    result[season] = game_key

    return result


def _leagues_from_game(game: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    leagues_list = game.get('leagues', [])
    if not isinstance(leagues_list, list):
        return result

    for league_item in leagues_list:
        if not (isinstance(league_item, dict) and 'league' in league_item):
            continue
        league = league_item['league']
        league_id = league.get('league_id')
        if league_id:
            result[str(league_id)] = {
                'league_key': league.get('league_key'),
                'name': league.get('name'),
                'league_id': league_id,
            }

    return result


def _leagues_from_games(games: Any) -> Dict[str, Any]:
    result = {}
    if not isinstance(games, list):
        return result

    for item in games:
        if isinstance(item, dict) and 'game' in item:
            result.update(_leagues_from_game(item['game']))

    return result


def fetch_user_leagues(access_token: str, game_key: str) -> Dict[str, Any]:
    """Fetch user's league(s) for a given game_key.
    Returns: {league_id: {name, team_key, ...}}"""
    request_url = f"{BASE_URL}/users;use_login=1/games/{game_key}/leagues?format=json"
    response = requests.get(request_url, headers=_auth_headers(access_token), timeout=30)
    response.raise_for_status()
    data = response.json()

    user_data = data.get('fantasy_content', {}).get('users', [])
    if not (isinstance(user_data, list) and len(user_data) > 0):
        return {}

    user = user_data[0].get('user', [])
    if not (isinstance(user, list) and len(user) > 1):
        return {}

    return _leagues_from_games(user[1].get('games', []))


def fetch_standings(access_token: str, league_key: str) -> Optional[Dict[str, Any]]:
    """Fetch league standings for a given league_key.
    Returns: {year, standings: []}"""
    request_url = f"{BASE_URL}/league/{league_key}/standings?format=json"
    response = requests.get(request_url, headers=_auth_headers(access_token), timeout=30)
    response.raise_for_status()
    data = response.json()

    league_data = data.get('fantasy_content', {}).get('league', [])
    if not isinstance(league_data, list) or len(league_data) < 2:
        return None

    standings_data = league_data[1] if len(league_data) > 1 else []

    standings = []
    if isinstance(standings_data, dict):
        standings_list = standings_data.get('standings', [])
        if isinstance(standings_list, list) and len(standings_list) > 0:
            teams_data = standings_list[0].get('standings', [])
            if isinstance(teams_data, list):
                for team_item in teams_data:
                    if isinstance(team_item, dict) and 'team' in team_item:
                        team = team_item['team']
                        team_name = team.get('name')
                        team_rank = team.get('team_standings', {}).get('rank')

                        standings_info = team.get('team_standings', {})
                        record = standings_info.get('outcome_totals', {})

                        standings.append({
                            'rank': int(team_rank) if team_rank else None,
                            'team': team_name,
                            'wins': int(record.get('wins', 0)),
                            'losses': int(record.get('losses', 0)),
                            'ties': int(record.get('ties', 0)),
                            'pointsFor': float(standings_info.get('points_for', 0)) or 0,
                            'pointsAgainst': float(standings_info.get('points_against', 0)) or 0,
                        })

    season = None
    if isinstance(league_data[0], dict):
        season = int(league_data[0].get('season', 0))

    return {
        'year': season,
        'standings': sorted(standings, key=lambda x: x.get('rank', 999)) if standings else [],
    }


def fetch_league_teams(access_token: str, league_key: str) -> List[Dict[str, Any]]:
    """Fetch all teams in a league.
    Returns: [{'team_key': str, 'team_id': int, 'name': str, 'manager_name': str}, ...]"""
    request_url = f"{BASE_URL}/league/{league_key}/teams?format=json"
    response = requests.get(request_url, headers=_auth_headers(access_token), timeout=30)
    response.raise_for_status()
    data = response.json()

    teams = []
    league_data = data.get('fantasy_content', {}).get('league', [])
    if isinstance(league_data, list) and len(league_data) > 1:
        teams_data = league_data[1]
        if isinstance(teams_data, dict):
            teams_list = teams_data.get('teams', [])
            if isinstance(teams_list, list):
                for team_item in teams_list:
                    if isinstance(team_item, dict) and 'team' in team_item:
                        team = team_item['team']
                        teams.append({
                            'team_key': team.get('team_key'),
                            'team_id': team.get('team_id'),
                            'name': team.get('name'),
                            'manager_name': team.get('managers', [{}])[0].get('manager', {}).get('nickname', ''),
                        })
    return teams


def fetch_team_roster(access_token: str, team_key: str) -> List[YahooRosterPlayer]:
    """Fetch roster for a specific team by team_key."""
    response = requests.get(f"{BASE_URL}/team/{team_key}/roster?format=json", headers=_auth_headers(access_token), timeout=30)
    response.raise_for_status()
    return parse_yahoo_roster_players(response.json())


def fetch_all_team_rosters(access_token: str, league_key: str) -> Dict[str, tuple[str, List[YahooRosterPlayer]]]:
    """Fetch rosters for all teams in a league.
    Returns: {team_key: (team_name, roster_players), ...}"""
    teams = fetch_league_teams(access_token, league_key)
    result = {}

    for team in teams:
        team_key = team.get('team_key')
        team_name = team.get('name')
        if team_key:
            try:
                roster = fetch_team_roster(access_token, team_key)
                result[team_key] = (team_name, roster)
            except Exception as e:
                print(f"Error fetching roster for {team_name}: {e}", file=__import__('sys').stderr)
                result[team_key] = (team_name, [])

    return result


if __name__ == '__main__':
    print('This module is intended to be imported by app.main')
