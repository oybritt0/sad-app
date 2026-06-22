r"""
repair_boundary_props.py

One-time fix for districts whose source/sad_boundary.geojson was overwritten by
an older boundary_update.py that wrote the RAW server GeoJSON -- dropping the
identity properties M1 needs (sad_id, sad_name, typology, anchor_venue) and, for
multi-polygon server files (e.g. Detroit), leaving more than one feature.

This does NOT touch geometry source: it keeps the NEW boundary geometry already
on disk (dissolved to a single feature) and re-attaches the identity properties
from the district's sad_boundary_prev.geojson backup (the pristine original).

Idempotent: a district whose sad_boundary.geojson already has a non-empty
sad_id AND is a single feature is left untouched.

USAGE
    python repair_boundary_props.py --data-dir "<data>"            # all non-drawn
    python repair_boundary_props.py --data-dir "<data>" --sads "03_...,32_..."
    python repair_boundary_props.py --data-dir "<data>" --dry-run
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import geopandas as gpd

PROP_KEYS = ('id', 'sad_id', 'sad_name', 'typology', 'anchor_venue')


def _union(gdf):
    return gdf.union_all() if hasattr(gdf, 'union_all') else gdf.unary_union


def _has_valid_sad_id(g) -> bool:
    if g is None or g.empty or 'sad_id' not in g.columns:
        return False
    v = g.iloc[0].get('sad_id')
    return v not in (None, '') and not (isinstance(v, float) and v != v)


def _props_from_prev(prev_path: Path, fallback: str) -> dict:
    if prev_path.exists():
        try:
            g = gpd.read_file(prev_path)
            if _has_valid_sad_id(g):
                row = g.iloc[0]
                return {k: (row[k] if k in g.columns else None) for k in PROP_KEYS}
        except Exception:
            pass
    return {'id': None, 'sad_id': fallback, 'sad_name': fallback,
            'typology': 'unspecified', 'anchor_venue': 'unknown'}


def repair(sad_dir: Path, dry_run: bool) -> str:
    src = sad_dir / 'source'
    cur = src / 'sad_boundary.geojson'
    prev = src / 'sad_boundary_prev.geojson'
    if not cur.exists():
        return 'skip (no sad_boundary.geojson)'
    try:
        g = gpd.read_file(cur)
    except Exception as e:
        return f'ERROR unreadable: {e}'
    if g.empty:
        return 'ERROR empty boundary'
    # already good? single feature with a real sad_id -> nothing to do
    if _has_valid_sad_id(g) and len(g) == 1:
        return 'ok (already has sad_id, 1 feature)'

    if g.crs is None:
        return 'ERROR current boundary has no CRS -- inspect manually'
    g = g.to_crs('EPSG:4326')
    geom = _union(g)
    if not geom.is_valid:
        geom = geom.buffer(0)
    props = _props_from_prev(prev, sad_dir.name)
    nfeat = len(g)

    if dry_run:
        return (f'WOULD repair: {nfeat} feat -> 1, attach sad_id='
                f"{props['sad_id']}")

    rec = dict(props); rec['geometry'] = geom
    gpd.GeoDataFrame([rec], crs='EPSG:4326').to_file(cur, driver='GeoJSON')
    return f"repaired: {nfeat} feat -> 1, sad_id={props['sad_id']}"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sads', type=str, default='')
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
                          if d.is_dir() and not d.name.startswith('_')
                          and 'drawn' not in d.name.lower()
                          and (d.name[:2].isdigit() or '_' in d.name))

    print(f"{'DRY-RUN: ' if args.dry_run else ''}repairing boundary props for "
          f"{len(sad_dirs)} district(s)\n")
    repaired = ok = skipped = errors = 0
    for d in sad_dirs:
        if not d.exists():
            print(f"  {d.name}\n      skip (folder not found)"); skipped += 1; continue
        msg = repair(d, args.dry_run)
        print(f"  {d.name}\n      {msg}")
        if msg.startswith('repaired') or msg.startswith('WOULD'):
            repaired += 1
        elif msg.startswith('ok'):
            ok += 1
        elif msg.startswith('ERROR'):
            errors += 1
        else:
            skipped += 1

    print("\n" + "=" * 56)
    print(f"  repaired (or would): {repaired}")
    print(f"  already ok:          {ok}")
    print(f"  skipped:             {skipped}")
    if errors:
        print(f"  ERRORS:              {errors}")
    print("=" * 56)
    if not args.dry_run and repaired:
        print("\nNext: re-run Module 1 (and downstream) for the repaired districts "
              "so the new boundary propagates into manifest.json, e.g.:\n"
              "  python batch_run_pipeline.py --data-dir <data> --stage per-sad "
              "--force --sads <repaired districts>")


if __name__ == '__main__':
    main()
