#!/usr/bin/env python3
"""
module_11_nolli_map.py

Produce a vector SVG figure-ground "Nolli map" for one SAD, layered for
clean editing in Illustrator.

The output is one SVG file with these named <g> groups, in draw order
(back to front):
    canvas      thin outline of the analysis frame
    parks       light grey solid fill (public open space)
    parking     medium grey diagonal hatch (parking polygons)
    streets     grey lines, stroke-width scaled by OSM highway class
    buildings   solid near-black fill (the figure)
    boundary    dashed outline of the SAD boundary
    scale_bar   500 m two-segment scale bar, bottom-left
    north_arrow simple triangle + "N", top-right (grid north of the
                local UTM zone, which differs from true geographic north
                by a small meridian-convergence angle — invisible at
                this scale)

Inside each group, every feature is its own <path> element so it can be
selected individually in Illustrator. Hatch patterns are defined in <defs>
and referenced as fills. No labels, no street names, no scale bar — pure
spatial figure, black/white/grey only.

USAGE
    python module_11_nolli_map.py
        --source  data/<sad_id>/source
        --derived data/derived/per_sad/<sad_id>
        [--size 1600]            output viewport size in pixels (square)
        [--margin 60]            inner margin in pixels
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import geopandas as gpd


# ─── Visual style — everything is a shade of grey ────────────────────

STYLE = {
    'background':      '#ffffff',
    'canvas_stroke':   '#bcbcbc',
    'canvas_width':    0.6,
    'parks_fill':      '#dcdcdc',
    'parking_bg':      '#ececec',
    'parking_hatch':   '#8a8a8a',
    'parking_period':  6,     # px between hatch lines
    'parking_stroke':  1.2,   # px line weight of hatch
    'street_color':    '#404040',
    'building_fill':   '#0f0f0f',
    'building_stroke': '#0f0f0f',
    'building_width':  0.4,
    'boundary_stroke': '#202020',
    'boundary_width':  1.4,
    'boundary_dash':   '6 4',
    'chrome_color':    '#1a1a1a',  # scale bar + north arrow
    'chrome_font':     'sans-serif',
}

# Scale bar: same length value across every district so cross-district
# visual comparison is direct. Switch SCALE_BAR_UNIT to 'm' for metric.
SCALE_BAR_VALUE = 1000
SCALE_BAR_UNIT = 'ft'   # 'ft' or 'm'

# Gutter between the canvas's drawn outline and the chrome (scale bar +
# north arrow), in SVG pixels. The default --margin must be larger than
# CHROME_GUTTER + chrome height for the chrome to fit under the canvas.
CHROME_GUTTER = 16

# Stroke widths (in SVG pixels) for street rendering, by OSM highway class.
# Tuned so motorways read at a glance and footways are visible but quiet.
STREET_WIDTHS = {
    'motorway':       3.5, 'motorway_link':   2.0,
    'trunk':          3.5, 'trunk_link':      2.0,
    'primary':        2.6, 'primary_link':    1.7,
    'secondary':      2.0, 'secondary_link':  1.5,
    'tertiary':       1.6, 'tertiary_link':   1.2,
    'residential':    1.2, 'unclassified':    1.2, 'living_street': 1.0,
    'service':        0.8,
    'footway':        0.5, 'cycleway':        0.5, 'path':          0.5,
    'pedestrian':     1.0,
    'steps':          0.4, 'track':           0.5,
}
STREET_DEFAULT = 1.0
SKIP_HIGHWAYS = {'proposed', 'construction', 'abandoned', 'razed', 'disused'}


# ─── Geometry / IO helpers ───────────────────────────────────────────

def read_optional(path: Path):
    if not path.exists():
        return None
    try:
        gdf = gpd.read_file(path)
    except Exception as e:
        print(f"  WARN: failed to read {path.name}: {e}")
        return None
    return gdf if not gdf.empty else None


def _to_metric(gdf, metric_crs):
    if gdf.crs is None:
        gdf = gdf.set_crs('EPSG:4326')
    return gdf.to_crs(metric_crs)


def _resolve_metric_crs(source_dir, boundary):
    """Pick a metric CRS the same way module_3b does."""
    image_extent = read_optional(source_dir / 'image_extent.geojson')
    if image_extent is not None and 'utm_crs' in image_extent.columns:
        v = image_extent['utm_crs'].iloc[0]
        if isinstance(v, str) and v.startswith('EPSG:'):
            return v, image_extent
    c = boundary.geometry.iloc[0].centroid
    zone = int((c.x + 180) / 6) + 1
    crs = f"EPSG:{32600 + zone}" if c.y >= 0 else f"EPSG:{32700 + zone}"
    return crs, image_extent


# ─── UTM-to-SVG transform ────────────────────────────────────────────

def make_transform(bbox, size, margin):
    """
    Build a coordinate transform from UTM (x,y) to SVG (x,y), centering
    the bbox inside a (size x size) viewport with `margin` inset, with
    aspect ratio preserved (so square SVG, possibly letterboxed).
    Returns (tx_function, svg_units_per_meter, canvas_svg_bbox) where
    canvas_svg_bbox is a dict with the actual SVG-coord extents of the
    canvas after letterboxing (xmin, ymin, xmax, ymax).
    """
    xmin, ymin, xmax, ymax = bbox
    bw, bh = xmax - xmin, ymax - ymin
    span = max(bw, bh)
    inner = size - 2 * margin
    scale = inner / span
    # Offsets so the bbox sits centred in the inner area
    ox = margin + (inner - bw * scale) / 2.0
    oy = margin + (inner - bh * scale) / 2.0

    def tx(x, y):
        sx = ox + (x - xmin) * scale
        sy = (size - oy) - (y - ymin) * scale   # flip Y
        return sx, sy

    canvas_svg = {
        'xmin': ox,
        'ymin': size - oy - bh * scale,   # SVG top  (smaller y)
        'xmax': ox + bw * scale,
        'ymax': size - oy,                 # SVG bottom (larger y)
    }
    return tx, scale, canvas_svg


def _meters_for_scale(value, unit):
    return value * 0.3048 if unit == 'ft' else float(value)


def emit_scale_bar(svg_units_per_meter, x_origin, y_origin, value, unit):
    """
    Two-segment scale bar with endpoint labels. `value` and `unit` ('ft'
    or 'm') determine the labeled length; the bar's drawn width is
    converted to SVG units via the metric scale.
    """
    meters = _meters_for_scale(value, unit)
    w = meters * svg_units_per_meter
    h = 4.5
    half = w / 2.0
    color = STYLE['chrome_color']
    font = STYLE['chrome_font']
    return (
        f'  <g id="scale_bar">\n'
        f'    <rect x="{x_origin:.1f}" y="{y_origin:.1f}" '
        f'width="{half:.1f}" height="{h}" fill="{color}"/>\n'
        f'    <rect x="{x_origin + half:.1f}" y="{y_origin:.1f}" '
        f'width="{half:.1f}" height="{h}" fill="#ffffff"/>\n'
        f'    <rect x="{x_origin:.1f}" y="{y_origin:.1f}" '
        f'width="{w:.1f}" height="{h}" '
        f'fill="none" stroke="{color}" stroke-width="0.8"/>\n'
        f'    <text x="{x_origin:.1f}" y="{y_origin - 4:.1f}" '
        f'font-family="{font}" font-size="10" fill="{color}">0</text>\n'
        f'    <text x="{x_origin + w:.1f}" y="{y_origin - 4:.1f}" '
        f'font-family="{font}" font-size="10" fill="{color}" '
        f'text-anchor="end">{value} {unit}</text>\n'
        f'  </g>\n'
    )


def emit_north_arrow(x_tip, y_tip, height=22):
    """Minimal triangle + 'N'. (x_tip, y_tip) is the arrow's tip."""
    half_w = 5.5
    color = STYLE['chrome_color']
    font = STYLE['chrome_font']
    return (
        f'  <g id="north_arrow">\n'
        f'    <polygon points="{x_tip:.1f},{y_tip:.1f} '
        f'{x_tip - half_w:.1f},{y_tip + height:.1f} '
        f'{x_tip + half_w:.1f},{y_tip + height:.1f}" fill="{color}"/>\n'
        f'    <text x="{x_tip:.1f}" y="{y_tip + height + 12:.1f}" '
        f'font-family="{font}" font-size="11" fill="{color}" '
        f'text-anchor="middle">N</text>\n'
        f'  </g>\n'
    )


