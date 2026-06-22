"""
module_06d_v4_anchor_connectivity.py

Network graph showing the SAD's anchor buildings as nodes positioned at
their true geographic centroids, with edges between them weighted by the
POI density of the CORRIDOR between each pair. Edges are colored by the
dominant program category in that corridor.

The result: a visual map of which anchors form "active coalitions"
(thick colored edges) vs which are isolated (no edges or thin gray
edges), and what kind of activity binds them (retail corridor vs
parking corridor vs office corridor).

INPUT
  source/<sad>/sad_boundary.geojson
  source/<sad>/image_extent.geojson
  source/<sad>/rod_places.geojson
  derived/<sad>/buildings_enriched.gpkg

OUTPUT
  derived/<sad>/anchor_connectivity.{png,svg}
  derived/<sad>/anchor_connectivity.json

USAGE
  python module_06d_v4_anchor_connectivity.py --source <dir> --derived <dir>
       [--cluster N] [--corridor-width 30] [--min-pois 3]
"""
from __future__ import annotations
import argparse
import json
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from shapely.geometry import LineString, MultiPolygon, Point


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



def to_polygon(geom):
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def corridor_between(a_poly, b_poly, width: float, exclude_halo: float):
    """
    Build a corridor polygon between two anchors.
    The corridor is a buffered line between their centroids, MINUS
    halos around each anchor (to avoid double-counting POIs that
    belong to a single anchor's relational field).
    Returns (corridor_polygon, effective_length_m).
    
    Note: exclude_halo should be smaller than typical anchor separation
    or the halos will consume the entire corridor between close anchors.
    25m is a reasonable default; 50m is too aggressive for tightly
    clustered anchors like LCA/Ford Field/Comerica.
    """
    a_c = a_poly.centroid
    b_c = b_poly.centroid
    line = LineString([a_c, b_c])
    corridor = line.buffer(width, cap_style=2)  # flat cap
    if exclude_halo > 0:
        halo_a = a_poly.buffer(exclude_halo)
        halo_b = b_poly.buffer(exclude_halo)
        corridor = corridor.difference(halo_a).difference(halo_b)
        effective_length = max(1.0, line.length - 2 * exclude_halo)
    else:
        effective_length = max(1.0, line.length)
    return corridor, effective_length


def analyze_corridor(corridor, length_m: float, places: gpd.GeoDataFrame):
    """
    Return (poi_count, dominant_program, density_per_100m) for POIs
    inside the corridor.
    
    density_per_100m normalizes by corridor length so that long-distance
    edges (e.g., across the whole canvas) don't appear "stronger" than
    short-distance edges (e.g., adjacent anchors) just because they're
    longer.
    """
    if corridor.is_empty:
        return 0, None, 0.0
    in_corridor = places[places.geometry.within(corridor)]
    if len(in_corridor) == 0:
        return 0, None, 0.0
    counts = Counter(in_corridor['rossetti_category'].dropna().tolist())
    if not counts:
        return 0, None, 0.0
    dominant = counts.most_common(1)[0][0]
    count = sum(counts.values())
    density = (count / length_m) * 100  # POIs per 100m of corridor
    return count, dominant, density


