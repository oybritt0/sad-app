"""
module_25_economic_prepost.py

The economic "pre vs post development" feature block for a SAD — the second new
band for the Module 8 cross-district vector. Unlike the more-than-human band,
economic indicators are block-aggregate TIME SERIES, not intra-district fields,
so this band is feature-vector-native: it does not feed the morphology field view.

The pivot is a per-SAD development MILESTONE year (anchor venue opening / major
district redevelopment). Every metric is computed as a PRE level, a POST level,
and the change across the milestone, so two districts with identical present-day
economics but opposite trajectories land far apart in the embedding.

WHAT IT MEASURES  (band prefix: eco_)
  From LODES (Module 20 time series — fully grounded, LODES8 2002-2022):
    eco_jobs_pre / eco_jobs_post / eco_jobs_delta_pct
    eco_jobs_cagr_pre / eco_jobs_cagr_post      growth rate before vs after
    eco_office_knowledge_pct_{pre,post,delta}   sector-mix shift
    eco_consumer_serving_pct_{pre,post,delta}
    eco_high_earning_pct_{pre,post,delta}       CE03 (> $3333/mo) share
    eco_young_firm_pct_{pre,post,delta}         CFA01+02 (<=3 yr) share — Innovation signal
    eco_jobs_per_resident_post                  daytime-swing (job center vs bedroom)
  From ACS (Module 4 — requires TWO vintages; optional, flagged if absent):
    eco_median_income_{pre,post}_usd / eco_median_income_delta_pct
    eco_median_home_value_delta_pct / eco_median_gross_rent_delta_pct
    eco_pct_renter_delta / eco_pct_bachelors_delta / eco_population_delta_pct
  Composite (clearly a proxy, not a measurement):
    eco_reinvestment_signal   z-blend of rising rent + home value + bachelors share
                              across the milestone (a gentrification/displacement proxy)

MILESTONE RESOLUTION (in priority order)
  1. --milestone-year on the command line
  2. district_profile.json: any of anchor_open_year / development_year /
     milestone_year / opened_year
  3. else: abort with a clear message (a pre/post split needs a pivot)

INPUTS
  derived/<sad>/jobs/jobs_timeseries.json     (run: module_20 ... --timeseries)
  derived/<sad>/district_profile.json         (milestone, optional)
  derived/<sad>/census_summary.json           (single ACS vintage, optional)
  derived/<sad>/census_summary_pre.json
  derived/<sad>/census_summary_post.json      (two ACS vintages, optional)

OUTPUT
  derived/<sad>/economic/economic_prepost_summary.json   (the M8 feed)

USAGE
  python module_25_economic_prepost.py ^
      --derived ..\\data\\32_District-Detroit_Detroit-MI\\derived ^
      --milestone-year 2017 --window 3
  # 2017 = Little Caesars Arena opening; window=3 -> pre 2014-2016, post 2018-2020.

HONEST CAVEATS
  - LODES deltas are real and apples-to-apples (constant 2020-block geography).
  - ACS pre/post is only as good as the two vintages you pull. ACS block-group
    boundaries shift between decennial frames; a 2013 vs 2023 comparison crosses
    the 2020 boundary change, so treat ACS deltas as directional, and the flags
    will say so. Pull both vintages with Module 4 into the _pre/_post files.
  - The reinvestment_signal is a composite proxy; it is computed only when its
    ACS inputs exist, and it is never presented as a measurement of displacement.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest


MILESTONE_KEYS = ('anchor_open_year', 'development_year', 'milestone_year',
                  'opened_year', 'anchor_year')


# ─── helpers ──────────────────────────────────────────────────────────────

def _cagr(v0, v1, years):
    if v0 in (None, 0) or v1 is None or years <= 0:
        return None
    try:
        return round(100 * ((v1 / v0) ** (1.0 / years) - 1.0), 2)
    except Exception:                              # noqa: BLE001
        return None


def _delta_pct(pre, post):
    if pre in (None, 0) or post is None:
        return None
    return round(100 * (post - pre) / pre, 1)


def _window_mean(series, key, lo, hi):
    vals = [r[key] for r in series
            if r.get('year') is not None and lo <= r['year'] <= hi
            and r.get(key) is not None]
    return float(np.mean(vals)) if vals else None


def resolve_milestone(derived_dir, cli_year):
    if cli_year:
        return int(cli_year), 'cli'
    prof = derived_dir / 'district_profile.json'
    if prof.exists():
        try:
            p = json.loads(prof.read_text())
            for k in MILESTONE_KEYS:
                if p.get(k):
                    return int(p[k]), f'district_profile.{k}'
        except Exception:                          # noqa: BLE001
            pass
    return None, None


# ─── LODES pre/post ──────────────────────────────────────────────────────

def lodes_prepost(derived_dir, milestone, window, flags):
    ts_path = derived_dir / 'jobs' / 'jobs_timeseries.json'
    if not ts_path.exists():
        flags.append('lodes_timeseries_missing')
        return {}
    ts = json.loads(ts_path.read_text())
    series = ts.get('series', [])
    if not series:
        flags.append('lodes_series_empty')
        return {}
    years = [r['year'] for r in series if r.get('year') is not None]
    lo_pre, hi_pre = milestone - window, milestone - 1
    lo_post, hi_post = milestone + 1, milestone + window
    if min(years) > hi_pre:
        flags.append('lodes_pre_window_unavailable')
    if max(years) < lo_post:
        flags.append('lodes_post_window_unavailable')

    out = {}
    pairs = [
        ('jobs', 'jobs_inside'),
        ('office_knowledge_pct', 'office_knowledge_pct'),
        ('consumer_serving_pct', 'consumer_serving_pct'),
        ('high_earning_pct', 'pct_jobs_high_earning'),
        ('young_firm_pct', 'pct_jobs_young_firms'),
    ]
    for label, key in pairs:
        pre = _window_mean(series, key, lo_pre, hi_pre)
        post = _window_mean(series, key, lo_post, hi_post)
        out[f'eco_{label}_pre'] = round(pre, 1) if pre is not None else None
        out[f'eco_{label}_post'] = round(post, 1) if post is not None else None
        out[f'eco_{label}_delta'] = (_delta_pct(pre, post) if label == 'jobs'
                                     else (round(post - pre, 1)
                                           if pre is not None and post is not None
                                           else None))
    # growth rate before vs after, bracketed within each window
    jp = [(r['year'], r['jobs_inside']) for r in series
          if r.get('jobs_inside') is not None]
    pre_pts = [(y, v) for y, v in jp if lo_pre <= y <= hi_pre]
    post_pts = [(y, v) for y, v in jp if lo_post <= y <= hi_post]
    if len(pre_pts) >= 2:
        out['eco_jobs_cagr_pre'] = _cagr(pre_pts[0][1], pre_pts[-1][1],
                                         pre_pts[-1][0] - pre_pts[0][0])
    if len(post_pts) >= 2:
        out['eco_jobs_cagr_post'] = _cagr(post_pts[0][1], post_pts[-1][1],
                                          post_pts[-1][0] - post_pts[0][0])

    # latest jobs-per-resident from the single-year jobs_summary if present
    js = derived_dir / 'jobs' / 'jobs_summary.json'
    if js.exists():
        try:
            out['eco_jobs_per_resident_post'] = json.loads(
                js.read_text()).get('jobs_per_resident')
        except Exception:                          # noqa: BLE001
            pass
    return out


# ─── ACS pre/post (two vintages) ──────────────────────────────────────────

def acs_prepost(derived_dir, flags):
    pre_p = derived_dir / 'census_summary_pre.json'
    post_p = derived_dir / 'census_summary_post.json'
    if not (pre_p.exists() and post_p.exists()):
        flags.append('acs_two_vintages_absent')
        return {}, None
    try:
        pre = json.loads(pre_p.read_text())
        post = json.loads(post_p.read_text())
    except Exception:                              # noqa: BLE001
        flags.append('acs_parse_failed')
        return {}, None
    flags.append('acs_boundary_caveat')            # block-group frames may differ

    def g(d, k):
        return d.get(k)

    out = {
        'eco_median_income_pre_usd': g(pre, 'median_household_income_pop_weighted'),
        'eco_median_income_post_usd': g(post, 'median_household_income_pop_weighted'),
        'eco_median_income_delta_pct': _delta_pct(
            g(pre, 'median_household_income_pop_weighted'),
            g(post, 'median_household_income_pop_weighted')),
        'eco_median_home_value_delta_pct': _delta_pct(
            g(pre, 'median_home_value_pop_weighted'),
            g(post, 'median_home_value_pop_weighted')),
        'eco_median_gross_rent_delta_pct': _delta_pct(
            g(pre, 'median_gross_rent_pop_weighted'),
            g(post, 'median_gross_rent_pop_weighted')),
        'eco_pct_renter_delta': (
            round(g(post, 'pct_renter_occupied') - g(pre, 'pct_renter_occupied'), 1)
            if g(pre, 'pct_renter_occupied') is not None
            and g(post, 'pct_renter_occupied') is not None else None),
        'eco_pct_bachelors_delta': (
            round(g(post, 'pct_bachelors_or_higher') - g(pre, 'pct_bachelors_or_higher'), 1)
            if g(pre, 'pct_bachelors_or_higher') is not None
            and g(post, 'pct_bachelors_or_higher') is not None else None),
        'eco_population_delta_pct': _delta_pct(
            g(pre, 'estimated_population'), g(post, 'estimated_population')),
    }
    return out, (pre, post)


def reinvestment_signal(acs_out, flags):
    """Directional composite of rent + home value + bachelors-share change.
    A proxy for reinvestment/displacement pressure — never a measurement."""
    parts = [acs_out.get('eco_median_gross_rent_delta_pct'),
             acs_out.get('eco_median_home_value_delta_pct'),
             acs_out.get('eco_pct_bachelors_delta')]
    parts = [p for p in parts if p is not None]
    if len(parts) < 2:
        return None
    # simple sign-preserving mean of standardized-ish components (percent units)
    return round(float(np.mean(parts)), 2)


# ─── Orchestration ────────────────────────────────────────────────────────

def process_sad(derived_dir: Path, source_dir: Path, cli_year, window):
    manifest = None
    # Module 1 writes the manifest into derived/ (canonical pipeline convention).
    mpath = derived_dir / 'manifest.json'
    if mpath.exists():
        manifest = Manifest.model_validate_json(mpath.read_text())
    sad_id = manifest.sad_id if manifest else derived_dir.name
    sad_name = manifest.sad_name if manifest else sad_id

    milestone, msrc = resolve_milestone(derived_dir, cli_year)
    if milestone is None:
        raise SystemExit(
            "No development milestone found. A pre/post split needs a pivot year.\n"
            "  -> pass --milestone-year YYYY, or add one of "
            f"{MILESTONE_KEYS} to district_profile.json")

    print(f"Economic pre/post profile for {sad_id}  "
          f"(milestone {milestone} via {msrc}, +/-{window} yr windows)...")
    flags: list[str] = []
    feats: dict = {}

    feats.update(lodes_prepost(derived_dir, milestone, window, flags))
    acs_out, _ = acs_prepost(derived_dir, flags)
    feats.update(acs_out)
    rs = reinvestment_signal(acs_out, flags)
    feats['eco_reinvestment_signal'] = rs

    summary = {
        'sad_id': sad_id, 'sad_name': sad_name, 'band': 'economic_prepost',
        'milestone_year': milestone, 'milestone_source': msrc,
        'window_years': window,
        'pre_window': [milestone - window, milestone - 1],
        'post_window': [milestone + 1, milestone + window],
        'sources': 'LEHD LODES8 (jobs); ACS 5-yr (demographics, two vintages)',
        'features': feats,
        'flags': sorted(set(flags)),
        'feature_count': len([v for v in feats.values() if isinstance(v, (int, float))]),
    }
    out_dir = derived_dir / 'economic'
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / 'economic_prepost_summary.json'
    out.write_text(json.dumps(summary, indent=2, default=str))

    print(f"\n[OK] {sad_id}")
    for k, v in feats.items():
        print(f"  {k:34s} {v}")
    if summary['flags']:
        print(f"\n  FLAGS: {', '.join(summary['flags'])}")
    print(f"\n  wrote {out}")
    return out


def main():
    p = argparse.ArgumentParser(description="Economic pre/post-development profile for a SAD")
    p.add_argument('--derived', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--milestone-year', type=int, default=None,
                   help='Development pivot year; else read from district_profile.json')
    p.add_argument('--window', type=int, default=3,
                   help='Years on each side of the milestone for pre/post means (default 3)')
    args = p.parse_args()
    process_sad(args.derived, args.source, args.milestone_year, args.window)


if __name__ == '__main__':
    main()