# ─── Path emitters ───────────────────────────────────────────────────

def _ring_to_d(ring, tx):
    """List of coords -> SVG path 'd' fragment 'M x,y L x,y ... Z'."""
    if not ring:
        return ''
    parts = []
    for i, c in enumerate(ring):
        x, y = c[0], c[1]
        sx, sy = tx(x, y)
        parts.append(f"{'M' if i == 0 else 'L'}{sx:.1f},{sy:.1f}")
    parts.append('Z')
    return ''.join(parts)


def polygon_path_d(geom, tx):
    """Polygon or MultiPolygon -> single 'd' string with all rings."""
    if geom is None or geom.is_empty:
        return ''
    if geom.geom_type == 'Polygon':
        d = _ring_to_d(list(geom.exterior.coords), tx)
        for interior in geom.interiors:
            d += _ring_to_d(list(interior.coords), tx)
        return d
    if geom.geom_type == 'MultiPolygon':
        return ''.join(polygon_path_d(p, tx) for p in geom.geoms)
    return ''


def line_path_d(geom, tx):
    """LineString or MultiLineString -> 'd' string."""
    if geom is None or geom.is_empty:
        return ''
    if geom.geom_type == 'LineString':
        parts = []
        for i, c in enumerate(geom.coords):
            x, y = c[0], c[1]
            sx, sy = tx(x, y)
            parts.append(f"{'M' if i == 0 else 'L'}{sx:.1f},{sy:.1f}")
        return ''.join(parts)
    if geom.geom_type == 'MultiLineString':
        return ''.join(line_path_d(line, tx) for line in geom.geoms)
    return ''


