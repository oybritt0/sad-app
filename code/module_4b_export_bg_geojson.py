"""
module_4b_export_bg_geojson.py

Companion to module_4b_census_timeseries.py. Reads the
census_blockgroups_<vintage>.gpkg files that M4b wrote into each SAD's
source/ folder and exports them as GeoJSON, which browsers can load
directly. Fast - no API calls, just a format flip.

USAGE
    python module_4b_export_bg_geojson.py <data_dir>

OUTPUT
    For each SAD with a census_blockgroups_<vintage>.gpkg:
        source/<sad>/census_blockgroups_<vintage>.geojson

Idempotent: skips files that already exist. Pass --force to overwrite.
"""
import argparse
import sys
import traceback
from pathlib import Path

try:
    import geopandas as gpd
except ImportError:
    sys.exit("This script requires geopandas. Install with: pip install geopandas")


def _coerce_for_geojson(gdf):
    """GeoJSON requires JSON-serializable property values. Drop non-geometry
    object columns that pandas often produces (Timestamps, raw dtypes)."""
    safe = gdf.copy()
    for col in safe.columns:
        if col == 'geometry':
            continue
        if safe[col].dtype == object:
            safe[col] = safe[col].astype(str)
    return safe


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('data_dir', type=Path,
                   help="Corpus root (folder containing <sad_id>/ folders)")
    p.add_argument('--force', action='store_true',
                   help="Re-export even if GeoJSON already exists")
    args = p.parse_args()
    
    if not args.data_dir.is_dir():
        sys.exit(f"Not a directory: {args.data_dir}")
    
    converted = 0
    skipped = 0
    failed = 0
    
    for sad_dir in sorted(args.data_dir.iterdir()):
        if not sad_dir.is_dir() or sad_dir.name.startswith('_'):
            continue
        # M4b writes GPKG to source/ if it exists, else to the SAD root.
        source = sad_dir / 'source' if (sad_dir / 'source').exists() else sad_dir
        
        for gpkg in sorted(source.glob('census_blockgroups_*.gpkg')):
            geojson_path = gpkg.with_suffix('.geojson')
            if geojson_path.exists() and not args.force:
                skipped += 1
                continue
            try:
                bgs = gpd.read_file(gpkg)
                # Ensure WGS84 for browser consumption.
                if bgs.crs and str(bgs.crs).upper() not in ('EPSG:4326', 'WGS 84'):
                    bgs = bgs.to_crs('EPSG:4326')
                bgs = _coerce_for_geojson(bgs)
                bgs.to_file(geojson_path, driver='GeoJSON')
                print(f"  + {sad_dir.name}/{geojson_path.name} ({len(bgs)} features)")
                converted += 1
            except Exception as e:
                print(f"  ! {sad_dir.name}/{gpkg.name}: {type(e).__name__}: {e}")
                traceback.print_exc(limit=2)
                failed += 1
    
    print(f"\nDone. {converted} converted, {skipped} already existed, {failed} failed.")


if __name__ == '__main__':
    main()
