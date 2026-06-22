"""
export_census_geojson.py

Converts each SAD's census_blockgroups.gpkg (written by Module 4) into a
browser-ready GeoJSON for the comparison tool's census-area map drill-down.

For each block group it carries the raw ACS measures plus the derived
percentages the map shades by (renter share, bachelor's+, unemployment, race
shares), computed per block group from the underlying counts. Geometry is
reprojected to EPSG:4326 and lightly rounded; only the properties the map needs
are kept, so files stay small.

This is additive — it reads the gpkg and writes a sibling GeoJSON; nothing
upstream is touched.

USAGE
  python export_census_geojson.py --data-dir ..\\data

OUTPUT (per SAD that has the gpkg)
  data/<sad>/derived/census_blockgroups.geojson
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import geopandas as gpd

NON_SAD = {'_ui', '_comparisons', '_compare_ui', 'source', 'derived'}

# raw measures kept as-is (rounded), if present
RAW_INT = ['total_pop', 'median_household_income', 'median_home_value', 'median_gross_rent']
RAW_F1 = ['median_age']


def safe_ratio(num, den):
    """Element-wise 100*num/den with zero/NaN guarded -> NaN."""
    num = num.astype(float); den = den.astype(float)
    out = np.where((den > 0) & np.isfinite(den) & np.isfinite(num), 100.0 * num / den, np.nan)
    return out


def derive(bgs: gpd.GeoDataFrame) -> dict:
    """Compute per-block-group percentages from the raw ACS count columns."""
    cols = bgs.columns
    d = {}
    if {'renter_occupied', 'occupied_total'} <= set(cols):
        d['pct_renter'] = safe_ratio(bgs['renter_occupied'], bgs['occupied_total'])
    if 'edu_total_pop_25plus' in cols:
        edu = sum(bgs[c] for c in ['edu_bachelors', 'edu_masters', 'edu_professional', 'edu_doctorate'] if c in cols)
        d['pct_bachelors'] = safe_ratio(edu, bgs['edu_total_pop_25plus'])
    if {'unemployed', 'labor_force'} <= set(cols):
        d['unemployment_rate'] = safe_ratio(bgs['unemployed'], bgs['labor_force'])
    if 'race_total' in cols:
        for src, name in [('race_white', 'pct_white'), ('race_black', 'pct_black'),
                          ('race_asian', 'pct_asian'), ('hispanic', 'pct_hispanic')]:
            if src in cols:
                d[name] = safe_ratio(bgs[src], bgs['race_total'])
    return d


def jnum(v, nd=None):
    """JSON-safe number: None for NaN/inf, optionally rounded."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return round(f, nd) if nd is not None else (round(f) if nd == 0 else f)


def export_one(sad_dir: Path) -> bool:
    gpkg = sad_dir / 'source' / 'census_blockgroups.gpkg'
    if not gpkg.exists():
        return False
    bgs = gpd.read_file(gpkg).to_crs('EPSG:4326')
    derived = derive(bgs)

    feats = []
    for i, (_, row) in enumerate(bgs.iterrows()):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {
            'GEOID': str(row.get('GEOID', '')),
            'zone': row.get('zone', 'unknown'),
            'ratio': jnum(row.get('intersection_area_ratio'), 3),
        }
        for c in RAW_INT:
            if c in bgs.columns:
                props[c] = (round(v) if (v := jnum(row.get(c))) is not None else None)
        for c in RAW_F1:
            if c in bgs.columns:
                props[c] = jnum(row.get(c), 1)
        for name, arr in derived.items():
            props[name] = jnum(arr[i], 1)
        feats.append({'type': 'Feature',
                      'geometry': json.loads(gpd.GeoSeries([geom]).to_json())['features'][0]['geometry'],
                      'properties': props})

    fc = {'type': 'FeatureCollection', 'features': feats}
    out = sad_dir / 'derived' / 'census_blockgroups.geojson'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc))
    print(f"  [OK] {sad_dir.name}: {len(feats)} block groups -> {out.name}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Export census block groups to GeoJSON for the map drill-down")
    ap.add_argument('--data-dir', type=Path, required=True)
    args = ap.parse_args()
    data = args.data_dir.resolve()
    n = 0
    for d in sorted(data.iterdir()):
        if not d.is_dir() or d.name in NON_SAD or d.name.startswith('_'):
            continue
        if export_one(d):
            n += 1
    print(f"\nExported census GeoJSON for {n} SADs.")


if __name__ == '__main__':
    main()
