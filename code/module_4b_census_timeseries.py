"""
module_4b_census_timeseries.py

Pulls ACS 5-year demographics for a SAD across multiple vintages (years),
producing a single time-series JSON that the Map and Viewer can use to show
demographic change over time.

Pre-pulls all requested years for one or many SADs. Network-bound; designed
for "slow once, fast forever" operation.

DESIGN
  - Reuses module_04's pull functions (fetch_block_groups_for_bbox,
    fetch_acs_for_block_groups, compute_summary). Single source of truth.
  - Block group geometries change with each decennial census (2010 vs 2020),
    so we cache one GPKG per decennial vintage rather than per year.
  - Output is a single census_timeseries.json per SAD containing year->summary
    plus year->per-block-group attribute rows (joinable to the corresponding
    decennial GPKG by GEOID).
  - Canadian / non-US SADs are skipped gracefully (no ACS coverage).

USAGE
  Single SAD:
    python module_4b_census_timeseries.py --sad-dir <path-to-sad-folder>

  Whole corpus:
    python module_4b_census_timeseries.py --data-dir <path-to-data> [--force]

  Custom year range:
    python module_4b_census_timeseries.py --data-dir <path> --years 2009-2023
    python module_4b_census_timeseries.py --data-dir <path> --years 2013,2018,2023

OUTPUTS
  source/<sad>/census_blockgroups_2010.gpkg   (geometries for ACS 2009-2019)
  source/<sad>/census_blockgroups_2020.gpkg   (geometries for ACS 2020+)
  derived/<sad>/census_timeseries.json        (year -> summary + bg attrs)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import box as shp_box

sys.path.insert(0, str(Path(__file__).parent))

# Reuse M4's pull functions so the variable list, sentinel handling, and
# summary computation stay in one place.
from module_04_census_pull import (
    ACS_VARS,
    ACS_NULL_SENTINELS,
    fetch_acs_for_block_groups,
    compute_summary,
)
from shared.schemas import Manifest


# ─── Multi-vintage block-group fetch ─────────────────────────────────────────
# M4's fetch_block_groups_for_bbox assumes 'STATEFP'/'COUNTYFP'/'GEOID' columns
# always exist. That's true for the 2020 cartographic boundary files but NOT
# for older vintages (2010 cb files use STATEFP10/COUNTYFP10/GEOID10). We
# need this module to work across all ACS years, so we override the fetch
# here with a vintage-tolerant version.

def _fetch_block_groups_robust(bbox_geo, vintage: int):
    """Vintage-tolerant block-group fetch.
    
    Uses 2020 cartographic-boundary counties (stable schema) to identify which
    counties the bbox touches, then fetches block groups at the requested
    vintage. Normalizes column names so downstream code can assume
    GEOID / STATEFP / COUNTYFP regardless of which TIGER year produced them.
    """
    try:
        import pygris
    except ImportError:
        sys.exit("Module 4b requires pygris. Install with: pip install pygris")
    
    minlon, minlat, maxlon, maxlat = bbox_geo
    bbox_poly = shp_box(minlon, minlat, maxlon, maxlat)
    
    # Use modern (2020) cb counties to find which counties the bbox touches.
    # Counties don't move, and the 2020 schema has the column names we expect.
    counties = pygris.counties(year=2020, cb=True).to_crs("EPSG:4326")
    intersecting = counties[counties.intersects(bbox_poly)]
    if len(intersecting) == 0:
        raise ValueError(f"No counties intersect bbox {bbox_geo}")
    
    print(f"  bbox touches {len(intersecting)} counties: "
          f"{', '.join(intersecting['NAME'].tolist())}")
    
    # Pull block groups at the requested vintage. Use a safe pygris year
    # to avoid the year=2010 cb bug.
    pygris_year = _pygris_year_for_vintage(vintage)
    bgs_list = []
    for _, c in intersecting.iterrows():
        state_fips = c['STATEFP']
        county_fips = c['COUNTYFP']
        # Try cb=True first (smaller cartographic boundary file). On Windows,
        # large state files (notably California, FIPS=06) sometimes fail to
        # be re-read after download due to temp-folder / antivirus interference
        # ("file does not exist in the file system"). Fall back to cb=False
        # (full TIGER/Line file) in that case — it's larger but more reliable.
        bgs_part = None
        last_err = None
        for cb_value in (True, False):
            try:
                bgs_part = pygris.block_groups(
                    state=state_fips, county=county_fips,
                    year=pygris_year, cb=cb_value,
                )
                break
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                # Only fall back for the specific download-then-can't-read issue
                if 'does not exist' not in msg and 'no such file' not in msg:
                    raise
                print(f"  [tiger] cb={cb_value} failed for state={state_fips}; "
                      f"trying cb={not cb_value}...")
        if bgs_part is None:
            raise last_err
        bgs_list.append(bgs_part)
    bgs = pd.concat(bgs_list, ignore_index=True)
    bgs = gpd.GeoDataFrame(bgs, geometry='geometry', crs=bgs_list[0].crs)
    bgs = bgs.to_crs("EPSG:4326")
    
    # Normalize columns: older vintages use STATEFP10/COUNTYFP10/GEOID10,
    # newer use STATEFP20/COUNTYFP20/GEOID20 (or unsuffixed).
    for canonical, alternatives in (
        ('GEOID',    ('GEOID20', 'GEOID10', 'GEO_ID')),
        ('STATEFP',  ('STATEFP20', 'STATEFP10', 'STATE')),
        ('COUNTYFP', ('COUNTYFP20', 'COUNTYFP10', 'COUNTY')),
    ):
        if canonical not in bgs.columns:
            for alt in alternatives:
                if alt in bgs.columns:
                    bgs[canonical] = bgs[alt]
                    break
    
    # Filter to BGs that actually touch the bbox
    bgs = bgs[bgs.intersects(bbox_poly)].copy()
    print(f"  -> {len(bgs)} intersecting block groups (vintage {vintage})")
    return bgs


# ─── Per-year ACS variable availability ──────────────────────────────────────
# Different ACS variables were introduced in different years. Requesting a
# variable before its introduction year returns HTTP 400 for the whole batch.
# Map each ACS variable to the earliest 5-year end-year it became available.
# Values not in this dict are assumed to be available from 2009 onward.

ACS_VAR_INTRODUCED = {
    # Educational attainment by detailed category (B15003) replaced B15002
    # starting with ACS 2012 5-year. Pre-2012 we cannot pull detailed edu.
    'B15003_001E': 2012,
    'B15003_022E': 2012,
    'B15003_023E': 2012,
    'B15003_024E': 2012,
    'B15003_025E': 2012,
    # Employment status table (B23025) was added in 2010 ACS 5-year.
    'B23025_005E': 2010,
    'B23025_002E': 2010,
}


def _fetch_acs_filtered(bgs, year: int, api_key: str | None):
    """Fetch ACS variables, automatically filtering to those available in
    `year`. Returns DataFrame keyed on GEOID with the available columns
    (any variable not yet introduced in `year` is simply omitted)."""
    import requests
    
    api_key = api_key or os.environ.get('CENSUS_API_KEY')
    if not api_key:
        sys.exit("No Census API key. Set CENSUS_API_KEY env var.")
    
    # Filter ACS_VARS to those available in this year
    codes = [c for c in ACS_VARS.keys()
             if year >= ACS_VAR_INTRODUCED.get(c, 0)]
    omitted = [c for c in ACS_VARS.keys() if c not in codes]
    if omitted:
        print(f"  [{year}] omitting {len(omitted)} variables not yet "
              f"introduced: {', '.join(omitted)}")
    
    base_url = f"https://api.census.gov/data/{year}/acs/acs5"
    pairs = bgs[['STATEFP', 'COUNTYFP']].drop_duplicates()
    
    all_rows = []
    for _, row in pairs.iterrows():
        state = str(row['STATEFP']).zfill(2)
        county = str(row['COUNTYFP']).zfill(3)
        
        # Try three formats in order. Older ACS endpoints are pickier about
        # geography hierarchy. By 2010+ the modern form works; 2009 may
        # require explicit per-tract iteration.
        attempts = [
            f'state:{state} county:{county}',
            f'state:{state} county:{county} tract:*',
        ]
        last_err = None
        data = None
        for in_clause in attempts:
            params = {
                'get': ','.join(codes),
                'for': 'block group:*',
                'in': in_clause,
                'key': api_key,
            }
            resp = requests.get(base_url, params=params, timeout=60)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    break
                except Exception as e:
                    last_err = e
                    continue
            last_err = f"{resp.status_code}: {resp.text[:200]}"
            if 'geography hierarchy' not in resp.text.lower():
                break  # different error — no point retrying
        
        # Last-ditch: enumerate tracts first, then loop block-groups per tract.
        # This is the oldest, most-supported hierarchy form.
        if data is None and 'geography hierarchy' in str(last_err).lower():
            try:
                tract_resp = requests.get(base_url, params={
                    'get': 'NAME',
                    'for': 'tract:*',
                    'in': f'state:{state} county:{county}',
                    'key': api_key,
                }, timeout=60)
                if tract_resp.status_code == 200:
                    tract_data = tract_resp.json()
                    tract_header = tract_data[0]
                    tract_idx = tract_header.index('tract')
                    tracts = [r[tract_idx] for r in tract_data[1:]]
                    
                    print(f"  [{year}] enumerating {len(tracts)} tracts "
                          f"individually (legacy hierarchy)...")
                    combined_rows = []
                    combined_header = None
                    for tract in tracts:
                        bg_resp = requests.get(base_url, params={
                            'get': ','.join(codes),
                            'for': 'block group:*',
                            'in': f'state:{state} county:{county} tract:{tract}',
                            'key': api_key,
                        }, timeout=60)
                        if bg_resp.status_code != 200:
                            continue  # skip tracts that fail
                        bg_data = bg_resp.json()
                        if combined_header is None:
                            combined_header = bg_data[0]
                        combined_rows.extend(bg_data[1:])
                    if combined_header and combined_rows:
                        data = [combined_header] + combined_rows
                        last_err = None
            except Exception as e:
                last_err = f"tract-iter fallback failed: {e}"
        
        if data is None:
            raise RuntimeError(
                f"ACS API failed for {year} state={state} county={county}: {last_err}"
            )
        
        header, *rows = data
        for row_data in rows:
            rec = dict(zip(header, row_data))
            rec['GEOID'] = (
                str(rec.get('state', '')).zfill(2)
                + str(rec.get('county', '')).zfill(3)
                + str(rec.get('tract', '')).zfill(6)
                + str(rec.get('block group', ''))
            )
            all_rows.append(rec)
    
    df = pd.DataFrame(all_rows)
    
    # Convert ACS values: replace sentinels with NaN, cast to numeric
    for code, name in ACS_VARS.items():
        if code in df.columns:
            s = pd.to_numeric(df[code], errors='coerce')
            s = s.where(~s.isin(ACS_NULL_SENTINELS), np.nan)
            df[name] = s
        else:
            # Variable wasn't requested for this year — fill with NaN
            df[name] = np.nan
    
    # Keep only GEOID + named columns
    return df[['GEOID'] + list(ACS_VARS.values())]


# ─── Year range and vintages ─────────────────────────────────────────────────

# ACS 5-year is published for end-years 2009 onward (released ~Dec of Y+1).
# Each ACS year is keyed to a "decennial vintage" of block groups (2010 or 2020
# census geography). For the pygris TIGER fetch we use a SAFE year value:
#   - decennial 2010: use pygris year=2019 (pygris year=2010 has a known bug
#     where it queries a non-existent COUNTYFP column on cb files)
#   - decennial 2020: use pygris year=2020+
DEFAULT_YEAR_RANGE = list(range(2013, 2024))  # 2013..2023 inclusive
                                              # 2013 is the empirical lower bound:
                                              # earlier ACS 5-year vintages return
                                              # "unknown/unsupported geography hierarchy"
                                              # from the Census API for block-group queries.

def _vintage_for_year(year: int) -> int:
    """Which decennial census's block groups does this ACS year use?
    
    ACS 5-year switched to 2020 census geographies starting with the
    2020 5-year release (covering 2016-2020). Prior years used 2010
    geographies.
    """
    return 2020 if year >= 2020 else 2010


def _pygris_year_for_vintage(vintage: int) -> int:
    """The safe TIGER year to pass to pygris to retrieve the given vintage's
    block groups. Avoids pygris's broken handling of year=2010 cb files."""
    return 2019 if vintage == 2010 else 2020


