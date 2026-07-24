#!/usr/bin/env python3
"""
Summarize draft tendency patterns and infer strategy types.
Generates human-readable summary of each manager's draft profile.
"""
import json
from pathlib import Path
from typing import Dict

def summarize_patterns():
    data_file = Path("data/processed/draft_tendencies_by_manager.json")
    if not data_file.exists():
        print("Run draft_tendency_mapper.py first")
        return

    data = json.loads(data_file.read_text())

    # Known positions to help with unknown classification
    known_qbs = {
        "Josh Allen", "Lamar Jackson", "Patrick Mahomes", "Jalen Hurts",
        "Kyler Murray", "Joe Burrow", "Travis Kelce", "Mark Andrews",
        "Brock Bowers", "Matthew Stafford", "Jared Goff", "Trevor Lawrence"
    }

    patterns = []

    for manager in data:
        name = manager["manager_name"]
        pos_dist = manager.get("position_summary", {})
        rd_pref = manager.get("round_preferences", {})

        # Extract main position tendencies
        wr_count = pos_dist.get("WR", 0)
        rb_count = pos_dist.get("RB", 0)
        qb_count = pos_dist.get("QB", 0)
        te_count = pos_dist.get("TE", 0)
        total = manager.get("total_picks", 0)

        # Infer primary strategy
        strategy = []

        # Early round tendencies (R1-3)
        early_rounds = {1, 2, 3}
        early_pos = {}
        for r in early_rounds:
            if str(r) in rd_pref:
                pos = rd_pref[str(r)]["primary"]
                early_pos[pos] = early_pos.get(pos, 0) + 1

        if early_pos.get("WR", 0) >= 2:
            strategy.append("WR-first")
        if early_pos.get("RB", 0) >= 2:
            strategy.append("RB-first")
        if early_pos.get("QB", 0) >= 2:
            strategy.append("QB-reach")

        # Position skew
        if wr_count > rb_count + 2:
            strategy.append("WR-heavy")
        elif rb_count > wr_count + 2:
            strategy.append("RB-heavy")

        # Late QB tendency
        late_rounds = {4, 5}
        late_qbs = sum(1 for r in late_rounds if str(r) in rd_pref and rd_pref[str(r)]["primary"] == "QB")
        if late_qbs >= 1:
            strategy.append("late-QB")

        pattern = {
            "manager": name,
            "total_picks": total,
            "position_distribution": {
                "WR": f"{wr_count} ({wr_count*100//total}%)",
                "RB": f"{rb_count} ({rb_count*100//total}%)",
                "QB": f"{qb_count} ({qb_count*100//total}%)",
                "TE": f"{te_count} ({te_count*100//total}%)"
            },
            "strategy": " + ".join(strategy) if strategy else "Balanced",
            "early_rounds": early_pos,
            "draft_history": manager.get("draft_history", [])[:1]  # Latest season
        }
        patterns.append(pattern)

    # Save summary
    output = Path("data/processed/draft_pattern_summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(patterns, indent=2))

    # Print readable summary
    print("DRAFT PATTERN SUMMARY BY MANAGER")
    print("="*80)
    for p in patterns:
        print(f"\n{p['manager']}")
        print(f"  Total picks: {p['total_picks']}")
        print(f"  Positions: {p['position_distribution']}")
        print(f"  Strategy: {p['strategy']}")
        if p["early_rounds"]:
            print(f"  Early round preferences (R1-3): {p['early_rounds']}")

    print(f"\n✓ Summary saved to {output}")

if __name__ == "__main__":
    summarize_patterns()