# ─── Layer emitters ──────────────────────────────────────────────────

def emit_polygon_layer(gdf, layer_id, fill, tx,
                       stroke='none', stroke_width=0):
    """Each feature as its own <path>. Drops empty 'd' strings."""
    if gdf is None or gdf.empty:
        return f'  <g id="{layer_id}"></g>\n'
    parts = [f'  <g id="{layer_id}" fill="{fill}" '
             f'stroke="{stroke}" stroke-width="{stroke_width}" '
             f'fill-rule="evenodd">']
    for geom in gdf.geometry:
        d = polygon_path_d(geom, tx)
        if d:
            parts.append(f'    <path d="{d}"/>')
    parts.append('  </g>')
    return '\n'.join(parts) + '\n'


def emit_street_layer(roads, layer_id, color, tx):
    """Each street segment as its own <path>, stroke-width by class."""
    if roads is None or roads.empty:
        return f'  <g id="{layer_id}"></g>\n'
    parts = [f'  <g id="{layer_id}" fill="none" stroke="{color}" '
             f'stroke-linecap="round" stroke-linejoin="round">']
    hw_col = roads.get('highway')
    for geom, hw in zip(roads.geometry, hw_col):
        if hw in SKIP_HIGHWAYS:
            continue
        d = line_path_d(geom, tx)
        if not d:
            continue
        w = STREET_WIDTHS.get(hw, STREET_DEFAULT)
        cls = (str(hw) if hw else 'unknown').replace(' ', '_')
        parts.append(f'    <path class="hw-{cls}" '
                     f'stroke-width="{w}" d="{d}"/>')
    parts.append('  </g>')
    return '\n'.join(parts) + '\n'


def emit_outline_layer(geom, layer_id, stroke, width, tx,
                       dasharray=None):
    """Single-feature outline layer (canvas or SAD boundary)."""
    d = polygon_path_d(geom, tx)
    dash = f' stroke-dasharray="{dasharray}"' if dasharray else ''
    return (f'  <g id="{layer_id}" fill="none" stroke="{stroke}" '
            f'stroke-width="{width}"{dash}>\n'
            f'    <path d="{d}"/>\n  </g>\n')


# ─── SVG assembly ────────────────────────────────────────────────────

