#!/usr/bin/env python3
"""
Map and analyze regular season standings with playoff classification.
Normalizes by W/L record and points for, identifies patterns between draft strategy and playoff success.
"""
import json
from pathlib import Path
from collections import defaultdict

data_dir = Path("data/raw")

def normalize_team_name(name):
    return name.lower().strip()

def load_year_standings(year):
    standings = json.loads((data_dir / f"standings/{year}.json").read_text())
    return standings.get("standings", [])

def load_year_managers(year):
    managers = json.loads((data_dir / f"managers/{year}.json").read_text())
    manager_map = {}
    for mgr in managers.get("managers", []):
        email = mgr.get("email", "").lower()
        team_name = mgr.get("team_name", "")
        manager_map[normalize_team_name(team_name)] = {
            "email": email,
            "name": mgr.get("manager_name", ""),
            "trades": mgr.get("trades", 0),
            "moves": mgr.get("moves", 0)
        }
    return manager_map

def build_standings_map():
    """Build normalized standings across all seasons"""
    all_standings = []

    for year in range(2021, 2026):
        standings = load_year_standings(year)
        managers = load_year_managers(year)

        for entry in standings:
            team_name = entry.get("team", "")
            team_norm = normalize_team_name(team_name)
            manager_info = managers.get(team_norm, {})

            # Calculate record stats
            wins = entry.get("wins", 0)
            losses = entry.get("losses", 0)
            ties = entry.get("ties", 0)
            total_games = wins + losses + ties
            win_pct = (wins / total_games * 100) if total_games > 0 else 0
            points_for = entry.get("pointsFor", 0)
            points_against = entry.get("pointsAgainst", 0)
            point_diff = points_for - points_against

            # Playoff status (top 6 = playoff, bottom 6 = regular season loser)
            rank = entry.get("rank", 0)
            made_playoffs = rank <= 6

            record = {
                "year": year,
                "team": team_name,
                "manager_email": manager_info.get("email", ""),
                "manager_name": manager_info.get("name", ""),
                "rank": rank,
                "made_playoffs": made_playoffs,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "record_str": f"{wins}-{losses}" + (f"-{ties}" if ties > 0 else ""),
                "win_pct": round(win_pct, 1),
                "points_for": round(points_for, 2),
                "points_against": round(points_against, 2),
                "point_diff": round(point_diff, 2),
                "strength_of_schedule": round(points_against / total_games, 2) if total_games > 0 else 0,
                "roster_activity": {
                    "trades": manager_info.get("trades", 0),
                    "waiver_moves": manager_info.get("moves", 0)
                }
            }
            all_standings.append(record)

    return all_standings

def analyze_playoff_vs_losers(standings):
    """Compare playoff teams vs non-playoff teams"""
    playoff_teams = [s for s in standings if s["made_playoffs"]]
    loser_teams = [s for s in standings if not s["made_playoffs"]]

    def stats_summary(teams, label):
        if not teams:
            return {}
        avg_pf = sum(t["points_for"] for t in teams) / len(teams)
        avg_pa = sum(t["points_against"] for t in teams) / len(teams)
        avg_diff = sum(t["point_diff"] for t in teams) / len(teams)
        avg_trades = sum(t["roster_activity"]["trades"] for t in teams) / len(teams)
        avg_moves = sum(t["roster_activity"]["waiver_moves"] for t in teams) / len(teams)
        avg_wins = sum(t["wins"] for t in teams) / len(teams)

        return {
            "label": label,
            "count": len(teams),
            "avg_wins": round(avg_wins, 1),
            "avg_points_for": round(avg_pf, 1),
            "avg_points_against": round(avg_pa, 1),
            "avg_point_diff": round(avg_diff, 1),
            "avg_trades": round(avg_trades, 1),
            "avg_waiver_moves": round(avg_moves, 1)
        }

    return {
        "playoff": stats_summary(playoff_teams, "Playoff Teams (Top 6)"),
        "losers": stats_summary(loser_teams, "Regular Season Losers (Bottom 6)")
    }

def main():
    standings = build_standings_map()
    comparison = analyze_playoff_vs_losers(standings)

    # Save full standings map
    output_file = Path("data/processed/regular_season_standings_normalized.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(standings, indent=2))

    # Save comparison
    comparison_file = Path("data/processed/playoff_vs_losers_comparison.json")
    comparison_file.write_text(json.dumps(comparison, indent=2))

    # Print summary
    print("REGULAR SEASON STANDINGS ANALYSIS (2021-2025)")
    print("="*80)
    print("\nPLAYOFF TEAMS vs REGULAR SEASON LOSERS\n")

    for category in ["playoff", "losers"]:
        stats = comparison[category]
        print(f"{stats['label']}")
        print(f"  Count: {stats['count']} teams")
        print(f"  Avg wins: {stats['avg_wins']}")
        print(f"  Avg points for: {stats['avg_points_for']}")
        print(f"  Avg points against: {stats['avg_points_against']}")
        print(f"  Avg point differential: {stats['avg_point_diff']}")
        print(f"  Avg trades/season: {stats['avg_trades']}")
        print(f"  Avg waiver moves/season: {stats['avg_waiver_moves']}")
        print()

    # Analyze by year
    print("BY YEAR BREAKDOWN:")
    print("-" * 80)
    standings_by_year = defaultdict(lambda: {"playoff": [], "losers": []})
    for record in standings:
        key = "playoff" if record["made_playoffs"] else "losers"
        standings_by_year[record["year"]][key].append(record)

    for year in sorted(standings_by_year.keys()):
        yearly = standings_by_year[year]
        print(f"\n{year}:")
        print(f"  Playoff teams: {len(yearly['playoff'])}")
        print(f"  Loser teams: {len(yearly['losers'])}")

        if yearly["playoff"]:
            avg_pf_playoff = sum(t["points_for"] for t in yearly["playoff"]) / len(yearly["playoff"])
            print(f"  Avg PF (playoff): {avg_pf_playoff:.1f}")

        if yearly["losers"]:
            avg_pf_loser = sum(t["points_for"] for t in yearly["losers"]) / len(yearly["losers"])
            print(f"  Avg PF (losers): {avg_pf_loser:.1f}")

    print(f"\n✓ Full standings saved to {output_file}")
    print(f"✓ Comparison saved to {comparison_file}")

if __name__ == "__main__":
    main()
