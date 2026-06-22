"""
module_06d_anchor_relational_field.py

Treats the SAD as a field of relational composition. For each anchor
building (default: cluster-2, the stadium-class), measures HOW
surrounding POIs are arranged relative to the building's geometry -
not just what's nearby, but where along each face, at what spacing,
and with what program rhythm.

The two-panel output per anchor:

  LEFT (plan view):
    Building polygon centered, with all POIs within --radius-m colored
    by Rossetti category. Shows the actual spatial distribution.

  RIGHT (carpet view):
    The same data unrolled. X-axis is position along the building's
    perimeter (0 to total perimeter, in meters). Y-axis is perpendicular
    distance from the building edge (0 to radius). Each POI is plotted
    at its arc-projection point with its perpendicular distance.
    Vertical dashed lines mark vertices of the simplified polygon -
    these are the "corners" between building faces.

The carpet view answers the question "how does activity arrange itself
along each face of the anchor?" - which is the geometric-relational
finding that no standard POI analysis can produce, because nobody else
has the building geometry tied to programmatic data at this granularity.

INPUTS (all from derived + source folders)
  buildings_enriched.gpkg, rod_places.geojson, manifest.json

OUTPUTS (per anchor building)
  anchor_profile_<building_id>.png + .svg
  anchor_profile_<building_id>.json   per-POI projections + per-face stats
And one summary file:
  anchor_relational_summary.json      cross-anchor aggregate

USAGE
  # Default: every cluster-2 (stadium-class) anchor
  python module_06d_anchor_relational_field.py --derived <dir> --source <dir>
  
  # Different cluster (e.g., the mid-rise office cluster)
  python module_06d_anchor_relational_field.py --derived <dir> --source <dir> --cluster 5
  
  # Specific buildings only
  python module_06d_anchor_relational_field.py --derived <dir> --source <dir> --building-ids b00100 b00132
  
  # Top N largest buildings regardless of cluster
  python module_06d_anchor_relational_field.py --derived <dir> --source <dir> --top-n 10
  
  # Adjust radius (default 100m)
  python module_06d_anchor_relational_field.py --derived <dir> --source <dir> --radius-m 150
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from matplotlib.lines import Line2D
from shapely.geometry import Polygon, MultiPolygon, Point


# Same Rossetti palette as Module 06b for consistency across all visualizations
PROGRAM_COLORS = {
    'sport':                     '#d62728',  # red
    'residential':               '#2ca02c',  # green
    'hotel':                     '#9467bd',  # purple
    'retail_food_entertainment': '#ff7f0e',  # orange
    'office':                    '#1f77b4',  # blue
    'parking':                   '#8c564b',  # brown
    'open_space':                '#bcbd22',  # yellow-green
    'other':                     '#7f7f7f',  # gray
}


def detect_anchor_cluster(buildings) -> int:
    """
    Auto-detect the anchor cluster as the one with the largest median
    building area. Cluster IDs are arbitrary across SADs (Detroit's anchors
    are cluster_2; Titletown's are cluster_1) because Ward linkage cluster
    labels depend on input data, so we identify by morphological signature
    rather than by arbitrary label.
    """
    medians = buildings.groupby('cluster_id')['area_m2'].median()
    anchor_cluster = int(medians.idxmax())
    return anchor_cluster


ROSSETTI_ORDER = list(PROGRAM_COLORS.keys())


def to_polygon(geom):
    """Convert MultiPolygon to its largest Polygon component; return Polygon unchanged."""
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def bearing_deg(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Compass bearing in degrees (0=N, 90=E) from p1 to p2, in projected x/y."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    angle_math = math.degrees(math.atan2(dy, dx))  # 0 = east, CCW positive
    # Convert to compass (0 = north, CW)
    return (90 - angle_math) % 360


def compass_label(bearing: float) -> str:
    """Convert bearing in degrees to 8-way compass label."""
    labels = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    return labels[int(((bearing + 22.5) % 360) // 45)]


def analyze_anchor(
    poly: Polygon,
    places_gdf: gpd.GeoDataFrame,
    radius_m: float,
    simplify_tolerance_m: float = 3.0,
) -> dict:
    """
    Project all POIs within radius_m of the building onto its exterior.
    Returns per-POI arc positions + perpendicular distances, plus
    per-face statistics.
    
    All inputs must already be in a metric CRS.
    """
    exterior = poly.exterior
    perimeter = exterior.length
    
    # Filter places within radius of the building polygon
    nearby = places_gdf[places_gdf.distance(poly) <= radius_m].copy()
    
    # Project each POI to the closest point on the exterior
    poi_data = []
    for _, p in nearby.iterrows():
        pt = p.geometry
        arc_pos = exterior.project(pt)  # arc length along perimeter to closest point
        perp_dist = pt.distance(poly)   # 0 if inside polygon, else distance to edge
        poi_data.append({
            'name': str(p.get('name', '') or ''),
            'rossetti_category': p.get('rossetti_category', 'other'),
            'arc_position_m': float(arc_pos),
            'perp_distance_m': float(perp_dist),
            'x': float(pt.x),
            'y': float(pt.y),
        })
    poi_data.sort(key=lambda d: d['arc_position_m'])
    
    # Per-face statistics from the simplified polygon. Each segment between
    # consecutive simplified vertices is one face.
    simple_poly = poly.simplify(simplify_tolerance_m, preserve_topology=True)
    if isinstance(simple_poly, MultiPolygon):
        simple_poly = max(simple_poly.geoms, key=lambda g: g.area)
    
    coords = list(simple_poly.exterior.coords)
    # Compute arc position of each simplified vertex (in original-poly arc space).
    # Use explicit x,y indexing in case the polygon has z coords from 3D source data.
    vertex_positions = []
    for c in coords:
        vp = exterior.project(Point(c[0], c[1]))
        vertex_positions.append(float(vp))
    
    faces = []
    for i in range(len(coords) - 1):
        a = coords[i]
        b = coords[i + 1]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        bearing = bearing_deg(a, b)
        
        # POIs whose arc position falls within this face's span
        v_start = vertex_positions[i]
        v_end = vertex_positions[i + 1]
        if v_end < v_start:  # wraps around 0
            in_face = [d for d in poi_data
                       if d['arc_position_m'] >= v_start
                       or d['arc_position_m'] <= v_end]
        else:
            in_face = [d for d in poi_data
                       if v_start <= d['arc_position_m'] <= v_end]
        
        category_counts: dict[str, int] = {}
        for d in in_face:
            cat = d['rossetti_category']
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        faces.append({
            'face_idx': i,
            'length_m': round(length, 2),
            'bearing_deg': round(bearing, 1),
            'compass': compass_label(bearing),
            'arc_start_m': round(v_start, 2),
            'arc_end_m': round(v_end, 2),
            'poi_count': len(in_face),
            'poi_density_per_m': round(len(in_face) / length, 4) if length > 0 else 0.0,
            'category_counts': category_counts,
            'dominant_category': (max(category_counts, key=category_counts.get)
                                  if category_counts else None),
        })
    
    return {
        'perimeter_m': round(perimeter, 2),
        'area_m2': round(poly.area, 2),
        'n_pois_in_radius': len(poi_data),
        'radius_m': radius_m,
        'simplify_tolerance_m': simplify_tolerance_m,
        'face_count': len(faces),
        'faces': faces,
        'vertex_arc_positions_m': vertex_positions,
        'pois': poi_data,
    }


def render_anchor_panel(
    poly: Polygon,
    analysis: dict,
    building_id: str,
    building_name: str,
    sad_name: str,
    out_png: Path,
    out_svg: Path,
) -> None:
    """Produce the two-panel composite: plan view (left) + carpet view (right)."""
    radius = analysis['radius_m']
    perimeter = analysis['perimeter_m']
    pois = analysis['pois']
    vertex_positions = analysis['vertex_arc_positions_m']
    
    fig, (ax_plan, ax_carpet) = plt.subplots(
        1, 2, figsize=(18, 7.5),
        gridspec_kw={'width_ratios': [1, 1.5]},
    )
    
    # ─── LEFT: plan view ─────────────────────────────────────────────────
    cx, cy = poly.centroid.x, poly.centroid.y
    
    # Radius circle
    radius_circle = Circle(
        (cx, cy), radius,
        fill=False, edgecolor='#999999',
        linewidth=0.8, linestyle='--', alpha=0.7,
    )
    ax_plan.add_patch(radius_circle)
    
    # Building polygon - extract x/y explicitly (handles 3D coords)
    xs = [c[0] for c in poly.exterior.coords]
    ys = [c[1] for c in poly.exterior.coords]
    ax_plan.fill(xs, ys, color='#cccccc', edgecolor='#333333', linewidth=1.2)
    
    # POIs
    for poi in pois:
        c = PROGRAM_COLORS.get(poi['rossetti_category'], '#999999')
        ax_plan.plot(poi['x'], poi['y'], 'o',
                     color=c, markersize=6, markeredgecolor='white',
                     markeredgewidth=0.4, alpha=0.9)
    
    # Compass arrow (north up)
    arrow_x = cx - radius * 0.85
    arrow_y = cy + radius * 0.7
    ax_plan.annotate('N', xy=(arrow_x, arrow_y), xytext=(arrow_x, arrow_y - radius * 0.18),
                     fontsize=11, fontweight='bold', ha='center',
                     arrowprops=dict(arrowstyle='->', color='#333', lw=1.2))
    
    # Scale bar (50m)
    scale_len = 50.0
    sb_x0 = cx + radius * 0.35
    sb_y0 = cy - radius * 0.93
    ax_plan.plot([sb_x0, sb_x0 + scale_len], [sb_y0, sb_y0],
                 'k-', linewidth=2)
    ax_plan.text(sb_x0 + scale_len / 2, sb_y0 + radius * 0.04,
                 '50 m', ha='center', fontsize=8)
    
    ax_plan.set_aspect('equal')
    margin = radius * 1.1
    ax_plan.set_xlim(cx - margin, cx + margin)
    ax_plan.set_ylim(cy - margin, cy + margin)
    ax_plan.set_xticks([])
    ax_plan.set_yticks([])
    for spine in ax_plan.spines.values():
        spine.set_visible(False)
    
    title_name = building_name if building_name else f"Building {building_id}"
    ax_plan.set_title(
        f"{title_name}  ({analysis['area_m2']:,.0f} m^2)\n"
        f"{analysis['n_pois_in_radius']} POIs within {radius:.0f}m",
        fontsize=11, pad=8,
    )
    
    # ─── RIGHT: carpet view ──────────────────────────────────────────────
    # Vertical dashed lines at simplified-polygon vertices (face boundaries)
    for vp in vertex_positions[:-1]:  # skip the closing duplicate
        ax_carpet.axvline(vp, color='#bbbbbb', linestyle='--',
                          linewidth=0.6, alpha=0.8)
    
    # Face labels along the top (compass bearing of each face)
    faces = analysis['faces']
    for f in faces:
        mid_arc = (f['arc_start_m'] + f['arc_end_m']) / 2
        if f['arc_end_m'] < f['arc_start_m']:
            mid_arc = (f['arc_start_m'] + perimeter + f['arc_end_m']) / 2 % perimeter
        ax_carpet.text(mid_arc, radius * 0.96, f['compass'],
                       ha='center', va='top', fontsize=8,
                       color='#555', alpha=0.8)
    
    # POIs as colored dots
    for poi in pois:
        c = PROGRAM_COLORS.get(poi['rossetti_category'], '#999999')
        ax_carpet.plot(poi['arc_position_m'], poi['perp_distance_m'], 'o',
                       color=c, markersize=6, markeredgecolor='white',
                       markeredgewidth=0.4, alpha=0.9)
    
    # Building edge as a thick line at y=0
    ax_carpet.axhline(0, color='#333333', linewidth=2.5, zorder=0)
    
    ax_carpet.set_xlim(0, perimeter)
    ax_carpet.set_ylim(-radius * 0.05, radius * 1.02)
    ax_carpet.set_xlabel('position along building perimeter (m)', fontsize=10)
    ax_carpet.set_ylabel('perpendicular distance from edge (m)', fontsize=10)
    ax_carpet.set_title(
        f"unrolled edge profile - {len(faces)} faces, "
        f"perimeter {perimeter:.0f} m",
        fontsize=11, pad=8,
    )
    ax_carpet.grid(True, alpha=0.25, linewidth=0.5)
    
    # Legend (program colors)
    present_categories = sorted({p['rossetti_category'] for p in pois},
                                key=lambda c: ROSSETTI_ORDER.index(c)
                                if c in ROSSETTI_ORDER else 99)
    handles = [
        Line2D([0], [0], marker='o', color='w',
               markerfacecolor=PROGRAM_COLORS.get(cat, '#999'),
               markersize=8, label=cat.replace('_', ' '),
               markeredgecolor='white', markeredgewidth=0.4)
        for cat in present_categories
    ]
    ax_carpet.legend(handles=handles, loc='upper right',
                     fontsize=8, framealpha=0.95)
    
    fig.suptitle(
        f"{sad_name} - anchor relational field",
        fontsize=13, y=1.0,
    )
    plt.tight_layout()
    fig.savefig(out_png, dpi=180, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Anchor relational field analysis")
    parser.add_argument('--derived', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--cluster', type=int, default=0,
                        help='Cluster ID to analyze (default 2 - stadium-class)')
    parser.add_argument('--building-ids', nargs='+', default=None,
                        help='Specific building IDs (overrides --cluster)')
    parser.add_argument('--top-n', type=int, default=None,
                        help='Top N largest buildings (overrides --cluster)')
    parser.add_argument('--min-area', type=float, default=None,
                        help='Only buildings with area >= this many m^2')
    parser.add_argument('--radius-m', type=float, default=100.0,
                        help='Search radius in meters (default 100)')
    parser.add_argument('--simplify-m', type=float, default=3.0,
                        help='Polygon simplification tolerance for face detection (default 3m)')
    args = parser.parse_args()
    
    # Load
    manifest = json.loads((args.derived / 'manifest.json').read_text())
    metric_crs = manifest['crs_metric']
    
    buildings = gpd.read_file(args.derived / 'buildings_enriched.gpkg', layer='buildings')
    places = gpd.read_file(args.source / 'rod_places.geojson')
    
    # Reproject to metric CRS for accurate distance/length operations
    buildings = buildings.to_crs(metric_crs)
    places = places.to_crs(metric_crs)
    
    # Select target buildings
    if args.building_ids:
        targets = buildings[buildings['building_id'].isin(args.building_ids)].copy()
    elif args.top_n:
        targets = buildings.nlargest(args.top_n, 'area_m2').copy()
    else:
        # Auto-detect anchor cluster when not specified (cluster=0)
        if args.cluster == 0:
            args.cluster = detect_anchor_cluster(buildings)
            print(f"  auto-detected anchor cluster: {args.cluster} "
                  f"(largest median building area)")
        targets = buildings[buildings['cluster_id'] == args.cluster].copy()
    
    if args.min_area is not None:
        targets = targets[targets['area_m2'] >= args.min_area]
    
    if len(targets) == 0:
        sys.exit(f"No buildings match the selection criteria.")
    
    print(f"Analyzing {len(targets)} anchor buildings "
          f"(radius={args.radius_m:.0f}m, simplify={args.simplify_m:.0f}m)")
    
    sad_name = manifest.get('sad_name', args.derived.name)
    
    summary = {
        'sad_name': sad_name,
        'radius_m': args.radius_m,
        'simplify_tolerance_m': args.simplify_m,
        'n_anchors': int(len(targets)),
        'cluster_filter': args.cluster if not args.building_ids and not args.top_n else None,
        'anchors': [],
    }
    
    for _, row in targets.iterrows():
        bid = row.get('building_id', f"b_idx_{row.name}")
        bname = str(row.get('name', '')) if row.get('name') else ''
        if bname == 'nan':
            bname = ''
        
        poly = to_polygon(row.geometry)
        analysis = analyze_anchor(
            poly, places,
            radius_m=args.radius_m,
            simplify_tolerance_m=args.simplify_m,
        )
        
        # Per-building outputs
        out_png = args.derived / f'anchor_profile_{bid}.png'
        out_svg = args.derived / f'anchor_profile_{bid}.svg'
        out_json = args.derived / f'anchor_profile_{bid}.json'
        
        render_anchor_panel(poly, analysis, bid, bname, sad_name, out_png, out_svg)
        out_json.write_text(json.dumps(analysis, indent=2))
        
        # Cross-anchor summary entry (compact)
        face_summary = [
            {'compass': f['compass'], 'length_m': f['length_m'],
             'poi_count': f['poi_count'], 'dominant_category': f['dominant_category']}
            for f in analysis['faces']
        ]
        summary['anchors'].append({
            'building_id': bid,
            'name': bname,
            'area_m2': analysis['area_m2'],
            'perimeter_m': analysis['perimeter_m'],
            'n_pois_in_radius': analysis['n_pois_in_radius'],
            'face_count': analysis['face_count'],
            'faces': face_summary,
        })
        
        # Console row
        dom_str = ''
        if analysis['pois']:
            cat_counts: dict[str, int] = {}
            for p in analysis['pois']:
                cat_counts[p['rossetti_category']] = cat_counts.get(p['rossetti_category'], 0) + 1
            dom_cat = max(cat_counts, key=cat_counts.get)
            dom_str = f"  dominant: {dom_cat} ({cat_counts[dom_cat]})"
        print(f"  [{bid}] area={analysis['area_m2']:,.0f}m^2, "
              f"perimeter={analysis['perimeter_m']:.0f}m, "
              f"faces={analysis['face_count']}, "
              f"POIs={analysis['n_pois_in_radius']}{dom_str}")
    
    summary_path = args.derived / 'anchor_relational_summary.json'
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n[OK] wrote {len(targets)} anchor profile images")
    print(f"[OK] wrote anchor_relational_summary.json")


if __name__ == '__main__':
    main()
