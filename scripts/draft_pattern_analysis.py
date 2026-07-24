#!/usr/bin/env python3
"""
Analyze draft and acquisition patterns for managers with consistent league history.
Maps teams by manager email across seasons to track behavior over time.
"""
import json
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, Set, List, Tuple

data_dir = Path("data/raw")

def load_year_data(year):
    """Load all data for a season"""
    standings = json.loads((data_dir / f"standings/{year}.json").read_text())
    draft_history = json.loads((data_dir / f"draft_history/{year}.json").read_text())
    rosters = json.loads((data_dir / f"season_rosters/{year}.json").read_text())
    managers = json.loads((data_dir / f"managers/{year}.json").read_text())
    return standings, draft_history, rosters, managers

def normalize_team_name(name):
    return name.lower().strip()

def find_roster_match(team_name, rosters):
    """Find roster entry by team name"""
    norm_name = normalize_team_name(team_name)
    for roster in rosters["rosters"]:
        if normalize_team_name(roster["team_name"]).startswith(norm_name[:10]):
            return roster
    for roster in rosters["rosters"]:
        if norm_name.startswith(normalize_team_name(roster["team_name"])[:10]):
            return roster
    return None

def get_nfl_position(player_name: str, nfl_stats: dict) -> str:
    """Look up player position from NFL stats"""
    if not nfl_stats:
        return "UNK"
    for entry in nfl_stats:
        if entry.get("playerName", "").lower() == player_name.lower():
            return entry.get("position", "UNK")
    return "UNK"

def build_manager_timeline() -> Dict[str, List[Tuple[int, str, str]]]:
    """Build mapping of email -> [(year, team_name, manager_name)]"""
    manager_timeline = defaultdict(list)

    for year in range(2021, 2026):
        try:
            managers = json.loads((data_dir / f"managers/{year}.json").read_text())
            for manager in managers.get("managers", []):
                email = manager.get("email", "").lower()
                if email:
                    manager_timeline[email].append((
                        year,
                        manager.get("team_name", ""),
                        manager.get("manager_name", "")
                    ))
        except FileNotFoundError:
            continue

    return manager_timeline

def analyze_manager_drafts(email: str, timeline: List[Tuple[int, str, str]]) -> Dict:
    """Analyze draft patterns for a manager across their seasons"""
    if not timeline:
        return {}

    result = {
        "email": email,
        "manager_name": timeline[-1][2],  # Most recent name
        "seasons": []
    }

    draft_positions = Counter()
    acquisition_rates = []

    for year, team_name, _ in sorted(timeline):
        try:
            standings, draft_history, rosters, managers_data = load_year_data(year)

            # Find manager's draft picks
            drafted_players = []
            for pick in draft_history["picks"]:
                if normalize_team_name(pick["team"]).startswith(normalize_team_name(team_name)[:8]):
                    drafted_players.append({
                        "player": pick["playerName"],
                        "round": pick["round"],
                        "pick": pick["pick"]
                    })

            # Find final roster
            roster_entry = find_roster_match(team_name, rosters)
            if not roster_entry:
                continue

            drafted_set = {p["player"] for p in drafted_players}
            rostered_set = {p["playerName"] for p in roster_entry["players"]}

            # Find standings rank
            team_rank = None
            for s in standings["standings"]:
                if normalize_team_name(s["team"]).startswith(normalize_team_name(team_name)[:8]):
                    team_rank = s["rank"]
                    break

            acquisitions = rostered_set - drafted_set
            retained = rostered_set & drafted_set
            acq_rate = len(acquisitions) / len(rostered_set) if rostered_set else 0

            # Track positional distribution
            for pick in drafted_players:
                draft_positions[f"R{pick['round']}"] += 1

            acquisition_rates.append(acq_rate)

            season_info = {
                "year": year,
                "team_name": team_name,
                "rank": team_rank,
                "drafted": len(drafted_set),
                "retained": len(retained),
                "acquisitions": len(acquisitions),
                "acquisition_rate": round(acq_rate, 2),
                "drafted_players": sorted([p["player"] for p in drafted_players[:5]])  # First 5
            }
            result["seasons"].append(season_info)

        except Exception as e:
            print(f"Error processing {email} / {year}: {e}")
            continue

    if result["seasons"]:
        result["avg_acquisitions"] = round(sum(acquisition_rates) / len(acquisition_rates), 2) if acquisition_rates else 0
        result["consistency"] = len(result["seasons"])

    return result

def main():
    manager_timeline = build_manager_timeline()

    # Filter for managers with 3+ seasons
    consistent_managers = {
        email: timeline
        for email, timeline in manager_timeline.items()
        if len(timeline) >= 3
    }

    print(f"Found {len(consistent_managers)} managers with 3+ seasons of history\n")

    results = []
    for email, timeline in sorted(consistent_managers.items(), key=lambda x: len(x[1]), reverse=True):
        analysis = analyze_manager_drafts(email, timeline)
        if analysis.get("seasons"):
            results.append(analysis)

            print(f"{'='*70}")
            print(f"{analysis['manager_name']} ({len(analysis['seasons'])} seasons)")
            print(f"{'='*70}")

            for season in analysis["seasons"]:
                print(f"\n{season['year']}: {season['team_name']} (#{season['rank']})")
                print(f"  Drafted: {season['drafted']}, Retained: {season['retained']}, Acquired: {season['acquisitions']} ({season['acquisition_rate']:.0%})")
                print(f"  First 5 picks: {', '.join(season['drafted_players'][:3])}...")

            print(f"\nAvg acquisition rate: {analysis['avg_acquisitions']:.0%}")

            # Pattern inference
            acq_rates = [s['acquisition_rate'] for s in analysis['seasons']]
            if sum(acq_rates) / len(acq_rates) > 0.4:
                print("Pattern: TRADE-HEAVY (high acquisition rate)")
            elif sum(acq_rates) / len(acq_rates) < 0.2:
                print("Pattern: DRAFT-FOCUSED (low acquisition rate)")
            else:
                print("Pattern: BALANCED (moderate acquisitions)")

    # Save detailed results
    output_file = Path("data/processed/draft_patterns_by_manager.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\n✓ Saved to {output_file}")

if __name__ == "__main__":
    main()
