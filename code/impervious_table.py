#!/usr/bin/env python
"""
impervious_table.py — every district's impervious / built-up surface %, ranked
most-paved first, with Philadelphia flagged. Prints a table and writes a CSV.

Reads built_up_pct from each derived/environment/environment_summary.json (M22).
Districts without M22 are listed separately as "not measured".

USAGE  (from code\\)
    python impervious_table.py --data-dir ..\\data
    python impervious_table.py --data-dir ..\\data --cohort original_37.txt
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
from statistics import median


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def nice(sad_id):
    parts = sad_id.split('_')
    if len(parts) >= 3:
        name = parts[1].replace('-', ' ')
        loc = parts[-1]
        if '-' in loc:
            *city, st = loc.split('-')
            loc = ' '.join(city).replace('-', ' ') + ', ' + st
        return f'{name} ({loc})'
    return sad_id.replace('_', ' ')


def read_cohort(path):
    if not path:
        return None
    ids = set()
    for line in Path(path).read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            ids.add(line)
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--cohort', type=Path, default=None)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    sads = sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())
    cohort = read_cohort(args.cohort)

    rows, missing = [], []
    for s in sads:
        if cohort is not None and s not in cohort:
            continue
        env = load(data_dir / s / 'derived' / 'environment' / 'environment_summary.json')
        v = (env or {}).get('built_up_pct')
        if isinstance(v, (int, float)):
            rows.append((s, nice(s), float(v)))
        else:
            missing.append(s)

    if not rows:
        raise SystemExit('No built_up_pct found. Run M22 across the corpus first.')

    rows.sort(key=lambda r: r[2], reverse=True)        # most paved first
    vals = [r[2] for r in rows]
    med = median(vals)

    print(f'\nImpervious / built-up surface %  ({len(rows)} measured districts)')
    print(f'corpus median: {med:.0f}%\n')
    print(f"{'RANK':>4}  {'BUILT-UP':>8}  DISTRICT")
    print('-' * 64)
    for i, (sid, name, v) in enumerate(rows, 1):
        tag = '  <-- Philadelphia' if 'phila' in sid.lower() else ''
        print(f'{i:>4}  {v:>7.0f}%  {name}{tag}')
    if missing:
        print(f'\nnot measured (no M22 yet): {len(missing)}')
        for s in missing[:8]:
            print(f'    {nice(s)}')
        if len(missing) > 8:
            print(f'    ... and {len(missing)-8} more')

    out_dir = (args.out or data_dir / '_eagles_figs').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / 'impervious_by_district.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['rank', 'sad_id', 'district', 'built_up_pct', 'is_philadelphia'])
        for i, (sid, name, v) in enumerate(rows, 1):
            w.writerow([i, sid, name, f'{v:.1f}', 'phila' in sid.lower()])
    print(f'\nwrote {csv_path}')


if __name__ == '__main__':
    main()
