"""
module_04b_metro_context.py

Pulls American Community Survey (ACS) demographics for the ENTIRE
metropolitan area (CBSA) that a SAD sits inside, then positions the SAD
against that metro envelope.

WHY THIS EXISTS (and why it's a separate module)
  Module 4 characterizes the immediate demographic context of a district:
  the block groups touching the SAD canvas, tagged interior/boundary/
  exterior. That answers "who lives right here." It deliberately stays
  small because Module 8 consumes its SAD-INTERIOR demographics as features.

  This module answers a different question: "what kind of place is this,
  relative to its whole region?" It pulls every block group in the SAD's
  Metropolitan Statistical Area (CBSA) and reports, for each headline
  metric, both the metro-wide figure and the SAD's PERCENTILE RANK within
  the metro (e.g. "median income here is in the 78th percentile of the
  metro"). That percentile is the "better sense of the place" — it tells a
  planner whether the district is rich/poor, dense/sparse, young/old
  RELATIVE TO ITS OWN REGION, which a raw number can't.

  It is intentionally additive: it never touches Module 4's outputs, so
  Module 8's feature pipeline is unaffected.

DESIGN DECISIONS
  - "Metropolitan area" = the CBSA (Core-Based Statistical Area) whose
    polygon contains the SAD centroid. CBSAs are built from whole counties,
    so the county set is exact. Metro and micro areas are both supported;
    if the SAD is outside any CBSA, we fall back to its single county.
  - Block-group granularity for the metro too (not tracts), so the SAD view
    (Module 4) and the metro view share one comparison currency and the
    percentile ranks are computed against the same geography the SAD is
    aggregated from.
  - ACS variable list, parsing, null-sentinel handling, and the
    area-weighted summary all come from module_04_census_pull — imported,
    not duplicated, so the two stay in sync.

PIPELINE
  1. Read manifest (bbox_geo, sad_id, sad_name) + sad_boundary for centroid
  2. Find the CBSA containing the SAD centroid; resolve its counties
  3. Pull block groups for those counties (whole metro)
  4. Pull ACS for them (reuses Module 4's fetcher)
  5. Compute a metro-wide area-weighted summary (every BG fully counts)
  6. Read the SAD's own census_summary.json (from Module 4) as the baseline
  7. For each headline metric: metro value, SAD value, ratio, and the SAD's
     population-weighted percentile rank within the metro BG distribution
  8. Save the metro spatial layer + census_metro_summary.json

USAGE
  python module_04b_metro_context.py ^
      --derived ..\\data\\derived\\per_sad\\district_detroit ^
      --source  ..\\data\\source\\per_sad\\district_detroit

  (Run module_04_census_pull.py first — this module reads its
   census_summary.json to know the SAD-blocks baseline.)

OUTPUTS
  source/<sad>/census_metro_blockgroups.gpkg  Metro BG polygons + ACS cols
  derived/<sad>/census_metro_summary.json     Metro summary + SAD-vs-metro
                                              positioning (the comparison)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import box as shp_box

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest

# Reuse Module 4 verbatim — same ACS variables, same parsing, same summary
# math. Importing (rather than copying) keeps the two modules in lockstep.
from module_04_census_pull import (
    ACS_VARS,
    DEFAULT_ACS_YEAR,
    fetch_acs_for_block_groups,
    compute_summary,
)

# Headline metrics that are meaningful to rank a SAD against its metro.
# Two flavors:
#   'level'  : per-block-group continuous values we can rank directly
#   'derived': SAD-vs-metro figures we report as a ratio only (no per-BG rank)
# For 'level' metrics we also need the per-BG column to build the metro
# distribution; for the SAD value we read the matching key from the SAD's
# census_summary.json (Module 4's output).
RANKABLE_METRICS = [
    # summary_key (Module 4)                  per_bg_col            label
    ('median_household_income_pop_weighted', 'median_household_income', 'Median household income'),
    ('median_age_pop_weighted',              'median_age',              'Median age'),
    ('median_home_value_pop_weighted',       'median_home_value',       'Median home value'),
    ('median_gross_rent_pop_weighted',       'median_gross_rent',       'Median gross rent'),
    ('pop_density_per_km2',                  'pop_density_per_km2',     'Population density'),
]

# Composition metrics: reported as SAD-vs-metro figures (both are already
# percentages in the summaries), with the SAD ranked against the per-BG
# percentage distribution where the inputs exist.
COMPOSITION_METRICS = [
    ('pct_renter_occupied',     'Renter-occupied housing'),
    ('pct_bachelors_or_higher', "Bachelor's degree or higher"),
    ('pct_white',               'White'),
    ('pct_black',               'Black'),
    ('pct_asian',               'Asian'),
    ('pct_hispanic',            'Hispanic'),
    ('unemployment_rate',       'Unemployment rate'),
]


# ─── CBSA (metro) resolution ─────────────────────────────────────────────────

def resolve_sad_centroid(manifest, source_dir: Path) -> tuple[float, float]:
    """Centroid of the SAD polygon if available, else the canvas bbox center."""
    sad_boundary_path = source_dir / 'sad_boundary.geojson'
    if sad_boundary_path.exists():
        gdf = gpd.read_file(sad_boundary_path).to_crs("EPSG:4326")
        try:
            poly = gdf.union_all()
        except AttributeError:
            poly = gdf.unary_union
        c = poly.centroid
        return float(c.x), float(c.y)
    minlon, minlat, maxlon, maxlat = manifest.bbox_geo
    return (minlon + maxlon) / 2.0, (minlat + maxlat) / 2.0


def _retry(fn, *, what: str, tries: int = 4, base_delay: float = 6.0):
    """
    Run a pygris/TIGER download with retries and exponential backoff.

    The Census TIGER servers (www2.census.gov / ftp2.census.gov) throttle and
    return transient failures under sustained load. A short backoff almost
    always clears it. Combined with cache=True (so each file downloads once and
    persists across SADs), this keeps a 30-SAD batch from tripping the limiter.
    """
    import time
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:  # pygris raises ValueError on HTTP+FTP failure
            last = e
            if attempt < tries:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"  [{what}] download failed (attempt {attempt}/{tries}); "
                      f"retrying in {delay:.0f}s...")
                time.sleep(delay)
    raise last


def find_cbsa_counties(
    centroid_lonlat: tuple[float, float],
    year: int,
) -> tuple[gpd.GeoDataFrame, dict]:
    """
    Find the CBSA whose polygon contains the SAD centroid, then return the
    counties that make it up plus a small descriptor dict.

    Falls back to the single containing county if the point is outside every
    CBSA (rural / non-metro SADs).
    """
    try:
        import pygris
    except ImportError:
        sys.exit("Module 4b requires pygris. Install with: pip install pygris")

    from shapely.geometry import Point
    pt = Point(*centroid_lonlat)

    # cache=True writes these national files to pygris's on-disk cache so they
    # download ONCE and are reused by every subsequent SAD (across processes),
    # instead of being re-fetched ~30 times. This is the main fix for the
    # TIGER throttling that killed the first batch run.
    cbsas = _retry(
        lambda: pygris.core_based_statistical_areas(year=year, cb=True, cache=True),
        what="CBSA").to_crs("EPSG:4326")
    hit = cbsas[cbsas.contains(pt)]

    counties = _retry(
        lambda: pygris.counties(year=year, cb=True, cache=True),
        what="counties").to_crs("EPSG:4326")

    if len(hit) == 0:
        # Non-metro fallback: just the county the point sits in.
        county_hit = counties[counties.contains(pt)]
        if len(county_hit) == 0:
            county_hit = counties[counties.intersects(pt.buffer(0.01))]
        if len(county_hit) == 0:
            raise ValueError(
                f"SAD centroid {centroid_lonlat} is not inside any CBSA or county."
            )
        descriptor = {
            'metro_kind': 'county_fallback',
            'cbsa_code': None,
            'cbsa_name': f"{county_hit.iloc[0].get('NAME', 'county')} (no CBSA)",
        }
        print(f"  SAD is not inside any CBSA — falling back to single county: "
              f"{descriptor['cbsa_name']}")
        return county_hit.copy(), descriptor

    cbsa_row = hit.iloc[0]
    cbsa_poly = cbsa_row.geometry
    # LSAD M1 = Metropolitan Statistical Area, M2 = Micropolitan
    lsad = cbsa_row.get('LSAD', '')
    metro_kind = 'metropolitan' if str(lsad) == 'M1' else (
        'micropolitan' if str(lsad) == 'M2' else 'cbsa')

    # CBSAs are whole counties: a county belongs if its representative point
    # falls inside the CBSA polygon. (representative_point is guaranteed to
    # lie inside the geometry, unlike centroid for concave shapes.)
    rep = counties.copy()
    rep['_rep'] = rep.geometry.representative_point()
    member_mask = rep['_rep'].apply(lambda p: cbsa_poly.contains(p))
    member_counties = counties[member_mask.values].copy()

    if len(member_counties) == 0:
        # Geometry edge case — fall back to intersection.
        member_counties = counties[counties.intersects(cbsa_poly)].copy()

    descriptor = {
        'metro_kind': metro_kind,
        'cbsa_code': str(cbsa_row.get('CBSAFP', cbsa_row.get('GEOID', ''))),
        'cbsa_name': str(cbsa_row.get('NAME', 'CBSA')),
    }
    print(f"  metro: {descriptor['cbsa_name']} ({metro_kind}), "
          f"{len(member_counties)} counties: "
          f"{', '.join(member_counties['NAME'].tolist())}")
    return member_counties, descriptor


def fetch_metro_block_groups(
    member_counties: gpd.GeoDataFrame,
    year: int,
) -> gpd.GeoDataFrame:
    """
    Pull block-group geometries for the metro, downloading once per STATE and
    filtering to the metro's counties.

    The old approach called block_groups(state, county) once per county, which
    made pygris re-download the whole state file for every county — e.g. 11
    downloads of the Texas file for an 11-county metro. Pulling per unique
    state (with cache=True) cuts that to one download per state, reused across
    every SAD in that state.
    """
    try:
        import pygris
    except ImportError:
        sys.exit("Module 4b requires pygris. Install with: pip install pygris")

    # (STATEFP, COUNTYFP) pairs that define this metro. We filter on the pair,
    # not the county NAME, because metros can include same-named counties in
    # different states (e.g. Lake County IL + Lake County IN in Chicago, or
    # St. Louis city + St. Louis County in the St. Louis metro).
    member_fips = set(
        zip(member_counties['STATEFP'].astype(str),
            member_counties['COUNTYFP'].astype(str))
    )
    states = sorted(member_counties['STATEFP'].astype(str).unique())

    frames = []
    for st in states:
        bgs_state = _retry(
            lambda st=st: pygris.block_groups(state=st, year=year, cb=True, cache=True),
            what=f"block groups (state {st})")
        frames.append(bgs_state)

    bgs = pd.concat(frames, ignore_index=True)
    bgs = gpd.GeoDataFrame(bgs, geometry='geometry', crs=frames[0].crs)

    # Keep only block groups in the metro's counties.
    pair = list(zip(bgs['STATEFP'].astype(str), bgs['COUNTYFP'].astype(str)))
    bgs = bgs[[p in member_fips for p in pair]].copy()

    bgs = bgs.to_crs("EPSG:4326")
    print(f"  -> {len(bgs)} block groups across the metro")
    return bgs


# ─── SAD-vs-metro positioning ────────────────────────────────────────────────

def weighted_percentile_rank(
    value: float,
    distribution: np.ndarray,
    weights: np.ndarray,
) -> float | None:
    """
    Percentile rank (0-100) of `value` within a population-weighted
    distribution. "78.0" means ~78% of the metro's population lives in block
    groups with a value at or below the SAD's value. Population weighting
    keeps a sparse outlying block group from skewing the rank.
    """
    if value is None or not np.isfinite(value):
        return None
    mask = np.isfinite(distribution) & np.isfinite(weights) & (weights > 0)
    if mask.sum() == 0:
        return None
    d = distribution[mask]
    w = weights[mask]
    below = w[d <= value].sum()
    total = w.sum()
    if total <= 0:
        return None
    return round(100.0 * below / total, 1)


def add_pop_density(bgs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add a per-block-group population density (people / km^2)."""
    metric = bgs.to_crs(bgs.estimate_utm_crs())
    area_km2 = metric.geometry.area / 1e6
    pop = bgs['total_pop'].fillna(0.0) if 'total_pop' in bgs.columns else 0.0
    bgs = bgs.copy()
    bgs['pop_density_per_km2'] = np.where(area_km2 > 0, pop / area_km2, np.nan)
    return bgs


