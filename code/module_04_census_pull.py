"""
module_04_census_pull.py

Pulls American Community Survey (ACS) demographics for the block groups
that intersect a SAD's bounding box. Block group is the smallest stable
Census geography (typically 600-3,000 people) and the right unit for
characterizing the immediate demographic context of a district.

PIPELINE
  1. Read manifest to get the canvas bbox
  2. Find which counties the bbox touches (via TIGER county geometries)
  3. Download block group geometries for those counties
  4. Filter to block groups that intersect the bbox
  5. Pull ACS variables for each intersecting block group
  6. Compute intersection-area ratios for partial coverage weighting
  7. Save the spatial layer + an area-weighted summary JSON

REQUIREMENTS
  Census API key (free, ~2 minutes to obtain):
    https://api.census.gov/data/key_signup.html
  
  Set as environment variable in PowerShell:
    $env:CENSUS_API_KEY = "your_40_character_key_here"
  
  Or persist across sessions:
    [System.Environment]::SetEnvironmentVariable("CENSUS_API_KEY", "key", "User")
  
  Python packages:
    pip install pygris census

USAGE
  python module_04_census_pull.py ^
      --derived ..\\data\\derived\\per_sad\\district_detroit ^
      --source  ..\\data\\source\\per_sad\\district_detroit

OUTPUTS
  source/<sad>/census_blockgroups.gpkg  Block group polygons with ACS columns
  derived/<sad>/census_summary.json     Area-weighted demographic summary
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import box as shp_box

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest


# ─── ACS variable selection ──────────────────────────────────────────────────
# Variables chosen to capture: who lives here, what's their economic profile,
# and how they interact with housing. The codes follow standard ACS table
# IDs (B-tables are 5-year ACS detailed tables, E suffix = estimate).
ACS_VARS = {
    # Population & age
    'B01003_001E': 'total_pop',
    'B01002_001E': 'median_age',
    # Income & employment
    'B19013_001E': 'median_household_income',
    'B23025_005E': 'unemployed',
    'B23025_002E': 'labor_force',
    # Housing
    'B25077_001E': 'median_home_value',
    'B25064_001E': 'median_gross_rent',
    'B25003_002E': 'owner_occupied',
    'B25003_003E': 'renter_occupied',
    'B25003_001E': 'occupied_total',
    # Education (population 25+)
    'B15003_001E': 'edu_total_pop_25plus',
    'B15003_022E': 'edu_bachelors',
    'B15003_023E': 'edu_masters',
    'B15003_024E': 'edu_professional',
    'B15003_025E': 'edu_doctorate',
    # Race & ethnicity
    'B02001_001E': 'race_total',
    'B02001_002E': 'race_white',
    'B02001_003E': 'race_black',
    'B02001_005E': 'race_asian',
    'B03003_003E': 'hispanic',
}

# ACS sentinel values that should be treated as missing data, not real numbers.
# The Census API encodes "not applicable" / "could not compute" as large
# negative integers; replacing them with NaN keeps statistics honest.
ACS_NULL_SENTINELS = (-666666666, -555555555, -333333333, -222222222, -888888888)

# Most recent 5-year ACS that's been released. Updated approximately each
# December: 5-year ACS for years (Y-4..Y) is released in December Y+1.
DEFAULT_ACS_YEAR = 2023


# ─── Block group geometry retrieval ──────────────────────────────────────────

def fetch_block_groups_for_bbox(
    bbox_geo: tuple[float, float, float, float],
    year: int = DEFAULT_ACS_YEAR,
) -> gpd.GeoDataFrame:
    """
    Fetch TIGER block group polygons for the counties touched by `bbox_geo`,
    then filter to those that actually intersect the bbox.
    """
    try:
        import pygris
    except ImportError:
        sys.exit("Module 4 requires pygris. Install with: pip install pygris")
    
    minlon, minlat, maxlon, maxlat = bbox_geo
    bbox_poly = shp_box(minlon, minlat, maxlon, maxlat)
    
    # Identify counties touched by the bbox via TIGER cartographic boundaries.
    # cb=True returns the simplified cartographic boundary (smaller files,
    # plenty of resolution for the intersection test).
    counties = pygris.counties(year=year, cb=True)
    counties = counties.to_crs("EPSG:4326")
    intersecting = counties[counties.intersects(bbox_poly)]
    if len(intersecting) == 0:
        raise ValueError(f"No counties intersect bbox {bbox_geo}")
    
    print(f"  bbox touches {len(intersecting)} counties: "
          f"{', '.join(intersecting['NAME'].tolist())}")
    
    # Pull block groups county-by-county and concatenate.
    bgs_list = []
    for _, c in intersecting.iterrows():
        state_fips = c['STATEFP']
        county_fips = c['COUNTYFP']
        bgs = pygris.block_groups(
            state=state_fips, county=county_fips,
            year=year, cb=True,
        )
        bgs_list.append(bgs)
    bgs = pd.concat(bgs_list, ignore_index=True)
    bgs = gpd.GeoDataFrame(bgs, geometry='geometry', crs=bgs_list[0].crs)
    bgs = bgs.to_crs("EPSG:4326")
    
    # Filter to block groups actually touching the bbox.
    bgs = bgs[bgs.intersects(bbox_poly)].copy()
    print(f"  -> {len(bgs)} intersecting block groups")
    return bgs


# ─── ACS variable retrieval ──────────────────────────────────────────────────

def fetch_acs_for_block_groups(
    block_groups: gpd.GeoDataFrame,
    year: int = DEFAULT_ACS_YEAR,
    api_key: str | None = None,
) -> pd.DataFrame:
    """
    Pull ACS variables for the block groups in `block_groups`.
    
    Uses the Census ACS API directly via HTTP, bypassing the `census` Python
    package whose variable-type lookup endpoint has been intermittently
    failing (returns non-JSON HTML). This is a more reliable path: we hit
    the well-tested data endpoint, not the metadata endpoint.
    
    Returns a DataFrame keyed on GEOID with the ACS_VARS columns.
    """
    import requests
    
    api_key = api_key or os.environ.get('CENSUS_API_KEY')
    if not api_key:
        sys.exit(
            "No Census API key found. Set CENSUS_API_KEY env var or pass --api-key.\n"
            "Get a free key at: https://api.census.gov/data/key_signup.html"
        )
    
    # Census ACS 5-year API endpoint
    base_url = f"https://api.census.gov/data/{year}/acs/acs5"
    variable_codes = list(ACS_VARS.keys())
    
    # Group block groups by (state, county) and make one request per pair.
    pairs = block_groups[['STATEFP', 'COUNTYFP']].drop_duplicates()
    
    all_rows: list[dict] = []
    for _, row in pairs.iterrows():
        state = str(row['STATEFP']).zfill(2)
        county = str(row['COUNTYFP']).zfill(3)
        
        # Census API allows up to 50 variables per request; we have 20.
        params = {
            'get': ','.join(variable_codes),
            'for': 'block group:*',
            'in': f'state:{state} county:{county}',
            'key': api_key,
        }
        
        try:
            resp = requests.get(base_url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.JSONDecodeError as e:
            sys.exit(
                f"Census API returned non-JSON response for state={state} "
                f"county={county}. Response body: {resp.text[:300]}"
            )
        except requests.exceptions.RequestException as e:
            sys.exit(f"Census API request failed: {e}")
        
        if not data or len(data) < 2:
            print(f"  WARN: no data returned for state={state} county={county}")
            continue
        
        headers = data[0]  # column names
        for record in data[1:]:
            all_rows.append(dict(zip(headers, record)))
    
    if not all_rows:
        sys.exit("Census API returned no data for any (state, county) pair.")
    
    acs_df = pd.DataFrame(all_rows)
    
    # Build the canonical 12-character GEOID (state 2 + county 3 + tract 6 + bg 1)
    acs_df['GEOID'] = (
        acs_df['state'].astype(str).str.zfill(2) +
        acs_df['county'].astype(str).str.zfill(3) +
        acs_df['tract'].astype(str).str.zfill(6) +
        acs_df['block group'].astype(str)
    )
    
    # Rename Census variable codes to readable names
    acs_df = acs_df.rename(columns=ACS_VARS)
    
    # Coerce to numeric and replace Census null sentinels with NaN
    for new_name in ACS_VARS.values():
        if new_name in acs_df.columns:
            acs_df[new_name] = pd.to_numeric(acs_df[new_name], errors='coerce')
            acs_df.loc[acs_df[new_name].isin(ACS_NULL_SENTINELS), new_name] = np.nan
    
    return acs_df


# ─── Area-weighted demographic summary ───────────────────────────────────────

def compute_summary(bgs: gpd.GeoDataFrame, sad_id: str, sad_name: str, year: int) -> dict:
    """
    Produce a single demographic snapshot for the SAD canvas. Block groups
    that only partially overlap the canvas are weighted by their intersection
    ratio: a block group with 30% of its area inside the canvas contributes
    30% of its population, 30% of its housing units, etc.
    
    This assumes uniform population density within each block group - a
    standard approximation in spatial demography that holds reasonably for
    Census-sized geographies.
    """
    w = bgs['intersection_area_ratio'].fillna(0.0)
    
    def weighted_sum(col):
        if col not in bgs.columns:
            return None
        v = bgs[col].fillna(0.0)
        return float((v * w).sum())
    
    def weighted_mean(col, weight_col='total_pop'):
        """Population-weighted mean of a per-capita-style variable (e.g. income)."""
        if col not in bgs.columns or weight_col not in bgs.columns:
            return None
        vals = bgs[col]
        weights = bgs[weight_col].fillna(0.0) * w
        mask = vals.notna() & (weights > 0)
        if mask.sum() == 0:
            return None
        return float((vals[mask] * weights[mask]).sum() / weights[mask].sum())
    
    total_pop = weighted_sum('total_pop')
    owner = weighted_sum('owner_occupied')
    renter = weighted_sum('renter_occupied')
    occupied = weighted_sum('occupied_total')
    
    edu_total = weighted_sum('edu_total_pop_25plus')
    edu_college_plus = sum(
        weighted_sum(c) or 0
        for c in ('edu_bachelors', 'edu_masters', 'edu_professional', 'edu_doctorate')
    )
    
    race_total = weighted_sum('race_total')
    
    summary = {
        'sad_id': sad_id,
        'sad_name': sad_name,
        'acs_year': year,
        'acs_vintage': f'ACS 5-year ({year - 4}-{year})',
        'block_groups_total': int(len(bgs)),
        'block_groups_fully_inside': int(bgs['fully_inside_bbox'].sum()),
        'block_groups_partial': int((~bgs['fully_inside_bbox']).sum()),
        
        'estimated_population': round(total_pop) if total_pop is not None else None,
        'median_age_pop_weighted': weighted_mean('median_age'),
        'median_household_income_pop_weighted': weighted_mean('median_household_income'),
        
        'occupied_housing_units': round(occupied) if occupied is not None else None,
        'pct_owner_occupied': (
            round(100 * owner / occupied, 1)
            if occupied and occupied > 0 else None
        ),
        'pct_renter_occupied': (
            round(100 * renter / occupied, 1)
            if occupied and occupied > 0 else None
        ),
        
        'median_home_value_pop_weighted': weighted_mean('median_home_value'),
        'median_gross_rent_pop_weighted': weighted_mean('median_gross_rent'),
        
        'pct_bachelors_or_higher': (
            round(100 * edu_college_plus / edu_total, 1)
            if edu_total and edu_total > 0 else None
        ),
        
        'pct_white': (
            round(100 * (weighted_sum('race_white') or 0) / race_total, 1)
            if race_total and race_total > 0 else None
        ),
        'pct_black': (
            round(100 * (weighted_sum('race_black') or 0) / race_total, 1)
            if race_total and race_total > 0 else None
        ),
        'pct_asian': (
            round(100 * (weighted_sum('race_asian') or 0) / race_total, 1)
            if race_total and race_total > 0 else None
        ),
        'pct_hispanic': (
            round(100 * (weighted_sum('hispanic') or 0) / race_total, 1)
            if race_total and race_total > 0 else None
        ),
        
        'unemployment_rate': (
            round(100 * (weighted_sum('unemployed') or 0) /
                  (weighted_sum('labor_force') or 1), 1)
            if weighted_sum('labor_force') else None
        ),
    }
    return summary


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
    
    print(f"Fetching ACS {year} 5-year data for {manifest.sad_id}...")
    bgs = fetch_block_groups_for_bbox(manifest.bbox_geo, year=year)
    acs = fetch_acs_for_block_groups(bgs, year=year, api_key=api_key)
    
    # Join ACS columns onto the spatial block-group frame
    bgs = bgs.merge(acs, on='GEOID', how='left')
    
    bbox_poly = shp_box(*manifest.bbox_geo)
    
    # Compute intersection-area weighting so partial coverage is honest.
    # Reproject to a metric CRS first; computing .area on geographic
    # (lat/lon) geometries triggers a Shapely warning and gives nominally
    # wrong absolute values. The RATIO would still be correct, but the
    # cleaner path is to reproject so absolute and ratio both make sense.
    metric_crs = bgs.estimate_utm_crs()
    bgs_metric = bgs.to_crs(metric_crs)
    bbox_metric = (
        gpd.GeoSeries([bbox_poly], crs='EPSG:4326')
        .to_crs(metric_crs).iloc[0]
    )
    bgs['fully_inside_bbox'] = bgs_metric.geometry.within(bbox_metric).values
    bgs['intersection_area_ratio'] = (
        (bgs_metric.geometry.intersection(bbox_metric).area /
         bgs_metric.geometry.area)
        .clip(0.0, 1.0)
        .values
    )
    
    # Also compute SAD-boundary overlap ratio (separate from canvas bbox).
    # This lets downstream analysis distinguish "BGs whose population is
    # really inside the SAD" from "BGs that just happen to overlap the
    # rectangular canvas". Three-way zone classification:
    #   interior:  >=70% of BG area inside SAD polygon
    #   boundary:  10-70% inside (straddling the edge)
    #   exterior:  <10% inside (BG mostly outside SAD; only included
    #              because part of it touches the canvas)
    sad_boundary_path = source_dir / 'sad_boundary.geojson'
    if sad_boundary_path.exists():
        sad_boundary = gpd.read_file(sad_boundary_path).to_crs("EPSG:4326")
        try:
            sad_poly = sad_boundary.union_all()
        except AttributeError:
            sad_poly = sad_boundary.unary_union
        sad_poly_metric = (
            gpd.GeoSeries([sad_poly], crs='EPSG:4326')
            .to_crs(metric_crs).iloc[0]
        )
        bgs['sad_overlap_ratio'] = (
            (bgs_metric.geometry.intersection(sad_poly_metric).area /
             bgs_metric.geometry.area)
            .clip(0.0, 1.0)
            .values
        )
        def classify_zone(ratio):
            if ratio >= 0.70:
                return 'interior'
            if ratio >= 0.10:
                return 'boundary'
            return 'exterior'
        bgs['zone'] = bgs['sad_overlap_ratio'].apply(classify_zone)
    else:
        print(f"  WARN: no sad_boundary.geojson - skipping SAD zone tagging")
        bgs['sad_overlap_ratio'] = None
        bgs['zone'] = 'unknown'
    
    # Save spatial layer (with all ACS columns) to source folder
    source_dir.mkdir(parents=True, exist_ok=True)
    out_gpkg = source_dir / 'census_blockgroups.gpkg'
    bgs.to_file(out_gpkg, driver='GPKG', layer='blockgroups')
    
    # Save analytical summary to derived folder
    derived_dir.mkdir(parents=True, exist_ok=True)
    summary = compute_summary(bgs, manifest.sad_id, manifest.sad_name, year)
    summary_path = derived_dir / 'census_summary.json'
    summary_path.write_text(json.dumps(summary, indent=2))
    
    # Console readout
    print(f"\n[OK] {manifest.sad_id}")
    print(f"  ACS vintage: {summary['acs_vintage']}")
    print(f"  block groups: {summary['block_groups_total']} "
          f"({summary['block_groups_fully_inside']} fully inside, "
          f"{summary['block_groups_partial']} partial)")
    pop = summary['estimated_population']
    if pop is not None:
        print(f"  estimated population (area-weighted): {pop:,}")
    age = summary['median_age_pop_weighted']
    if age is not None:
        print(f"  median age: {age:.1f}")
    inc = summary['median_household_income_pop_weighted']
    if inc is not None:
        print(f"  median household income: ${inc:,.0f}")
    edu = summary['pct_bachelors_or_higher']
    if edu is not None:
        print(f"  bachelor's degree or higher: {edu}%")
    rent = summary['pct_renter_occupied']
    if rent is not None:
        print(f"  renter-occupied housing: {rent}%")
    print(f"\n  wrote {out_gpkg}")
    print(f"  wrote {summary_path}")
    return out_gpkg


def main():
    parser = argparse.ArgumentParser(description="Census ACS demographic pull for a SAD")
    parser.add_argument('--derived', type=Path, required=True,
                        help='Derived directory for this SAD (must contain manifest.json)')
    parser.add_argument('--source', type=Path, required=True,
                        help='Source directory (output census_blockgroups.gpkg goes here)')
    parser.add_argument('--year', type=int, default=DEFAULT_ACS_YEAR,
                        help=f'ACS 5-year end year (default {DEFAULT_ACS_YEAR})')
    parser.add_argument('--api-key', type=str, default=None,
                        help='Census API key (default reads CENSUS_API_KEY env var)')
    args = parser.parse_args()
    
    process_sad(args.derived, args.source,
                api_key=args.api_key, year=args.year)


if __name__ == '__main__':
    main()
