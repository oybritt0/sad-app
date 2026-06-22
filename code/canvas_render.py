"""
canvas_render.py - shared rendering utilities for M12/M13/M15.

Loads canvas-extent buildings and streets from `source/`, partitions
each into inside-SAD and outside-SAD groups, and provides hand-rolled
SVG construction helpers with properly named `<g>` layers.

Layer naming convention used across M12/M13/M15:
    <g id="background">              dark fill
    <g id="streets_outside_sad">     low-contrast street network
    <g id="buildings_outside_sad">   low-contrast building footprints
    <g id="streets_inside_sad">      high-contrast street network
    <g id="buildings_inside_sad">    high-contrast building footprints
    <g id="...">                     module-specific data layers
    <g id="sad_boundary">            yellow dashed SAD outline
    <g id="chrome_title">            title text + subtitle
    <g id="chrome_legend">           data legend (always present)
    <g id="chrome_scale_bar">        imperial scale bar
    <g id="chrome_north_arrow">      north arrow

All measurements use FEET for display. Internal computation stays in
meters (UTM) for accuracy.
"""
from __future__ import annotations
import base64
import io
from pathlib import Path

import geopandas as gpd
from shapely.geometry import (Polygon, MultiPolygon, LineString,
                              MultiLineString)


# ─── Unit conversion ─────────────────────────────────────────────────────────

M_PER_FT = 0.3048
FT_PER_M = 1.0 / M_PER_FT

def m_to_ft(m: float) -> float:
    return m * FT_PER_M

def ft_to_m(ft: float) -> float:
    return ft * M_PER_FT


# ─── Shared style constants ──────────────────────────────────────────────────

BG_COLOR              = '#0a0a0a'
# Inside SAD — full contrast, brighter for better legibility on heatmaps
BUILDING_INSIDE       = '#3a3a3a'   # was 2e2e2e — brighter
STREET_INSIDE         = '#525252'   # was 4a4a4a
STREET_INSIDE_WIDTH   = 0.7
# Outside SAD — subtle but visible
BUILDING_OUTSIDE      = '#222222'   # was 1a1a1a — brighter, more visible
STREET_OUTSIDE        = '#2e2e2e'   # was 262626
STREET_OUTSIDE_WIDTH  = 0.45
# SAD boundary
BOUNDARY_COLOR        = '#f5d76e'
BOUNDARY_DASH         = '10,6'
BOUNDARY_WIDTH        = 2.4
# Chrome
TEXT_COLOR            = '#dcdcdc'
TEXT_DIM              = '#8a8a8a'


# ─── Canvas context loader ───────────────────────────────────────────────────

def load_canvas_context(source_dir: Path) -> dict:
    """Load and partition canvas content for rendering."""
    boundary = gpd.read_file(source_dir / 'sad_boundary.geojson')
    metric_crs = boundary.estimate_utm_crs().to_string()
    boundary_m = boundary.to_crs(metric_crs)
    sad_geom_m = boundary_m.geometry.iloc[0]

    extent_path = source_dir / 'image_extent.geojson'
    if extent_path.exists():
        canvas_m = gpd.read_file(extent_path).to_crs(metric_crs)
        canvas_geom_m = canvas_m.geometry.iloc[0]
    else:
        canvas_geom_m = sad_geom_m

    canvas_bbox = canvas_geom_m.bounds

    buildings_inside = None
    buildings_outside = None
    bldg_path = source_dir / 'buildings.geojson'
    if bldg_path.exists():
        try:
            bldgs = gpd.read_file(bldg_path).to_crs(metric_crs)
            bldgs = gpd.clip(bldgs, canvas_geom_m)
            inside_mask = bldgs.intersects(sad_geom_m)
            buildings_inside = bldgs[inside_mask].copy()
            buildings_outside = bldgs[~inside_mask].copy()
        except Exception:
            pass

    streets_inside = None
    streets_outside = None
    hwy_path = source_dir / 'highways.geojson'
    if hwy_path.exists():
        try:
            hwys = gpd.read_file(hwy_path).to_crs(metric_crs)
            hwys = gpd.clip(hwys, canvas_geom_m)
            inside_mask = hwys.intersects(sad_geom_m)
            streets_inside = hwys[inside_mask].copy()
            streets_outside = hwys[~inside_mask].copy()
        except Exception:
            pass

    return {
        'metric_crs':        metric_crs,
        'sad_geom_m':        sad_geom_m,
        'canvas_geom_m':     canvas_geom_m,
        'canvas_bbox':       canvas_bbox,
        'buildings_inside':  buildings_inside,
        'buildings_outside': buildings_outside,
        'streets_inside':    streets_inside,
        'streets_outside':   streets_outside,
    }


