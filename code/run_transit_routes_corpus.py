"""
run_transit_routes_corpus.py

Populate transit_routes.geojson across the whole corpus using the EXISTING
local GTFS cache (data/_gtfs_cache, ~187 feeds) instead of re-downloading.

Why: module_14's --discover path returns Mobility Database catalog URLs, and
the shared _open_zip() derives its cache filename from the URL tail (e.g.
'latest.zip'), which does NOT match the descriptively-named cached files
(e.g. 'us-michigan-...-gtfs-714.zip'). So --discover re-downloads every feed
(~37 min/district). This helper matches feeds to the cache by MDB id and feeds
module_14 local --gtfs-zip paths, which _open_zip opens directly (no network).

Per district: read derived/manifest.json bbox -> pick cached zips whose catalog
bbox overlaps -> run module_14_transit_routes.py with those --gtfs-zip paths.

USAGE
    python run_transit_routes_corpus.py --data-dir <DATA> --code-dir <CODE>
    # options:
    #   --sads "29_...,31_..."   only these districts (default: all)
    #   --catalog-url <url>      override MDB catalog (default: module_21's)
    #   --force                  re-run districts that already have routes
    #   --dry-run                print feed selection per district, run nothing
"""
from __future__ import annotations
import argparse
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import requests

ID_RE = re.compile(r'-gtfs-(\d+)\.zip$', re.I)


def index_cache(cache_dir: Path) -> dict:
    """MDB id -> local zip path, from descriptively-named cache files."""
    idx = {}
    for z in cache_dir.glob('*.zip'):
        m = ID_RE.search(z.name)
        if m:
            idx[m.group(1)] = z
    return idx


def catalog_bboxes(catalog_url: str) -> pd.DataFrame:
    """Fetch MDB catalog -> DataFrame with id + bbox columns (GTFS rows only)."""
    csv = requests.get(catalog_url, timeout=120, allow_redirects=True).content
    cat = pd.read_csv(io.BytesIO(csv), low_memory=False)
    cols = {c.lower(): c for c in cat.columns}
    def col(*cands):
        for c in cands:
            if c in cols:
                return cols[c]
        return None
    dt = col('data_type')
    if dt is not None:
        cat = cat[cat[dt].astype(str).str.lower().str.contains('gtfs', na=False)]
    id_col = col('mdb_source_id', 'id')
    la1, la2 = col('location.bounding_box.minimum_latitude'), col('location.bounding_box.maximum_latitude')
    lo1, lo2 = col('location.bounding_box.minimum_longitude'), col('location.bounding_box.maximum_longitude')
    if not all([id_col, la1, la2, lo1, lo2]):
        sys.exit("catalog schema unexpected (missing id or bbox columns)")
    out = cat[[id_col, la1, la2, lo1, lo2]].copy()
    out.columns = ['id', 'minlat', 'maxlat', 'minlon', 'maxlon']
    for c in ('minlat', 'maxlat', 'minlon', 'maxlon'):
        out[c] = pd.to_numeric(out[c], errors='coerce')
    out['id'] = out['id'].astype(str).str.replace(r'\.0$', '', regex=True)
    return out.dropna(subset=['minlat', 'maxlat', 'minlon', 'maxlon'])


def feeds_for_bbox(bbox, cat: pd.DataFrame, cache_idx: dict,
                   max_span: float = 3.0) -> list[Path]:
    """Cached zips whose catalog bbox overlaps the district bbox. Skips feeds
    whose own bbox spans more than max_span degrees in either axis -- these are
    nationwide/intercity feeds (Amtrak, Flixbus, megabus) whose geometry mostly
    clips away and just slows parsing. A metro agency spans well under 1 deg."""
    minlon, minlat, maxlon, maxlat = bbox
    hit = cat[(cat['minlat'] <= maxlat) & (cat['maxlat'] >= minlat) &
              (cat['minlon'] <= maxlon) & (cat['maxlon'] >= minlon)]
    if max_span and max_span > 0:
        hit = hit[((hit['maxlon'] - hit['minlon']) <= max_span) &
                  ((hit['maxlat'] - hit['minlat']) <= max_span)]
    paths = []
    for fid in hit['id']:
        if fid in cache_idx:
            paths.append(cache_idx[fid])
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--code-dir', type=Path, required=True)
    ap.add_argument('--sads', default='')
    ap.add_argument('--catalog-url',
                    default='https://bit.ly/catalogs-csv')  # MDB redirect; override if needed
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--max-feed-span', type=float, default=3.0,
                    help='Skip feeds whose bbox spans more than this many degrees '
                         '(nationwide/intercity feeds; default 3.0, set 0 to disable).')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    cache_dir = args.data_dir / '_gtfs_cache'
    cache_idx = index_cache(cache_dir)
    print(f"cache: {len(cache_idx)} feeds indexed by MDB id from {cache_dir}")

    print("fetching MDB catalog for feed bboxes...")
    cat = catalog_bboxes(args.catalog_url)
    print(f"catalog: {len(cat)} GTFS feeds with bboxes")

    # district list
    if args.sads:
        sad_dirs = [args.data_dir / s.strip() for s in args.sads.split(',') if s.strip()]
    else:
        sad_dirs = sorted([p for p in args.data_dir.iterdir()
                           if p.is_dir() and (p / 'derived' / 'manifest.json').exists()])

    m14 = args.code_dir / 'module_14_transit_routes.py'
    if not m14.exists():
        sys.exit(f"module_14 not found: {m14}")

    ok = skip = fail = 0
    for sd in sad_dirs:
        name = sd.name
        man = sd / 'derived' / 'manifest.json'
        if not man.exists():
            print(f"  - {name}: no manifest, skip"); skip += 1; continue
        out = sd / 'derived' / 'transit' / 'transit_routes.geojson'
        if out.exists() and not args.force:
            print(f"  = {name}: already has routes (use --force)"); skip += 1; continue

        bbox = json.loads(man.read_text())['bbox_geo']
        feeds = feeds_for_bbox(bbox, cat, cache_idx, args.max_feed_span)
        if not feeds:
            print(f"  ! {name}: no cached feeds overlap bbox (transit-sparse or feed not cached)")
            # still run with no feeds? module_14 needs >=1; skip cleanly.
            skip += 1
            continue

        print(f"  > {name}: {len(feeds)} cached feed(s)")
        if args.dry_run:
            for f in feeds[:6]:
                print(f"        {f.name}")
            if len(feeds) > 6:
                print(f"        ... +{len(feeds)-6} more")
            continue

        cmd = [sys.executable, str(m14),
               '--derived', str(sd / 'derived'),
               '--source', str(sd / 'source')]
        for f in feeds:
            cmd += ['--gtfs-zip', str(f)]
        r = subprocess.run(cmd, cwd=str(args.code_dir))
        if r.returncode == 0:
            ok += 1
        else:
            print(f"  ! {name}: module_14 exited {r.returncode}")
            fail += 1

    print(f"\nDONE. ok={ok} skip={skip} fail={fail}")
    if not args.dry_run:
        print("Next: python build_ui_manifest.py --data-dir <DATA>")


if __name__ == '__main__':
    main()
