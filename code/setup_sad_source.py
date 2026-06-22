"""
setup_sad_source.py - one-shot prep utility

Takes your source GeoJSONs (from any paths, with any filenames) and writes
them into a clean source folder using the canonical filenames the pipeline
expects (buildings.geojson, sad_boundary.geojson, image_extent.geojson).

CANVAS EXTENT - three ways to provide it
    (a) --extent <path>                 use your hand-authored canvas GeoJSON
    (b) --anchor-lat / --anchor-lon     generate a square at given coordinates
    (c) (omit both)                     generate a square at boundary centroid
    
    For (b) and (c), the side length defaults to 4286m; override with
    --extent-meters.

The script also augments the sad_boundary feature with the metadata the
pipeline needs (sad_id, sad_name, typology, anchor_venue) if those columns
are missing from your input.

--typology is optional. If you do not know the classification yet, omit it
and it is recorded as "unspecified"; set the real value later by re-running
this utility. All other metadata flags are required.

USAGE - with a hand-authored canvas extent (Windows; ^ continues lines)
    python setup_sad_source.py ^
        --buildings "C:\\...\\Detroit_Buildings_Test2.geojson" ^
        --boundary  "C:\\...\\Detroit_SAD_Test.geojson" ^
        --extent    "C:\\...\\canvasextent_Large_test.geojson" ^
        --output    "..\\data\\source\\per_sad\\district_detroit" ^
        --sad-id    district_detroit ^
        --sad-name  "District Detroit" ^
        --typology  innovation ^
        --anchor-venue "Little Caesars Arena"

USAGE - generate the canvas extent programmatically (no --extent)
    python setup_sad_source.py ^
        --buildings "..." --boundary "..." --output "..." ^
        --sad-id district_detroit --typology innovation ^
        --anchor-venue "Little Caesars Arena" ^
        --anchor-lat 42.341045 --anchor-lon -83.055534
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box
from shapely.ops import transform as shp_transform
from pyproj import Transformer


def utm_epsg_for(lat: float, lon: float) -> str:
    """Pick the appropriate UTM zone EPSG for metric calcs at this location."""
    zone = int((lon + 180) / 6) + 1
    return f"EPSG:{32600 + zone}" if lat >= 0 else f"EPSG:{32700 + zone}"


def make_image_extent(center_lat: float, center_lon: float, extent_m: float):
    """
    Build a true geographic square `extent_m` on a side, centered on (lat, lon).
    The square is constructed in metric CRS then reprojected back to EPSG:4326,
    so it has consistent physical extent regardless of latitude.
    """
    utm = utm_epsg_for(center_lat, center_lon)
    to_metric = Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform
    to_geo = Transformer.from_crs(utm, "EPSG:4326", always_xy=True).transform
    cx, cy = to_metric(center_lon, center_lat)
    half = extent_m / 2.0
    square_m = box(cx - half, cy - half, cx + half, cy + half)
    return shp_transform(to_geo, square_m), utm


def main():
    p = argparse.ArgumentParser(description="Prep a SAD source folder for the pipeline.")
    p.add_argument('--buildings', type=Path, required=True,
                   help='Path to your buildings GeoJSON (any filename).')
    p.add_argument('--boundary', type=Path, required=True,
                   help='Path to your SAD boundary GeoJSON (any filename).')
    p.add_argument('--output', type=Path, required=True,
                   help='Output source folder (will be created).')
    p.add_argument('--sad-id', required=True,
                   help='Slug ID, e.g. "district_detroit".')
    p.add_argument('--sad-name', default=None,
                   help='Human-readable name. Defaults to --sad-id if omitted.')
    p.add_argument('--typology', default='unspecified',
                   choices=['entertainment', 'community', 'innovation',
                            'tourism', 'unspecified'],
                   help="SAD typology. Optional - defaults to 'unspecified' "
                        "when the classification is not known yet. Set it "
                        "later by re-running this utility with the real "
                        "value (which regenerates sad_boundary.geojson).")
    p.add_argument('--anchor-venue', required=True,
                   help='Name of the primary anchor (e.g. "Little Caesars Arena").')
    p.add_argument('--anchor-lat', type=float, default=None,
                   help='Anchor venue latitude. If omitted, uses SAD boundary centroid. '
                        'Ignored when --extent is provided.')
    p.add_argument('--anchor-lon', type=float, default=None,
                   help='Anchor venue longitude. Required if --anchor-lat is given. '
                        'Ignored when --extent is provided.')
    p.add_argument('--extent', type=Path, default=None,
                   help='Path to existing canvas extent GeoJSON. If provided, this '
                        'geometry is used directly. If omitted, a square is generated '
                        'programmatically from anchor lat/lon or boundary centroid.')
    p.add_argument('--extent-meters', type=float, default=4286.0,
                   help='Side length of the canvas square in meters when generating '
                        '(default 4286). Ignored when --extent is provided.')
    args = p.parse_args()

    # ─── 1. Validate inputs ────────────────────────────────────────────────────
    if not args.buildings.exists():
        sys.exit(f"buildings GeoJSON not found: {args.buildings}")
    if not args.boundary.exists():
        sys.exit(f"boundary GeoJSON not found: {args.boundary}")
    if args.extent is not None and not args.extent.exists():
        sys.exit(f"extent GeoJSON not found: {args.extent}")
    if (args.anchor_lat is None) != (args.anchor_lon is None):
        sys.exit("--anchor-lat and --anchor-lon must be provided together.")

    args.output.mkdir(parents=True, exist_ok=True)
    sad_name = args.sad_name or args.sad_id

    # ─── 2. Read source GeoJSONs and reproject to EPSG:4326 ───────────────────
    print(f"Reading {args.buildings.name}...")
    buildings = gpd.read_file(args.buildings)
    if buildings.crs is None:
        sys.exit(f"buildings GeoJSON has no CRS. Re-export from Pro with projection metadata.")
    buildings = buildings.to_crs("EPSG:4326")
    print(f"  {len(buildings)} building polygons")

    print(f"Reading {args.boundary.name}...")
    boundary = gpd.read_file(args.boundary)
    if boundary.crs is None:
        sys.exit(f"boundary GeoJSON has no CRS. Re-export from Pro with projection metadata.")
    boundary = boundary.to_crs("EPSG:4326")
    if len(boundary) != 1:
        print(f"  WARNING: boundary file has {len(boundary)} features, expected 1. "
              f"Will use the first feature only.")
        boundary = boundary.iloc[[0]].reset_index(drop=True)

    # ─── 3. Determine canvas extent geometry, center, and effective side ──────
    # Three paths into the canvas extent:
    #   (a) User provided --extent  -> read that GeoJSON, use its geometry directly
    #   (b) User provided anchor    -> generate square at those coordinates
    #   (c) Neither                 -> generate square at SAD boundary centroid
    
    if args.extent is not None:
        print(f"Reading {args.extent.name}...")
        ext_gdf = gpd.read_file(args.extent)
        if ext_gdf.crs is None:
            sys.exit(f"extent GeoJSON has no CRS. Re-export from Pro with projection metadata.")
        ext_gdf = ext_gdf.to_crs("EPSG:4326")
        if len(ext_gdf) != 1:
            print(f"  WARNING: extent file has {len(ext_gdf)} features, expected 1. "
                  f"Will use the first feature only.")
            ext_gdf = ext_gdf.iloc[[0]].reset_index(drop=True)
        
        extent_geom = ext_gdf.geometry.iloc[0]
        c = extent_geom.centroid
        center_lon, center_lat = c.x, c.y
        utm = utm_epsg_for(center_lat, center_lon)
        
        # Compute effective side length from the geometry's metric bbox.
        # Module 1 rasterizes from total_bounds, so we report bbox dimensions here.
        ext_metric = ext_gdf.to_crs(utm).geometry.iloc[0]
        minx, miny, maxx, maxy = ext_metric.bounds
        side_x = maxx - minx
        side_y = maxy - miny
        eff_extent_m = (side_x + side_y) / 2.0
        
        print(f"Using user-authored canvas extent: {side_x:.0f}m x {side_y:.0f}m (UTM {utm})")
        print(f"  centroid: ({center_lat:.6f}, {center_lon:.6f})")
        
        # Two sanity warnings about the user's extent:
        if abs(side_x - side_y) / max(side_x, side_y) > 0.05:
            print(f"  WARNING: canvas is not square (diff = {abs(side_x - side_y):.0f}m). "
                  f"Module 1 rasterizes into a 1080x1080 grid, so a non-square extent "
                  f"means non-square pixels in geographic terms.")
        bb_area = side_x * side_y
        if bb_area > 0 and ext_metric.area / bb_area < 0.95:
            print(f"  WARNING: canvas is rotated relative to its axis-aligned bbox. "
                  f"Module 1 rasterizes the bbox, not the rotated polygon - your "
                  f"effective frame will be larger than your drawn shape.")
        
        if args.anchor_lat is not None:
            print(f"  (note: --anchor-lat / --anchor-lon were ignored because --extent was given)")
    
    else:
        # No --extent - generate one programmatically
        if args.anchor_lat is not None:
            center_lat, center_lon = args.anchor_lat, args.anchor_lon
            print(f"Centering canvas on explicit anchor: ({center_lat}, {center_lon})")
        else:
            c = boundary.geometry.iloc[0].centroid
            center_lon, center_lat = c.x, c.y
            print(f"Centering canvas on SAD boundary centroid: ({center_lat:.6f}, {center_lon:.6f})")
            print(f"  (you can override with --anchor-lat / --anchor-lon, or pass "
                  f"--extent to use a hand-drawn canvas)")
        
        extent_geom, utm = make_image_extent(center_lat, center_lon, args.extent_meters)
        eff_extent_m = args.extent_meters
        print(f"Generated {args.extent_meters}m x {args.extent_meters}m canvas (UTM {utm})")
    
    # Quick sanity check: are all building footprints inside the canvas?
    buildings_inside = buildings[buildings.intersects(extent_geom)]
    pct_in = 100 * len(buildings_inside) / len(buildings) if len(buildings) else 0
    print(f"  {len(buildings_inside)} of {len(buildings)} buildings fall inside the canvas "
          f"({pct_in:.1f}%)")
    if pct_in < 50:
        print(f"  WARNING: less than half of buildings fall inside the canvas. "
              f"You probably want a larger canvas or a different center.")

    # ─── 4. Augment SAD boundary attrs ─────────────────────────────────────────
    boundary_out = boundary.copy()
    for col, val in [
        ('sad_id', args.sad_id),
        ('sad_name', sad_name),
        ('typology', args.typology),
        ('anchor_venue', args.anchor_venue),
    ]:
        if col not in boundary_out.columns or boundary_out[col].iloc[0] in (None, ''):
            boundary_out[col] = val

    # ─── 5. Build canvas extent GeoDataFrame ──────────────────────────────────
    extent_gdf = gpd.GeoDataFrame(
        [{
            'sad_id': args.sad_id,
            'extent_meters': eff_extent_m,
            'center_lat': center_lat,
            'center_lon': center_lon,
            'utm_crs': utm,
            'geometry': extent_geom,
        }],
        crs='EPSG:4326',
    )

    # ─── 6. Write the three canonical files ───────────────────────────────────
    buildings.to_file(args.output / 'buildings.geojson', driver='GeoJSON')
    boundary_out.to_file(args.output / 'sad_boundary.geojson', driver='GeoJSON')
    extent_gdf.to_file(args.output / 'image_extent.geojson', driver='GeoJSON')

    print(f"\n[OK] Source folder ready: {args.output}")
    print(f"\nNext step:")
    print(f"  cd code")
    print(f"  python module_01_image_generator.py "
          f"--source {args.output} --out ../data/derived/per_sad/{args.sad_id}")


if __name__ == '__main__':
    main()