# ─── SVG coordinate transform ────────────────────────────────────────────────

def make_transform(canvas_bbox: tuple,
                   svg_width: int = 1100,
                   margin: int = 40,
                   chrome_height: int = 150):
    """Build coordinate transform from metric (x, y) to SVG (px, py)."""
    minx, miny, maxx, maxy = canvas_bbox
    aspect = (maxy - miny) / max(maxx - minx, 1e-9)
    drawable_w = svg_width - 2 * margin
    drawable_h = drawable_w * aspect
    svg_height = int(drawable_h + 2 * margin + chrome_height)
    scale = drawable_w / max(maxx - minx, 1e-9)   # svg_px per meter

    plot_top = margin
    plot_left = margin

    def tx(x, y):
        px = plot_left + (x - minx) * scale
        py = plot_top + drawable_h - (y - miny) * scale  # flip y
        return (px, py)

    plot_area = {
        'left': plot_left,
        'top': plot_top,
        'width': drawable_w,
        'height': drawable_h,
        'bottom': plot_top + drawable_h,
        'right': plot_left + drawable_w,
        'svg_px_per_m': scale,
    }
    return tx, svg_width, svg_height, scale, plot_area


# ─── Geometry to SVG path ────────────────────────────────────────────────────

def _coords_to_d(coords, tx, close=True):
    parts = []
    for i, c in enumerate(coords):
        x, y = c[0], c[1]
        px, py = tx(x, y)
        cmd = 'M' if i == 0 else 'L'
        parts.append(f'{cmd}{px:.1f},{py:.1f}')
    if close:
        parts.append('Z')
    return ''.join(parts)


def polygon_to_path(geom, tx) -> str:
    rings = []
    if isinstance(geom, Polygon):
        rings.append(_coords_to_d(geom.exterior.coords, tx, close=True))
        for hole in geom.interiors:
            rings.append(_coords_to_d(hole.coords, tx, close=True))
    elif isinstance(geom, MultiPolygon):
        for poly in geom.geoms:
            rings.append(_coords_to_d(poly.exterior.coords, tx, close=True))
            for hole in poly.interiors:
                rings.append(_coords_to_d(hole.coords, tx, close=True))
    return ' '.join(rings)


def linestring_to_path(geom, tx) -> str:
    lines = []
    if isinstance(geom, LineString):
        lines.append(_coords_to_d(geom.coords, tx, close=False))
    elif isinstance(geom, MultiLineString):
        for line in geom.geoms:
            lines.append(_coords_to_d(line.coords, tx, close=False))
    return ' '.join(lines)


# ─── SVG document open/close ────────────────────────────────────────────────

def write_svg_open(fh, svg_width, svg_height, title=''):
    fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'viewBox="0 0 {svg_width} {svg_height}" '
             f'width="{svg_width}" height="{svg_height}">\n')
    if title:
        safe = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        fh.write(f'  <title>{safe}</title>\n')
    fh.write('  <g id="background">\n')
    fh.write(f'    <rect x="0" y="0" width="{svg_width}" '
             f'height="{svg_height}" fill="{BG_COLOR}"/>\n')
    fh.write('  </g>\n')


def write_svg_close(fh):
    fh.write('</svg>\n')


# ─── Context layers ──────────────────────────────────────────────────────────

def write_streets(fh, layer_id, streets_gdf, tx, color, width, opacity=1.0):
    if streets_gdf is None or streets_gdf.empty:
        fh.write(f'  <g id="{layer_id}"></g>\n')
        return
    fh.write(f'  <g id="{layer_id}" fill="none" stroke="{color}" '
             f'stroke-width="{width}" stroke-linecap="round" '
             f'stroke-linejoin="round"')
    if opacity != 1.0:
        fh.write(f' opacity="{opacity}"')
    fh.write('>\n')
    for geom in streets_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        d = linestring_to_path(geom, tx)
        if d:
            fh.write(f'    <path d="{d}"/>\n')
    fh.write('  </g>\n')


def write_buildings(fh, layer_id, buildings_gdf, tx, fill, opacity=1.0):
    if buildings_gdf is None or buildings_gdf.empty:
        fh.write(f'  <g id="{layer_id}"></g>\n')
        return
    fh.write(f'  <g id="{layer_id}" fill="{fill}" stroke="none"')
    if opacity != 1.0:
        fh.write(f' opacity="{opacity}"')
    fh.write('>\n')
    for geom in buildings_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        d = polygon_to_path(geom, tx)
        if d:
            fh.write(f'    <path d="{d}"/>\n')
    fh.write('  </g>\n')


