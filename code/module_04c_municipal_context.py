"""
module_04c_municipal_context.py

Adds a MUNICIPAL census scope to each SAD: instead of only the block groups
tightly ringing the stadium, this computes an area/population-weighted ACS
profile for the entire incorporated place (city/municipality) that contains the
district. That is a more realistic "who lives in this city" reading, and it
complements the existing scopes:

    district  (Module 4)   — block groups around the SAD
    municipal (this module) — the whole host city / place
    metro     (Module 4b)  — the whole CBSA

It reuses Module 4's ACS fetch and area-weighted summary verbatim, so the
output schema is identical to census_summary.json.

It also exposes municipal_summary_for_point(), which the draw-a-district server
calls so a sketched area gets the same municipal context.

USAGE
    python module_04c_municipal_context.py --data-dir ..\\data
    python module_04c_municipal_context.py --data-dir ..\\data --sads 32_District-Detroit_Detroit-MI

OUTPUT (per US SAD)
    data/<sad>/derived/census_municipal_summary.json
    data/<sad>/source/census_municipal_blockgroups.gpkg
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import module_04_census_pull as m4  # noqa: E402

NON_SAD = {'_ui', '_comparisons', '_compare_ui'}
EQUAL_AREA = 'EPSG:5070'
CACHE = True  # reuse downloaded TIGER shapefiles across runs (pygris cache)


def _retry(fn, *args, _tries: int = 4, _base: float = 2.0, _label: str = '', **kwargs):
    """Call a flaky TIGER/ACS fn with exponential backoff (HTTP+FTP can blip)."""
    last = None
    for i in range(_tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            wait = _base * (2 ** i)
            print(f"    retry {i + 1}/{_tries} {_label} after: {e} (waiting {wait:.0f}s)")
            time.sleep(wait)
    raise last


def _counties(year):
    import pygris
    return _retry(pygris.counties, year=year, cb=True, cache=CACHE, _label='counties').to_crs('EPSG:4326')


def _states(year):
    import pygris
    return _retry(pygris.states, year=year, cb=True, cache=CACHE, _label='states').to_crs('EPSG:4326')


def _places(state, year):
    import pygris
    return _retry(pygris.places, state=state, year=year, cb=True, cache=CACHE, _label=f'places {state}').to_crs('EPSG:4326')


def _county_subdivisions(state, county, year):
    import pygris
    return _retry(pygris.county_subdivisions, state=state, county=county, year=year,
                  cb=True, cache=CACHE, _label=f'cousub {state}{county}').to_crs('EPSG:4326')


def _block_groups(state, county, year):
    import pygris
    return _retry(pygris.block_groups, state=state, county=county, year=year,
                  cb=True, cache=CACHE, _label=f'bg {state}{county}')


def fetch_block_groups_for_bbox(bbox_geo, year):
    """Cached + retrying bbox -> block groups (replaces M4's uncached version)."""
    from shapely.geometry import box as shp_box
    minlon, minlat, maxlon, maxlat = bbox_geo
    bbox_poly = shp_box(minlon, minlat, maxlon, maxlat)
    counties = _counties(year)
    inter = counties[counties.intersects(bbox_poly)]
    if len(inter) == 0:
        raise ValueError(f"No counties intersect bbox {bbox_geo}")
    print(f"  bbox touches {len(inter)} counties: {', '.join(inter['NAME'].tolist())}")
    parts = [_block_groups(c['STATEFP'], c['COUNTYFP'], year) for _, c in inter.iterrows()]
    bgs = pd.concat(parts, ignore_index=True)
    bgs = gpd.GeoDataFrame(bgs, geometry='geometry', crs=parts[0].crs).to_crs('EPSG:4326')
    bgs = bgs[bgs.intersects(bbox_poly)].copy()
    print(f"  -> {len(bgs)} intersecting block groups")
    return bgs


# ── place resolution ─────────────────────────────────────────────────────────
def _state_fips_for_point(lon: float, lat: float, year: int) -> str | None:
    pt = gpd.GeoSeries.from_xy([lon], [lat], crs='EPSG:4326').iloc[0]
    states = _states(year)
    hit = states[states.contains(pt)]
    if len(hit):
        return str(hit.iloc[0]['STATEFP'])
    return None


def resolve_place(lon: float, lat: float, year: int, state_fips: str | None = None):
    """Return (place_geom_4326, info_dict) for the incorporated place containing
    the point, or (None, None) if the point falls outside any place."""
    pt = gpd.GeoSeries.from_xy([lon], [lat], crs='EPSG:4326').iloc[0]
    state_fips = state_fips or _state_fips_for_point(lon, lat, year)
    if not state_fips:
        return None, None
    places = _places(state_fips, year)
    hit = places[places.contains(pt)]
    if len(hit):
        row = hit.iloc[0]
        info = {'name': row.get('NAME'), 'placefp': str(row.get('PLACEFP', '')),
                'statefp': str(state_fips), 'namelsad': row.get('NAMELSAD', row.get('NAME')),
                'kind': 'place'}
        return hit.geometry.iloc[0], info
    # Fallback: New England towns and similar are county subdivisions (MCDs),
    # not incorporated places — try those before giving up.
    try:
        counties = _counties(year)
        cc = counties[counties.contains(pt)]
        if len(cc):
            cs = cc.iloc[0]
            subs = _county_subdivisions(cs['STATEFP'], cs['COUNTYFP'], year)
            sub_hit = subs[subs.contains(pt)]
            if len(sub_hit):
                row = sub_hit.iloc[0]
                info = {'name': row.get('NAME'), 'placefp': str(row.get('COUSUBFP', '')),
                        'statefp': str(cs['STATEFP']),
                        'namelsad': row.get('NAMELSAD', row.get('NAME')),
                        'kind': 'county_subdivision'}
                return sub_hit.geometry.iloc[0], info
    except Exception as e:
        print(f"    (MCD fallback failed: {e})")
    return None, None


def municipal_census(place_geom, year: int, api_key: str) -> dict | None:
    """Area/pop-weighted ACS summary across the block groups of one place."""
    minlon, minlat, maxlon, maxlat = place_geom.bounds
    bgs = fetch_block_groups_for_bbox((minlon, minlat, maxlon, maxlat), year=year)
    bgs = bgs[bgs.intersects(place_geom)].copy()
    if bgs.empty:
        return None
    acs = _retry(m4.fetch_acs_for_block_groups, bgs, year=year, api_key=api_key, _label='ACS')
    bgs = bgs.merge(acs, on='GEOID', how='left')

    bgs_ea = bgs.to_crs(EQUAL_AREA)
    place_ea = gpd.GeoSeries([place_geom], crs='EPSG:4326').to_crs(EQUAL_AREA).iloc[0]
    inter = bgs_ea.geometry.intersection(place_ea).area
    area = bgs_ea.geometry.area.replace(0, np.nan)
    bgs['intersection_area_ratio'] = (inter.values / area.values)
    bgs['fully_inside_bbox'] = bgs.geometry.within(place_geom).values
    return m4.compute_summary(bgs, 'municipal', 'Municipality', year), bgs


def municipal_summary_for_point(lon: float, lat: float, year: int, api_key: str,
                                state_fips: str | None = None) -> dict | None:
    """Used by the draw server: municipal profile for a drawn area's centroid."""
    place_geom, info = resolve_place(lon, lat, year, state_fips)
    if place_geom is None:
        return None
    result = municipal_census(place_geom, year, api_key)
    if not result:
        return None
    summary, _ = result
    return {'municipality': info, 'summary': summary}


# ── per-SAD processing ───────────────────────────────────────────────────────
def _sad_centroid_and_state(sad_dir: Path):
    """Centroid (lon,lat) + a STATEFP hint from existing SAD data."""
    gpkg = sad_dir / 'source' / 'census_blockgroups.gpkg'
    boundary = sad_dir / 'source' / 'sad_boundary.geojson'
    state = None
    if gpkg.exists():
        bgs = gpd.read_file(gpkg)
        if 'STATEFP' in bgs.columns and len(bgs):
            state = str(bgs.iloc[0]['STATEFP'])
    if boundary.exists():
        gj = json.loads(boundary.read_text())
        geom = shape(gj['features'][0]['geometry']) if gj.get('features') else shape(gj)
        c = geom.centroid
        return (c.x, c.y), state
    if gpkg.exists():
        c = gpd.read_file(gpkg).to_crs('EPSG:4326').unary_union.centroid
        return (c.x, c.y), state
    return None, state


def process_sad(sad_dir: Path, year: int, api_key: str) -> bool:
    # Skip non-US SADs (no ACS); detected by absence of the district census pull
    if not (sad_dir / 'source' / 'census_blockgroups.gpkg').exists():
        print(f"  [skip] {sad_dir.name}: no US census (likely Canadian)")
        return False
    cs, state = _sad_centroid_and_state(sad_dir)
    if cs is None:
        print(f"  [skip] {sad_dir.name}: no centroid")
        return False
    lon, lat = cs
    place_geom, info = resolve_place(lon, lat, year, state)
    if place_geom is None:
        print(f"  [warn] {sad_dir.name}: no incorporated place contains the centroid")
        return False
    result = municipal_census(place_geom, year, api_key)
    if not result:
        print(f"  [warn] {sad_dir.name}: no block groups for {info.get('name')}")
        return False
    summary, bgs = result

    out = {'sad_id': sad_dir.name, 'municipality': info,
           'scope_summaries': {'municipal': summary}}
    (sad_dir / 'derived').mkdir(parents=True, exist_ok=True)
    (sad_dir / 'derived' / 'census_municipal_summary.json').write_text(json.dumps(out, indent=2))
    # write-then-replace to dodge Windows file locks on the gpkg
    tmp = sad_dir / 'source' / 'census_municipal_blockgroups.tmp.gpkg'
    final = sad_dir / 'source' / 'census_municipal_blockgroups.gpkg'
    bgs.to_file(tmp, driver='GPKG', layer='blockgroups')
    os.replace(tmp, final)
    pop = summary.get('estimated_population')
    print(f"  [OK] {sad_dir.name}: {info.get('namelsad') or info.get('name')} "
          f"(pop ~{pop:,})" if pop else f"  [OK] {sad_dir.name}: {info.get('name')}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Municipal-scope census for SADs")
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sads', nargs='*', default=None)
    ap.add_argument('--api-key', default=None)
    ap.add_argument('--year', type=int, default=m4.DEFAULT_ACS_YEAR)
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get('CENSUS_API_KEY')
    if not api_key:
        sys.exit("Set CENSUS_API_KEY or pass --api-key.")
    os.environ['CENSUS_API_KEY'] = api_key

    data = args.data_dir.resolve()
    targets = ([data / s for s in args.sads] if args.sads else
               [d for d in sorted(data.iterdir())
                if d.is_dir() and d.name not in NON_SAD and not d.name.startswith('_')])
    n = 0
    for d in targets:
        try:
            if process_sad(d, args.year, api_key):
                n += 1
            time.sleep(0.4)  # be gentle with TIGER/ACS
        except SystemExit as e:
            print(f"  [error] {d.name}: {e}")
        except Exception as e:
            print(f"  [error] {d.name}: {e}")
    print(f"\nMunicipal census written for {n} SADs.")


if __name__ == '__main__':
    main()
