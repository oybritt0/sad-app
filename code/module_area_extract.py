"""
module_area_extract.py

Bounded acquisition for a drawn district. The user draws a SAD boundary; we
resolve a fixed acquisition EXTENT (the SAD itself, or its host city), and pull
every layer once within that extent — never streaming as the map moves:

    buildings / parks / parking / highways   — OpenStreetMap via OSMnx
    pois                                      — Overture via module_overture_places

Each layer is produced in the SAME file shape the pipeline already consumes
(source/buildings.geojson, parks.geojson, parking.geojson, highways.geojson,
rod_places.geojson), so a drawn area can be SAVED as a first-class district that
the viewer and the rest of the pipeline understand.

Used by the draw-a-district server (/extract per layer, /save_area to persist).

NETWORK: OSMnx hits the Overpass API; Overture hits S3 (same as the ROD tool).
Neither is reachable from a sandbox, so the first real pull happens on your
machine — same as M4/M4c.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
from shapely.geometry import shape
from rasterio.features import rasterize
from PIL import Image
from shared.geo_utils import affine_from_geo_bbox

sys.path.insert(0, str(Path(__file__).resolve().parent))
import module_04c_municipal_context as m4c   # extent resolution (host place)
import module_overture_places as ovp          # POIs

EQUAL_AREA = 'EPSG:5070'
MAX_BUILDINGS = 20000          # cap so a whole-city pull can't hang the browser

# OSM tag sets per layer (mirrors what the pipeline's source files contain)
OSM_TAGS = {
    'buildings': {'building': True},
    'parks': {'leisure': ['park', 'garden', 'recreation_ground', 'pitch', 'playground'],
              'landuse': ['grass', 'recreation_ground', 'village_green']},
    'parking': {'amenity': 'parking'},
}
# transit POIs pulled from Overture (mirrors the ROD tool's Places search).
# Leaf categories vary by release; tune this set against a live pull if needed.
TRANSIT_CATS = {
    'bus_station', 'bus_stop', 'train_station', 'subway_station', 'metro_station',
    'light_rail_station', 'tram_station', 'transit_station', 'transit_stop',
    'public_transportation', 'transportation_service', 'railway_station',
    'commuter_rail_station', 'ferry_terminal', 'transit_depot',
}
KEEP_COLS = {
    'buildings': ['building', 'name', 'height', 'building:levels'],
    'parks': ['leisure', 'landuse', 'name'],
    'parking': ['amenity', 'parking', 'building', 'access'],
    'highways': ['highway', 'name', 'lanes', 'oneway'],
    'transit': ['railway', 'public_transport', 'amenity', 'highway', 'name'],
}

_OSM_READY = False

# Public Overpass mirrors, tried in order. The primary (.de) is frequently
# overloaded; rotating to a mirror on a connection timeout keeps draw/extract
# working instead of 500-ing the whole save.
OVERPASS_MIRRORS = [
    'https://overpass-api.de/api',
    'https://overpass.kumi.systems/api',
    'https://overpass.private.coffee/api',
    ]


def _set_overpass(ox, url):
    for attr in ('overpass_url', 'overpass_endpoint'):
        if hasattr(ox.settings, attr):
            try:
                setattr(ox.settings, attr, url)
                return
            except Exception:
                pass


def _overpass_retry(call):
    """Retry primary Overpass up to 5x with exponential backoff before mirrors."""
    import time
    try:
        import osmnx as ox
    except ImportError:
        raise RuntimeError("osmnx not installed")
    last = None
    primary = OVERPASS_MIRRORS[0]
    backoffs = [2, 4, 8, 12, 16]  # seconds
    _set_overpass(ox, primary)
    for i, wait in enumerate(backoffs):
        try:
            return call(ox)
        except Exception as e:
            last = e
            msg = str(e).lower()
            if 'timeout' in msg or 'unreachable' in msg or '504' in msg or '429' in msg or 'too many' in msg:
                print(f"  [overpass] primary attempt {i+1}/5 failed ({type(e).__name__}); waiting {wait}s and retrying...")
                time.sleep(wait)
                continue
            raise
    # Primary exhausted — try other mirrors once each (likely blocked but worth a shot)
    for url in OVERPASS_MIRRORS[1:]:
        _set_overpass(ox, url)
        try:
            return call(ox)
        except Exception as e:
            last = e
            print(f"  [overpass] {url} unreachable, trying next mirror...")
    raise last if last else RuntimeError('All Overpass mirrors unreachable')

def _overpass_retry_OLD(call):
    """Run an OSMnx call, rotating Overpass mirrors on connection/timeout
    errors. Empty-area responses are re-raised immediately (handled upstream)."""
    ox = _osm()
    last = None
    for url in OVERPASS_MIRRORS:
        _set_overpass(ox, url)
        try:
            return call(ox)
        except Exception as e:
            if _is_empty_osm(e):
                raise
            n = type(e).__name__.lower()
            if 'timeout' in n or 'connection' in n or 'maxretry' in n:
                last = e
                print(f"  [overpass] {url} unreachable, trying next mirror...")
                continue
            raise
    raise last if last else RuntimeError('All Overpass mirrors unreachable')


def _osm():
    """Import OSMnx once and enable its on-disk response cache so repeat pulls of
    the same area are fast even across server restarts (the first pull is still a
    live Overpass query)."""
    global _OSM_READY
    import osmnx as ox
    if not _OSM_READY:
        try:
            ox.settings.use_cache = True
            ox.settings.log_console = False
            ox.settings.requests_timeout = 180
        except Exception:
            pass
        _OSM_READY = True
    return ox


# ── extent resolution ────────────────────────────────────────────────────────
def resolve_extent(geometry: dict, extent: str, year: int, api_key: str, extent_km2: float = 4.0):
    """Return (extent_polygon_4326, info). extent: 'sad' | 'city'.

    For 'sad', the drawn polygon is buffered outward to a target analysis
    canvas of ~4 km^2 (roughly 20-30 min walking radius), providing context
    that matches the scale of pre-saved corpus districts. Draws already
    >= the target keep their original shape.
    """
    poly = shape(geometry)
    if extent == 'sad':
        # Axis-aligned square canvas centered on the polygon centroid.
        # Minimum 4 km^2 (2 km half-extent each direction) so small draws still
        # get usable neighborhood context. Larger draws expand to fit their
        # full bounding box but stay square so the SVG/PDF exports keep a
        # consistent 1:1 aspect ratio that stacks cleanly in decks.
        # Caller can request a larger canvas via extent_km2 (clamped 4-12 km^2).
        import math as _math
        target_km2 = max(1.0, min(12.0, float(extent_km2)))
        MIN_HALF_M = _math.sqrt(target_km2 * 1_000_000.0) / 2.0
        TARGET_KM2 = target_km2
        from shapely.geometry import box as _box
        gs = gpd.GeoSeries([poly], crs='EPSG:4326').to_crs(EQUAL_AREA)
        poly_m = gs.iloc[0]
        original_area_km2 = poly_m.area / 1_000_000.0
        # Centroid of the actual polygon (not the bbox) so long/asymmetric
        # draws pull from where the mass is, not where the bbox happens to be.
        cx, cy = poly_m.centroid.x, poly_m.centroid.y
        # Size the square to the larger of (minimum, drawn bbox half-extent).
        minx, miny, maxx, maxy = poly_m.bounds
        half = max(MIN_HALF_M, (maxx - minx) / 2.0, (maxy - miny) / 2.0)
        square_m = _box(cx - half, cy - half, cx + half, cy + half)
        achieved_km2 = (2.0 * half / 1000.0) ** 2
        square = gpd.GeoSeries([square_m], crs=EQUAL_AREA).to_crs('EPSG:4326').iloc[0]
        return square, {'kind': 'sad_square',
                        'name': 'Drawn boundary + 4 km^2 square canvas',
                        'area_km2': round(achieved_km2, 2),
                        'original_area_km2': round(original_area_km2, 2),
                        'side_km': round(2.0 * half / 1000.0, 2)}
    c = poly.centroid
    place_geom, place_info = m4c.resolve_place(c.x, c.y, year)
    if place_geom is None:
        # fall back to the drawn boundary if no place resolves
        return poly, {'kind': 'sad', 'name': 'Drawn boundary (no place found)'}
    return place_geom, {**(place_info or {}), 'kind': 'city'}


# ── OSM + POI extraction ─────────────────────────────────────────────────────
def _features(poly, tags):
    return _overpass_retry(lambda ox: (
        getattr(ox, 'features_from_polygon', None) or
        getattr(ox, 'geometries_from_polygon'))(poly, tags))


def _poly_geojson(poly):
    return json.loads(gpd.GeoSeries([poly], crs='EPSG:4326').to_json())['features'][0]['geometry']


def _points_fc(gdf, keep) -> dict:
    """Any geometry -> Point (centroid for ways/areas); keep simple cols."""
    if gdf is None or len(gdf) == 0:
        return dict(_EMPTY)
    gdf = gdf.to_crs('EPSG:4326').reset_index(drop=True)
    cols = [c for c in keep if c in gdf.columns]
    feats = []
    for _, row in gdf.iterrows():
        g = row.geometry
        if g is None or g.is_empty:
            continue
        pt = g if g.geom_type == 'Point' else g.representative_point()
        props = {c: (None if row[c] is None else (row[c] if isinstance(row[c], (str, int, float)) else str(row[c]))) for c in cols}
        feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [pt.x, pt.y]}, 'properties': props})
    return {'type': 'FeatureCollection', 'features': feats}


def _is_empty_osm(e) -> bool:
    """OSMnx 2.x raises when an area has no matching features; treat as empty."""
    n = type(e).__name__
    return 'InsufficientResponse' in n or 'EmptyOverpass' in n


_EMPTY = {'type': 'FeatureCollection', 'features': []}


def _to_fc(gdf: gpd.GeoDataFrame, keep, poly_only=True) -> dict:
    if gdf is None or len(gdf) == 0:
        return {'type': 'FeatureCollection', 'features': []}
    gdf = gdf.to_crs('EPSG:4326').reset_index(drop=True)
    if poly_only:
        gdf = gdf[gdf.geometry.type.isin(['Polygon', 'MultiPolygon'])].copy()
    cols = [c for c in keep if c in gdf.columns]
    out = gdf[cols + ['geometry']].copy()
    # stringify any non-scalar cells so to_json doesn't choke
    for c in cols:
        out[c] = out[c].apply(lambda v: None if v is None else (v if isinstance(v, (str, int, float)) else str(v)))
    return json.loads(out.to_json())


def extract_layer(extent_poly, layer: str, release: str | None = None) -> dict:
    if layer == 'pois':
        return ovp.places_in_polygon(_poly_geojson(extent_poly), release=release)
    if layer == 'highways':
        ox = _osm()
        try:
            G = _overpass_retry(lambda ox: ox.graph_from_polygon(
                extent_poly, network_type='drive', simplify=True, retain_all=True))
        except Exception as e:
            if _is_empty_osm(e):
                return dict(_EMPTY)
            raise
        edges = ox.graph_to_gdfs(G, nodes=False)
        return _to_fc(edges, KEEP_COLS['highways'], poly_only=False)
    if layer == 'transit':
        # Pull transit as POIs from Overture (same engine as the ROD tool),
        # filtered to transportation categories — not an OSM tag query.
        return ovp.places_in_polygon(_poly_geojson(extent_poly), categories=list(TRANSIT_CATS))
    if layer in OSM_TAGS:
        try:
            gdf = _features(extent_poly, OSM_TAGS[layer])
        except Exception as e:
            if _is_empty_osm(e):
                return dict(_EMPTY)
            raise
        fc = _to_fc(gdf, KEEP_COLS[layer], poly_only=True)
        if layer == 'buildings' and len(fc['features']) > MAX_BUILDINGS:
            fc = _cap_buildings(fc)
        return fc
    raise ValueError(f"unknown layer: {layer}")


def _cap_buildings(fc: dict) -> dict:
    """Keep the largest MAX_BUILDINGS footprints; flag truncation."""
    feats = fc['features']
    gdf = gpd.GeoDataFrame.from_features(feats, crs='EPSG:4326').to_crs(EQUAL_AREA)
    gdf['_a'] = gdf.geometry.area
    order = gdf['_a'].sort_values(ascending=False).index[:MAX_BUILDINGS]
    kept = [feats[i] for i in order]
    return {'type': 'FeatureCollection', 'features': kept,
            'truncated': True, 'total': len(feats), 'kept': len(kept)}


def walkshed_polygon(drawn_geometry: dict, minutes=(5, 10, 15), speed_m_s: float = 1.35) -> dict:
    """Pedestrian isochrone(s) from the drawn boundary's centroid (M15-style):
    build a walk network around the centroid, Dijkstra out to each time budget,
    concave-hull the reachable nodes. Independent of the acquisition extent."""
    ox = _osm()
    import networkx as nx
    from shapely.geometry import shape, MultiPoint, mapping
    try:
        from shapely import concave_hull
    except Exception:
        concave_hull = None
    c = shape(drawn_geometry).centroid
    budget = max(minutes) * 60 * speed_m_s
    G = _overpass_retry(lambda ox: ox.graph_from_point(
        (c.y, c.x), dist=budget * 1.4, network_type='walk', simplify=True))
    if G.number_of_nodes() == 0:
        return dict(_EMPTY)
    origin = ox.distance.nearest_nodes(G, c.x, c.y)
    feats = []
    for m in sorted(minutes, reverse=True):     # largest first so smaller sit on top
        bud = m * 60 * speed_m_s
        reach = nx.single_source_dijkstra_path_length(G, origin, cutoff=bud, weight='length')
        pts = [(G.nodes[n]['x'], G.nodes[n]['y']) for n in reach]
        if len(pts) < 3:
            continue
        mp = MultiPoint(pts)
        hull = concave_hull(mp, ratio=0.3) if concave_hull else mp.convex_hull
        feats.append({'type': 'Feature', 'properties': {'minutes': m}, 'geometry': mapping(hull)})
    return {'type': 'FeatureCollection', 'features': feats}


def layer_meta(fc: dict) -> dict:
    return {'count': len(fc.get('features', [])),
            'truncated': bool(fc.get('truncated')),
            'total': fc.get('total')}


# ── save as a district ───────────────────────────────────────────────────────
def _slug(name: str) -> str:
    s = re.sub(r'[^A-Za-z0-9]+', '-', (name or 'drawn').strip()).strip('-')
    return s or 'drawn'


def _rewrite_pois_for_m3(fc: dict) -> dict:
    """Rewrite the ROD-formatted POI FC into the schema M3 expects.

    M3 reads its input file expecting raw-Overture column names:
      - primary_category   (string)
      - taxonomy_hierarchy (list — M3 takes the first element as top-level)

    The ROD-formatted FC has `category` (the Overture primary) and
    `rossetti_category` (the rollup). Rename `category` -> `primary_category`
    and synthesize `taxonomy_hierarchy` as a single-element list. M3 will
    then run its own rollup logic correctly. The ROD-formatted file at
    `source/rod_places.geojson` is written separately for the viewer / M16.
    """
    out_feats = []
    for f in fc.get('features', []):
        p = dict(f.get('properties', {}))
        pc = p.pop('category', None)
        p['primary_category'] = pc
        p['taxonomy_hierarchy'] = [pc] if pc else []
        out_feats.append({
            'type': 'Feature',
            'geometry': f.get('geometry'),
            'properties': p,
        })
    return {'type': 'FeatureCollection', 'features': out_feats}

def _write_figureground_for_drawn(extent_poly, buildings_fc: dict, derived_dir: Path) -> bool:
    """Rasterize buildings into M2's figureground_mask.npy (and a .png).

    M1 (module_01_image_generator) normally produces these, but for drawn
    districts save_district writes manifest.json itself which makes M1's
    marker check skip the run. Lift M1's rasterize block here so M2 / M2b /
    M2c can run on drawn districts without needing M1.

    Layout matches M1 exactly:
      - 1080x1080 binary mask, uint8, 1=building/0=void
      - figureground.png: building=BLACK (0), void=WHITE (255)
      - figureground_mask.npy: raw mask
    Returns True on success, False otherwise.
    """
    IMAGE_W = 1080
    IMAGE_H = 1080
    try:
        derived_dir.mkdir(parents=True, exist_ok=True)
        feats = buildings_fc.get('features', []) if isinstance(buildings_fc, dict) else []
        if not feats:
            print('  WARN figureground skip: no building features for drawn district')
            return False
        bgdf = gpd.GeoDataFrame.from_features(feats, crs='EPSG:4326')
        minlon, minlat, maxlon, maxlat = extent_poly.bounds
        bbox = (minlon, minlat, maxlon, maxlat)
        transform = affine_from_geo_bbox(bbox, IMAGE_W, IMAGE_H)
        in_frame = bgdf[bgdf.intersects(extent_poly)]
        valid_geoms = [g for g in in_frame.geometry if g is not None and not g.is_empty]
        if not valid_geoms:
            print('  WARN figureground skip: no buildings intersect extent')
            return False
        mask = rasterize(
            [(geom, 1) for geom in valid_geoms],
            out_shape=(IMAGE_H, IMAGE_W),
            transform=transform,
            fill=0,
            dtype='uint8',
        )
        img_array = np.where(mask == 1, 0, 255).astype('uint8')
        Image.fromarray(img_array, mode='L').save(derived_dir / 'figureground.png')
        np.save(derived_dir / 'figureground_mask.npy', mask)
        print(f'  wrote figureground.png + figureground_mask.npy ({len(valid_geoms)} buildings)')
        return True
    except Exception as e:
        print(f'  WARN figureground generation failed: {e}')
        return False

def save_district(name: str, drawn_geometry: dict, extent_poly, extent_info: dict,
                  data_dir: Path, layers: dict, year: int, api_key: str) -> str:
    """Write source/*.geojson for a new district folder; returns sad_id.

    The viewer/pipeline read these from source/. Full corpus membership
    (census, embedding) still requires running the analysis modules afterward —
    this lays down the raw layers + boundary so the area is immediately viewable.
    """
    existing = [d.name for d in data_dir.iterdir() if d.is_dir() and re.match(r'^\d{2}_', d.name)]
    nums = [int(n[:2]) for n in existing if n[:2].isdigit()]
    nxt = (max(nums) + 1) if nums else 2
    city = (extent_info.get('name') or 'City').replace(' ', '-')
    sad_id = f"{nxt:02d}_{_slug(name)}_{_slug(city)}"
    src = data_dir / sad_id / 'source'
    src.mkdir(parents=True, exist_ok=True)

    # boundary = the drawn SAD
    (src / 'sad_boundary.geojson').write_text(json.dumps(
        {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {}, 'geometry': drawn_geometry}]}))

    # image_extent = analysis-canvas bbox, no inflation. With the square 4 km^2
    # canvas, the extent_poly already includes the right amount of context;
    # adding a buffer here would just create dead space outside the data pull.
    # The SVG/PDF exports render exactly the square the user drew + minimum
    # context, with a clean 1:1 aspect ratio that stacks in decks.
    from shapely.geometry import shape as _shape, box as _box
    minx, miny, maxx, maxy = extent_poly.bounds
    ext = _box(minx, miny, maxx, maxy)
    (src / 'image_extent.geojson').write_text(json.dumps(
        {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {}, 'geometry': ext.__geo_interface__}]}))

    # write each layer to the path(s) the viewer's LAYER_CATALOG expects.
    # buildings goes to BOTH source/ (module_3b reads it) and derived/ (viewer reads it).
    path_map = {
        'buildings': ['source/buildings.geojson', 'derived/buildings_enriched.geojson'],
        'parks':     ['source/parks.geojson'],
        'parking':   ['source/parking.geojson'],
        'highways':  ['source/highways.geojson'],
        'pois':      ['source/rod_places.geojson', '03_ROD-Search-Tool/ROD_Search/overture_places.geojson'],
        'transit':   ['derived/transit/transit_stations.geojson'],
        'walkshed':  ['derived/walkshed/walksheds.geojson'],
        'census':    ['derived/census_blockgroups.geojson'],
    }
    OSM_EXTRACTABLE = {'buildings', 'parks', 'parking', 'highways', 'pois', 'transit'}
    base = data_dir / sad_id
    for key, rels in path_map.items():
        fc = layers.get(key)
        if fc is None and key in OSM_EXTRACTABLE:           # walkshed/census must be supplied by caller
            try:
                fc = extract_layer(extent_poly, key)
            except Exception:
                fc = None
        if fc is None:
            continue
        for rel in rels:
            p = base / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            # Special-case the canonical-path POI file: M3 expects raw Overture
            # column names (primary_category, taxonomy_hierarchy). Rewrite the
            # ROD-formatted features into M3's expected schema before writing.
            if rel.endswith('overture_places.geojson') and key == 'pois':
                fc_for_m3 = _rewrite_pois_for_m3(fc)
                p.write_text(json.dumps(fc_for_m3))
            else:
                p.write_text(json.dumps(fc))
    (src / 'extent.json').write_text(json.dumps({'extent': extent_info, 'name': name}, indent=2))
    # Write a georeferencing manifest now so the metadata modules (M3, M4,
    # M20-22) can run without M1's image generator (which targets the CV path).
    try:
        import make_drawn_manifest as _mdm
        _mdm.write_drawn_manifest(base)
    except Exception as e:
        print(f"  WARN: drawn manifest generation failed ({e})")
    # buildings_enriched.gpkg from the OSM footprints, so the cross-SAD
    # embedding (M8) can place this district in the latent field.
    try:
        import make_drawn_buildings as _mdb
        _mdb.write_drawn_buildings(base)
    except Exception as e:
        print(f"  WARN: drawn buildings_enriched generation failed ({e})")

    # Write figureground mask + PNG inline so M2 / M2b / M2c don't need M1.
    # M1's marker (manifest.json) gets written by save_district above, which
    # causes M1 to skip; produce M1's CV outputs here from buildings + extent.
    buildings_fc_for_fg = layers.get('buildings')
    if buildings_fc_for_fg is None:
        try:
            buildings_fc_for_fg = extract_layer(extent_poly, 'buildings')
        except Exception:
            buildings_fc_for_fg = None
    if buildings_fc_for_fg is not None:
        _write_figureground_for_drawn(extent_poly, buildings_fc_for_fg, base / 'derived')

    return sad_id
