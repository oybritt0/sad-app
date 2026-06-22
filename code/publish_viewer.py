"""
publish_viewer.py

Build a self-contained static site for hosting the SAD viewer on a free static
host (Cloudflare Pages / GitHub Pages / Render static). Produces a layout that
matches what the viewer expects: it fetches `'../' + info.path`, so viewer assets
live in _site/_ui/ and district data at _site/<sad_id>/..., making '../<sad_id>/'
resolve correctly. The published manifest.json matches build_ui_manifest.py's
schema exactly (generated_at, n_sads, sads[], layer_catalog) so viewer.js parses
it unchanged.

Copies ONLY the layers the viewer fetches (not the 2-3x redundant source/ +
01_GeoJSONs/ copies) and simplifies geometry per layer type for web delivery.

Per-layer simplification (shrink hard where invisible, preserve morphology):
  buildings      -> coord precision 6dp (~0.1 m); KEEP all vertices
  roads/routes   -> precision + light line simplify (invisible at zoom)
  walkshed       -> precision + light simplify
  points         -> precision only
  boundary/extent/parking/parks -> precision only, keep vertices

USAGE
    python publish_viewer.py --data-dir <DATA> [--out <DATA>\\_site]
      [--precision 6] [--simplify-m 2.0] [--no-simplify]

Then host the _site/ folder. The viewer entry point is _site/_ui/index.html.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import shutil
import sys
from pathlib import Path

import geopandas as gpd
from shapely import set_precision

LAYERS = [
    ('sad_boundary',  'source/sad_boundary.geojson',              'base'),
    ('buildings',     'derived/buildings_enriched.geojson',       'base'),
    ('parking',       'source/parking.geojson',                   'base'),
    ('parks',         'source/parks.geojson',                     'base'),
    ('roads',         'source/highways.geojson',                  'base'),
    ('image_extent',  'source/image_extent.geojson',              'base'),
    ('pois',          'source/rod_places.geojson',                'activity'),
    ('transit',       'derived/transit/transit_stations.geojson', 'activity'),
    ('transit_routes','derived/transit/transit_routes.geojson',   'activity'),
    ('walkshed',      'derived/walkshed/walksheds.geojson',       'analysis'),
    # extra layers used by map/field mode and the side-panel modules
    ('census_blockgroups', 'derived/census_blockgroups.geojson',  'analysis'),
    ('heat_grid',          'derived/environment/heat_grid.geojson','analysis'),
    ('jobs_blocks',        'derived/jobs/jobs_blocks.geojson',     'analysis'),
    ('transit_los_stops',  'derived/transit_los/transit_los_stops.geojson', 'analysis'),
    ('census_bg_2010',     'source/census_blockgroups_2010.geojson','analysis'),
    ('census_bg_2020',     'source/census_blockgroups_2020.geojson','analysis'),
]
# small per-district JSON sidecars copied as-is (no geometry)
EXTRA_JSON = [
    'derived/census_timeseries.json',
    'derived/jobs/jobs_timeseries.json',
    'derived/jobs/jobs_summary.json',
    'derived/transit_los/transit_los_summary.json',
    'derived/environment/environment_summary.json',
]
SIMPLIFY_LINES = {'roads', 'transit_routes'}
SIMPLIFY_POLYS = {'walkshed', 'census_blockgroups', 'heat_grid', 'jobs_blocks', 'census_bg_2010', 'census_bg_2020'}
KEEP_VERTICES  = {'buildings', 'sad_boundary', 'image_extent', 'parking', 'parks'}
POINTS         = {'pois', 'transit', 'transit_los_stops'}


def deg_tol(metres, lat):
    return metres / (111_320.0 * max(math.cos(math.radians(lat)), 0.1))


def process_layer(src_path, key, precision, simplify_m):
    gdf = gpd.read_file(src_path)
    if gdf.empty:
        return '{"type":"FeatureCollection","features":[]}', 0
    if gdf.crs is not None and (gdf.crs.to_epsg() or 4326) != 4326:
        gdf = gdf.to_crs(4326)
    if simplify_m and simplify_m > 0 and (key in SIMPLIFY_LINES or key in SIMPLIFY_POLYS):
        lat = float(gdf.total_bounds[1])
        gdf['geometry'] = gdf.geometry.simplify(deg_tol(simplify_m, lat), preserve_topology=True)
    grid = 10 ** (-precision)
    gdf['geometry'] = gdf.geometry.apply(lambda g: set_precision(g, grid) if g is not None else g)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    if gdf.empty:
        return '{"type":"FeatureCollection","features":[]}', 0
    return gdf.to_json(drop_id=True), len(gdf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--out', type=Path, default=None)
    ap.add_argument('--precision', type=int, default=6)
    ap.add_argument('--simplify-m', type=float, default=2.0)
    ap.add_argument('--no-simplify', action='store_true')
    args = ap.parse_args()

    data_dir = args.data_dir
    out = args.out or (data_dir / '_site')
    ui_src = data_dir / '_ui'
    if not ui_src.exists():
        sys.exit(f"_ui not found at {ui_src}; run build_ui_manifest.py first.")
    simplify_m = 0 if args.no_simplify else args.simplify_m

    if out.exists():
        shutil.rmtree(out)
    ui_dst = out / '_ui'
    ui_dst.mkdir(parents=True, exist_ok=True)

    # copy viewer assets into _site/_ui/ (so '../<sad>/...' resolves from there)
    for f in ui_src.iterdir():
        if f.is_file() and f.suffix.lower() in {'.html', '.js', '.css', '.png', '.ico', '.svg'}:
            shutil.copy2(f, ui_dst / f.name)

    # copy whole-tree directories that field/map mode + shared modules need.
    # These are small and self-contained; copy as-is (no simplification).
    for d in ('_compare_ui', '_shared'):
        srcd = data_dir / d
        if srcd.exists():
            shutil.copytree(srcd, out / d, dirs_exist_ok=True)
            print(f"  copied {d}/")
    # place-card PNGs referenced by compare_manifest artifacts
    comp = data_dir / '_comparisons' / 'place_cards'
    if comp.exists():
        dst = out / '_comparisons' / 'place_cards'
        dst.mkdir(parents=True, exist_ok=True)
        for png in comp.glob('*.png'):
            shutil.copy2(png, dst / png.name)
        print(f"  copied _comparisons/place_cards/ ({len(list(comp.glob('*.png')))} cards)")

    # load the real manifest to preserve per-sad metadata exactly
    real_manifest = json.loads((ui_src / 'manifest.json').read_text())
    real_by_id = {s['sad_id']: s for s in real_manifest.get('sads', [])}

    sad_dirs = sorted([p for p in data_dir.iterdir()
                       if p.is_dir() and (p / 'derived' / 'manifest.json').exists()])

    in_bytes = out_bytes = 0
    out_bytes_extra = [0]
    sads_out = []
    for sd in sad_dirs:
        name = sd.name
        base_meta = real_by_id.get(name)
        if base_meta is None:
            # not in the built manifest (e.g. never had build_ui_manifest run); skip
            print(f"  - {name}: not in _ui manifest, skipping")
            continue
        layers_meta = {}
        for key, rel, group in LAYERS:
            src_path = sd / Path(rel)
            if not src_path.exists():
                continue
            in_bytes += src_path.stat().st_size
            try:
                txt, n = process_layer(src_path, key, args.precision, simplify_m)
            except Exception as e:
                print(f"  ! {name}/{key}: {e}")
                continue
            dst = out / name / Path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(txt)
            out_bytes += len(txt.encode('utf-8'))
            # path format identical to build_ui_manifest: '<sad>/<rel>'
            layers_meta[key] = {'category': group, 'path': f'{name}/{rel}'}
        # copy small JSON sidecars (census timeseries, jobs/env/transit summaries)
        for rel in EXTRA_JSON:
            sp = sd / Path(rel)
            if sp.exists():
                dp = out / name / Path(rel)
                dp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sp, dp)
                out_bytes_extra[0] += sp.stat().st_size

        # clone the real per-sad metadata, swap in the rebuilt layers dict
        s_out = dict(base_meta)
        s_out['layers'] = layers_meta
        sads_out.append(s_out)
        print(f"  + {name}: {len(layers_meta)} layers")

    manifest = {
        'generated_at': dt.datetime.now().isoformat(timespec='seconds'),
        'n_sads': len(sads_out),
        'sads': sads_out,
        'layer_catalog': real_manifest.get('layer_catalog', {}),
    }
    (ui_dst / 'manifest.json').write_text(json.dumps(manifest, indent=0))

    print(f"\nwrote {ui_dst / 'manifest.json'}  ({len(sads_out)} districts)")
    print(f"input layers : {in_bytes/1024/1024:,.1f} MB")
    print(f"published    : {out_bytes/1024/1024:,.1f} MB  ({100*out_bytes/max(in_bytes,1):.0f}% of input)")
    total = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    print(f"_site total  : {total/1024/1024:,.1f} MB")
    print(f"\nViewer entry point: {ui_dst / 'index.html'}")
    print("Host the entire _site/ folder. Open _site/_ui/index.html as the start page.")


if __name__ == '__main__':
    main()