def build_positioning(
    metro_bgs: gpd.GeoDataFrame,
    sad_summary: dict,
    metro_summary: dict,
) -> list[dict]:
    """
    For each headline metric, report the metro figure, the SAD figure, their
    ratio, and the SAD's population-weighted percentile rank in the metro.
    """
    pop = (metro_bgs['total_pop'].fillna(0.0).values
           if 'total_pop' in metro_bgs.columns
           else np.ones(len(metro_bgs)))
    rows = []

    for summary_key, bg_col, label in RANKABLE_METRICS:
        sad_val = sad_summary.get(summary_key)
        metro_val = metro_summary.get(summary_key)
        rank = None
        if bg_col in metro_bgs.columns and sad_val is not None:
            rank = weighted_percentile_rank(
                float(sad_val), metro_bgs[bg_col].values.astype(float), pop)
        rows.append({
            'metric': label,
            'key': summary_key,
            'sad_value': sad_val,
            'metro_value': metro_val,
            'sad_to_metro_ratio': (
                round(sad_val / metro_val, 3)
                if sad_val and metro_val else None),
            'sad_percentile_in_metro': rank,
            'kind': 'level',
        })

    for summary_key, label in COMPOSITION_METRICS:
        sad_val = sad_summary.get(summary_key)
        metro_val = metro_summary.get(summary_key)
        rows.append({
            'metric': label,
            'key': summary_key,
            'sad_value': sad_val,
            'metro_value': metro_val,
            'sad_to_metro_ratio': (
                round(sad_val / metro_val, 3)
                if sad_val and metro_val else None),
            'sad_percentile_in_metro': None,  # percentage-of-population metrics
            'kind': 'composition',
        })

    return rows


