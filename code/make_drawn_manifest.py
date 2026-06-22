"""
make_drawn_manifest.py

Write a schema-valid derived/manifest.json for a map-drawn district WITHOUT
running Module 1's image generator. Drawn districts arrive as real OSM geometry
(save_district wrote source/sad_boundary.geojson, source/image_extent.geojson,
source/buildings.geojson, source/extent.json), so the georeferencing manifest
that downstream modules (M3, M4, M20, M21, M22) read can be computed directly
from those files.

This unblocks the metadata-only modules for drawn districts. The CV/buildings
chain (M2 -> M5) is separate and still uses real OSM buildings.

USAGE
  # one district
  python make_drawn_manifest.py --data-dir <data> --sad 43_Drawn-district_Ann-Arbor
  # every drawn district missing a manifest (has source/extent.json, no derived/manifest.json)
  python make_drawn_manifest.py --data-dir <data> --all-drawn
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

import geopandas as gpd
from shapely.geometry import shape


def _bounds(path: Path):
    gj = json.loads(path.read_text())
    feats = gj.get('features') or []
    if not feats:
        return None
    geoms = [shape(f['geometry']) for f in feats if f.get('geometry')]
    if not geoms:
        return None
    minx = min(g.bounds[0] for g in geoms)
    miny = min(g.bounds[1] for g in geoms)
    maxx = max(g.bounds[2] for g in geoms)
    maxy = max(g.bounds[3] for g in geoms)
    return (minx, miny, maxx, maxy)


def _utm_epsg(lon: float, lat: float) -> str:
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _count_features(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len((json.loads(path.read_text()).get('features') or []))
    except Exception:
        return 0


def write_drawn_manifest(sad_dir: Path, image_px: int = 1080) -> Path | None:
    """Build derived/manifest.json from the drawn district's source files.
    Returns the manifest path, or None if the boundary is missing."""
    sad_id = sad_dir.name
    source = sad_dir / 'source'
    derived = sad_dir / 'derived'

    extent_path = source / 'image_extent.geojson'
    bnd_path = source / 'sad_boundary.geojson'
    bbox = _bounds(extent_path) if extent_path.exists() else None
    if bbox is None:
        bbox = _bounds(bnd_path) if bnd_path.exists() else None
    if bbox is None:
        return None
    minlon, minlat, maxlon, maxlat = bbox

    cx, cy = (minlon + maxlon) / 2.0, (minlat + maxlat) / 2.0
    crs_metric = _utm_epsg(cx, cy)

    # nominal side length in meters: project the bbox to the metric CRS
    try:
        g = gpd.GeoDataFrame(
            geometry=[shape({'type': 'Polygon', 'coordinates': [[
                [minlon, minlat], [maxlon, minlat],
                [maxlon, maxlat], [minlon, maxlat], [minlon, minlat]]]})],
            crs='EPSG:4326').to_crs(crs_metric)
        b = g.total_bounds
        extent_meters = float(max(b[2] - b[0], b[3] - b[1]))
    except Exception:
        # rough fallback: degrees -> meters at this latitude
        extent_meters = float(max((maxlon - minlon) * 111320 * math.cos(math.radians(cy)),
                                  (maxlat - minlat) * 110540))

    # pixel<-geo affine (rasterio order a,b,c,d,e,f): x = a*px+b*py+c, y = d*px+e*py+f
    a = (maxlon - minlon) / image_px
    e = -(maxlat - minlat) / image_px
    affine = (a, 0.0, minlon, 0.0, e, maxlat)

    # name / typology / anchor from what we have on disk
    extent_json = {}
    if (source / 'extent.json').exists():
        try:
            extent_json = json.loads((source / 'extent.json').read_text())
        except Exception:
            extent_json = {}
    sad_name = extent_json.get('name') or sad_id
    typ_json = {}
    if (derived / 'typology.json').exists():
        try:
            typ_json = json.loads((derived / 'typology.json').read_text())
        except Exception:
            typ_json = {}
    typology = typ_json.get('primary_typology') or 'unspecified'
    anchor_venue = typ_json.get('anchor_venue') or ''

    building_count = _count_features(source / 'buildings.geojson')

    manifest = {
        'sad_id': sad_id,
        'sad_name': sad_name,
        'typology': typology,
        'anchor_venue': anchor_venue,
        'bbox_geo': [minlon, minlat, maxlon, maxlat],
        'crs_source': 'EPSG:4326',
        'crs_metric': crs_metric,
        'image_width_px': image_px,
        'image_height_px': image_px,
        'extent_meters': round(extent_meters, 1),
        'affine_geo_to_pixel': list(affine),
        'building_count': building_count,
        'source': 'make_drawn_manifest (OSM-drawn district, no M1 image)',
    }
    derived.mkdir(parents=True, exist_ok=True)
    out = derived / 'manifest.json'
    out.write_text(json.dumps(manifest, indent=2))
    return out


def is_drawn(sad_dir: Path) -> bool:
    return (sad_dir / 'source' / 'extent.json').exists()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sad', default=None, help='one sad_id')
    ap.add_argument('--all-drawn', action='store_true',
                    help='every drawn district (has source/extent.json) lacking derived/manifest.json')
    ap.add_argument('--force', action='store_true', help='overwrite existing manifest.json')
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    targets = []
    if args.sad:
        targets = [data_dir / args.sad]
    elif args.all_drawn:
        for d in sorted(data_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('_') and is_drawn(d):
                if args.force or not (d / 'derived' / 'manifest.json').exists():
                    targets.append(d)
    else:
        ap.error('give --sad or --all-drawn')

    n = 0
    for d in targets:
        if not d.exists():
            print(f"  ! {d.name} not found"); continue
        if (d / 'derived' / 'manifest.json').exists() and not args.force and args.sad:
            print(f"  - {d.name}: manifest.json exists (use --force to overwrite)"); continue
        out = write_drawn_manifest(d)
        if out:
            m = json.loads(out.read_text())
            print(f"  + {d.name}: {m['crs_metric']} · {m['extent_meters']}m · "
                  f"{m['building_count']} buildings -> {out}")
            n += 1
        else:
            print(f"  ! {d.name}: no boundary/extent geometry, skipped")
    print(f"\nwrote {n} manifest(s)")


if __name__ == '__main__':
    main()
