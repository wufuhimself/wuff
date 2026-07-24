#!/usr/bin/env python3
"""
Build comprehensive team/manager profile mapping:
- Manager identity, email, history
- Draft patterns (positional strategy)
- Roster retention (draft vs acquisitions)
- Results (wins/losses/rank)
"""
import json
from pathlib import Path
from collections import defaultdict

data_dir = Path("data/raw")

def load_year_data(year):
    standings = json.loads((data_dir / f"standings/{year}.json").read_text())
    draft_history = json.loads((data_dir / f"draft_history/{year}.json").read_text())
    managers = json.loads((data_dir / f"managers/{year}.json").read_text())
    rosters = json.loads((data_dir / f"season_rosters/{year}.json").read_text())
    return standings, draft_history, managers, rosters

def normalize_team_name(name):
    return name.lower().strip()

def find_roster_match(team_name, rosters):
    norm_name = normalize_team_name(team_name)
    for roster in rosters.get("rosters", []):
        if normalize_team_name(roster["team_name"]).startswith(norm_name[:10]):
            return roster
    for roster in rosters.get("rosters", []):
        if norm_name.startswith(normalize_team_name(roster["team_name"])[:10]):
            return roster
    return None

def build_manager_timeline():
    manager_timeline = defaultdict(list)
    for year in range(2021, 2026):
        try:
            managers = json.loads((data_dir / f"managers/{year}.json").read_text())
            for manager in managers.get("managers", []):
                email = manager.get("email", "").lower()
                if email:
                    manager_timeline[email].append({
                        "year": year,
                        "team_name": manager.get("team_name", ""),
                        "manager_name": manager.get("manager_name", ""),
                        "moves": manager.get("moves", 0),
                        "trades": manager.get("trades", 0)
                    })
        except FileNotFoundError:
            continue
    return manager_timeline

def build_team_profiles():
    manager_timeline = build_manager_timeline()
    draft_patterns = json.loads(Path("data/processed/draft_tendencies_by_manager.json").read_text())
    pattern_summary = json.loads(Path("data/processed/draft_pattern_summary.json").read_text())

    # Index draft patterns by manager name
    patterns_by_mgr = {p["manager"]: p for p in pattern_summary}

    profiles = []

    for email, timeline in sorted(manager_timeline.items(), key=lambda x: len(x[1]), reverse=True):
        if len(timeline) < 3:
            continue

        manager_name = timeline[-1]["manager_name"]
        pattern = patterns_by_mgr.get(manager_name, {})

        # Build season-by-season record
        seasons = []
        for season_data in sorted(timeline, key=lambda x: x["year"]):
            year = season_data["year"]
            team_name = season_data["team_name"]

            try:
                standings, draft_history, managers, rosters = load_year_data(year)

                # Find draft picks
                drafted_set = set()
                for pick in draft_history.get("picks", []):
                    if normalize_team_name(pick["team"]).startswith(normalize_team_name(team_name)[:8]):
                        drafted_set.add(pick["playerName"])

                # Find roster
                roster_entry = find_roster_match(team_name, rosters)
                rostered_set = {p["playerName"] for p in roster_entry.get("players", [])} if roster_entry else set()

                # Find standings
                team_rank = None
                for s in standings.get("standings", []):
                    if normalize_team_name(s["team"]).startswith(normalize_team_name(team_name)[:8]):
                        team_rank = s["rank"]
                        break

                retained = len(rostered_set & drafted_set)
                acquisitions = len(rostered_set - drafted_set)
                acq_rate = acquisitions / len(rostered_set) if rostered_set else 0

                season_record = {
                    "year": year,
                    "team_name": team_name,
                    "rank": team_rank,
                    "drafted_count": len(drafted_set),
                    "retained_from_draft": retained,
                    "acquisitions": acquisitions,
                    "acquisition_rate": round(acq_rate, 2),
                    "roster_activity": {
                        "trades": season_data.get("trades", 0),
                        "waiver_moves": season_data.get("moves", 0)
                    }
                }
                seasons.append(season_record)
            except Exception as e:
                print(f"Error processing {manager_name}/{year}: {e}")

        # Aggregate stats
        if seasons:
            avg_rank = sum(s["rank"] for s in seasons if s["rank"]) / len([s for s in seasons if s["rank"]]) if any(s["rank"] for s in seasons) else None
            avg_acquisitions = sum(s["acquisition_rate"] for s in seasons) / len(seasons)
            total_trades = sum(s["roster_activity"]["trades"] for s in seasons)
            total_moves = sum(s["roster_activity"]["waiver_moves"] for s in seasons)

            profile = {
                "manager": manager_name,
                "email": email,
                "seasons_active": len(timeline),
                "years": [t["year"] for t in timeline],
                "avg_rank": round(avg_rank, 1) if avg_rank else None,
                "draft_strategy": pattern.get("strategy", "Unknown"),
                "position_distribution": pattern.get("position_distribution", {}),
                "roster_behavior": {
                    "avg_acquisition_rate": round(avg_acquisitions, 2),
                    "total_trades": total_trades,
                    "total_waiver_moves": total_moves,
                    "avg_waiver_moves_per_season": round(total_moves / len(seasons), 1)
                },
                "seasons": seasons
            }
            profiles.append(profile)

    # Save master profile
    output = Path("data/processed/team_manager_profiles.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profiles, indent=2))

    # Print summary
    print("TEAM/MANAGER PROFILE MAPPING")
    print("="*80)
    for p in profiles:
        print(f"\n{p['manager']} (Years: {', '.join(str(y) for y in p['years'])})")
        print(f"  Email: {p['email']}")
        print(f"  Avg rank: #{p.get('avg_rank', 'N/A')}")
        print(f"  Draft strategy: {p['draft_strategy']}")
        print(f"  Positions: {p['position_distribution']}")
        print(f"  Roster behavior:")
        print(f"    - Avg acquisition rate: {p['roster_behavior']['avg_acquisition_rate']:.0%}")
        print(f"    - Total trades: {p['roster_behavior']['total_trades']}")
        print(f"    - Avg waiver moves/season: {p['roster_behavior']['avg_waiver_moves_per_season']}")

    print(f"\n✓ Master profile saved to {output}")

if __name__ == "__main__":
    build_team_profiles()
