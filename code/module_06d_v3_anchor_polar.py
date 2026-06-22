"""
module_06d_v3_anchor_polar.py

Small-multiples grid of polar plots, one per anchor. Each polar plot
divides the area around an anchor building into 8 compass sectors (N/NE/E/
SE/S/SW/W/NW) and 4 distance rings (default 100 / 200 / 300 / 400 ft from
the building's polygon edge). Each (direction, distance) cell is colored
by the DOMINANT PROGRAM in that wedge.

REWRITE NOTES (v3 — KPF aesthetic, June 2026)
    - Dark background (#0a0a0a) to match M12/M13/M15
    - The ORIGINAL BUILDING POLYGON is now drawn at the center of each
      polar plot in yellow-cream, so the radar reads as "this anchor +
      its surroundings" rather than an abstract diagram
    - Ring radii now in FEET (default 100/200/300/400 ft) with ring
      distance labels on each plot
    - Hand-rolled SVG output with named layers per anchor for clean
      editing in Illustrator
    - PNG via matplotlib but on the same dark palette

INPUT
  derived/<sad>/buildings_enriched.gpkg
  source/<sad>/rod_places.geojson

OUTPUT
  derived/<sad>/anchor_polar_plots.{png,svg}
  derived/<sad>/anchor_polar_plots.json     (schema unchanged for M10 compatibility)

USAGE
  python module_06d_v3_anchor_polar.py --source <dir> --derived <dir>
       [--cluster N] [--rings 100,200,300,400]   # rings in FT now

This module's JSON output keys remain m-denominated for back-compatibility
with downstream modules (M10 reads `anchors[].building_id`).
"""
from __future__ import annotations
import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Patch, Polygon as MplPolygon
from shapely.geometry import MultiPolygon, Polygon
from shapely.affinity import translate


# ─── Unit conversion ─────────────────────────────────────────────────────────

M_PER_FT = 0.3048
FT_PER_M = 1.0 / M_PER_FT


# ─── Categorization ──────────────────────────────────────────────────────────

ROSSETTI_ORDER = [
    'sport', 'residential', 'hotel', 'retail_food_entertainment',
    'office', 'parking', 'open_space', 'other',
]

PROGRAM_LABELS = {
    'sport':                     'Sport',
    'residential':               'Residential',
    'hotel':                     'Hotel',
    'retail_food_entertainment': 'Retail / F&B',
    'office':                    'Office',
    'parking':                   'Parking',
    'open_space':                'Open space',
    'other':                     'Other',
}

# Same palette as M10 plan view for visual continuity across the deck
PROGRAM_COLORS = {
    'sport':                     '#d62728',
    'residential':               '#2ca02c',
    'hotel':                     '#9467bd',
    'retail_food_entertainment': '#ff7f0e',
    'office':                    '#1f77b4',
    'parking':                   '#8c564b',
    'open_space':                '#bcbd22',
    'other':                     '#7f7f7f',
}

EMPTY_CELL_COLOR = '#1f1f1f'
EMPTY_CELL_EDGE  = '#2a2a2a'

# Chrome palette (matches canvas_render.py for M12/M13/M15)
BG_COLOR        = '#0a0a0a'
TEXT_COLOR      = '#dcdcdc'
TEXT_DIM        = '#8a8a8a'
RING_COLOR      = '#4a4a4a'      # subtle outlines on dark BG
BUILDING_FILL   = 'none'         # transparent — let sector colors show through
BUILDING_EDGE   = '#ffffff'      # bright white outline reads against both dark BG and colored wedges
BUILDING_STROKE_WIDTH = 1.8
ANCHOR_LABEL_COLOR = '#f5d76e'


# 8 compass sectors, each 45 degrees wide
SECTOR_NAMES = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
SECTOR_CENTERS = [0, 45, 90, 135, 180, 225, 270, 315]


# ─── Geometry helpers ────────────────────────────────────────────────────────