def render_connectivity(
    anchors: gpd.GeoDataFrame,
    places: gpd.GeoDataFrame,
    sad_boundary,
    canvas_polygon,
    sad_name: str,
    corridor_width: float,
    min_pois: int,
    exclude_halo: float,
    out_png: Path,
    out_svg: Path,
):
    fig, ax = plt.subplots(figsize=(11, 11))
    
    # Canvas backdrop
    cx, cy = canvas_polygon.exterior.coords.xy
    ax.fill(cx, cy, facecolor='#fafafa', edgecolor='#ccc',
             linewidth=0.5, zorder=0)
    
    # SAD boundary
    if sad_boundary is not None:
        sad_poly = to_polygon(sad_boundary)
        sx, sy = sad_poly.exterior.coords.xy
        ax.plot(sx, sy, color='#444', linewidth=2, zorder=1)
    
    # Light scatter of all POIs as background context
    ax.scatter(places.geometry.x, places.geometry.y,
                s=3, color='#bbbbbb', alpha=0.4, zorder=2)
    
    # ─── Compute corridors and edges ────────────────────────────────────
    anchor_polys = []
    for idx, row in anchors.iterrows():
        poly = to_polygon(row.geometry)
        anchor_id = row.get('building_id', f'anchor_{idx}')
        anchor_name = row.get('name', '')
        if anchor_name is None or (isinstance(anchor_name, float) and np.isnan(anchor_name)):
            anchor_name = ''
        anchor_name = str(anchor_name).strip()
        if not anchor_name or anchor_name == 'nan':
            anchor_name = anchor_id
        anchor_polys.append({'id': anchor_id, 'name': anchor_name, 'poly': poly})
    
    # All pairwise corridors
    edges = []
    for i, j in combinations(range(len(anchor_polys)), 2):
        a, b = anchor_polys[i], anchor_polys[j]
        corridor, length_m = corridor_between(
            a['poly'], b['poly'], corridor_width, exclude_halo)
        poi_count, dominant, density = analyze_corridor(corridor, length_m, places)
        if poi_count >= min_pois:
            edges.append({
                'a': a['id'], 'b': b['id'],
                'a_idx': i, 'b_idx': j,
                'poi_count': poi_count,
                'corridor_length_m': round(length_m, 1),
                'poi_density_per_100m': round(density, 2),
                'dominant_program': dominant,
            })
    
    if edges:
        max_density = max(e['poi_density_per_100m'] for e in edges)
    else:
        max_density = 1.0
    
    # Draw edges
    for e in edges:
        a = anchor_polys[e['a_idx']]['poly'].centroid
        b = anchor_polys[e['b_idx']]['poly'].centroid
        color = PROGRAM_COLORS.get(e['dominant_program'], '#999')
        # Line width now scales with DENSITY (POIs/100m), not raw count.
        # This makes short dense corridors visible alongside long thin ones.
        lw = 1 + 7 * (e['poi_density_per_100m'] / max_density)
        ax.plot([a.x, b.x], [a.y, b.y],
                color=color, linewidth=lw, alpha=0.75,
                solid_capstyle='round', zorder=3)
        
        # Midpoint label: density value (POIs/100m)
        mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
        label = f"{e['poi_density_per_100m']:.1f}"
        ax.text(mx, my, label,
                ha='center', va='center', fontsize=7,
                color='white', fontweight='bold', zorder=4,
                bbox=dict(boxstyle='circle,pad=0.18',
                          facecolor=color, edgecolor='none', alpha=0.9))
    
    # Draw anchor nodes (building footprints filled in dark gray)
    for a in anchor_polys:
        poly = a['poly']
        bx, by = poly.exterior.coords.xy
        ax.fill(bx, by, facecolor='#2a2a2a', edgecolor='#000',
                 linewidth=0.8, zorder=10)
        c = poly.centroid
        ax.annotate(
            a['name'], xy=(c.x, c.y), xytext=(0, 0),
            textcoords='offset points',
            ha='center', va='center', fontsize=7, fontweight='bold',
            color='white', zorder=11,
        )
    
    # Legend
    legend_handles = [
        Patch(facecolor=PROGRAM_COLORS[c], edgecolor='none',
              label=c.replace('_', ' '))
        for c in ROSSETTI_ORDER
    ]
    ax.legend(handles=legend_handles, loc='lower left', fontsize=8,
              framealpha=0.95, ncol=2, title='dominant program in corridor',
              title_fontsize=8.5)
    
    bbox = canvas_polygon.bounds
    margin = 0.02 * max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    ax.set_xlim(bbox[0] - margin, bbox[2] + margin)
    ax.set_ylim(bbox[1] - margin, bbox[3] + margin)
    ax.set_aspect('equal')
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(
        f"{sad_name} — anchor connectivity graph\n"
        f"edges weighted by POI density (per 100m of corridor); "
        f"corridor width {int(corridor_width)}m, min {min_pois} POIs total",
        fontsize=11, pad=12, loc='left',
    )
    
    fig.savefig(out_png, dpi=180, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)
    
    return edges, anchor_polys


