#!/usr/bin/env python
"""
peer_labels.py — for each metric, print Philadelphia's exact rank in the
corpus plus the named districts at each end, so you can label a slide with
real peers ("aiming toward X") instead of anonymous dots.

USAGE  (from code\)
    python peer_labels.py --data-dir ..\\data
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import median


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def g(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def nice(sad_id: str) -> str:
    """'32_District-Detroit_Detroit-MI' -> 'District Detroit (Detroit, MI)'."""
    parts = sad_id.split('_')
    if len(parts) >= 3:
        name = parts[1].replace('-', ' ')
        loc = parts[-1]
        if '-' in loc:
            *city, st = loc.split('-')
            loc = ' '.join(city).replace('-', ' ') + ', ' + st
        return f'{name} ({loc})'
    return sad_id.replace('_', ' ')


# label, file, getter, higher_is_better, fmt, plain-language direction word
METRICS = [
    ('Heat-island delta', 'environment/environment_summary.json',
     lambda d: g(d, 'lst_vs_surroundings_c'), False, '{:+.1f} C', 'hottest', 'coolest'),
    ('Daytime jobs', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_inside'), True, '{:,.0f}', 'fewest', 'most'),
    ('Jobs per resident', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_per_resident'), True, '{:.2f}', 'lowest', 'highest'),
    ('Transit trips / day', 'transit_los/transit_los_summary.json',
     lambda d: g(d, 'trips_per_day'), True, '{:,.0f}', 'least', 'most'),
    ('Greenness (NDVI)', 'environment/environment_summary.json',
     lambda d: g(d, 'mean_ndvi'), True, '{:.3f}', 'least green', 'greenest'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sad', default=None)
    args = ap.parse_args()
    data_dir = args.data_dir.resolve()

    sads = sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())
    philly = args.sad or next((s for s in sads if 'phila' in s.lower()), None)
    if not philly:
        raise SystemExit('Philadelphia SAD not found; pass --sad <id>.')

    for label, rel, getter, hib, fmt, low_word, high_word in METRICS:
        pairs = []
        for s in sads:
            doc = load(data_dir / s / 'derived' / rel)
            if doc:
                v = getter(doc)
                if isinstance(v, (int, float)):
                    pairs.append((s, v))
        if len(pairs) < 5:
            continue
        vals = [v for _, v in pairs]
        pval = next((v for s, v in pairs if s == philly), None)
        if pval is None:
            continue

        # rank from the "favourable" end
        ordered = sorted(pairs, key=lambda kv: kv[1], reverse=hib)  # best first
        rank = [s for s, _ in ordered].index(philly) + 1
        n = len(ordered)
        best = ordered[:3]            # the end to aim toward
        med = median(vals)

        print(f'\n{label}')
        print(f'  Philadelphia : {fmt.format(pval)}   '
              f'(#{rank} of {n} from the {high_word if hib else low_word} end; '
              f'median {fmt.format(med)})')
        aim = high_word if hib else low_word  # the good direction in words
        print(f'  aim toward ({aim} in the set):')
        for s, v in best:
            tag = '  <-- best' if s == ordered[0][0] else ''
            print(f'     {fmt.format(v):>10}  {nice(s)}{tag}')


if __name__ == '__main__':
    main()