def to_polygon(geom):
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def detect_anchor_cluster(buildings) -> int:
    """Auto-detect the anchor cluster as the one with the largest median
    building area."""
    medians = buildings.groupby('cluster_id')['area_m2'].median()
    return int(medians.idxmax())


def bearing_from_anchor(anchor_x: float, anchor_y: float,
                          point_x: float, point_y: float) -> float:
    """Compass bearing in degrees (0=N, 90=E, etc.) from anchor to point."""
    dx = point_x - anchor_x
    dy = point_y - anchor_y
    math_angle_rad = np.arctan2(dy, dx)
    math_angle_deg = np.degrees(math_angle_rad)
    compass_deg = (90 - math_angle_deg) % 360
    return compass_deg


def sector_index(bearing: float) -> int:
    """Map bearing 0-360 to sector index 0-7."""
    return int(((bearing + 22.5) % 360) / 45)


# ─── Analysis ────────────────────────────────────────────────────────────────

def analyze_anchor_polar(anchor_poly, places, ring_outer_radii_m):
    """Bin POIs within max(ring_outer_radii_m) meters of the anchor edge
    into (sector, ring) cells. Returns dominant program per cell.
    """
    cx = anchor_poly.centroid.x
    cy = anchor_poly.centroid.y
    max_radius = max(ring_outer_radii_m)

    nearby_idx = []
    for idx, geom in places.geometry.items():
        d = anchor_poly.distance(geom)
        if d > max_radius:
            continue
        nearby_idx.append(idx)
    nearby = places.loc[nearby_idx]

    cells = {}
    for idx, row in nearby.iterrows():
        p = row.geometry
        edge_distance = anchor_poly.distance(p)
        bearing = bearing_from_anchor(cx, cy, p.x, p.y)
        sector = sector_index(bearing)
        ring = None
        for ri, outer in enumerate(ring_outer_radii_m):
            if edge_distance <= outer:
                ring = ri
                break
        if ring is None:
            continue
        cat = row.get('rossetti_category', 'other')
        if cat is None or (isinstance(cat, float) and np.isnan(cat)):
            continue
        cells.setdefault((sector, ring), Counter())[cat] += 1

    dominant = {}
    for (sec, ring), counter in cells.items():
        if counter:
            dominant[(sec, ring)] = {
                'dominant_program': counter.most_common(1)[0][0],
                'count': sum(counter.values()),
            }
    return dominant


# ─── Coordinate setup ────────────────────────────────────────────────────────

def building_local_coords(anchor_poly):
    """Translate anchor polygon so its centroid is at (0, 0). Returns
    a shapely polygon in local coordinates."""
    cx = anchor_poly.centroid.x
    cy = anchor_poly.centroid.y
    return translate(anchor_poly, xoff=-cx, yoff=-cy)


# ─── PNG rendering (matplotlib, dark BG) ─────────────────────────────────────