def main():
    parser = argparse.ArgumentParser(
        description="Anchor connectivity graph")
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--derived', type=Path, required=True)
    parser.add_argument('--cluster', type=int, default=0)
    parser.add_argument('--corridor-width', type=float, default=30.0,
                        help='Half-width of corridor between anchors in meters')
    parser.add_argument('--min-pois', type=int, default=3,
                        help='Min POIs in corridor for edge to be drawn')
    parser.add_argument('--exclude-halo', type=float, default=25.0,
                        help='Halo around each anchor to exclude from '
                             'corridor (default 25m; set 0 to disable). '
                             'Avoids double-counting POIs in anchor\'s '
                             'own relational field.')
    args = parser.parse_args()
    
    enriched_path = args.derived / 'buildings_enriched.gpkg'
    buildings = gpd.read_file(enriched_path, layer='buildings')
    places = gpd.read_file(args.source / 'rod_places.geojson')
    extent_gdf = gpd.read_file(args.source / 'image_extent.geojson')
    
    sad_boundary = None
    sad_boundary_path = args.source / 'sad_boundary.geojson'
    if sad_boundary_path.exists():
        sad_gdf = gpd.read_file(sad_boundary_path)
        try:
            sad_boundary = sad_gdf.union_all()
        except AttributeError:
            sad_boundary = sad_gdf.unary_union
    
    metric_crs = buildings.estimate_utm_crs()
    buildings = buildings.to_crs(metric_crs)
    places = places.to_crs(metric_crs)
    extent_gdf = extent_gdf.to_crs(metric_crs)
    canvas_polygon = to_polygon(extent_gdf.geometry.iloc[0])
    if sad_boundary is not None:
        sad_boundary = gpd.GeoSeries([sad_boundary], crs=sad_gdf.crs)\
                          .to_crs(metric_crs).iloc[0]
    
    if args.cluster == 0:
        args.cluster = detect_anchor_cluster(buildings)
        print(f"  auto-detected anchor cluster: {args.cluster} "
              f"(largest median building area)")
    
    anchors = buildings[buildings['cluster_id'] == args.cluster]
    if len(anchors) == 0:
        raise SystemExit(f"no buildings in cluster {args.cluster}")
    print(f"  {len(anchors)} anchors -> {len(anchors)*(len(anchors)-1)//2} possible corridors")
    
    profile_path = args.derived / 'district_profile.json'
    sad_name = args.derived.name
    if profile_path.exists():
        sad_name = json.loads(profile_path.read_text()).get('sad_name', sad_name)
    
    out_png = args.derived / 'anchor_connectivity.png'
    out_svg = args.derived / 'anchor_connectivity.svg'
    edges, anchor_polys = render_connectivity(
        anchors, places, sad_boundary, canvas_polygon, sad_name,
        args.corridor_width, args.min_pois, args.exclude_halo,
        out_png, out_svg,
    )
    
    print(f"  drew {len(edges)} edges (passed min-pois={args.min_pois} threshold)")
    
    out_json = args.derived / 'anchor_connectivity.json'
    out_json.write_text(json.dumps({
        'sad_name': sad_name,
        'cluster': args.cluster,
        'corridor_width_m': args.corridor_width,
        'exclude_halo_m': args.exclude_halo,
        'min_pois_threshold': args.min_pois,
        'metric': 'poi_density_per_100m (POI count normalized by corridor length)',
        'n_anchors': len(anchors),
        'n_edges_drawn': len(edges),
        'anchors': [{'id': a['id'], 'name': a['name']} for a in anchor_polys],
        'edges': edges,
    }, indent=2))
    
    print(f"[OK] wrote {out_png.name}")
    print(f"[OK] wrote {out_svg.name}")
    print(f"[OK] wrote {out_json.name}")


if __name__ == '__main__':
    main()
