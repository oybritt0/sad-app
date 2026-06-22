r"""
clip_drawn_places.py

Drawn districts carry an UNCLIPPED, city-wide POI dump in
source/rod_places.geojson (e.g. Philadelphia: 63,073 POIs spanning the whole
city, vs a ~1.5 km district). That bloats the viewer's SVG export to ~11 MB --
94% of which is POI <circle> + <title> elements -- and makes Illustrator choke.
Real (non-drawn) districts come from clipped ROD exports and don't have this.

This clips each drawn district's rod_places.geojson to its canvas
(image_extent.geojson), backing up the original to rod_places_unclipped.geojson
once. Idempotent: a district already clipped (POIs all inside the extent, and a
backup present) is left alone.

Clip target = image_extent (the canvas square the viewer renders), NOT the tight
sad_boundary -- the viewer draws POIs across the whole canvas frame, so clipping
to the boundary would drop dots that legitimately appear around the district.

USAGE
    python clip_drawn_places.py --data-dir "<data>"                 # all drawn
    python clip_drawn_places.py --data-dir "<data>" --sads "59_...,60_..."
    python clip_drawn_places.py --data-dir "<data>" --dry-run
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import geopandas as gpd


def _read(path: Path):
    g = gpd.read_file(path)
    if g.crs is None:
        g = g.set_crs('EPSG:4326')
    elif str(g.crs).upper() != 'EPSG:4326':
        g = g.to_crs('EPSG:4326')
    return g


def clip_one(sad_dir: Path, dry_run: bool) -> str:
    src = sad_dir / 'source'
    places = src / 'rod_places.geojson'
    extent = src / 'image_extent.geojson'
    backup = src / 'rod_places_unclipped.geojson'

    if not places.exists():
        return 'skip (no rod_places.geojson)'
    if not extent.exists():
        return 'ERROR no image_extent.geojson to clip against'

    try:
        pts = _read(places)
        ext = _read(extent)
    except Exception as e:
        return f'ERROR read failed: {e}'
    if ext.empty:
        return 'ERROR empty image_extent'

    ext_poly = ext.geometry.union_all() if hasattr(ext.geometry, 'union_all') \
        else ext.geometry.unary_union
    n_before = len(pts)

    # already clipped? everything inside the extent AND a backup exists -> idempotent
    inside_mask = pts.geometry.within(ext_poly)
    n_inside = int(inside_mask.sum())
    if n_inside == n_before and backup.exists():
        return f'ok (already clipped, {n_before} POIs all inside extent)'

    clipped = pts[inside_mask].copy()
    n_after = len(clipped)

    if dry_run:
        return (f'WOULD clip: {n_before:,} -> {n_after:,} POIs '
                f'({n_before - n_after:,} outside canvas dropped)')

    # back up the unclipped original ONCE (never overwrite a prior backup)
    if not backup.exists():
        # write the raw original bytes so the true source is preserved verbatim
        backup.write_bytes(places.read_bytes())
    clipped.to_file(places, driver='GeoJSON')
    return f'clipped: {n_before:,} -> {n_after:,} POIs (backup: {backup.name})'


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sads', type=str, default='',
                    help='Comma-separated SAD names. Default: all drawn districts.')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    if not data_dir.exists():
        sys.exit(f"data dir does not exist: {data_dir}")

    if args.sads:
        names = [s.strip() for s in args.sads.split(',') if s.strip()]
        sad_dirs = [data_dir / n for n in names]
    else:
        sad_dirs = sorted(d for d in data_dir.iterdir()
                          if d.is_dir() and 'drawn' in d.name.lower())

    print(f"{'DRY-RUN: ' if args.dry_run else ''}clipping POIs for "
          f"{len(sad_dirs)} drawn district(s)\n")
    clipped = ok = skipped = errors = 0
    for d in sad_dirs:
        if not d.exists():
            print(f"  {d.name}\n      skip (folder not found)"); skipped += 1; continue
        msg = clip_one(d, args.dry_run)
        print(f"  {d.name}\n      {msg}")
        if msg.startswith(('clipped', 'WOULD')):
            clipped += 1
        elif msg.startswith('ok'):
            ok += 1
        elif msg.startswith('ERROR'):
            errors += 1
        else:
            skipped += 1

    print("\n" + "=" * 56)
    print(f"  clipped (or would): {clipped}")
    print(f"  already ok:         {ok}")
    print(f"  skipped:            {skipped}")
    if errors:
        print(f"  ERRORS:             {errors}")
    print("=" * 56)
    if not args.dry_run and clipped:
        print("\nThe viewer reads rod_places.geojson directly, so re-export the "
              "affected drawn districts from the viewer to get the lighter SVG. "
              "No pipeline re-run needed (M3/M12 re-derive from the clipped file "
              "on their next run if you force them).")


if __name__ == '__main__':
    main()