def render_polar_panel_mpl(ax, anchor_local, anchor_name, anchor_id,
                            cells, ring_outer_radii_m, ring_outer_radii_ft):
    """Render a single polar panel on a matplotlib axis (dark background)."""
    n_rings = len(ring_outer_radii_m)
    max_r = ring_outer_radii_m[-1]

    ax.set_facecolor(BG_COLOR)

    # ── Wedges (sectors x rings) ─────────────────────────────────────
    for ring_idx in range(n_rings - 1, -1, -1):
        outer = ring_outer_radii_m[ring_idx]
        inner = ring_outer_radii_m[ring_idx - 1] if ring_idx > 0 else 0
        for sector in range(8):
            theta1 = sector * 45 - 22.5
            theta2 = sector * 45 + 22.5
            mpl_t1 = (90 - theta2) % 360
            mpl_t2 = (90 - theta1) % 360
            cell = cells.get((sector, ring_idx))
            if cell:
                color = PROGRAM_COLORS.get(cell['dominant_program'], '#666')
                alpha = 0.85
            else:
                color = EMPTY_CELL_COLOR
                alpha = 0.9
            w = Wedge(
                center=(0, 0), r=outer,
                theta1=mpl_t1, theta2=mpl_t2,
                width=outer - inner,
                facecolor=color, edgecolor=EMPTY_CELL_EDGE,
                linewidth=0.6, alpha=alpha,
            )
            ax.add_patch(w)

    # ── Ring outlines (subtle dashed) + labels in FT ───────────────
    for ri, r_m in enumerate(ring_outer_radii_m):
        circle_theta = np.linspace(0, 2 * np.pi, 200)
        ax.plot(r_m * np.cos(circle_theta), r_m * np.sin(circle_theta),
                color=RING_COLOR, linestyle=(0, (2, 3)),
                linewidth=0.6, zorder=2)
        # Ring distance label at NE diagonal
        ang = np.radians(45)
        ax.text(r_m * np.cos(ang) + 2, r_m * np.sin(ang) + 2,
                f"{int(ring_outer_radii_ft[ri])}",
                color=TEXT_DIM, fontsize=7,
                ha='left', va='bottom', zorder=3)

    # ── Compass labels ─────────────────────────────────────────────
    for i, name in enumerate(SECTOR_NAMES):
        angle_rad = np.radians(90 - SECTOR_CENTERS[i])
        x = (max_r * 1.18) * np.cos(angle_rad)
        y = (max_r * 1.18) * np.sin(angle_rad)
        ax.text(x, y, name, ha='center', va='center',
                fontsize=8, color=TEXT_DIM, fontweight='bold')

    # ── Building polygon (the actual anchor) drawn on top ─────────
    # Outline only (no fill) so the underlying sector colors stay legible.
    if anchor_local is not None:
        coords = list(anchor_local.exterior.coords)
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        ax.fill(xs, ys, facecolor='none', edgecolor=BUILDING_EDGE,
                linewidth=BUILDING_STROKE_WIDTH, zorder=5)
        # Holes
        for hole in anchor_local.interiors:
            coords_h = list(hole.coords)
            ax.fill([c[0] for c in coords_h], [c[1] for c in coords_h],
                    facecolor='none', edgecolor=BUILDING_EDGE,
                    linewidth=BUILDING_STROKE_WIDTH * 0.7, zorder=6)

    # ── Anchor name label below ────────────────────────────────────
    label = anchor_name if anchor_name and anchor_name != anchor_id \
            else anchor_id
    ax.text(0, -max_r * 1.45, label,
            ha='center', va='top', fontsize=8.5, fontweight='bold',
            color=ANCHOR_LABEL_COLOR)

    pad = max_r * 0.30
    ax.set_xlim(-max_r - pad, max_r + pad)
    ax.set_ylim(-max_r * 1.60, max_r + pad)
    ax.set_aspect('equal')
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def render_all_anchors_png(anchors, places, ring_outer_radii_m,
                            ring_outer_radii_ft, sad_name, out_png):
    """Multi-anchor small-multiples grid as PNG on dark BG."""
    n_anchors = len(anchors)
    n_cols = min(4, n_anchors)
    n_rows = int(np.ceil(n_anchors / n_cols))

    fig_w = 3.6 * n_cols
    fig_h = 4.0 * n_rows + 0.9
    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(fig_w, fig_h),
        gridspec_kw={'wspace': 0.05, 'hspace': 0.20},
    )
    fig.patch.set_facecolor(BG_COLOR)
    if n_anchors == 1:
        axes = np.array([[axes]])
    elif n_rows == 1:
        axes = axes.reshape(1, -1)

    summaries = []
    for i, (idx, row) in enumerate(anchors.iterrows()):
        r, c = i // n_cols, i % n_cols
        ax = axes[r, c]
        poly = to_polygon(row.geometry)
        anchor_local = building_local_coords(poly)
        anchor_id = row.get('building_id', f'anchor_{idx}')
        anchor_name = row.get('name', '')
        if anchor_name is None or (isinstance(anchor_name, float) and np.isnan(anchor_name)):
            anchor_name = ''
        anchor_name = str(anchor_name).strip()
        if not anchor_name or anchor_name == 'nan':
            anchor_name = anchor_id

        cells = analyze_anchor_polar(poly, places, ring_outer_radii_m)
        render_polar_panel_mpl(ax, anchor_local, anchor_name, anchor_id,
                                cells, ring_outer_radii_m, ring_outer_radii_ft)

        summary = {
            'building_id': anchor_id,
            'name': anchor_name,
            'area_m2': float(row.get('area_m2', 0)),
            'cells': [
                {
                    'sector': SECTOR_NAMES[sec],
                    'ring_m': ring_outer_radii_m[ring],
                    'ring_ft': ring_outer_radii_ft[ring],
                    'dominant_program': info['dominant_program'],
                    'count': info['count'],
                }
                for (sec, ring), info in cells.items()
            ],
        }
        summaries.append(summary)

    for j in range(n_anchors, n_rows * n_cols):
        r, c = j // n_cols, j % n_cols
        axes[r, c].axis('off')
        axes[r, c].set_facecolor(BG_COLOR)

    # Title
    fig.suptitle(
        f"{sad_name}  -  anchor relational fields",
        fontsize=13, color=TEXT_COLOR, y=0.98, x=0.02, ha='left',
        fontweight='bold',
    )
    subtitle = ('rings: ' + ' / '.join(f"{int(r)} ft"
                                        for r in ring_outer_radii_ft)
                + '  from building edge  *  wedges colored by dominant program')
    fig.text(0.02, 0.95, subtitle, fontsize=9, color=TEXT_DIM, ha='left')

    # Legend at bottom
    legend_handles = [
        Patch(facecolor=PROGRAM_COLORS[c], edgecolor='none',
              label=PROGRAM_LABELS[c])
        for c in ROSSETTI_ORDER
    ]
    legend_handles.append(Patch(facecolor=EMPTY_CELL_COLOR,
                                  edgecolor='none', label='empty'))
    leg = fig.legend(handles=legend_handles, loc='lower center',
                ncol=9, fontsize=8, frameon=False,
                bbox_to_anchor=(0.5, 0.005))
    for txt in leg.get_texts():
        txt.set_color(TEXT_COLOR)

    fig.tight_layout(rect=[0, 0.05, 1, 0.93])
    fig.savefig(out_png, dpi=180, facecolor=BG_COLOR, bbox_inches='tight')
    plt.close(fig)
    return summaries


