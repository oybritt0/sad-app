r"""
boundary_update.py

Pull updated SAD boundaries from the server drive into the local pipeline data
tree, safely, and auto-manage the canvas extent.

WHAT IT DOES (per non-drawn district)
    1. Finds the NEWEST .geojson in the server's 02_SAD-Boundary folder (names
       are inconsistent, so newest-modified wins).
    2. Reprojects it to EPSG:4326 if needed. A file with NO CRS is refused
       (reported, never written) -- we don't guess projections.
    3. Compares it to the current pipeline boundary
       (source/sad_boundary.geojson) and classifies the district:
         - identical          -> nothing to do
         - LIGHT (fits canvas) -> new boundary is inside the existing canvas.
                                  Overwrite the boundary, keep the canvas.
                                  Only boundary-dependent modules need re-run.
         - HEAVY (spills)      -> new boundary extends past the existing canvas.
                                  Overwrite the boundary AND regenerate the
                                  canvas (image_extent.geojson) from the new
                                  boundary. Source layers must be re-pulled.
    4. Backs up whatever it overwrites (sad_boundary_prev.geojson,
       image_extent_prev.geojson) before writing.

CANVAS REGENERATION (heavy districts)
    Mirrors setup_sad_source.make_image_extent: a true geographic square built
    in UTM and reprojected to 4326, centered on the new boundary's bbox center,
    with side = max(--extent-meters, boundary_long_edge * --margin-factor). The
    floor keeps small districts at the standard canvas size; the margin keeps
    the boundary comfortably inside. image_extent.geojson keeps the same schema
    as the pipeline (sad_id, extent_meters, center_lat, center_lon, utm_crs).

USAGE
    python boundary_update.py --data-dir "<data>" --boundaries-root "<server>" --dry-run
    python boundary_update.py --data-dir "<data>" --boundaries-root "<server>"
"""
from __future__ import annotations
import argparse
import datetime as dt
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box
from shapely.ops import transform as shp_transform
from pyproj import Transformer


def utm_epsg_for(lat: float, lon: float) -> str:
    zone = int((lon + 180) / 6) + 1
    return f"EPSG:{32600 + zone}" if lat >= 0 else f"EPSG:{32700 + zone}"


def regen_canvas_for(boundary_wgs, sad_id: str, extent_floor_m: float,
                     margin_factor: float):
    """Build a fresh image_extent GeoDataFrame sized to contain the boundary."""
    minx, miny, maxx, maxy = boundary_wgs.bounds
    c_lat = (miny + maxy) / 2.0
    c_lon = (minx + maxx) / 2.0
    utm = utm_epsg_for(c_lat, c_lon)
    b_metric = gpd.GeoSeries([boundary_wgs], crs='EPSG:4326').to_crs(utm).iloc[0]
    bx0, by0, bx1, by1 = b_metric.bounds
    long_edge = max(bx1 - bx0, by1 - by0)
    side = max(extent_floor_m, long_edge * margin_factor)
    bcx, bcy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
    to_geo = Transformer.from_crs(utm, "EPSG:4326", always_xy=True).transform
    half = side / 2.0
    square_geo = shp_transform(to_geo, box(bcx - half, bcy - half,
                                           bcx + half, bcy + half))
    cc = square_geo.centroid
    extent_gdf = gpd.GeoDataFrame(
        [{'sad_id': sad_id, 'extent_meters': side,
          'center_lat': cc.y, 'center_lon': cc.x, 'utm_crs': utm,
          'geometry': square_geo}],
        crs='EPSG:4326')
    return extent_gdf, side


def _union(gdf):
    return gdf.union_all() if hasattr(gdf, 'union_all') else gdf.unary_union


_BOUNDARY_PROP_KEYS = ('id', 'sad_id', 'sad_name', 'typology', 'anchor_venue')


def _load_boundary_props(sad_dir: Path, fallback_name: str) -> dict:
    """Recover the identity properties M1 needs (sad_id, sad_name, typology,
    anchor_venue). Priority: the _prev backup (the pristine original), then the
    current on-disk boundary, then a minimal fallback keyed to the folder name.
    A property source 'counts' only if it actually carries sad_id."""
    src = sad_dir / 'source'
    for cand in (src / 'sad_boundary_prev.geojson', src / 'sad_boundary.geojson'):
        if not cand.exists():
            continue
        try:
            g = gpd.read_file(cand)
        except Exception:
            continue
        if g.empty or 'sad_id' not in g.columns:
            continue
        row = g.iloc[0]
        if row.get('sad_id') in (None, '', float('nan')):
            continue
        return {k: (row[k] if k in g.columns else None) for k in _BOUNDARY_PROP_KEYS}
    # fallback: derive a sad_id from the folder name; leave the rest blank-ish
    return {'id': None, 'sad_id': fallback_name, 'sad_name': fallback_name,
            'typology': 'unspecified', 'anchor_venue': 'unknown'}


def _build_boundary_gdf(geom, props: dict):
    """One feature, dissolved geometry, identity props attached -- exactly the
    shape M1 expects (1 feature carrying sad_id/sad_name/typology/anchor_venue)."""
    rec = dict(props)
    rec['geometry'] = geom
    return gpd.GeoDataFrame([rec], crs='EPSG:4326')


