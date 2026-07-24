#!/usr/bin/env python3
import json
from pathlib import Path
from collections import defaultdict

data_dir = Path("data/raw")

def load_year_data(year):
    standings = json.loads((data_dir / f"standings/{year}.json").read_text())
    draft_history = json.loads((data_dir / f"draft_history/{year}.json").read_text())
    rosters = json.loads((data_dir / f"season_rosters/{year}.json").read_text())
    return standings, draft_history, rosters

def normalize_team_name(name):
    """Normalize team name for matching (handle truncation)"""
    return name.lower().strip()

def find_roster_match(team_name, rosters):
    """Find matching roster entry by team name"""
    norm_name = normalize_team_name(team_name)
    for roster in rosters["rosters"]:
        if normalize_team_name(roster["team_name"]).startswith(norm_name[:10]):
            return roster
    # Try reverse: find prefix of team_name in roster
    for roster in rosters["rosters"]:
        if norm_name.startswith(normalize_team_name(roster["team_name"])[:10]):
            return roster
    return None

def analyze_year(year):
    standings, draft_history, rosters = load_year_data(year)

    # Get top 3 teams
    top_3_teams = [s["team"] for s in standings["standings"][:3]]

    # Build draft picks by team
    drafted_by_team = defaultdict(list)
    for pick in draft_history["picks"]:
        team = pick["team"]
        drafted_by_team[team].append(pick["playerName"])

    # Analyze each top 3 team
    results = {"year": year, "top_3": []}

    for i, standings_team in enumerate(standings["standings"][:3], 1):
        team_name = standings_team["team"]
        rank = standings_team["rank"]

        # Find draft picks
        drafted_players = set()
        for team_short, picks in drafted_by_team.items():
            if normalize_team_name(team_short).startswith(normalize_team_name(team_name)[:8]):
                drafted_players = set(picks)
                break

        # Find end-of-season roster
        roster_entry = find_roster_match(team_name, rosters)
        if not roster_entry:
            print(f"  WARNING: Could not find roster for {team_name}")
            continue

        rostered_players = {p["playerName"] for p in roster_entry["players"]}

        # Calculate acquisitions
        drafted_still_on_team = rostered_players & drafted_players
        acquisitions = rostered_players - drafted_players

        results["top_3"].append({
            "rank": rank,
            "team": team_name,
            "drafted_count": len(drafted_players),
            "drafted_retained": len(drafted_still_on_team),
            "acquisitions": len(acquisitions),
            "acquisition_rate": len(acquisitions) / len(rostered_players) if rostered_players else 0,
            "acquired_players": sorted(acquisitions)
        })

    # Calculate league averages
    all_drafted = defaultdict(lambda: {"drafted": 0, "retained": 0, "total_rostered": 0, "acquisitions": 0})
    for standings_team in standings["standings"]:
        team_name = standings_team["team"]

        drafted_players = set()
        for team_short, picks in drafted_by_team.items():
            if normalize_team_name(team_short).startswith(normalize_team_name(team_name)[:8]):
                drafted_players = set(picks)
                break

        roster_entry = find_roster_match(team_name, rosters)
        if not roster_entry:
            continue

        rostered_players = {p["playerName"] for p in roster_entry["players"]}
        drafted_retained = rostered_players & drafted_players
        acquisitions = len(rostered_players - drafted_players)

        all_drafted[team_name] = {
            "drafted": len(drafted_players),
            "retained": len(drafted_retained),
            "total_rostered": len(rostered_players),
            "acquisitions": acquisitions
        }

    if all_drafted:
        avg_drafted = sum(d["drafted"] for d in all_drafted.values()) / len(all_drafted)
        avg_retained = sum(d["retained"] for d in all_drafted.values()) / len(all_drafted)
        avg_acquisitions = sum(d["acquisitions"] for d in all_drafted.values()) / len(all_drafted)

        results["league_avg"] = {
            "drafted_per_team": round(avg_drafted, 1),
            "drafted_retained_per_team": round(avg_retained, 1),
            "acquisitions_per_team": round(avg_acquisitions, 1),
            "avg_acquisition_rate": round(avg_acquisitions / 10, 2)  # Assuming 10 roster spots
        }

    return results

# Analyze all years
all_results = []
for year in range(2021, 2026):
    print(f"\n=== {year} ===")
    result = analyze_year(year)
    all_results.append(result)

    for team in result["top_3"]:
        print(f"#{team['rank']} {team['team']}: {team['drafted_count']} drafted, {team['drafted_retained']} retained, {team['acquisitions']} acquired ({team['acquisition_rate']:.0%})")

    if "league_avg" in result:
        avg = result["league_avg"]
        print(f"League avg: {avg['drafted_per_team']} drafted, {avg['drafted_retained_per_team']} retained, {avg['acquisitions_per_team']} acquired")

# Save detailed results
output_file = Path("data/processed/top_teams_analysis.json")
output_file.parent.mkdir(parents=True, exist_ok=True)
output_file.write_text(json.dumps(all_results, indent=2))
print(f"\n✓ Detailed analysis saved to {output_file}")