# ─── SVG rendering (hand-rolled, named layers) ───────────────────────────────

def _polar_to_xy_compass(r, compass_deg):
    """Convert (radius, compass_degrees) to local (x, y). Compass 0=N=+y."""
    a = np.radians(90 - compass_deg)
    return r * np.cos(a), r * np.sin(a)


def _wedge_path_d(inner_r, outer_r, t1_deg, t2_deg, scale, cx_svg, cy_svg):
    """Build SVG path string for an annular wedge spanning compass [t1, t2]
    at radii [inner, outer]. scale = svg_px_per_meter."""
    # Convert compass degrees to math angles (radians)
    a1 = np.radians(90 - t1_deg)
    a2 = np.radians(90 - t2_deg)
    # Outer arc
    ox1, oy1 = outer_r * np.cos(a1), outer_r * np.sin(a1)
    ox2, oy2 = outer_r * np.cos(a2), outer_r * np.sin(a2)
    # Inner arc
    ix1, iy1 = inner_r * np.cos(a1), inner_r * np.sin(a1)
    ix2, iy2 = inner_r * np.cos(a2), inner_r * np.sin(a2)
    # Translate to SVG pixels (flip Y for SVG)
    def to_svg(x, y):
        return (cx_svg + x * scale, cy_svg - y * scale)
    O1 = to_svg(ox1, oy1); O2 = to_svg(ox2, oy2)
    I1 = to_svg(ix1, iy1); I2 = to_svg(ix2, iy2)

    # Sweep is large-arc=0 since each wedge is 45deg
    large = 0
    sweep_outer = 0   # outer arc goes clockwise from t1 -> t2 (compass)
    sweep_inner = 1
    if inner_r > 0:
        d = (f"M{O1[0]:.2f},{O1[1]:.2f} "
             f"A{outer_r * scale:.2f},{outer_r * scale:.2f} 0 "
             f"{large},{sweep_outer} {O2[0]:.2f},{O2[1]:.2f} "
             f"L{I2[0]:.2f},{I2[1]:.2f} "
             f"A{inner_r * scale:.2f},{inner_r * scale:.2f} 0 "
             f"{large},{sweep_inner} {I1[0]:.2f},{I1[1]:.2f} Z")
    else:
        d = (f"M{cx_svg:.2f},{cy_svg:.2f} "
             f"L{O1[0]:.2f},{O1[1]:.2f} "
             f"A{outer_r * scale:.2f},{outer_r * scale:.2f} 0 "
             f"{large},{sweep_outer} {O2[0]:.2f},{O2[1]:.2f} Z")
    return d