def _read_wgs84(path: Path):
    try:
        g = gpd.read_file(path)
    except Exception as e:
        return None, f'unreadable: {e}'
    if g.empty:
        return None, 'empty'
    if g.crs is None:
        return None, 'no CRS (refusing to guess) -- re-export with projection'
    if str(g.crs).upper() != 'EPSG:4326':
        g = g.to_crs('EPSG:4326')
    return g, None


def find_server_boundary(boundaries_root: Path, sad_name: str):
    candidates = []
    exact = boundaries_root / sad_name
    if exact.is_dir():
        candidates.append(exact)
    else:
        prefix = sad_name.split('_', 1)[0]
        candidates += [d for d in boundaries_root.iterdir()
                       if d.is_dir() and d.name.split('_', 1)[0] == prefix]
    for folder in candidates:
        for base in (folder / '01_GeoJSONs', folder):
            if not base.is_dir():
                continue
            for sub in base.iterdir():
                if sub.is_dir() and sub.name.startswith('02') \
                   and 'sad-boundary' in sub.name.lower():
                    hits = list(sub.glob('*.geojson'))
                    if hits:
                        return max(hits, key=lambda f: f.stat().st_mtime)
    return None


def process(sad_dir: Path, server_file: Path, dry_run: bool,
            extent_floor_m: float, margin_factor: float,
            force_heavy: bool = False, canvas_only: bool = False) -> dict:
    name = sad_dir.name
    src = sad_dir / 'source'
    cur_b = src / 'sad_boundary.geojson'
    cur_e = src / 'image_extent.geojson'
    r = {'sad': name, 'tier': '', 'status': '', 'change_pct': None,
         'new_canvas_m': None}

    new, err = _read_wgs84(server_file)
    if err:
        r['status'] = f'ERROR {err}'
        return r
    if not new.geometry.is_valid.all():
        new = new.copy()
        new['geometry'] = new.geometry.buffer(0)
    new_geom = _union(new)

    # ── canvas-only: regenerate the canvas from the CURRENT on-disk boundary,
    # regardless of whether the server boundary differs. For districts whose
    # boundary was already applied but whose canvas still needs re-centering.
    if canvas_only:
        if not cur_b.exists():
            r['tier'] = 'error'; r['status'] = 'ERROR no current boundary to size canvas from'
            return r
        curb, cerr = _read_wgs84(cur_b)
        if cerr:
            r['tier'] = 'error'; r['status'] = f'ERROR current boundary {cerr}'
            return r
        cur_bgeom = _union(curb)
        sad_id_c = curb['sad_id'].iloc[0] if 'sad_id' in curb.columns else name
        ext_gdf, side = regen_canvas_for(cur_bgeom, sad_id_c, extent_floor_m, margin_factor)
        r['tier'] = 'heavy'; r['new_canvas_m'] = round(side)
        if dry_run:
            r['status'] = f'CANVAS-ONLY would regen canvas to {round(side)}m square'
            return r
        if cur_e.exists():
            bke = src / 'image_extent_prev.geojson'
            if not bke.exists():
                gpd.read_file(cur_e).to_file(bke, driver='GeoJSON')
        ext_gdf.to_file(cur_e, driver='GeoJSON')
        r['status'] = f'CANVAS-ONLY regenerated canvas to {round(side)}m square'
        return r

    if not cur_b.exists():
        r['tier'] = 'heavy'
        r['status'] = 'no current boundary'
    else:
        cur, cerr = _read_wgs84(cur_b)
        if cerr:
            r['status'] = f'ERROR current boundary {cerr}'
            return r
        cur_geom = _union(cur)
        a = gpd.GeoSeries([cur_geom], crs='EPSG:4326').to_crs(3857).iloc[0]
        b = gpd.GeoSeries([new_geom], crs='EPSG:4326').to_crs(3857).iloc[0]
        if a.equals(b):
            r['tier'] = 'identical'
            r['status'] = 'identical (no change)'
            r['change_pct'] = 0.0
            return r
        r['change_pct'] = round(100 * a.symmetric_difference(b).area / a.area, 1) \
            if a.area else None

    canvas_geom = None
    if cur_e.exists():
        cv, cverr = _read_wgs84(cur_e)
        if not cverr:
            canvas_geom = _union(cv)
    fits = (canvas_geom is not None) and new_geom.within(canvas_geom.buffer(1e-9))
    if force_heavy:
        fits = False   # caller forced a canvas regen for this district
    if r['tier'] != 'heavy':
        r['tier'] = 'light' if fits else 'heavy'

    sad_id = new['sad_id'].iloc[0] if 'sad_id' in new.columns else name

    if dry_run:
        if r['tier'] == 'light':
            r['status'] = f"LIGHT - fits canvas (boundary changed {r['change_pct']}%)"
        else:
            _, side = regen_canvas_for(new_geom, sad_id, extent_floor_m, margin_factor)
            r['new_canvas_m'] = round(side)
            r['status'] = (f"HEAVY - spills canvas (changed {r['change_pct']}%); "
                           f"would regen canvas to {round(side)}m square")
        return r

    if cur_b.exists():
        bk = src / 'sad_boundary_prev.geojson'
        if not bk.exists():
            gpd.read_file(cur_b).to_file(bk, driver='GeoJSON')
    # carry over identity props (from _prev/current), dissolve to 1 feature,
    # and write geometry from the server -- M1 requires exactly 1 feature with
    # sad_id. Writing the raw server GDF would drop these and (e.g. Detroit)
    # leave multiple features.
    props = _load_boundary_props(sad_dir, name)
    _build_boundary_gdf(new_geom, props).to_file(cur_b, driver='GeoJSON')

    if r['tier'] == 'light':
        r['status'] = f"LIGHT overwrote boundary (changed {r['change_pct']}%), canvas kept"
    else:
        ext_gdf, side = regen_canvas_for(new_geom, sad_id, extent_floor_m, margin_factor)
        if cur_e.exists():
            bke = src / 'image_extent_prev.geojson'
            if not bke.exists():
                gpd.read_file(cur_e).to_file(bke, driver='GeoJSON')
        ext_gdf.to_file(cur_e, driver='GeoJSON')
        r['new_canvas_m'] = round(side)
        r['status'] = (f"HEAVY overwrote boundary + regenerated canvas "
                       f"to {round(side)}m square (changed {r['change_pct']}%)")
    return r


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--boundaries-root', type=Path, required=True)
    ap.add_argument('--sads', type=str, default='')
    ap.add_argument('--extent-meters', type=float, default=4286.0)
    ap.add_argument('--margin-factor', type=float, default=1.25)
    ap.add_argument('--canvas-only', type=str, default='',
                    help='Comma-separated SAD names: regenerate ONLY the canvas '
                         'from the current on-disk boundary (no boundary overwrite). '
                         'For districts already boundary-updated whose canvas still '
                         'needs re-centering.')
    ap.add_argument('--force-heavy', type=str, default='',
                    help='Comma-separated SAD folder names to force into the '
                         'HEAVY path (regenerate canvas even if the new '
                         'boundary fits the existing one).')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    broot = args.boundaries_root
    if not data_dir.exists():
        sys.exit(f"data dir does not exist: {data_dir}")
    if not broot.exists():
        sys.exit(f"boundaries root does not exist: {broot}")

    if args.sads:
        names = [s.strip() for s in args.sads.split(',') if s.strip()]
        sad_dirs = [data_dir / n for n in names]
    else:
        sad_dirs = sorted(d for d in data_dir.iterdir()
                          if d.is_dir() and not d.name.startswith('_')
                          and 'drawn' not in d.name.lower()
                          and (d.name[:2].isdigit() or '_' in d.name))

    force_set = {x.strip() for x in args.force_heavy.split(',') if x.strip()}
    canvas_only_set = {x.strip() for x in args.canvas_only.split(',') if x.strip()}
    print(f"{'DRY-RUN: ' if args.dry_run else ''}boundary update for "
          f"{len(sad_dirs)} non-drawn district(s)"
          + (f"  (forced heavy: {', '.join(sorted(force_set))})" if force_set else "")
          + "\n")

    light, heavy, identical, missing, errors = [], [], 0, 0, []
    for d in sad_dirs:
        server_file = find_server_boundary(broot, d.name)
        if server_file is None:
            missing += 1
            print(f"  {d.name}\n      no 02_SAD-Boundary on server -- left as-is")
            continue
        mtime = dt.datetime.fromtimestamp(server_file.stat().st_mtime)
        r = process(d, server_file, args.dry_run, args.extent_meters,
                    args.margin_factor, force_heavy=(d.name in force_set),
                    canvas_only=(d.name in canvas_only_set))
        print(f"  {r['sad']}")
        print(f"      using: {server_file.name}  (modified {mtime:%Y-%m-%d %H:%M})")
        print(f"      {r['status']}")
        if r['status'].startswith('ERROR'):
            errors.append(r['sad'])
        elif r['tier'] == 'identical':
            identical += 1
        elif r['tier'] == 'light':
            light.append(r['sad'])
        elif r['tier'] == 'heavy':
            heavy.append(r['sad'])

    print("\n" + "=" * 60)
    print(f"  LIGHT (boundary only, re-run derived):   {len(light)}")
    print(f"  HEAVY (canvas regen, re-pull + re-run):  {len(heavy)}")
    print(f"  identical:                               {identical}")
    print(f"  no server update:                        {missing}")
    if errors:
        print(f"  ERRORS (left as-is):                     {len(errors)}")
    if light:
        print("\n  LIGHT -- re-run boundary-dependent modules for:")
        for s in light:
            print(f"    {s}")
    if heavy:
        print("\n  HEAVY -- re-pull source layers + full pipeline for:")
        for s in heavy:
            print(f"    {s}")
    if errors:
        print("\n  ERRORS -- inspect manually:")
        for s in errors:
            print(f"    {s}")
    print("=" * 60)


if __name__ == '__main__':
    main()