# ─── Main orchestration ──────────────────────────────────────────────────────

def process_sad(
    derived_dir: Path,
    source_dir: Path,
    api_key: str | None = None,
    year: int = DEFAULT_ACS_YEAR,
) -> Path:
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        sys.exit(f"manifest.json not found at {manifest_path}. Run Module 1 first.")
    manifest = Manifest.model_validate_json(manifest_path.read_text())

    # Module 4's per-SAD summary is the baseline we position against.
    sad_summary_path = derived_dir / 'census_summary.json'
    if not sad_summary_path.exists():
        sys.exit(
            f"census_summary.json not found at {sad_summary_path}.\n"
            "Run module_04_census_pull.py first — this module compares the SAD "
            "blocks against the metro, and needs the SAD-blocks summary."
        )
    sad_summary = json.loads(sad_summary_path.read_text())

    print(f"Building metro context for {manifest.sad_id} (ACS {year} 5-year)...")
    centroid = resolve_sad_centroid(manifest, source_dir)
    member_counties, metro_desc = find_cbsa_counties(centroid, year=year)

    metro_bgs = fetch_metro_block_groups(member_counties, year=year)
    acs = fetch_acs_for_block_groups(metro_bgs, year=year, api_key=api_key)
    metro_bgs = metro_bgs.merge(acs, on='GEOID', how='left')
    metro_bgs = add_pop_density(metro_bgs)

    # Metro-wide summary: every block group fully counts, so we set the
    # intersection weight to 1.0 and reuse Module 4's summary math verbatim.
    # compute_summary also reads 'fully_inside_bbox' (a column M4 builds during
    # its bbox-clip step). There's no bbox to clip against here — the metro IS
    # the extent — so every block group is, by definition, fully inside.
    metro_bgs['intersection_area_ratio'] = 1.0
    metro_bgs['fully_inside_bbox'] = True
    metro_summary = compute_summary(
        metro_bgs, manifest.sad_id, manifest.sad_name, year)
    # compute_summary doesn't compute density (it's a Module 4b addition) —
    # add the population-weighted metro density so positioning has a baseline.
    pop = metro_bgs['total_pop'].fillna(0.0)
    dens = metro_bgs['pop_density_per_km2']
    mask = dens.notna() & (pop > 0)
    metro_summary['pop_density_per_km2'] = (
        float((dens[mask] * pop[mask]).sum() / pop[mask].sum())
        if mask.sum() else None)

    positioning = build_positioning(metro_bgs, sad_summary, metro_summary)

    # ── Save spatial layer ──────────────────────────────────────────────────
    source_dir.mkdir(parents=True, exist_ok=True)
    out_gpkg = source_dir / 'census_metro_blockgroups.gpkg'
    metro_bgs.to_file(out_gpkg, driver='GPKG', layer='metro_blockgroups')

    # ── Save the comparison summary ──────────────────────────────────────────
    out = {
        'sad_id': manifest.sad_id,
        'sad_name': manifest.sad_name,
        'acs_year': year,
        'acs_vintage': f'ACS 5-year ({year - 4}-{year})',
        'metro': {
            'kind': metro_desc['metro_kind'],
            'cbsa_code': metro_desc['cbsa_code'],
            'cbsa_name': metro_desc['cbsa_name'],
            'counties': member_counties['NAME'].tolist(),
            'block_groups_total': int(len(metro_bgs)),
        },
        # The two scopes the comparison tool can toggle between:
        #   'sad'   -> Module 4's census_summary.json (SAD blocks only)
        #   'metro' -> this metro-wide summary
        'scope_summaries': {
            'sad': sad_summary,
            'metro': metro_summary,
        },
        # SAD positioned against its metro — the headline comparison.
        'sad_vs_metro': positioning,
    }
    derived_dir.mkdir(parents=True, exist_ok=True)
    out_path = derived_dir / 'census_metro_summary.json'
    out_path.write_text(json.dumps(out, indent=2, default=str))

    # ── Console readout ───────────────────────────────────────────────────────
    print(f"\n[OK] {manifest.sad_id}")
    print(f"  metro: {metro_desc['cbsa_name']}")
    print(f"  metro block groups: {len(metro_bgs)}")
    mp = metro_summary.get('estimated_population')
    if mp is not None:
        print(f"  metro population (sum): {mp:,}")
    print("\n  SAD vs metro:")
    for row in positioning:
        sv, mv = row['sad_value'], row['metro_value']
        rank = row['sad_percentile_in_metro']
        if sv is None or mv is None:
            continue
        if rank is not None:
            r = int(round(rank))
            suffix = 'th' if 10 <= (r % 100) <= 20 else \
                {1: 'st', 2: 'nd', 3: 'rd'}.get(r % 10, 'th')
            rank_str = f"  [{r}{suffix} pct]"
        else:
            rank_str = ""
        print(f"    {row['metric']:<32} SAD {sv:>12,.1f} | "
              f"metro {mv:>12,.1f}{rank_str}")
    print(f"\n  wrote {out_gpkg}")
    print(f"  wrote {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Metro-wide (CBSA) census context + SAD-vs-metro positioning")
    parser.add_argument('--derived', type=Path, required=True,
                        help='Derived dir for this SAD (manifest.json + census_summary.json)')
    parser.add_argument('--source', type=Path, required=True,
                        help='Source dir (metro gpkg output goes here)')
    parser.add_argument('--year', type=int, default=DEFAULT_ACS_YEAR,
                        help=f'ACS 5-year end year (default {DEFAULT_ACS_YEAR})')
    parser.add_argument('--api-key', type=str, default=None,
                        help='Census API key (default reads CENSUS_API_KEY env var)')
    args = parser.parse_args()
    process_sad(args.derived, args.source, api_key=args.api_key, year=args.year)


if __name__ == '__main__':
    main()