def _polygon_to_svg_path(geom, scale, cx_svg, cy_svg):
    """Convert a shapely polygon (in local coords) to an SVG path string.
    Uses c[0], c[1] indexing rather than (x, y) unpacking so 3D
    coordinate triples from QGIS exports don't crash."""
    def to_svg(x, y):
        return (cx_svg + x * scale, cy_svg - y * scale)
    rings = []
    polys = [geom] if isinstance(geom, Polygon) \
            else list(geom.geoms) if isinstance(geom, MultiPolygon) \
            else []
    for poly in polys:
        ext = list(poly.exterior.coords)
        if not ext:
            continue
        parts = []
        for i, c in enumerate(ext):
            px, py = to_svg(c[0], c[1])
            parts.append(f"{'M' if i == 0 else 'L'}{px:.2f},{py:.2f}")
        parts.append('Z')
        rings.append(' '.join(parts))
        for hole in poly.interiors:
            hcoords = list(hole.coords)
            parts = []
            for i, c in enumerate(hcoords):
                px, py = to_svg(c[0], c[1])
                parts.append(f"{'M' if i == 0 else 'L'}{px:.2f},{py:.2f}")
            parts.append('Z')
            rings.append(' '.join(parts))
    return ' '.join(rings)


def _safe_id(s):
    """Make a string safe for SVG id attribute."""
    out = ''.join(c if c.isalnum() else '_' for c in str(s))
    return out[:60] or 'anchor'


