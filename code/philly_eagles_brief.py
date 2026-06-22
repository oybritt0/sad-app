#!/usr/bin/env python
"""
philly_eagles_brief.py — pull the owner/developer-facing numbers for the
South Philadelphia Sports Complex and rank them against the full SAD corpus.

This does NOT invent anything. It reads the JSON outputs the pipeline already
wrote under each <sad>/derived/ and prints, for a curated set of metrics that
a team owner or developer actually cares about:
    - Philadelphia's value
    - the corpus median
    - Philadelphia's percentile rank (where it sits among all districts)
    - the file path of the image export to drop on the slide

Metrics that depend on M20 (jobs) / M22 (environment) will show "(missing —
run module)" until you've run those for Philadelphia. Run
    python batch_run_m20_22.py --data-dir ..\\data --sads <philly_id>
first, then re-run this.

USAGE  (from the code\ directory)
    python philly_eagles_brief.py --data-dir ..\\data
    python philly_eagles_brief.py --data-dir ..\\data --sad <exact_philly_id>
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import median


def load(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def find_philly(data_dir: Path, explicit: str | None) -> str | None:
    sads = sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())
    if explicit:
        return explicit if explicit in sads else None
    # match on name fragments
    for s in sads:
        low = s.lower()
        if 'philadelphia' in low or 'south-phil' in low or 'phila' in low:
            return s
    return None


# ─── Metric registry ─────────────────────────────────────────────────────────
# Each entry: (label, file under derived/, getter(dict)->float|None,
#              higher_is_better_for_pitch, fmt)
# higher_is_better is used only to phrase the percentile sensibly.

def g(d, *keys):
    """Nested .get chain returning None on any miss."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


METRICS = [
    ('Daytime jobs inside district', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_inside'), True, '{:,.0f}'),
    ('Jobs per resident (daytime swing)', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_per_resident'), True, '{:.2f}'),
    ('Residential program share %', 'program_summary.json',
     lambda d: (g(d, 'rossetti_percentages', 'residential') or 0), True, '{:.1f}%'),
    ('Retail / F&B program share %', 'program_summary.json',
     lambda d: (g(d, 'rossetti_percentages', 'retail_food_entertainment') or 0), True, '{:.1f}%'),
    ('Parking program share %', 'program_summary.json',
     lambda d: (g(d, 'rossetti_percentages', 'parking') or 0), False, '{:.1f}%'),
    ('Open space program share %', 'program_summary.json',
     lambda d: (g(d, 'rossetti_percentages', 'open_space') or 0), True, '{:.1f}%'),
    ('Heat-island delta vs surroundings (C)', 'environment/environment_summary.json',
     lambda d: g(d, 'lst_vs_surroundings_c'), False, '{:+.1f}'),
    ('Greenness (mean NDVI)', 'environment/environment_summary.json',
     lambda d: g(d, 'mean_ndvi'), True, '{:.3f}'),
    ('Impervious / built-up surface %', 'environment/environment_summary.json',
     lambda d: (g(d, 'impervious_pct') if g(d, 'impervious_pct') is not None
                else g(d, 'built_up')), False, '{:.0f}%'),
    ('Transit trips/day serving district', 'transit_los/transit_los_summary.json',
     lambda d: g(d, 'trips_per_day'), True, '{:,.0f}'),
    ('Built coverage (fabric density)', 'cv_metrics.json',
     lambda d: g(d, 'field', 'coverage'), True, '{:.2f}'),
]

# Image exports worth dropping on a slide, in priority order for an owner pitch.
EXPORTS = [
    ('jobs/jobs_density.png',
     'M20 daytime jobs density — answers "what happens here outside event days?"'),
    ('environment/heat_island.png',
     'M22 heat-island map — the sea-of-parking thermal penalty, design-actionable'),
    ('place_cards/{sad}_place_card.png',
     'M10 place card — one-glance program donut + strengths/opportunities'),
    ('walkshed/walkshed.png',
     'M15 5/10/15-min walkshed — pedestrian reach from the complex'),
    ('transit_los/transit_los.png',
     'M21 transit level-of-service — how often you can actually get here'),
]


def pct_rank(value, all_values, higher_is_better) -> float:
    """Percentile of `value` within all_values (0-100)."""
    vals = [v for v in all_values if v is not None]
    if not vals or value is None:
        return float('nan')
    below = sum(1 for v in vals if v < value)
    pr = 100.0 * below / len(vals)
    return pr if higher_is_better else 100.0 - pr


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sad', type=str, default=None,
                    help='Exact Philadelphia SAD id (else auto-detected)')
    args = ap.parse_args()
    data_dir = args.data_dir.resolve()

    philly = find_philly(data_dir, args.sad)
    if not philly:
        raise SystemExit("Could not find a Philadelphia SAD under --data-dir. "
                         "Pass --sad <exact_id>.")
    all_sads = sorted(c.name for c in data_dir.iterdir()
                      if c.is_dir() and (c / 'source').is_dir())
    print(f"Philadelphia SAD : {philly}")
    print(f"Corpus size      : {len(all_sads)} districts\n")

    # Pre-load each metric file once per SAD
    cache: dict[tuple[str, str], dict | None] = {}

    def get_file(sad, rel):
        key = (sad, rel)
        if key not in cache:
            cache[key] = load(data_dir / sad / 'derived' / rel)
        return cache[key]

    print(f"{'METRIC':<42}{'PHILLY':>12}{'MEDIAN':>12}{'RANK':>14}")
    print('-' * 80)
    for label, rel, getter, hib, fmt in METRICS:
        philly_doc = get_file(philly, rel)
        philly_val = getter(philly_doc) if philly_doc else None

        corpus_vals = []
        for s in all_sads:
            doc = get_file(s, rel)
            if doc:
                try:
                    corpus_vals.append(getter(doc))
                except Exception:
                    corpus_vals.append(None)
        clean = [v for v in corpus_vals if isinstance(v, (int, float))]

        if philly_val is None:
            print(f"{label:<42}{'(missing — run module)':>38}")
            continue
        med = median(clean) if clean else float('nan')
        pr = pct_rank(philly_val, corpus_vals, hib)
        try:
            pv, mv = fmt.format(philly_val), fmt.format(med)
        except Exception:
            pv, mv = str(philly_val), str(med)
        rank_str = f"{pr:.0f}th pctile" if pr == pr else "n/a"
        print(f"{label:<42}{pv:>12}{mv:>12}{rank_str:>14}")

    print('\nRecommended exports to attach (in priority order):')
    for rel, why in EXPORTS:
        path = data_dir / philly / 'derived' / rel.format(sad=philly)
        flag = 'OK ' if path.exists() else '-- '
        print(f"  [{flag}] {why}")
        print(f"        {path}")


if __name__ == '__main__':
    main()
