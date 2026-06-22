#!/usr/bin/env python3
"""
module_3b_context_layers.py

Compute plan-footprint percentages for one SAD.

For each district, this measures what share of the SAD area is covered by
each of the following polygon-or-line layers:

    buildings           — polygon footprints from source/buildings.geojson
    parks               — polygon footprints from source/parks.geojson
                          (the file is treated as already curated for
                          "public civic use" during QGIS export)
    parking (surface)   — parking polygons whose `building` tag is unset
                          (surface lots — the legible, comparable metric)
    parking (total)     — all parking polygons including structures
                          (multi-level structures are under-counted because
                          only the building footprint is captured)
    roads               — line geometries from source/highways.geojson,
                          buffered to a per-class estimated width and
                          dissolved

These categories OVERLAP (a road runs through a park; a building sits on
a parking lot). The script reports two views:

  per-layer percentages         each layer's footprint as a % of the SAD,
                                independent of the others. Sum will exceed
                                100% because of overlap.

  partition_pct                  a non-overlapping partition produced by
                                claiming area in priority order
                                buildings > roads > parking_surface > parks.
                                Sums to <= 100%, with "other" filling the
                                gap. The priority is deliberate: buildings
                                are the figure, roads structure the void,
                                parking is the built-but-unbuilt, parks are
                                the public ground.

OUTPUTS
    derived/<sad>/plan_footprint.json    JSON with all the values and
                                          methodological notes.

ROAD WIDTH ESTIMATES
    OSM roads are lines without intrinsic width. Per-class defaults:
        motorway / trunk:                14 m
        primary:                         12 m
        secondary:                       10 m
        tertiary:                         8 m
        residential / unclassified:       6 m
        living_street:                    5 m
        service:                          5 m
        footway / cycleway / path:        2 m
        steps / pedestrian / track:       2 m

    If `lanes` is set on the feature, width is overridden to
    `max(lanes * 3.5, default)`. This captures wide arterials but does not
    under-estimate small roads. Width is reported alongside the value.

USAGE
    python module_3b_context_layers.py
        --source  data/<sad_id>/source
        --derived data/derived/per_sad/<sad_id>
        [--no-clip-to-boundary]   measure inside the canvas extent instead
                                  of the SAD boundary (rarely useful)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union


# ─── Configuration ────────────────────────────────────────────────────

ROAD_WIDTHS_M = {
    'motorway': 14, 'motorway_link': 10,
    'trunk':    14, 'trunk_link':    10,
    'primary':  12, 'primary_link':  9,
    'secondary': 10, 'secondary_link': 8,
    'tertiary': 8,  'tertiary_link': 6,
    'residential': 6, 'unclassified': 6, 'living_street': 5,
    'service':   5,
    'footway':   2, 'cycleway': 2, 'path': 2, 'pedestrian': 4,
    'steps':     2, 'track':   2,
}
ROAD_DEFAULT_WIDTH_M = 6
LANE_WIDTH_M = 3.5

# Highway classes excluded from the road footprint entirely (these are
# almost-never paved corridors in the sense of "street area"):
ROAD_SKIP = {'proposed', 'construction', 'abandoned', 'razed', 'disused'}


# ─── Helpers ──────────────────────────────────────────────────────────

def read_optional(path: Path):
    """Read a GeoDataFrame if it exists, else return None."""
    if not path.exists():
        return None
    try:
        gdf = gpd.read_file(path)
    except Exception as e:
        print(f"  WARN: failed to read {path.name}: {e}")
        return None
    return gdf if not gdf.empty else None


def _to_metric(gdf, metric_crs):
    """Reproject (handles no-CRS gracefully by assuming WGS84)."""
    if gdf.crs is None:
        gdf = gdf.set_crs('EPSG:4326')
    return gdf.to_crs(metric_crs)


def estimate_road_widths(roads):
    """
    Per-feature width in meters, using class default unless `lanes` is set.
    Returns a Series the same length as `roads`.
    """
    hw = roads.get('highway')
    lanes = pd.to_numeric(roads.get('lanes'), errors='coerce') \
        if 'lanes' in roads.columns else pd.Series([None] * len(roads))

    widths = []
    for h, ln in zip(hw, lanes):
        default = ROAD_WIDTHS_M.get(h, ROAD_DEFAULT_WIDTH_M)
        if pd.notna(ln) and ln > 0:
            widths.append(max(ln * LANE_WIDTH_M, default))
        else:
            widths.append(default)
    return pd.Series(widths, index=roads.index)


def buffer_roads(roads, sad_geom):
    """
    Buffer line geometries by half their estimated width, dissolve, clip to
    the SAD. Returns (geom, mean_width_m).
    """
    if roads is None or roads.empty:
        return None, 0.0

    hw = roads.get('highway')
    if hw is not None:
        roads = roads[~hw.isin(ROAD_SKIP)].copy()
    if roads.empty:
        return None, 0.0

    widths = estimate_road_widths(roads)
    buffered = []
    for geom, w in zip(roads.geometry, widths):
        if geom is None or geom.is_empty:
            continue
        # cap_style=2 is flat, join_style=2 is mitre — usual for road buffers
        buffered.append(geom.buffer(w / 2.0, cap_style=2, join_style=2))
    if not buffered:
        return None, 0.0

    merged = unary_union(buffered)
    clipped = merged.intersection(sad_geom)
    return clipped, float(widths.mean())


# Residential building footprints from the OSM `building` tag — the honest
# residential signal that replaces the sparse/inconsistent Overture residential
# POIs. Tag set aligned with the viewer and Module 10.
RESIDENTIAL_BUILDING_TAGS = {
    'apartments', 'residential', 'house', 'houses', 'detached',
    'semidetached_house', 'terrace', 'dormitory', 'bungalow',
}


def split_parking(parking):
    """Return (surface_lots, structures) GeoDataFrames."""
    if parking is None or parking.empty:
        return None, None
    building_col = parking.get('building')
    if building_col is None:
        # No `building` column at all — treat everything as surface lots
        return parking, parking.iloc[0:0]
    is_structure = building_col.notna() & (building_col.astype(str) != '')
    return parking[~is_structure].copy(), parking[is_structure].copy()


def clip_and_area(gdf, sad_geom):
    """Clip GeoDataFrame to sad_geom, return total area in m²."""
    if gdf is None or gdf.empty:
        return 0.0, None
    parts = []
    for g in gdf.geometry:
        if g is None or g.is_empty:
            continue
        try:
            clipped = g.intersection(sad_geom)
        except Exception:
            try:
                clipped = g.buffer(0).intersection(sad_geom)
            except Exception:
                continue
        if not clipped.is_empty:
            parts.append(clipped)
    if not parts:
        return 0.0, None
    merged = unary_union(parts)
    return merged.area, merged


def pct(area, total):
    return round(100.0 * area / total, 2) if total > 0 else 0.0


# ─── Main computation ────────────────────────────────────────────────

def compute_footprint(source_dir: Path, clip_to_boundary: bool = True) -> dict:
    # Required: buildings + boundary
    buildings = read_optional(source_dir / 'buildings.geojson')
    boundary = read_optional(source_dir / 'sad_boundary.geojson')
    if buildings is None:
        sys.exit(f"buildings.geojson missing under {source_dir}")
    if boundary is None:
        sys.exit(f"sad_boundary.geojson missing under {source_dir}")

    # Find a metric CRS to work in. image_extent.geojson carries it in its
    # properties (written by setup_sad_source.py); if not present, derive
    # from the boundary centroid.
    image_extent = read_optional(source_dir / 'image_extent.geojson')
    metric_crs = None
    if image_extent is not None and 'utm_crs' in image_extent.columns:
        v = image_extent['utm_crs'].iloc[0]
        if isinstance(v, str) and v.startswith('EPSG:'):
            metric_crs = v
    if metric_crs is None:
        c = boundary.geometry.iloc[0].centroid
        zone = int((c.x + 180) / 6) + 1
        metric_crs = (f"EPSG:{32600 + zone}" if c.y >= 0
                      else f"EPSG:{32700 + zone}")

    boundary_m = _to_metric(boundary, metric_crs)
    sad_geom = boundary_m.geometry.iloc[0]
    canvas_geom = (None if image_extent is None
                   else _to_metric(image_extent, metric_crs).geometry.iloc[0])
    clip_geom = sad_geom if clip_to_boundary else (canvas_geom or sad_geom)
    sad_area = clip_geom.area

    # Buildings
    buildings_m = _to_metric(buildings, metric_crs)
    b_area, b_union = clip_and_area(buildings_m, clip_geom)

    # Residential buildings (OSM `building` tag) — honest residential signal.
    res_b_area = 0.0
    res_b_count = 0
    _bcol = buildings_m.get('building')
    if _bcol is not None:
        _is_res = _bcol.astype(str).str.lower().str.strip().isin(
            RESIDENTIAL_BUILDING_TAGS)
        _res_b = buildings_m[_is_res]
        res_b_count = int(len(_res_b))
        res_b_area, _ = clip_and_area(_res_b, clip_geom)

    # Parks (optional)
    parks = read_optional(source_dir / 'parks.geojson')
    parks_m = _to_metric(parks, metric_crs) if parks is not None else None
    p_area, p_union = clip_and_area(parks_m, clip_geom)

    # Parking — split surface vs structures, report both
    parking = read_optional(source_dir / 'parking.geojson')
    parking_m = _to_metric(parking, metric_crs) if parking is not None else None
    surface, structures = split_parking(parking_m)
    surf_area, surf_union = clip_and_area(surface, clip_geom)
    total_park_area, _ = clip_and_area(parking_m, clip_geom)

    # Roads (line geometries, buffered to estimated width)
    roads = read_optional(source_dir / 'highways.geojson')
    roads_m = _to_metric(roads, metric_crs) if roads is not None else None
    road_geom, road_mean_width = buffer_roads(roads_m, clip_geom) \
        if roads_m is not None else (None, 0.0)
    road_area = road_geom.area if road_geom is not None else 0.0

    # ─── Partition view: claim in priority order ─────────────────────
    # buildings -> roads -> parking_surface -> parks -> other
    remaining = clip_geom
    parts = {}

    def claim(geom, key):
        nonlocal remaining
        if geom is None:
            parts[key] = 0.0
            return
        claimed = geom.intersection(remaining)
        a = claimed.area if not claimed.is_empty else 0.0
        parts[key] = a
        if a > 0:
            remaining = remaining.difference(claimed)

    claim(b_union,  'buildings')
    claim(road_geom, 'roads')
    claim(surf_union, 'parking_surface')
    claim(p_union,  'parks')
    parts['other'] = max(0.0, remaining.area)

    # ─── Assemble result ─────────────────────────────────────────────
    result = {
        'sad_id':       None,  # filled in by main
        'sad_area_m2':  round(sad_area, 1),
        'clipped_to':   'sad_boundary' if clip_to_boundary else 'canvas_extent',
        'metric_crs':   metric_crs,
        'per_layer': {
            'buildings':       {'area_m2': round(b_area, 1),
                                'pct_of_sad': pct(b_area, sad_area)},
            'residential_buildings': {
                'area_m2': round(res_b_area, 1),
                'pct_of_sad': pct(res_b_area, sad_area),
                'pct_of_building_footprint': pct(res_b_area, b_area),
                'count': res_b_count,
                'source': 'OSM building tag',
                'note': ('residential footprint from OSM building tags '
                         '(apartments/residential/house/terrace/dormitory/etc.); '
                         'replaces Overture residential POIs'),
            },
            'parks':           {'area_m2': round(p_area, 1),
                                'pct_of_sad': pct(p_area, sad_area)},
            'parking_surface': {'area_m2': round(surf_area, 1),
                                'pct_of_sad': pct(surf_area, sad_area)},
            'parking_total':   {'area_m2': round(total_park_area, 1),
                                'pct_of_sad': pct(total_park_area, sad_area),
                                'note': ('includes structures; footprints '
                                         'under-count multi-level garages')},
            'roads':           {'area_m2': round(road_area, 1),
                                'pct_of_sad': pct(road_area, sad_area),
                                'method': (f"buffered OSM lines; mean "
                                           f"estimated width "
                                           f"{road_mean_width:.1f} m")},
        },
        'partition_pct': {k: pct(v, sad_area) for k, v in parts.items()},
        'partition_priority': ['buildings', 'roads', 'parking_surface',
                               'parks', 'other'],
        'notes': [
            "per_layer values are raw footprints; categories overlap.",
            "partition_pct claims area in priority order so values sum to "
            "<=100% with 'other' filling the gap.",
            "Road area uses buffered line geometries — see road method note.",
            "parking_surface excludes any feature with a `building` tag set.",
        ],
    }
    return result


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('--source',  type=Path, required=True,
                    help='data/<sad_id>/source/')
    ap.add_argument('--derived', type=Path, required=True,
                    help='data/derived/per_sad/<sad_id>/')
    ap.add_argument('--no-clip-to-boundary', action='store_true',
                    help='Measure inside canvas extent rather than the SAD '
                         'boundary. Rarely useful.')
    args = ap.parse_args()

    source = args.source.resolve()
    derived = args.derived.resolve()
    if not source.is_dir():
        sys.exit(f"source not found: {source}")
    derived.mkdir(parents=True, exist_ok=True)

    sad_id = source.parent.name
    print(f"Computing plan footprint for {sad_id}...")
    result = compute_footprint(source, not args.no_clip_to_boundary)
    result['sad_id'] = sad_id

    out = derived / 'plan_footprint.json'
    out.write_text(json.dumps(result, indent=2))
    print(f"  wrote {out}")
    print()
    print(f"  partition (% of SAD area, non-overlapping):")
    for k in result['partition_priority']:
        print(f"    {k:18s} {result['partition_pct'][k]:5.1f}%")
    print()
    print(f"  per-layer footprints (% of SAD area, may overlap):")
    for k, v in result['per_layer'].items():
        print(f"    {k:18s} {v['pct_of_sad']:5.1f}%  ({v['area_m2']:>10,.0f} m²)")


if __name__ == '__main__':
    main()
