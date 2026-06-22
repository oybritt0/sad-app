"""
make_drawn_buildings.py

Build derived/buildings_enriched.gpkg (layer 'buildings') for a map-drawn
district directly from its OSM building footprints — bypassing the M1 image ->
M2 CV -> M5 join path, which doesn't apply to OSM-sourced geometry.

WHY
  The cross-SAD embedding (M8) excludes any district missing
  buildings_enriched.gpkg. M8's morphology reader needs only geometry + an
  'area_m2' column (perimeter_m / orientation_deg / cluster_id are optional with
  fallbacks). Drawn districts already have real building polygons in
  source/buildings.geojson, so we compute the morphometrics directly and write
  the gpkg in the shape M8 / M6* read.

  This is the honest path for drawn districts: real footprints, real
  morphometrics — no synthetic CV-from-image step.

USAGE
  python make_drawn_buildings.py --data-dir <data> --sad 43_Drawn-district_Ann-Arbor
  python make_drawn_buildings.py --data-dir <data> --all-drawn
"""
from __future__ import annotations
import argparse
import math
from pathlib import Path

import numpy as np
import geopandas as gpd
from shapely.geometry import shape


def _orientation_deg(geom) -> float:
    """Angle (0-180) of the longest edge of the minimum rotated rectangle."""
    try:
        mrr = geom.minimum_rotated_rectangle
        xs, ys = mrr.exterior.coords.xy
        best_len, best_ang = 0.0, 0.0
        for i in range(len(xs) - 1):
            dx, dy = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
            L = math.hypot(dx, dy)
            if L > best_len:
                best_len = L
                best_ang = math.degrees(math.atan2(dy, dx)) % 180.0
        return best_ang
    except Exception:
        return 0.0


def write_drawn_buildings(sad_dir: Path) -> Path | None:
    """Compute morphometrics from OSM footprints and write
    derived/buildings_enriched.gpkg (layer 'buildings'). Returns the path."""
    src = sad_dir / 'source' / 'buildings.geojson'
    if not src.exists() or src.stat().st_size == 0:
        return None
    gdf = gpd.read_file(src)
    if gdf.empty:
        return None
    gdf = gdf[gdf.geometry.type.isin(['Polygon', 'MultiPolygon'])].copy()
    if gdf.empty:
        return None
    if gdf.crs is None:
        gdf = gdf.set_crs('EPSG:4326')

    metric = gdf.estimate_utm_crs()
    gm = gdf.to_crs(metric)
    gdf = gdf.reset_index(drop=True)
    gm = gm.reset_index(drop=True)

    gdf['building_id'] = range(len(gdf))
    gdf['area_m2'] = gm.geometry.area.values
    gdf['perimeter_m'] = gm.geometry.length.values
    with np.errstate(divide='ignore', invalid='ignore'):
        comp = 4 * np.pi * gdf['area_m2'].values / np.square(gdf['perimeter_m'].values)
    gdf['compactness'] = np.clip(np.nan_to_num(comp), 0, 1)
    gdf['orientation_deg'] = [_orientation_deg(g) for g in gm.geometry]

    # cluster_id: light KMeans on (log area, compactness) so cluster_diversity
    # isn't degenerate. Falls back to a single cluster if sklearn is absent or
    # there are too few buildings.
    gdf['cluster_id'] = 0
    try:
        from sklearn.cluster import KMeans
        n = len(gdf)
        if n >= 8:
            X = np.column_stack([
                np.log1p(gdf['area_m2'].values),
                gdf['compactness'].values,
            ])
            X = (X - X.mean(0)) / (X.std(0) + 1e-9)
            k = min(4, max(2, n // 20))
            gdf['cluster_id'] = KMeans(n_clusters=k, n_init=5,
                                       random_state=0).fit_predict(X)
    except Exception:
        pass

    keep = ['building_id', 'area_m2', 'perimeter_m', 'compactness',
            'orientation_deg', 'cluster_id', 'geometry']
    out_gdf = gdf[keep].copy()

    out = sad_dir / 'derived' / 'buildings_enriched.gpkg'
    out.parent.mkdir(parents=True, exist_ok=True)
    out_gdf.to_file(out, driver='GPKG', layer='buildings')
    return out


def is_drawn(sad_dir: Path) -> bool:
    return (sad_dir / 'source' / 'extent.json').exists()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sad', default=None)
    ap.add_argument('--all-drawn', action='store_true')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    targets = []
    if args.sad:
        targets = [data_dir / args.sad]
    elif args.all_drawn:
        for d in sorted(data_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('_') and is_drawn(d):
                if args.force or not (d / 'derived' / 'buildings_enriched.gpkg').exists():
                    targets.append(d)
    else:
        ap.error('give --sad or --all-drawn')

    n = 0
    for d in targets:
        if not d.exists():
            print(f"  ! {d.name} not found"); continue
        out = write_drawn_buildings(d)
        if out:
            g = gpd.read_file(out, layer='buildings')
            print(f"  + {d.name}: {len(g)} buildings, "
                  f"median {g['area_m2'].median():.0f} m\u00b2, "
                  f"{g['cluster_id'].nunique()} cluster(s) -> {out}")
            n += 1
        else:
            print(f"  ! {d.name}: no usable source/buildings.geojson, skipped")
    print(f"\nwrote {n} buildings_enriched.gpkg")


if __name__ == '__main__':
    main()
