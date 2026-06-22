"""
module_06b_v2_axis_transect.py — REDESIGN

Walking-transect visualization: shows what you'd encounter walking along
the primary axis of the SAD. Each axis transect is a horizontal strip where:

  - the X axis is real meters along the walking path
  - the Y axis is perpendicular distance from the centerline (left = above,
    right = below)
  - buildings render as filled silhouettes at their TRUE GEOGRAPHIC SCALE
    (no normalization, no fixed-size icons — Lambeau Field looks 200x bigger
    than a corner shop because it IS)
  - POIs are colored dots at their projected axis position
  - the figure uses EQUAL ASPECT so shapes aren't stretched
  - the visible range is clipped to the SAD-interior segment of the axis,
    with a 50m breathing-room buffer at each end
  - a small plan-view inset in the top-right corner shows the axis cutting
    through the SAD, so the viewer knows which transect they're looking at

Primary axis: detected from modal orientation of anchor-cluster buildings
              (which are usually parallel-aligned to the stadium long faces)
Secondary axis: perpendicular to primary

INPUT
  source/<sad>/sad_boundary.geojson
  source/<sad>/image_extent.geojson
  source/<sad>/rod_places.geojson
  derived/<sad>/buildings_enriched.gpkg

OUTPUT
  derived/<sad>/axis_transect_primary.{png,svg}
  derived/<sad>/axis_transect_secondary.{png,svg}
  derived/<sad>/axis_transect_summary.json

USAGE
  python module_06b_v2_axis_transect.py --source <dir> --derived <dir>
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.lines import Line2D
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from shapely.geometry import Polygon, MultiPolygon, LineString, Point


ROSSETTI_ORDER = [
    'sport', 'residential', 'hotel', 'retail_food_entertainment',
    'office', 'parking', 'open_space', 'other',
]

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

# Perpendicular distance from axis at which buildings/POIs are "fronting" the walk
FRONT_DISTANCE_M = 75.0

# Breathing-room buffer at each end of the SAD-interior segment
END_BUFFER_M = 50.0


def to_polygon(geom):
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def detect_primary_axis(buildings: gpd.GeoDataFrame) -> float:
    """
    Modal orientation of large (anchor-scale) buildings, weighted by area.
    The anchor cluster is the one with the largest median area_m2.
    """
    if 'orientation_deg' not in buildings.columns:
        return 0.0
    
    weights = buildings['area_m2'].copy().astype(float)
    
    if 'cluster_id' in buildings.columns:
        medians = buildings.groupby('cluster_id')['area_m2'].median()
        if len(medians) > 0:
            anchor_cluster = int(medians.idxmax())
            weights[buildings['cluster_id'] == anchor_cluster] *= 10.0
    
    orientations = buildings['orientation_deg'].dropna() % 180
    if len(orientations) == 0:
        return 0.0
    weights = weights.loc[orientations.index]
    
    hist, edges = np.histogram(orientations, bins=36, range=(0, 180),
                                weights=weights)
    peak_bin = np.argmax(hist)
    return float((edges[peak_bin] + edges[peak_bin + 1]) / 2)


def axis_line_through(canvas_polygon: Polygon, angle_deg: float,
                       through_point: Point) -> LineString:
    """Construct an axis line at angle_deg through through_point, clipped to canvas."""
    bbox = canvas_polygon.bounds
    diag = np.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1])
    
    angle_rad = np.radians(angle_deg)
    dx, dy = np.cos(angle_rad), np.sin(angle_rad)
    
    p1 = (through_point.x - 2 * diag * dx, through_point.y - 2 * diag * dy)
    p2 = (through_point.x + 2 * diag * dx, through_point.y + 2 * diag * dy)
    long_line = LineString([p1, p2])
    
    clipped = long_line.intersection(canvas_polygon)
    if clipped.is_empty:
        return long_line
    if hasattr(clipped, 'geoms'):
        clipped = max(clipped.geoms, key=lambda g: g.length)
    return clipped


def sad_axis_range(axis: LineString, sad_polygon) -> tuple[float, float]:
    """
    Compute the (s_min, s_max) range along the axis where it intersects the
    SAD interior, padded by END_BUFFER_M on each side.
    """
    intersection = axis.intersection(sad_polygon)
    if intersection.is_empty:
        return (0.0, axis.length)
    
    if hasattr(intersection, 'geoms'):
        seg = max(intersection.geoms, key=lambda g: g.length)
    else:
        seg = intersection
    
    coords = list(seg.coords)
    if len(coords) < 2:
        return (0.0, axis.length)
    
    s_start = axis.project(Point(coords[0][0], coords[0][1]))
    s_end = axis.project(Point(coords[-1][0], coords[-1][1]))
    s_min = max(0.0, min(s_start, s_end) - END_BUFFER_M)
    s_max = min(axis.length, max(s_start, s_end) + END_BUFFER_M)
    return (s_min, s_max)


def project_onto_axis(point: Point, axis: LineString) -> tuple[float, float]:
    """Returns (position_along_axis, perpendicular_distance).
    Perp is SIGNED: positive = LEFT, negative = RIGHT relative to axis direction."""
    s = axis.project(point)
    foot = axis.interpolate(s)
    coords = list(axis.coords)
    ax_dx = coords[-1][0] - coords[0][0]
    ax_dy = coords[-1][1] - coords[0][1]
    axis_len = np.hypot(ax_dx, ax_dy)
    if axis_len == 0:
        return s, 0.0
    nx, ny = -ax_dy / axis_len, ax_dx / axis_len  # CCW perpendicular = "left"
    dx = point.x - foot.x
    dy = point.y - foot.y
    signed_dist = dx * nx + dy * ny
    return s, signed_dist


def project_polygon_to_axis(poly: Polygon, axis: LineString
                              ) -> list[tuple[float, float]]:
    """Project every vertex of a polygon to (s, perp) axis coordinates."""
    return [project_onto_axis(Point(c[0], c[1]), axis)
            for c in poly.exterior.coords]


def render_inset(ax_inset, canvas_polygon, sad_polygon, axis,
                  s_min: float, s_max: float, axis_color: str):
    """Plan-view inset showing canvas, SAD, and the axis with highlighted clip range."""
    if canvas_polygon is not None:
        cx = [c[0] for c in canvas_polygon.exterior.coords]
        cy = [c[1] for c in canvas_polygon.exterior.coords]
        ax_inset.fill(cx, cy, facecolor='#fafafa', edgecolor='#ccc',
                       linewidth=0.5)
    if sad_polygon is not None:
        sad_p = to_polygon(sad_polygon)
        sx = [c[0] for c in sad_p.exterior.coords]
        sy = [c[1] for c in sad_p.exterior.coords]
        ax_inset.fill(sx, sy, facecolor='#e8e8e8', edgecolor='#666',
                       linewidth=1.0)
    
    # Full axis line as dashed grey
    ac = list(axis.coords)
    ax_inset.plot([ac[0][0], ac[-1][0]], [ac[0][1], ac[-1][1]],
                   color='#999', linewidth=0.8, linestyle='--')
    
    # Highlighted clipped segment
    a = axis.interpolate(s_min)
    b = axis.interpolate(s_max)
    ax_inset.plot([a.x, b.x], [a.y, b.y],
                   color=axis_color, linewidth=2.2, solid_capstyle='round')
    # Direction arrow at end
    ax_inset.plot(b.x, b.y, marker='>', color=axis_color, markersize=6,
                   markeredgecolor='none')
    
    ax_inset.set_aspect('equal')
    ax_inset.set_xticks([])
    ax_inset.set_yticks([])
    for spine in ax_inset.spines.values():
        spine.set_edgecolor('#888')
        spine.set_linewidth(0.5)
    ax_inset.set_title('plan', fontsize=7, pad=2, color='#444')


def render_transect(
    buildings: gpd.GeoDataFrame,
    places: gpd.GeoDataFrame,
    sad_polygon,
    canvas_polygon,
    axis: LineString,
    axis_name: str,
    axis_angle: float,
    sad_name: str,
    out_png: Path,
    out_svg: Path,
):
    """
    Render the walking transect — equal aspect, SAD-clipped, true scale.
    Long transects (>MAX_ROW_LENGTH) wrap into multiple stacked rows so
    each row keeps a readable aspect ratio.
    """
    MAX_ROW_LENGTH = 900.0  # meters per row before wrapping
    
    s_min, s_max = sad_axis_range(axis, sad_polygon) if sad_polygon \
                    else (0.0, axis.length)
    visible_length = s_max - s_min
    
    # Decide how many rows: each row gets at most MAX_ROW_LENGTH of axis
    n_rows = max(1, int(np.ceil(visible_length / MAX_ROW_LENGTH)))
    row_length = visible_length / n_rows
    
    # ─── Filter buildings + project their vertices into axis space ──────
    bldgs_data = []
    for idx, row in buildings.iterrows():
        poly = to_polygon(row.geometry)
        centroid = poly.centroid
        s_c, perp_c = project_onto_axis(centroid, axis)
        if abs(perp_c) > FRONT_DISTANCE_M + 30:
            continue
        verts = project_polygon_to_axis(poly, axis)
        ss = [v[0] for v in verts]
        ps = [v[1] for v in verts]
        if max(ss) < s_min or min(ss) > s_max:
            continue
        if max(ps) < -FRONT_DISTANCE_M or min(ps) > FRONT_DISTANCE_M:
            continue
        bldgs_data.append({
            'idx': idx,
            'vertices': verts,
            'centroid_s': s_c,
            'centroid_perp': perp_c,
            'area': float(row.get('area_m2', poly.area)),
            'cluster_id': int(row.get('cluster_id', 0)),
            'dominant_program': row.get('dominant_program_inside', 'other'),
        })
    
    pois_data = []
    for idx, row in places.iterrows():
        s, perp = project_onto_axis(row.geometry, axis)
        if s < s_min or s > s_max:
            continue
        if abs(perp) > FRONT_DISTANCE_M:
            continue
        pois_data.append({
            's': s, 'perp': perp,
            'rossetti': row.get('rossetti_category', 'other'),
        })
    
    print(f"  axis {axis_name} ({axis_angle:.0f}deg): "
          f"visible window {visible_length:.0f}m -> {n_rows} row(s) of "
          f"{row_length:.0f}m each, "
          f"{len(bldgs_data)} buildings, {len(pois_data)} POIs")
    
    # ─── Figure sizing ──────────────────────────────────────────────────
    perp_visible = 2 * FRONT_DISTANCE_M + 30
    # Each row at equal aspect: width = row_length / 50 inches (~50m/inch)
    target_width = max(12.0, min(22.0, row_length / 50.0))
    row_height = target_width * perp_visible / row_length
    row_height = max(2.5, min(5.0, row_height))
    
    total_height = row_height * n_rows + 0.6 * (n_rows - 1) + 1.2  # gaps + title
    
    fig, axes = plt.subplots(
        n_rows, 1,
        figsize=(target_width, total_height),
        gridspec_kw={'hspace': 0.45},
    )
    if n_rows == 1:
        axes = [axes]
    
    # ─── Render each row ────────────────────────────────────────────────
    cats_seen = set()
    for row_idx in range(n_rows):
        ax = axes[row_idx]
        row_s_min = s_min + row_idx * row_length
        row_s_max = s_min + (row_idx + 1) * row_length
        
        # SAD-interior tint (highlight where axis is inside SAD)
        if sad_polygon is not None:
            sad_p = to_polygon(sad_polygon)
            sad_intersect = axis.intersection(sad_p)
            if not sad_intersect.is_empty:
                if hasattr(sad_intersect, 'geoms'):
                    segs = list(sad_intersect.geoms)
                else:
                    segs = [sad_intersect]
                for seg in segs:
                    if len(list(seg.coords)) < 2:
                        continue
                    sc = list(seg.coords)
                    s_a = axis.project(Point(sc[0][0], sc[0][1]))
                    s_b = axis.project(Point(sc[-1][0], sc[-1][1]))
                    ax.axvspan(min(s_a, s_b), max(s_a, s_b),
                               color='#f5f1e8', alpha=0.6, zorder=0)
        
        # Centerline
        ax.axhline(0, color='#222', linewidth=1.2, zorder=2)
        
        # Buildings whose centroid falls in this row's range, OR which
        # straddle the boundary — render any building overlapping this row
        for b in bldgs_data:
            ss = [v[0] for v in b['vertices']]
            if max(ss) < row_s_min or min(ss) > row_s_max:
                continue
            color = PROGRAM_COLORS.get(b['dominant_program'], '#bbbbbb')
            if (b['dominant_program'] in (None, '', 'nan') or
                (isinstance(b['dominant_program'], float) and
                 np.isnan(b['dominant_program']))):
                color = '#bbbbbb'
            cats_seen.add(b['dominant_program'])
            patch = MplPolygon(b['vertices'], closed=True,
                               facecolor=color, edgecolor='#222',
                               linewidth=0.4, alpha=0.88, zorder=3)
            ax.add_patch(patch)
        
        # POIs in this row's range
        for p in pois_data:
            if p['s'] < row_s_min or p['s'] > row_s_max:
                continue
            c = PROGRAM_COLORS.get(p['rossetti'], '#999')
            cats_seen.add(p['rossetti'])
            ax.scatter(p['s'], p['perp'], s=22, c=c, marker='o',
                       edgecolors='white', linewidths=0.6, zorder=5)
        
        # Side labels (only on first row to avoid clutter)
        if row_idx == 0:
            label_x = row_s_min + row_length * 0.005
            ax.text(label_x, FRONT_DISTANCE_M * 0.92,
                    "LEFT", fontsize=10, fontweight='bold', color='#444',
                    va='top', ha='left')
            ax.text(label_x, -FRONT_DISTANCE_M * 0.92,
                    "RIGHT", fontsize=10, fontweight='bold', color='#444',
                    va='bottom', ha='left')
        
        # Row label
        if n_rows > 1:
            ax.text(row_s_max - row_length * 0.005, FRONT_DISTANCE_M * 0.92,
                    f"row {row_idx + 1}/{n_rows}",
                    fontsize=8, color='#777', va='top', ha='right',
                    fontfamily='monospace')
        
        ax.set_xlim(row_s_min, row_s_max)
        ax.set_ylim(-FRONT_DISTANCE_M - 5, FRONT_DISTANCE_M + 5)
        ax.set_aspect('equal')  # equal aspect on EVERY row
        ax.set_ylabel('perp (m)', fontsize=8)
        ax.grid(axis='x', linestyle='--', alpha=0.25, linewidth=0.5)
        ax.set_axisbelow(True)
        for spine in ('top', 'right'):
            ax.spines[spine].set_visible(False)
        ax.tick_params(labelsize=8)
        
        # Only bottom row gets the x label
        if row_idx == n_rows - 1:
            ax.set_xlabel(f'position along {axis_name} axis (m)', fontsize=9)
    
    # Title above all rows
    fig.suptitle(
        f"{sad_name} — walking transect, {axis_name} axis "
        f"({axis_angle:.0f}deg from N)  |  "
        f"{visible_length:.0f}m visible (SAD-interior + {int(END_BUFFER_M)}m buffer)"
        + (f", {n_rows} rows" if n_rows > 1 else ""),
        fontsize=11, y=0.995,
    )
    
    # Legend in the bottom row, lower right
    cats_seen_list = sorted(
        [c for c in cats_seen if c in PROGRAM_COLORS],
        key=lambda c: ROSSETTI_ORDER.index(c) if c in ROSSETTI_ORDER else 99,
    )
    handles = [Line2D([], [], marker='s', linestyle='', markersize=9,
                       markerfacecolor=PROGRAM_COLORS[c], markeredgecolor='#222',
                       label=c.replace('_', ' '))
               for c in cats_seen_list]
    if handles:
        axes[-1].legend(handles=handles, loc='lower right', fontsize=8,
                        framealpha=0.96, ncol=2, title='dominant program',
                        title_fontsize=8.5)
    
    # Inset plan view in the top-right of the FIRST row
    axis_color = '#cc4400' if axis_name == 'primary' else '#0066aa'
    ax_inset = inset_axes(
        axes[0], width="14%", height="60%", loc='upper right',
        bbox_to_anchor=(0, -0.02, 1, 1), bbox_transform=axes[0].transAxes,
    )
    render_inset(ax_inset, canvas_polygon, sad_polygon, axis,
                  s_min, s_max, axis_color)
    
    fig.savefig(out_png, dpi=160, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)
    
    return {
        'axis_name': axis_name,
        'axis_angle_deg': float(axis_angle),
        'visible_length_m': float(visible_length),
        'n_rows': n_rows,
        'row_length_m': float(row_length),
        's_min': float(s_min),
        's_max': float(s_max),
        'n_buildings_visible': len(bldgs_data),
        'n_pois_visible': len(pois_data),
        'front_distance_m': FRONT_DISTANCE_M,
        'end_buffer_m': END_BUFFER_M,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Walking-transect visualization (true-scale, SAD-clipped)")
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--derived', type=Path, required=True)
    args = parser.parse_args()
    
    enriched_path = args.derived / 'buildings_enriched.gpkg'
    if not enriched_path.exists():
        raise SystemExit(f"missing {enriched_path}")
    buildings = gpd.read_file(enriched_path, layer='buildings')
    
    places_path = args.source / 'rod_places.geojson'
    if not places_path.exists():
        raise SystemExit(f"missing {places_path}")
    places = gpd.read_file(places_path)
    
    extent_path = args.source / 'image_extent.geojson'
    if not extent_path.exists():
        raise SystemExit(f"missing {extent_path}")
    extent_gdf = gpd.read_file(extent_path)
    
    sad_polygon = None
    sad_boundary_path = args.source / 'sad_boundary.geojson'
    if sad_boundary_path.exists():
        sad_gdf = gpd.read_file(sad_boundary_path)
        try:
            sad_polygon = sad_gdf.union_all()
        except AttributeError:
            sad_polygon = sad_gdf.unary_union
    
    # Reproject everything to metric CRS
    metric_crs = buildings.estimate_utm_crs()
    buildings = buildings.to_crs(metric_crs)
    places = places.to_crs(metric_crs)
    extent_gdf = extent_gdf.to_crs(metric_crs)
    canvas_polygon = to_polygon(extent_gdf.geometry.iloc[0])
    if sad_polygon is not None:
        sad_polygon = (gpd.GeoSeries([sad_polygon], crs=sad_gdf.crs)
                          .to_crs(metric_crs).iloc[0])
    
    profile_path = args.derived / 'district_profile.json'
    sad_name = args.derived.name
    if profile_path.exists():
        sad_name = json.loads(profile_path.read_text()).get('sad_name', sad_name)
    
    # ─── Detect axes ────────────────────────────────────────────────────
    primary_angle = detect_primary_axis(buildings)
    secondary_angle = (primary_angle + 90.0) % 180
    print(f"detected primary axis:    {primary_angle:.1f} deg")
    print(f"        secondary axis:   {secondary_angle:.1f} deg")
    
    origin = sad_polygon.centroid if sad_polygon is not None \
              else canvas_polygon.centroid
    
    primary_axis = axis_line_through(canvas_polygon, primary_angle, origin)
    secondary_axis = axis_line_through(canvas_polygon, secondary_angle, origin)
    
    summary = {'sad_name': sad_name, 'transects': []}
    
    for axis, name, angle in [
        (primary_axis, 'primary', primary_angle),
        (secondary_axis, 'secondary', secondary_angle),
    ]:
        out_png = args.derived / f'axis_transect_{name}.png'
        out_svg = args.derived / f'axis_transect_{name}.svg'
        info = render_transect(
            buildings, places, sad_polygon, canvas_polygon,
            axis, name, angle, sad_name, out_png, out_svg,
        )
        summary['transects'].append(info)
        print(f"[OK] wrote {out_png.name}")
        print(f"[OK] wrote {out_svg.name}")
    
    out_summary = args.derived / 'axis_transect_summary.json'
    out_summary.write_text(json.dumps(summary, indent=2))
    print(f"[OK] wrote {out_summary.name}")


if __name__ == '__main__':
    main()
