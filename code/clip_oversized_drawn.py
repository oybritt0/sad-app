#!/usr/bin/env python3
"""Clip oversized drawn-district building layers to their image_extent."""
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path
import geopandas as gpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data")
    ap.add_argument("--threshold", type=int, default=5000)
    ap.add_argument("--pattern", default="drawn")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    candidates = sorted(d for d in data_dir.iterdir()
                        if d.is_dir() and args.pattern.lower() in d.name.lower())
    print(f"scanning {len(candidates)} '{args.pattern}' districts (threshold={args.threshold})\n")

    n_clipped = n_skip = n_fail = 0
    for d in candidates:
        name = d.name
        bpath = d / "source" / "buildings.geojson"
        epath = d / "source" / "image_extent.geojson"
        if not bpath.exists() or not epath.exists():
            print(f"SKIP {name} (missing buildings or image_extent)")
            n_skip += 1
            continue
        try:
            bgdf = gpd.read_file(bpath)
            egdf = gpd.read_file(epath)
        except Exception as e:
            print(f"FAIL {name} (read error: {e})")
            n_fail += 1
            continue
        n_before = len(bgdf)
        if n_before <= args.threshold:
            print(f"skip {name} ({n_before} buildings, under threshold)")
            n_skip += 1
            continue
        try:
            if bgdf.crs is None:
                bgdf = bgdf.set_crs("EPSG:4326")
            if egdf.crs is None:
                egdf = egdf.set_crs("EPSG:4326")
            extent_geom = egdf.geometry.union_all() if hasattr(egdf.geometry, "union_all") else egdf.geometry.unary_union
            cent = bgdf.geometry.representative_point()
            clipped = bgdf[cent.within(extent_geom)].copy()
            n_after = len(clipped)
        except Exception as e:
            print(f"FAIL {name} (clip error: {e})")
            n_fail += 1
            continue
        pct = (100 * n_after // n_before) if n_before else 0
        print(f"CLIP {name}: {n_before} -> {n_after} buildings ({pct}% kept)")
        if not args.dry_run:
            bak = bpath.with_suffix(".geojson.preclip_bak")
            if not bak.exists():
                shutil.copyfile(bpath, bak)
            clipped.to_crs("EPSG:4326").to_file(bpath, driver="GeoJSON")
        n_clipped += 1

    action = "would clip" if args.dry_run else "clipped"
    print(f"\n=== {action} {n_clipped}, skipped {n_skip}, failed {n_fail} ===")


if __name__ == "__main__":
    main()