def write_context_layers(fh, ctx: dict, tx):
    """Write context layers in correct draw order (outside under inside)."""
    write_streets(fh, 'streets_outside_sad',
                  ctx.get('streets_outside'), tx,
                  STREET_OUTSIDE, STREET_OUTSIDE_WIDTH)
    write_buildings(fh, 'buildings_outside_sad',
                    ctx.get('buildings_outside'), tx,
                    BUILDING_OUTSIDE)
    write_streets(fh, 'streets_inside_sad',
                  ctx.get('streets_inside'), tx,
                  STREET_INSIDE, STREET_INSIDE_WIDTH)
    write_buildings(fh, 'buildings_inside_sad',
                    ctx.get('buildings_inside'), tx,
                    BUILDING_INSIDE)


def write_sad_boundary(fh, sad_geom_m, tx):
    fh.write(f'  <g id="sad_boundary" fill="none" '
             f'stroke="{BOUNDARY_COLOR}" stroke-width="{BOUNDARY_WIDTH}" '
             f'stroke-dasharray="{BOUNDARY_DASH}" '
             f'stroke-linejoin="round">\n')
    d = polygon_to_path(sad_geom_m, tx)
    if d:
        fh.write(f'    <path d="{d}"/>\n')
    fh.write('  </g>\n')


def write_title(fh, title: str, subtitle: str, plot_area: dict):
    x = plot_area['left'] + 8
    y = plot_area['top'] + 22
    fh.write('  <g id="chrome_title" font-family="sans-serif">\n')
    fh.write(f'    <text x="{x}" y="{y}" font-size="18" font-weight="700" '
             f'fill="{TEXT_COLOR}">{title}</text>\n')
    if subtitle:
        fh.write(f'    <text x="{x}" y="{y + 18}" font-size="11" '
                 f'fill="{TEXT_DIM}">{subtitle}</text>\n')
    fh.write('  </g>\n')


# ─── Imperial scale bar ──────────────────────────────────────────────────────

def select_scale_length_ft(canvas_extent_m: float) -> int:
    """Pick a sensible round-number scale-bar length in feet.

    Targets ~12% of the canvas width. For typical 2km canvases this
    yields 1000 ft; for smaller districts it scales down."""
    canvas_extent_ft = m_to_ft(canvas_extent_m)
    target = canvas_extent_ft * 0.12
    candidates = [50, 100, 200, 250, 500, 1000, 1500, 2000, 2500, 5000]
    return min(candidates, key=lambda c: abs(c - target))


def write_scale_bar(fh, plot_area, svg_px_per_m, x_offset=8, y_offset=68):
    """Imperial scale bar in feet, positioned in the chrome band below
    the plot. Bar shows 0 — midpoint — total ft with tick marks."""
    canvas_extent_m = plot_area['width'] / svg_px_per_m
    length_ft = select_scale_length_ft(canvas_extent_m)
    length_m = ft_to_m(length_ft)
    bar_px = length_m * svg_px_per_m

    x0 = plot_area['left'] + x_offset
    y0 = plot_area['bottom'] + y_offset
    midpoint = length_ft // 2

    fh.write('  <g id="chrome_scale_bar" font-family="sans-serif" '
             f'font-size="9" fill="{TEXT_DIM}">\n')
    # Main bar line
    fh.write(f'    <line x1="{x0}" y1="{y0}" '
             f'x2="{x0 + bar_px:.1f}" y2="{y0}" '
             f'stroke="{TEXT_DIM}" stroke-width="1.2"/>\n')
    # Tick marks at 0 / mid / end
    for t_ft in [0, midpoint, length_ft]:
        t_px = x0 + (t_ft / length_ft) * bar_px
        fh.write(f'    <line x1="{t_px:.1f}" y1="{y0 - 4}" '
                 f'x2="{t_px:.1f}" y2="{y0 + 4}" '
                 f'stroke="{TEXT_DIM}" stroke-width="1.2"/>\n')
    # Labels under ticks
    fh.write(f'    <text x="{x0}" y="{y0 + 16}" text-anchor="middle">0</text>\n')
    fh.write(f'    <text x="{x0 + bar_px/2:.1f}" y="{y0 + 16}" '
             f'text-anchor="middle">{midpoint:,}</text>\n')
    fh.write(f'    <text x="{x0 + bar_px:.1f}" y="{y0 + 16}" '
             f'text-anchor="middle">{length_ft:,} ft</text>\n')
    fh.write('  </g>\n')


# ─── North arrow ─────────────────────────────────────────────────────────────