def _parse_years(arg: str | None) -> list[int]:
    """Accept '2009-2023' or '2013,2018,2023' or default."""
    if not arg:
        return list(DEFAULT_YEAR_RANGE)
    arg = arg.strip()
    if '-' in arg and ',' not in arg:
        a, b = arg.split('-', 1)
        return list(range(int(a), int(b) + 1))
    return sorted({int(x.strip()) for x in arg.split(',') if x.strip()})


# ─── Per-SAD orchestration ───────────────────────────────────────────────────

def _attrs_for_year(bgs: gpd.GeoDataFrame, acs: pd.DataFrame) -> list[dict]:
    """Extract a compact list of per-block-group attribute dicts for the
    map/viewer to render. Drops geometry; keeps GEOID for join-back."""
    df = bgs[['GEOID']].copy()
    df = df.merge(acs, on='GEOID', how='left')
    rows = []
    for _, r in df.iterrows():
        rec = {'GEOID': str(r['GEOID'])}
        for code, name in ACS_VARS.items():
            v = r.get(name)
            if v is None or (isinstance(v, float) and (np.isnan(v) or v != v)):
                rec[name] = None
            else:
                # Round floats to keep JSON compact
                rec[name] = round(float(v), 2) if isinstance(v, float) else int(v)
        rows.append(rec)
    return rows