def build_svg(source_dir: Path, size: int, margin: int) -> str:
    buildings = read_optional(source_dir / 'buildings.geojson')
    boundary  = read_optional(source_dir / 'sad_boundary.geojson')
    if buildings is None or boundary is None:
        sys.exit(f"buildings.geojson and sad_boundary.geojson are required "
                 f"in {source_dir}")

    metric_crs, image_extent = _resolve_metric_crs(source_dir, boundary)

    buildings_m = _to_metric(buildings, metric_crs)
    boundary_m  = _to_metric(boundary, metric_crs)
    canvas_m    = (_to_metric(image_extent, metric_crs)
                   if image_extent is not None else None)
    canvas_geom = (canvas_m.geometry.iloc[0]
                   if canvas_m is not None else boundary_m.geometry.iloc[0])

    parks     = read_optional(source_dir / 'parks.geojson')
    parking   = read_optional(source_dir / 'parking.geojson')
    roads     = read_optional(source_dir / 'highways.geojson')

    parks_m   = _to_metric(parks, metric_crs)   if parks   is not None else None
    parking_m = _to_metric(parking, metric_crs) if parking is not None else None
    roads_m   = _to_metric(roads, metric_crs)   if roads   is not None else None

    # Use the canvas bbox so the framing is uniform across districts
    bbox = canvas_geom.bounds
    tx, svg_scale, canvas_svg = make_transform(bbox, size, margin)

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}">')

    # Hatch patterns
    period = STYLE['parking_period']
    out.append(f'  <defs>')
    out.append(
        f'    <pattern id="parking-hatch" patternUnits="userSpaceOnUse" '
        f'width="{period}" height="{period}" '
        f'patternTransform="rotate(45)">')
    out.append(
        f'      <rect width="{period}" height="{period}" '
        f'fill="{STYLE["parking_bg"]}"/>')
    out.append(
        f'      <line x1="0" y1="0" x2="0" y2="{period}" '
        f'stroke="{STYLE["parking_hatch"]}" '
        f'stroke-width="{STYLE["parking_stroke"]}"/>')
    out.append(f'    </pattern>')
    out.append(f'  </defs>')

    # White background (so SVG opens cleanly in Illustrator)
    out.append(f'  <rect id="background" width="{size}" '
               f'height="{size}" fill="{STYLE["background"]}"/>')

    # Layers, back to front
    out.append(emit_outline_layer(canvas_geom, 'canvas',
                                  STYLE['canvas_stroke'],
                                  STYLE['canvas_width'], tx))
    out.append(emit_polygon_layer(parks_m, 'parks',
                                  STYLE['parks_fill'], tx))
    out.append(emit_polygon_layer(parking_m, 'parking',
                                  'url(#parking-hatch)', tx))
    out.append(emit_street_layer(roads_m, 'streets',
                                 STYLE['street_color'], tx))
    out.append(emit_polygon_layer(
        buildings_m, 'buildings',
        STYLE['building_fill'], tx,
        stroke=STYLE['building_stroke'],
        stroke_width=STYLE['building_width']))
    out.append(emit_outline_layer(boundary_m.geometry.iloc[0],
                                  'boundary',
                                  STYLE['boundary_stroke'],
                                  STYLE['boundary_width'], tx,
                                  dasharray=STYLE['boundary_dash']))

    # Chrome — drawn last so they overlay everything. Positioned just
    # below the canvas's actual bottom-left corner (after letterboxing),
    # with a small gutter separating them from the canvas outline. Scale
    # bar on the left, north arrow to its right, sharing a baseline.
    bar_w = _meters_for_scale(SCALE_BAR_VALUE, SCALE_BAR_UNIT) * svg_scale
    chrome_y_top = canvas_svg['ymax'] + CHROME_GUTTER
    scale_bar_x = canvas_svg['xmin']
    scale_bar_y = chrome_y_top + 16     # bar itself; "0"/"1000 ft" sit above
    arrow_x_tip = scale_bar_x + bar_w + 36
    arrow_y_tip = chrome_y_top + 2

    out.append(emit_scale_bar(
        svg_scale,
        x_origin=scale_bar_x,
        y_origin=scale_bar_y,
        value=SCALE_BAR_VALUE,
        unit=SCALE_BAR_UNIT))
    out.append(emit_north_arrow(
        x_tip=arrow_x_tip,
        y_tip=arrow_y_tip))

    out.append('</svg>')
    return '\n'.join(out)


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
    ap.add_argument('--size',   type=int, default=1600,
                    help='Square output viewport size in pixels '
                         '(default 1600).')
    ap.add_argument('--margin', type=int, default=90,
                    help='Inner margin in pixels (default 90). Must be '
                         'large enough to fit the scale bar + north arrow '
                         'below the canvas — values under ~70 will clip '
                         'the chrome.')
    args = ap.parse_args()

    source = args.source.resolve()
    if not source.is_dir():
        sys.exit(f"source not found: {source}")
    derived = args.derived.resolve()
    derived.mkdir(parents=True, exist_ok=True)

    sad_id = source.parent.name
    print(f"Building Nolli map for {sad_id}...")
    svg = build_svg(source, args.size, args.margin)
    out = derived / 'nolli_map.svg'
    out.write_text(svg, encoding='utf-8')
    print(f"  wrote {out}  ({len(svg):,} bytes)")


if __name__ == '__main__':
    main()