def write_north_arrow(fh, plot_area, size=22, padding=12):
    """Simple north arrow in the bottom-right corner of the chrome band.

    UTM grid north is aligned to the +Y axis of our metric CRS, which in
    SVG is pointing up (we flip Y in make_transform). So a triangle
    pointing up labels as N.
    """
    cx = plot_area['right'] - padding - size / 2
    cy = plot_area['bottom'] + 80
    tip_y = cy - size / 2
    base_l_x = cx - size / 2.5
    base_r_x = cx + size / 2.5
    base_y = cy + size / 2

    fh.write('  <g id="chrome_north_arrow" font-family="sans-serif" '
             f'font-size="11" font-weight="700" fill="{TEXT_COLOR}">\n')
    # Arrow triangle (filled)
    fh.write(f'    <path d="M{cx:.1f},{tip_y:.1f} '
             f'L{base_r_x:.1f},{base_y:.1f} '
             f'L{cx:.1f},{cy + size/8:.1f} '
             f'L{base_l_x:.1f},{base_y:.1f} Z" '
             f'fill="{TEXT_COLOR}" stroke="none"/>\n')
    # "N" label
    fh.write(f'    <text x="{cx:.1f}" y="{base_y + 14:.1f}" '
             f'text-anchor="middle">N</text>\n')
    fh.write('  </g>\n')


# ─── PNG counterparts (matplotlib) ──────────────────────────────────────────

def draw_scale_bar_mpl(ax, canvas_bbox_m, fig_transform=None):
    """Draw scale bar on a matplotlib axes (data coords = UTM meters).

    Returns the (length_ft, length_m) used.
    """
    minx, miny, maxx, maxy = canvas_bbox_m
    canvas_extent_m = maxx - minx
    length_ft = select_scale_length_ft(canvas_extent_m)
    length_m = ft_to_m(length_ft)

    # Position: bottom-left, 5% inset from frame
    x0 = minx + (maxx - minx) * 0.04
    y0 = miny + (maxy - miny) * 0.05
    midpoint_ft = length_ft // 2
    tick_h = (maxy - miny) * 0.01

    # Bar
    ax.plot([x0, x0 + length_m], [y0, y0],
            color=TEXT_DIM, linewidth=1.4, zorder=15, solid_capstyle='butt')
    for frac in [0, 0.5, 1.0]:
        x = x0 + frac * length_m
        ax.plot([x, x], [y0 - tick_h, y0 + tick_h],
                color=TEXT_DIM, linewidth=1.4, zorder=15)

    ax.text(x0, y0 - tick_h * 2.5, '0', color=TEXT_DIM, fontsize=8,
            ha='center', va='top', zorder=15, family='sans-serif')
    ax.text(x0 + length_m / 2, y0 - tick_h * 2.5, f'{midpoint_ft:,}',
            color=TEXT_DIM, fontsize=8, ha='center', va='top', zorder=15,
            family='sans-serif')
    ax.text(x0 + length_m, y0 - tick_h * 2.5, f'{length_ft:,} ft',
            color=TEXT_DIM, fontsize=8, ha='center', va='top', zorder=15,
            family='sans-serif')
    return length_ft, length_m


def draw_north_arrow_mpl(ax, canvas_bbox_m):
    """Draw a north arrow in the bottom-right of a matplotlib axes."""
    minx, miny, maxx, maxy = canvas_bbox_m
    w = maxx - minx
    h = maxy - miny
    cx = minx + w * 0.94
    cy = miny + h * 0.06
    arrow_h = h * 0.04
    arrow_w = w * 0.018

    # Triangle pointing up (UTM north)
    import matplotlib.patches as mpatches
    tri = mpatches.Polygon(
        [(cx, cy + arrow_h),
         (cx + arrow_w, cy - arrow_h * 0.4),
         (cx, cy),
         (cx - arrow_w, cy - arrow_h * 0.4)],
        closed=True, color=TEXT_COLOR, zorder=15)
    ax.add_patch(tri)
    ax.text(cx, cy - arrow_h * 1.2, 'N',
            color=TEXT_COLOR, fontsize=11, fontweight='bold',
            ha='center', va='top', zorder=15, family='sans-serif')


# ─── PNG embedding helpers ──────────────────────────────────────────────────

def png_bytes_to_data_uri(png_bytes: bytes) -> str:
    b64 = base64.b64encode(png_bytes).decode('ascii')
    return f'data:image/png;base64,{b64}'


def write_embedded_image(fh, layer_id: str, data_uri: str,
                        plot_area: dict, opacity: float = 1.0):
    fh.write(f'  <g id="{layer_id}">\n')
    fh.write(f'    <image href="{data_uri}" '
             f'x="{plot_area["left"]}" y="{plot_area["top"]}" '
             f'width="{plot_area["width"]}" height="{plot_area["height"]}" '
             f'preserveAspectRatio="none"')
    if opacity != 1.0:
        fh.write(f' opacity="{opacity}"')
    fh.write('/>\n  </g>\n')