def process_sad_timeseries(
    derived_dir: Path,
    source_dir: Path,
    years: list[int],
    api_key: str | None = None,
    force: bool = False,
) -> Path | None:
    """Pull every year in `years` for this SAD; write a single census_timeseries.json."""
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        print(f"  [skip] no manifest at {manifest_path}")
        return None
    manifest = Manifest.model_validate_json(manifest_path.read_text())
    sad_id = manifest.sad_id
    
    ts_path = derived_dir / 'census_timeseries.json'
    
    # Skip-if-exists logic: if the existing file already has every requested year, bail.
    if ts_path.exists() and not force:
        try:
            existing = json.loads(ts_path.read_text())
            existing_years = set(existing.get('years_pulled', []))
            if set(years).issubset(existing_years):
                print(f"  [skip] {sad_id}: all requested years already present")
                return ts_path
        except (json.JSONDecodeError, KeyError):
            pass  # malformed — re-pull
    
    # Cache block group geometries by decennial vintage so we don't refetch
    # TIGER for every year that shares geometries.
    bgs_cache: dict[int, gpd.GeoDataFrame] = {}
    
    summaries: dict[str, dict] = {}
    bg_attrs: dict[str, list[dict]] = {}
    years_pulled: list[int] = []
    
    for year in years:
        vintage = _vintage_for_year(year)
        try:
            # Fetch/cache geometries for this vintage
            if vintage not in bgs_cache:
                print(f"  [{sad_id}] fetching {vintage}-vintage block groups...")
                bgs_v = _fetch_block_groups_robust(manifest.bbox_geo, vintage=vintage)
                # Persist the GPKG for the UI to use
                source_dir.mkdir(parents=True, exist_ok=True)
                gpkg_path = source_dir / f'census_blockgroups_{vintage}.gpkg'
                bgs_v.to_file(gpkg_path, driver='GPKG', layer='blockgroups')
                # Also write GeoJSON inline — browser-readable, no separate export
                # step needed for new (drawn) districts.
                geojson_path = source_dir / f'census_blockgroups_{vintage}.geojson'
                try:
                    bgs_browser = bgs_v.copy()
                    if bgs_browser.crs and str(bgs_browser.crs).upper() not in ('EPSG:4326', 'WGS 84'):
                        bgs_browser = bgs_browser.to_crs('EPSG:4326')
                    bgs_browser.to_file(geojson_path, driver='GeoJSON')
                except Exception as ge:
                    print(f"  [warn] could not write {geojson_path.name}: {ge}")
                bgs_cache[vintage] = bgs_v
            bgs = bgs_cache[vintage]
            
            # Pull ACS for this year — use our filtered fetcher that
            # handles per-year variable availability automatically.
            print(f"  [{sad_id}] pulling ACS {year} 5-year...")
            acs = _fetch_acs_filtered(bgs, year=year, api_key=api_key)
            
            # Compute summary (reuse M4's function)
            bgs_with_acs = bgs.merge(acs, on='GEOID', how='left')
            # M4's compute_summary expects intersection_area_ratio columns.
            # For time-series we use a simpler equal-weighting fallback.
            if 'intersection_area_ratio' not in bgs_with_acs.columns:
                bgs_with_acs['intersection_area_ratio'] = 1.0
                bgs_with_acs['fully_inside_bbox'] = True
            summary = compute_summary(bgs_with_acs, sad_id, manifest.sad_name, year)
            summaries[str(year)] = summary
            
            # Per-BG attrs for choropleth
            bg_attrs[str(year)] = _attrs_for_year(bgs, acs)
            years_pulled.append(year)
            
        except ValueError as e:
            # No counties intersect (likely Canadian SAD) — skip whole SAD
            print(f"  [skip] {sad_id}: {e}")
            return None
        except Exception as e:
            print(f"  [warn] {sad_id} year {year}: {type(e).__name__}: {e}")
            continue  # try the next year
    
    if not years_pulled:
        print(f"  [skip] {sad_id}: no years pulled successfully")
        return None
    
    payload = {
        'sad_id': sad_id,
        'sad_name': manifest.sad_name,
        'years_pulled': years_pulled,
        'years_requested': years,
        'vintages_used': sorted({_vintage_for_year(y) for y in years_pulled}),
        'acs_variables': dict(ACS_VARS),  # so the UI knows the full attr list
        'summaries': summaries,
        'block_groups': bg_attrs,
    }
    
    derived_dir.mkdir(parents=True, exist_ok=True)
    ts_path.write_text(json.dumps(payload, indent=2))
    print(f"  [OK] {sad_id}: {len(years_pulled)} years -> {ts_path}")
    return ts_path


