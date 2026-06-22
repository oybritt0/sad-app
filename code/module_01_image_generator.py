"""
module_01_image_generator.py

Reads a SAD's three source GeoJSONs and produces the rasterized
figure-ground image plus the georeferencing manifest that every
downstream module depends on.

EXPECTED SOURCE DIRECTORY LAYOUT (--source)
    {source}/
      ├── buildings.geojson      polygons; OSM tags preserved as attrs
      ├── sad_boundary.geojson   1 feature; attrs: sad_id, sad_name,
      │                                              typology, anchor_venue
      └── image_extent.geojson   1 feature; attr: extent_meters

OUTPUTS (in --out)
    figureground.png       1080x1080, 8-bit grayscale, building=black, void=white
    figureground_mask.npy  Same data as numpy uint8 (0=void, 1=building)
    manifest.json          Affine transform, bbox, CRS, building count, typology

USAGE
    python module_01_image_generator.py \\
        --source ../data/source/per_sad/the_battery_atl \\
        --out    ../data/derived/per_sad/the_battery_atl
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
from PIL import Image
from rasterio.features import rasterize

# Local imports - relative paths assume running from code/
sys.path.insert(0, str(Path(__file__).parent))
from shared.geo_utils import affine_from_geo_bbox, utm_epsg_for
from shared.schemas import Manifest


IMAGE_W = 1080
IMAGE_H = 1080


def process_sad(source_dir: Path, out_dir: Path) -> Manifest:
    """
    Rasterize one SAD's source GeoJSONs into the figure-ground PNG + manifest.
    Returns the Manifest (also written to disk).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # ─── 1. Locate and read the three source GeoJSONs ─────────────────────────
    buildings_path = source_dir / 'buildings.geojson'
    boundary_path = source_dir / 'sad_boundary.geojson'
    extent_path = source_dir / 'image_extent.geojson'
    
    for p in (buildings_path, boundary_path, extent_path):
        if not p.exists():
            raise FileNotFoundError(
                f"Expected {p.name} in {source_dir}.\n"
                f"Each SAD's source folder must contain three GeoJSONs:\n"
                f"  - buildings.geojson\n"
                f"  - sad_boundary.geojson\n"
                f"  - image_extent.geojson"
            )
    
    buildings = gpd.read_file(buildings_path)
    boundary = gpd.read_file(boundary_path)
    extent = gpd.read_file(extent_path)
    
    # Sanity checks
    if len(buildings) == 0:
        raise ValueError(f"No buildings in {buildings_path}")
    if len(extent) != 1:
        raise ValueError(f"Expected exactly 1 image_extent feature, got {len(extent)}")
    if len(boundary) != 1:
        raise ValueError(f"Expected exactly 1 sad_boundary feature, got {len(boundary)}")
    
    # Make sure everything is in EPSG:4326 (lat/lon).
    # GeoJSON conventionally is WGS84, but ArcGIS Pro will sometimes export in
    # the source's native projection - defensive reproject covers both cases.
    if buildings.crs is None:
        raise ValueError(
            f"{buildings_path.name} has no CRS. Re-export from Pro with the "
            f"projection metadata included (it should be EPSG:4326)."
        )
    buildings = buildings.to_crs("EPSG:4326")
    boundary = boundary.to_crs("EPSG:4326")
    extent = extent.to_crs("EPSG:4326")
    
    # ─── 2. Get the rasterization frame ───────────────────────────────────────
    minlon, minlat, maxlon, maxlat = extent.total_bounds
    bbox = (minlon, minlat, maxlon, maxlat)
    
    # The rasterio affine transform maps pixel (col, row) -> geographic (lon, lat)
    # row=0 is at the TOP of the image (north), matching PIL convention.
    transform = affine_from_geo_bbox(bbox, IMAGE_W, IMAGE_H)
    
    # ─── 3. Rasterize buildings ───────────────────────────────────────────────
    # Filter to buildings that intersect the extent (avoid wasted work)
    extent_poly = extent.iloc[0].geometry
    in_frame = buildings[buildings.intersects(extent_poly)]
    
    # Burn polygons to a binary mask; 1 = building, 0 = void
    valid_geoms = [g for g in in_frame.geometry if g is not None and not g.is_empty]
    if not valid_geoms:
        raise ValueError("No valid building geometries in the image extent")
    
    mask = rasterize(
        [(geom, 1) for geom in valid_geoms],
        out_shape=(IMAGE_H, IMAGE_W),
        transform=transform,
        fill=0,
        dtype='uint8',
    )
    
    # ─── 4. Write the figure-ground PNG ───────────────────────────────────────
    # Architectural figure-ground convention: building = BLACK (0), void = WHITE (255).
    # This matches Mean City and the existing triptych corpus, and is what the LoRA
    # base model expects to see.
    img_array = np.where(mask == 1, 0, 255).astype('uint8')
    Image.fromarray(img_array, mode='L').save(out_dir / 'figureground.png')
    
    # And the raw numpy mask for fast reload by Module 2
    np.save(out_dir / 'figureground_mask.npy', mask)
    
    # ─── 5. Build and write manifest ──────────────────────────────────────────
    sad_meta = boundary.iloc[0]
    extent_meta = extent.iloc[0]
    
    # Get center for UTM zone selection
    center_lon = (minlon + maxlon) / 2
    center_lat = (minlat + maxlat) / 2
    utm_crs = utm_epsg_for(center_lat, center_lon)
    
    manifest = Manifest(
        sad_id=str(sad_meta['sad_id']),
        sad_name=str(sad_meta.get('sad_name', sad_meta['sad_id'])),
        typology=str(sad_meta.get('typology', 'unknown')),
        anchor_venue=str(sad_meta.get('anchor_venue', 'unknown')),
        bbox_geo=bbox,
        crs_source="EPSG:4326",
        crs_metric=utm_crs,
        image_width_px=IMAGE_W,
        image_height_px=IMAGE_H,
        extent_meters=float(extent_meta.get('extent_meters', 4286.0)),
        affine_geo_to_pixel=tuple(transform)[:6],
        building_count=int(len(in_frame)),
    )
    
    with open(out_dir / 'manifest.json', 'w') as f:
        f.write(manifest.model_dump_json(indent=2))
    
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[2])
    parser.add_argument('--source', type=Path, required=True,
                        help='Source directory containing the three SAD GeoJSONs')
    parser.add_argument('--out', type=Path, required=True, help='Output directory')
    args = parser.parse_args()
    
    if not args.source.exists() or not args.source.is_dir():
        sys.exit(f"Source directory not found: {args.source}")
    
    manifest = process_sad(args.source, args.out)
    
    print(f"[OK] {manifest.sad_id}")
    print(f"  buildings: {manifest.building_count}")
    print(f"  bbox: {manifest.bbox_geo}")
    print(f"  output: {args.out}")


if __name__ == '__main__':
    main()
