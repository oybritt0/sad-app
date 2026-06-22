"""
convert_enriched_buildings.py

M5 writes enriched buildings (with dominant_program_inside, programs_inside, etc.)
to derived/<sad>/buildings_enriched.gpkg. The web viewer can only load GeoJSON,
so this walks every SAD directory and writes a sibling
derived/<sad>/buildings_enriched.geojson in WGS84 lat/lng.

Run once after M5 has produced the GPKGs:

    python convert_enriched_buildings.py ^
        --data-dir "C:\\Users\\rbritain\\Documents\\ROD\\Search Tool\\ROD\\Detroit_Test\\data"
"""
import argparse
import sys
from pathlib import Path

try:
    import geopandas as gpd
except ImportError:
    print("ERROR: geopandas is required. pip install geopandas")
    sys.exit(1)


def convert_one(gpkg_path: Path) -> str:
    out_path = gpkg_path.with_suffix(".geojson")
    gdf = gpd.read_file(gpkg_path, layer="buildings")
    # Web viewer expects WGS84 lat/lng
    try:
        if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
    except Exception:
        # If CRS is missing/odd, leave as-is; sanitizer handles wrapper rings
        pass
    # GeoJSON driver overwrites cleanly
    if out_path.exists():
        out_path.unlink()
    gdf.to_file(out_path, driver="GeoJSON")
    return f"{out_path.name}  ({len(gdf)} buildings)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", help="Convert every */derived/buildings_enriched.gpkg under this corpus dir.")
    ap.add_argument("--derived", help="Convert a single district's derived/buildings_enriched.gpkg (used by the pipeline + draw-tool flow).")
    args = ap.parse_args()

    # Single-district mode (preferred for the per-SAD pipeline and draw tool).
    if args.derived:
        derived = Path(args.derived)
        gpkg = derived / "buildings_enriched.gpkg"
        if not gpkg.exists():
            print(f"ERROR: no buildings_enriched.gpkg in {derived} (run M5 first).")
            sys.exit(1)
        try:
            msg = convert_one(gpkg)
            print(f"  + {derived.parts[-2]}: {msg}")
        except Exception as e:
            print(f"  ! {derived.parts[-2]}: FAILED - {e}")
            sys.exit(1)
        return

    if not args.data_dir:
        print("ERROR: pass --derived <district/derived> or --data-dir <corpus>.")
        sys.exit(1)

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"ERROR: data dir not found: {data_dir}")
        sys.exit(1)

    gpkgs = sorted(data_dir.glob("*/derived/buildings_enriched.gpkg"))
    if not gpkgs:
        print("No buildings_enriched.gpkg files found under */derived/.")
        print("Has Module 5 (spatial join) been run for these SADs?")
        sys.exit(1)

    print(f"Found {len(gpkgs)} enriched building files")
    ok, fail = 0, 0
    for gpkg in gpkgs:
        sad = gpkg.parts[-3]  # <sad>/derived/buildings_enriched.gpkg
        try:
            msg = convert_one(gpkg)
            print(f"  + {sad}: {msg}")
            ok += 1
        except Exception as e:
            print(f"  ! {sad}: FAILED - {e}")
            fail += 1

    print(f"\nDone. {ok} converted, {fail} failed.")
    if ok:
        print("Next: re-run build_ui_manifest.py so the viewer picks up the enriched buildings.")


if __name__ == "__main__":
    main()