# ─── Multi-SAD batch ─────────────────────────────────────────────────────────

def _iter_sad_dirs(data_dir: Path):
    """Yield (derived_dir, source_dir) for each SAD folder in data_dir."""
    for d in sorted(data_dir.iterdir()):
        if not d.is_dir() or d.name.startswith('_') or d.name == 'source':
            continue
        derived = d / 'derived'
        source = d / 'source'
        # Some pipelines use flat structure
        if not derived.exists():
            derived = d
        if not source.exists():
            source = d
        yield derived, source, d.name


def process_data_dir(
    data_dir: Path,
    years: list[int],
    api_key: str | None = None,
    force: bool = False,
):
    sads_processed = 0
    sads_skipped = 0
    for derived, source, name in _iter_sad_dirs(data_dir):
        print(f"\n=== {name} ===")
        try:
            result = process_sad_timeseries(derived, source, years, api_key, force)
            if result:
                sads_processed += 1
            else:
                sads_skipped += 1
        except KeyboardInterrupt:
            print("\n  [abort] keyboard interrupt — re-run to resume")
            raise
        except Exception as e:
            print(f"  [error] {name}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)
            sads_skipped += 1
        # Small pause to be a good citizen to the Census API
        time.sleep(0.5)
    
    print(f"\nDone. {sads_processed} processed, {sads_skipped} skipped.")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Pull ACS 5-year demographics across multiple vintages "
                    "for one or all SADs."
    )
    group = p.add_mutually_exclusive_group(required=False)
    group.add_argument('--sad-dir', type=Path, help='Single SAD folder')
    group.add_argument('--data-dir', type=Path, help='Corpus root folder')
    
    # Pipeline-style alternative: pass --source and --derived directly. Matches
    # M4's CLI shape so M4b can be wired into batch_run_pipeline.py / pipeline_ui.py.
    p.add_argument('--source', type=Path,
                   help='Source dir (alternative to --sad-dir; pair with --derived)')
    p.add_argument('--derived', type=Path,
                   help='Derived dir (alternative to --sad-dir; pair with --source)')
    
    p.add_argument('--years', type=str, default=None,
                   help="Year range or list. e.g. '2013-2023' or '2013,2018,2023'. "
                        f"Default: {DEFAULT_YEAR_RANGE[0]}-{DEFAULT_YEAR_RANGE[-1]}")
    p.add_argument('--api-key', type=str, default=None,
                   help='Census API key (default reads CENSUS_API_KEY env var)')
    p.add_argument('--force', action='store_true',
                   help='Re-pull even if census_timeseries.json already has the years')
    
    args = p.parse_args()
    years = _parse_years(args.years)
    print(f"Years to pull: {years}")
    
    if not (args.sad_dir or args.data_dir or args.source or args.derived):
        sys.exit("Need one of: --sad-dir, --data-dir, or both --source + --derived")
    
    # Resolve the actual source / derived dirs given how the user invoked us.
    if args.source or args.derived:
        if not (args.source and args.derived):
            sys.exit("--source and --derived must be passed together")
        process_sad_timeseries(args.derived, args.source, years, args.api_key, args.force)
    elif args.sad_dir:
        derived = args.sad_dir / 'derived' if (args.sad_dir / 'derived').exists() else args.sad_dir
        source = args.sad_dir / 'source' if (args.sad_dir / 'source').exists() else args.sad_dir
        process_sad_timeseries(derived, source, years, args.api_key, args.force)
    else:
        process_data_dir(args.data_dir, years, args.api_key, args.force)


if __name__ == '__main__':
    main()