def render_all_anchors_svg(anchors_data, ring_outer_radii_m,
                            ring_outer_radii_ft, sad_name, out_svg):
    """Hand-rolled SVG with named layers per anchor."""
    n = len(anchors_data)
    n_cols = min(4, n)
    n_rows = int(np.ceil(n / n_cols))

    max_r = ring_outer_radii_m[-1]
    panel_world = max_r * 2.5    # world space per panel
    panel_px = 280               # svg pixels per panel
    scale = panel_px / panel_world

    panel_w = panel_px + 40
    panel_h = panel_px + 80
    fig_w = panel_w * n_cols + 80
    fig_h = panel_h * n_rows + 140

    with open(out_svg, 'w', encoding='utf-8') as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'viewBox="0 0 {fig_w} {fig_h}" '
                 f'width="{fig_w}" height="{fig_h}">\n')
        fh.write(f'  <title>Anchor relational fields - {sad_name}</title>\n')

        # Background layer
        fh.write('  <g id="background">\n')
        fh.write(f'    <rect x="0" y="0" width="{fig_w}" height="{fig_h}" '
                 f'fill="{BG_COLOR}"/>\n')
        fh.write('  </g>\n')

        # Title layer
        fh.write('  <g id="chrome_title" font-family="sans-serif">\n')
        fh.write(f'    <text x="40" y="38" font-size="18" font-weight="700" '
                 f'fill="{TEXT_COLOR}">{sad_name} - anchor relational fields</text>\n')
        rings_str = ' / '.join(f"{int(r)} ft" for r in ring_outer_radii_ft)
        fh.write(f'    <text x="40" y="58" font-size="11" '
                 f'fill="{TEXT_DIM}">rings: {rings_str} from building edge '
                 f'\u00b7 wedges colored by dominant program</text>\n')
        fh.write('  </g>\n')

        # Anchors container
        fh.write('  <g id="anchors">\n')
        for i, ad in enumerate(anchors_data):
            r_grid = i // n_cols
            c_grid = i % n_cols
            cx_svg = 40 + c_grid * panel_w + panel_w / 2
            cy_svg = 90 + r_grid * panel_h + panel_h / 2 - 20

            safe_label = _safe_id(ad['name'])
            fh.write(f'    <g id="anchor_{i+1}_{safe_label}">\n')

            # Sectors layer
            fh.write(f'      <g id="anchor_{i+1}_sectors">\n')
            for ring_idx in range(len(ring_outer_radii_m) - 1, -1, -1):
                outer = ring_outer_radii_m[ring_idx]
                inner = ring_outer_radii_m[ring_idx - 1] if ring_idx > 0 else 0
                for sector in range(8):
                    t1 = sector * 45 - 22.5
                    t2 = sector * 45 + 22.5
                    cell = ad['cells_dict'].get((sector, ring_idx))
                    if cell:
                        color = PROGRAM_COLORS.get(cell['dominant_program'],
                                                     '#666')
                        opacity = '0.85'
                    else:
                        color = EMPTY_CELL_COLOR
                        opacity = '0.9'
                    d = _wedge_path_d(inner, outer, t1, t2, scale,
                                       cx_svg, cy_svg)
                    fh.write(f'        <path d="{d}" fill="{color}" '
                             f'fill-opacity="{opacity}" '
                             f'stroke="{EMPTY_CELL_EDGE}" '
                             f'stroke-width="0.6"/>\n')
            fh.write('      </g>\n')

            # Rings layer (subtle outlines + ft labels)
            fh.write(f'      <g id="anchor_{i+1}_rings" fill="none" '
                     f'stroke="{RING_COLOR}" stroke-width="0.6" '
                     f'stroke-dasharray="2 3">\n')
            for r_m in ring_outer_radii_m:
                fh.write(f'        <circle cx="{cx_svg:.2f}" '
                         f'cy="{cy_svg:.2f}" r="{r_m * scale:.2f}"/>\n')
            fh.write('      </g>\n')

            # Ring distance labels (NE diagonal)
            fh.write(f'      <g id="anchor_{i+1}_ring_labels" '
                     f'font-family="sans-serif" font-size="7" '
                     f'fill="{TEXT_DIM}">\n')
            for ri, r_m in enumerate(ring_outer_radii_m):
                lx, ly = _polar_to_xy_compass(r_m, 45)
                lx_svg = cx_svg + lx * scale + 2
                ly_svg = cy_svg - ly * scale - 2
                fh.write(f'        <text x="{lx_svg:.2f}" y="{ly_svg:.2f}">'
                         f'{int(ring_outer_radii_ft[ri])}</text>\n')
            fh.write('      </g>\n')

            # Compass labels layer
            fh.write(f'      <g id="anchor_{i+1}_compass" '
                     f'font-family="sans-serif" font-size="8" '
                     f'font-weight="700" fill="{TEXT_DIM}" '
                     f'text-anchor="middle">\n')
            for si, name in enumerate(SECTOR_NAMES):
                x, y = _polar_to_xy_compass(max_r * 1.18, SECTOR_CENTERS[si])
                px = cx_svg + x * scale
                py = cy_svg - y * scale + 3   # +3 to vertically center
                fh.write(f'        <text x="{px:.2f}" y="{py:.2f}">'
                         f'{name}</text>\n')
            fh.write('      </g>\n')

            # Building polygon layer (outline only, lets sector colors show through)
            fh.write(f'      <g id="anchor_{i+1}_building" '
                     f'fill="none" '
                     f'stroke="{BUILDING_EDGE}" '
                     f'stroke-width="{BUILDING_STROKE_WIDTH}">\n')
            if ad['local_geom'] is not None:
                d = _polygon_to_svg_path(ad['local_geom'], scale,
                                          cx_svg, cy_svg)
                if d:
                    fh.write(f'        <path d="{d}"/>\n')
            fh.write('      </g>\n')

            # Anchor name label below
            fh.write(f'      <g id="anchor_{i+1}_label" '
                     f'font-family="sans-serif" font-size="9" '
                     f'font-weight="700" fill="{ANCHOR_LABEL_COLOR}" '
                     f'text-anchor="middle">\n')
            ly = cy_svg + max_r * 1.45 * scale + 18
            label = ad['name'] if ad['name'] and ad['name'] != ad['building_id'] \
                    else ad['building_id']
            esc = str(label).replace('&', '&amp;').replace('<', '&lt;')
            fh.write(f'        <text x="{cx_svg:.2f}" y="{ly:.2f}">'
                     f'{esc}</text>\n')
            fh.write('      </g>\n')

            fh.write('    </g>\n')
        fh.write('  </g>\n')

        # Legend (chrome) — single shared legend at the bottom
        legend_y = fig_h - 70
        fh.write('  <g id="chrome_legend" font-family="sans-serif" '
                 f'font-size="10" fill="{TEXT_COLOR}">\n')
        fh.write(f'    <text x="40" y="{legend_y - 12}" font-size="9" '
                 f'fill="{TEXT_DIM}" font-weight="700">DOMINANT PROGRAM '
                 f'IN (DIRECTION, DISTANCE) CELL</text>\n')
        x_offset = 40
        for cat in ROSSETTI_ORDER:
            color = PROGRAM_COLORS[cat]
            label = PROGRAM_LABELS[cat]
            fh.write(f'    <rect x="{x_offset}" y="{legend_y}" '
                     f'width="14" height="14" fill="{color}"/>\n')
            fh.write(f'    <text x="{x_offset + 19}" y="{legend_y + 11}">'
                     f'{label}</text>\n')
            x_offset += len(label) * 7 + 40
        # empty cell indicator
        fh.write(f'    <rect x="{x_offset}" y="{legend_y}" '
                 f'width="14" height="14" fill="{EMPTY_CELL_COLOR}" '
                 f'stroke="{EMPTY_CELL_EDGE}" stroke-width="0.6"/>\n')
        fh.write(f'    <text x="{x_offset + 19}" y="{legend_y + 11}">'
                 f'empty</text>\n')
        fh.write('  </g>\n')

        # North arrow (single, top-right)
        north_cx = fig_w - 50
        north_cy = 50
        fh.write('  <g id="chrome_north_arrow" font-family="sans-serif" '
                 f'font-size="11" font-weight="700" fill="{TEXT_COLOR}">\n')
        fh.write(f'    <path d="M{north_cx},{north_cy - 12} '
                 f'L{north_cx + 8},{north_cy + 10} '
                 f'L{north_cx},{north_cy + 4} '
                 f'L{north_cx - 8},{north_cy + 10} Z" '
                 f'fill="{TEXT_COLOR}" stroke="none"/>\n')
        fh.write(f'    <text x="{north_cx}" y="{north_cy + 24}" '
                 f'text-anchor="middle">N</text>\n')
        fh.write('  </g>\n')

        fh.write('</svg>\n')


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Anchor relational fields as polar plots (KPF aesthetic)")
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--derived', type=Path, required=True)
    parser.add_argument('--cluster', type=int, default=0)
    parser.add_argument('--rings', type=str, default='100,200,300,400',
                        help='Ring outer radii in FEET (default 100,200,300,400)')
    args = parser.parse_args()

    ring_outer_radii_ft = [float(x) for x in args.rings.split(',')]
    ring_outer_radii_m = [r * M_PER_FT for r in ring_outer_radii_ft]

    enriched_path = args.derived / 'buildings_enriched.gpkg'
    if not enriched_path.exists():
        raise SystemExit(f"missing {enriched_path}")
    buildings = gpd.read_file(enriched_path, layer='buildings')

    places_path = args.source / 'rod_places.geojson'
    if not places_path.exists():
        raise SystemExit(f"missing {places_path}")
    places = gpd.read_file(places_path)

    metric_crs = buildings.estimate_utm_crs()
    buildings = buildings.to_crs(metric_crs)
    places = places.to_crs(metric_crs)

    if args.cluster == 0:
        args.cluster = detect_anchor_cluster(buildings)
        print(f"  auto-detected anchor cluster: {args.cluster} "
              f"(largest median building area)")

    anchors = buildings[buildings['cluster_id'] == args.cluster]
    if len(anchors) == 0:
        raise SystemExit(f"no buildings in cluster {args.cluster}")

    print(f"  {len(anchors)} anchors")
    print(f"  rings: {ring_outer_radii_ft} ft "
          f"({[round(r, 1) for r in ring_outer_radii_m]} m)")

    profile_path = args.derived / 'district_profile.json'
    sad_name = args.derived.name
    if profile_path.exists():
        sad_name = json.loads(profile_path.read_text()).get('sad_name', sad_name)

    # Pre-compute everything once
    anchors_data = []
    for idx, row in anchors.iterrows():
        poly = to_polygon(row.geometry)
        anchor_local = building_local_coords(poly)
        anchor_id = row.get('building_id', f'anchor_{idx}')
        anchor_name = row.get('name', '')
        if anchor_name is None or (isinstance(anchor_name, float)
                                     and np.isnan(anchor_name)):
            anchor_name = ''
        anchor_name = str(anchor_name).strip()
        if not anchor_name or anchor_name == 'nan':
            anchor_name = anchor_id

        cells = analyze_anchor_polar(poly, places, ring_outer_radii_m)
        anchors_data.append({
            'building_id': anchor_id,
            'name': anchor_name,
            'area_m2': float(row.get('area_m2', 0)),
            'cells_dict': cells,
            'local_geom': anchor_local,
        })

    out_png = args.derived / 'anchor_polar_plots.png'
    out_svg = args.derived / 'anchor_polar_plots.svg'

    # PNG via matplotlib
    summaries = render_all_anchors_png(anchors, places, ring_outer_radii_m,
                                         ring_outer_radii_ft,
                                         sad_name, out_png)
    # SVG via hand-rolled (named layers)
    render_all_anchors_svg(anchors_data, ring_outer_radii_m,
                            ring_outer_radii_ft, sad_name, out_svg)

    # JSON output - schema kept for back-compatibility (M10 reads
    # `anchors[].building_id`)
    out_json = args.derived / 'anchor_polar_plots.json'
    out_json.write_text(json.dumps({
        'sad_name': sad_name, 'cluster': args.cluster,
        'ring_outer_radii_m':  ring_outer_radii_m,
        'ring_outer_radii_ft': ring_outer_radii_ft,
        'anchors': summaries,
    }, indent=2))

    print(f"[OK] wrote {out_png.name}")
    print(f"[OK] wrote {out_svg.name}")
    print(f"[OK] wrote {out_json.name}")


if __name__ == '__main__':
    main()
