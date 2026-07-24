#!/usr/bin/env python3
"""
Cross-reference draft strategy with playoff performance.
Maps each manager's draft patterns against their playoff/loser status across seasons.
"""
import json
from pathlib import Path
from collections import defaultdict

data_dir = Path("data/raw")

def normalize_team_name(name):
    return name.lower().strip()

def load_team_profiles():
    """Load master team/manager profiles with draft strategies"""
    profiles_file = Path("data/processed/team_manager_profiles.json")
    if profiles_file.exists():
        return json.loads(profiles_file.read_text())
    return []

def load_standings_normalized():
    """Load normalized standings"""
    standings_file = Path("data/processed/regular_season_standings_normalized.json")
    if standings_file.exists():
        return json.loads(standings_file.read_text())
    return []

def main():
    profiles = load_team_profiles()
    standings = load_standings_normalized()

    # Build standings map by email + year
    standings_by_email_year = {}
    for record in standings:
        key = (record["manager_email"].lower(), record["year"])
        standings_by_email_year[key] = record

    # Cross-reference each manager
    results = []

    for profile in profiles:
        email = profile["email"].lower()
        manager_name = profile["manager"]
        draft_strategy = profile["draft_strategy"]
        position_dist = profile.get("position_distribution", {})
        acquisition_rate = profile.get("roster_behavior", {}).get("avg_acquisition_rate", 0)
        avg_rank = profile.get("avg_rank", 0)

        # Get playoff/loser records for this manager
        playoff_years = []
        loser_years = []
        playoff_pct = 0

        for year in profile.get("years", []):
            key = (email, year)
            if key in standings_by_email_year:
                record = standings_by_email_year[key]
                if record["made_playoffs"]:
                    playoff_years.append(year)
                else:
                    loser_years.append(year)

        if playoff_years or loser_years:
            total_seasons = len(playoff_years) + len(loser_years)
            playoff_pct = len(playoff_years) / total_seasons * 100 if total_seasons > 0 else 0

        correlation = {
            "manager": manager_name,
            "email": email,
            "total_seasons": len(profile.get("years", [])),
            "playoff_seasons": len(playoff_years),
            "loser_seasons": len(loser_years),
            "playoff_pct": round(playoff_pct, 0),
            "avg_rank": avg_rank,
            "draft_strategy": draft_strategy,
            "position_dist": position_dist,
            "acquisition_rate": acquisition_rate,
            "playoff_years": playoff_years,
            "loser_years": loser_years
        }
        results.append(correlation)

    # Sort by playoff percentage
    results.sort(key=lambda x: x["playoff_pct"], reverse=True)

    # Save
    output_file = Path("data/processed/draft_strategy_playoff_correlation.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(results, indent=2))

    # Print summary
    print("DRAFT STRATEGY vs PLAYOFF SUCCESS CORRELATION")
    print("="*80)
    print()

    for r in results:
        status = "✓ CONSISTENT WINNER" if r["playoff_pct"] >= 80 else \
                 "✓ STRONG PERFORMER" if r["playoff_pct"] >= 60 else \
                 "≈ MIXED" if r["playoff_pct"] >= 40 else \
                 "✗ UNDERPERFORMER"

        print(f"{r['manager']} ({status})")
        print(f"  Playoff rate: {r['playoff_pct']:.0f}% ({r['playoff_seasons']}/{r['total_seasons']} seasons)")
        print(f"  Avg rank: #{r['avg_rank']}")
        print(f"  Draft strategy: {r['draft_strategy']}")
        print(f"  Positions: WR {r['position_dist'].get('WR', 'N/A')}, RB {r['position_dist'].get('RB', 'N/A')}")
        print(f"  Acquisition rate: {r['acquisition_rate']:.0%}")
        if r['playoff_years']:
            print(f"  Playoff years: {r['playoff_years']}")
        if r['loser_years']:
            print(f"  Loser years: {r['loser_years']}")
        print()

    print(f"✓ Correlation saved to {output_file}")

if __name__ == "__main__":
    main()
