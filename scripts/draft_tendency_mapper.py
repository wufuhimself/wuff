#!/usr/bin/env python3
"""
Map draft tendencies for each manager across their seasons.
Analyzes positional preferences, reach patterns, and round-by-round behavior.
"""
import json
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List, Tuple

data_dir = Path("data/raw")

def load_year_data(year):
    standings = json.loads((data_dir / f"standings/{year}.json").read_text())
    draft_history = json.loads((data_dir / f"draft_history/{year}.json").read_text())
    managers = json.loads((data_dir / f"managers/{year}.json").read_text())
    return standings, draft_history, managers

_nfl_rosters_cache = {}
_rankings_cache = None

def load_nfl_rosters_csv():
    """Load all NFL rosters CSVs into memory for position lookup"""
    global _nfl_rosters_cache
    if _nfl_rosters_cache:
        return _nfl_rosters_cache

    for year in range(2022, 2026):
        try:
            csv_file = data_dir / f"nfl_stats/rosters/{year}.csv"
            if csv_file.exists():
                with open(csv_file) as f:
                    lines = f.readlines()
                    for line in lines[1:]:  # Skip header
                        parts = line.strip().split(',')
                        if len(parts) >= 7:
                            full_name = parts[6]  # Column 6 is full_name
                            position = parts[2]   # Column 2 is position
                            if full_name and position:
                                _nfl_rosters_cache[full_name.lower()] = position
        except Exception as e:
            print(f"Warning: Could not load NFL rosters for {year}: {e}")

    return _nfl_rosters_cache

def load_combined_rankings():
    """Load combined rankings once"""
    global _rankings_cache
    if _rankings_cache is None:
        try:
            _rankings_cache = json.loads((data_dir / "rankings/rankings_combined.json").read_text())
        except:
            _rankings_cache = []
    return _rankings_cache

def get_player_position(player_name: str) -> str:
    """Look up player position from NFL stats rosters (primary) or rankings (fallback)"""
    # Try NFL rosters first (complete coverage)
    nfl_rosters = load_nfl_rosters_csv()
    if player_name.lower() in nfl_rosters:
        return nfl_rosters[player_name.lower()]

    # Fallback to combined rankings
    rankings = load_combined_rankings()
    for player in rankings:
        if player.get("playerName", "").lower() == player_name.lower():
            return player.get("position", "UNK")

    return "UNK"

def build_manager_timeline():
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

def analyze_draft_tendencies(email: str, timeline: List[Tuple[int, str, str]]) -> Dict:
    """Analyze draft positional tendencies for a manager"""
    if not timeline:
        return {}

    result = {
        "email": email,
        "manager_name": timeline[-1][2],
        "draft_history": []
    }

    # Collect all draft picks by position/round
    position_by_round = Counter()  # (round, position) -> count
    round_picks = defaultdict(list)  # round -> [players]
    all_positions = Counter()  # position -> count

    for year, team_name, _ in sorted(timeline):
        try:
            standings, draft_history, managers_data = load_year_data(year)

            # Find manager's draft picks
            drafted_picks = []
            for pick in draft_history["picks"]:
                team_norm = pick["team"].lower().strip()
                name_norm = team_name.lower().strip()
                if team_norm.startswith(name_norm[:8]) or name_norm.startswith(team_norm[:8]):
                    pos = get_player_position(pick["playerName"])
                    drafted_picks.append({
                        "player": pick["playerName"],
                        "round": pick["round"],
                        "pick": pick["pick"],
                        "position": pos
                    })

            # Aggregate
            for p in drafted_picks:
                position_by_round[(p["round"], p["position"])] += 1
                round_picks[p["round"]].append((p["player"], p["position"]))
                all_positions[p["position"]] += 1

            result["draft_history"].append({
                "year": year,
                "team_name": team_name,
                "picks": drafted_picks
            })

        except Exception as e:
            print(f"Error processing {email} / {year}: {e}")
            continue

    # Summarize tendencies
    if position_by_round:
        # Group by round, find most common position
        round_preferences = {}
        for round_num in range(1, 16):
            positions_this_round = {}
            for (r, pos), count in position_by_round.items():
                if r == round_num:
                    positions_this_round[pos] = count
            if positions_this_round:
                most_common = max(positions_this_round.items(), key=lambda x: x[1])
                round_preferences[round_num] = {
                    "primary": most_common[0],
                    "count": most_common[1],
                    "all_positions": positions_this_round
                }

        result["round_preferences"] = round_preferences

    if all_positions:
        result["position_summary"] = dict(all_positions.most_common())
        result["total_picks"] = sum(all_positions.values())

    return result

def main():
    manager_timeline = build_manager_timeline()

    # Filter for 3+ seasons
    consistent_managers = {
        email: timeline
        for email, timeline in manager_timeline.items()
        if len(timeline) >= 3
    }

    results = []
    for email, timeline in sorted(consistent_managers.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"Analyzing {timeline[-1][2]}...")
        analysis = analyze_draft_tendencies(email, timeline)
        if analysis.get("draft_history"):
            results.append(analysis)

    # Display summary
    print("\n" + "="*80)
    print("DRAFT TENDENCY SUMMARY")
    print("="*80)

    for mgr in results:
        print(f"\n{mgr['manager_name']} ({mgr.get('total_picks', 0)} picks across seasons)")
        print(f"  Position distribution: {mgr.get('position_summary', {})}")

        if "round_preferences" in mgr:
            print("  Round tendencies:")
            for round_num in range(1, 6):
                if round_num in mgr["round_preferences"]:
                    pref = mgr["round_preferences"][round_num]
                    print(f"    R{round_num}: {pref['primary']} ({pref['count']}x)")

    # Save detailed results
    output_file = Path("data/processed/draft_tendencies_by_manager.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\n✓ Saved to {output_file}")

if __name__ == "__main__":
    main()
