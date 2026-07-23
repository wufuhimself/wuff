#!/usr/bin/env python3
"""Fix keeper export CSVs by replacing ranking values with correct ADP values."""

import csv
import json
from pathlib import Path

# Load correct ADP from the combined ADP data
adp_file = Path(__file__).parent.parent / 'data' / 'raw' / 'adp' / 'adp_combined.json'
if not adp_file.exists():
    print(f"Error: {adp_file} not found")
    exit(1)

adp_data = json.loads(adp_file.read_text())
adp_map = {entry['playerName'].lower(): entry['adp'] for entry in adp_data}

# Process all keeper export CSVs
exports_dir = Path(__file__).parent.parent / 'data' / 'processed' / 'keeper_exports'

fixed_count = 0
for csv_file in sorted(exports_dir.glob('keepers_*.csv')):
    # Only process files that have K1_ADP and K2_ADP columns (not the new format)
    with open(csv_file, 'r') as f:
        header = f.readline().strip()
        if 'K1_ADP' not in header:
            continue

    rows = []
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Look up correct ADP for each keeper
            if 'Keeper1' in row:
                k1_name = row['Keeper1'].strip().lower()
                if k1_name in adp_map:
                    row['K1_ADP'] = adp_map[k1_name]

            if 'Keeper2' in row:
                k2_name = row['Keeper2'].strip().lower()
                if k2_name in adp_map:
                    row['K2_ADP'] = adp_map[k2_name]

            rows.append(row)

    # Write back with corrected ADPs
    with open(csv_file, 'w', newline='') as f:
        if rows:
            fieldnames = list(rows[0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            print(f"✓ Fixed {csv_file.name}")
            fixed_count += 1

print(f"\nFixed {fixed_count} keeper export files")
