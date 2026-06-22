"""
build_ui_manifest.py

Walks the data/ directory, builds a manifest of all SADs and the
GeoJSON layers each one has on disk, and copies the viewer assets
into data/_ui/. The web viewer reads the manifest to populate its
SAD selector and layer toggles.

Run this:
  - Once after initial pipeline setup
  - Again whenever you add new SADs or new derived layers
  - Re-runs are idempotent

USAGE
  python build_ui_manifest.py --data-dir <data_dir>
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

# Optional geopandas for fast bbox computation. If unavailable, we
# fall back to reading the raw GeoJSON and computing bounds manually.
try:
    import geopandas as gpd
    HAVE_GPD = True
except ImportError:
    HAVE_GPD = False


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬ Layer catalog Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
# Each entry: (manifest_key, relative path, category for UI grouping)
LAYER_CATALOG = [
    # Source layers (base map)
    ('sad_boundary',  'source/sad_boundary.geojson',                'base'),
    ('buildings',     'derived/buildings_enriched.geojson',                   'base'),
    ('parking',       'source/parking.geojson',                     'base'),
    ('parks',         'source/parks.geojson',                       'base'),
    ('roads',         'source/highways.geojson',                    'base'),
    ('image_extent',  'source/image_extent.geojson',                'base'),
    # Activity
    ('pois',          'source/rod_places.geojson',                  'activity'),
    ('transit',       'derived/transit/transit_stations.geojson',   'activity'),
    ('transit_routes','derived/transit/transit_routes.geojson',     'activity'),
    # Analysis
    ('walkshed',      'derived/walkshed/walksheds.geojson', 'analysis'),
]


def compute_bbox(geojson_path: Path):
    """Return [minx, miny, maxx, maxy] from a GeoJSON, or None."""
    if HAVE_GPD:
        try:
            gdf = gpd.read_file(geojson_path)
            if gdf.empty:
                return None
            return [float(v) for v in gdf.total_bounds]
        except Exception:
            return None
    try:
        data = json.loads(geojson_path.read_text())
    except Exception:
        return None
    minx = miny = float('inf')
    maxx = maxy = float('-inf')

    def visit_coords(c):
        nonlocal minx, miny, maxx, maxy
        # Coordinate triple/pair leaf node
        if (isinstance(c, list) and len(c) >= 2
            and not isinstance(c[0], list)):
            x, y = c[0], c[1]
            if x < minx: minx = x
            if x > maxx: maxx = x
            if y < miny: miny = y
            if y > maxy: maxy = y
            return
        if isinstance(c, list):
            for it in c:
                visit_coords(it)

    for feat in data.get('features', []):
        geom = feat.get('geometry') or {}
        coords = geom.get('coordinates')
        if coords is not None:
            visit_coords(coords)
    if minx == float('inf'):
        return None
    return [minx, miny, maxx, maxy]


def load_profile(profile_path: Path) -> dict:
    if not profile_path.exists():
        return {}
    try:
        return json.loads(profile_path.read_text())
    except Exception:
        return {}


def build_sad_entry(sad_dir: Path):
    sad_id = sad_dir.name
    profile = load_profile(sad_dir / 'derived' / 'district_profile.json')
    typology = load_profile(sad_dir / 'derived' / 'typology.json')

    # Discover available layers
    layers = {}
    for key, rel, category in LAYER_CATALOG:
        p = sad_dir / rel
        if p.exists() and p.stat().st_size > 0:
            layers[key] = {
                'path': f"{sad_id}/{rel}",   # relative to data/ root
                'category': category,
            }

    # SAD boundary is the anchor for bbox computation
    bbox_path = sad_dir / 'source' / 'sad_boundary.geojson'
    sad_bbox = compute_bbox(bbox_path) if bbox_path.exists() else None
    extent_bbox = None
    ext = sad_dir / 'source' / 'image_extent.geojson'
    if ext.exists():
        extent_bbox = compute_bbox(ext)

    entry = {
        'sad_id':      sad_id,
        'sad_name':    profile.get('sad_name', sad_id),
        'anchor_venue': profile.get('anchor_venue',
                                      typology.get('anchor_venue', '')),
        'primary_typology':   typology.get('primary_typology', ''),
        'secondary_typology': typology.get('secondary_typology', ''),
        'sad_bbox':    sad_bbox,
        'extent_bbox': extent_bbox,
        'layers':      layers,
    }
    return entry


def copy_viewer_assets(code_dir: Path, ui_dir: Path):
    """Copy code/viewer/*.{html,css,js} into data/_ui/."""
    src_viewer = code_dir / 'viewer'
    if not src_viewer.exists():
        print(f"[warn] viewer assets folder not found at {src_viewer}")
        print(f"[warn] skipping copy. UI will not work until you")
        print(f"[warn] place index.html, viewer.css, viewer.js into {ui_dir}/")
        return
    for fname in ('index.html', 'viewer.css', 'viewer.js', 'viewer_modules.js',
    'viewer_census_timeseries.js', 'viewer_parcels.js', 'viewer_integration.js',
    'viewer_export_legends.js', 'viewer_export_pdf.js', 'viewer_export_shrink.js',
    'logo.png'):
        src = src_viewer / fname
        if not src.exists():
            print(f"[warn] missing {src} -- skipping")
            continue
        dst = ui_dir / fname
        shutil.copyfile(src, dst)
        print(f"  copied {fname}")

    # Optional Synthesis nav bridge: copy it and auto-inject the <script> tag
    # into the viewer's index.html so Map/Field/Viewer navigation + ?sad deep
    # links work without hand-editing HTML.
    # viewer_integration.js is already copied in the loop above. Here we only
    # ensure index.html actually loads it (auto-inject if a hand-edited
    # index.html is missing the tag).
    integ = src_viewer / 'viewer_integration.js'
    if integ.exists():
        idx = ui_dir / 'index.html'
        if idx.exists():
            html = idx.read_text(encoding='utf-8')
            if 'viewer_integration.js' not in html:
                tag = '  <script src="viewer_integration.js"></script>\n'
                if '</body>' in html:
                    html = html.replace('</body>', tag + '</body>')
                else:
                    html = html + '\n' + tag
                idx.write_text(html, encoding='utf-8')
                print("  injected viewer_integration.js into index.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--code-dir', type=Path, default=None,
                    help='Path to code/ folder containing viewer/ '
                         '(defaults to script\'s parent directory)')
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    if not data_dir.exists():
        sys.exit(f"data dir does not exist: {data_dir}")

    code_dir = args.code_dir.resolve() if args.code_dir \
                else Path(__file__).parent.resolve()

    # Discover SADs (directories starting with a number)
    sad_dirs = sorted([d for d in data_dir.iterdir()
                        if d.is_dir() and not d.name.startswith('_')
                        and (d.name[0:2].isdigit()
                              or '_' in d.name)])
    print(f"Found {len(sad_dirs)} SAD directories")

    entries = []
    for d in sad_dirs:
        try:
            e = build_sad_entry(d)
            if e['sad_bbox']:    # only include SADs that have at least a boundary
                entries.append(e)
                print(f"  + {e['sad_name']} ({len(e['layers'])} layers)")
            else:
                print(f"  - {d.name} (no SAD boundary, skipped)")
        except Exception as ex:
            print(f"  ! {d.name} -- {ex}")

    # Build manifest
    manifest = {
        'generated_at': dt.datetime.utcnow().isoformat() + 'Z',
        'n_sads':       len(entries),
        'sads':         entries,
        'layer_catalog': [
            {'key': k, 'path': rel, 'category': cat}
            for k, rel, cat in LAYER_CATALOG
        ],
    }

    ui_dir = data_dir / '_ui'
    ui_dir.mkdir(exist_ok=True)
    manifest_path = ui_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {manifest_path}")

    copy_viewer_assets(code_dir, ui_dir)
    print(f"\nDone.")


if __name__ == '__main__':
    main()


