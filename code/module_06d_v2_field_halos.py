"""
module_06d_v2_field_halos.py

Plan view of the SAD showing each cluster-2 (anchor-scale) building as a
filled footprint, surrounded by concentric "halo" bands colored by the
DOMINANT PROGRAM in each distance ring. Overlapping halos visualize
where anchor relational fields intersect.

Distance rings: 0-50m, 50-100m, 100-150m, 150-200m.

INPUT
  source/<sad>/sad_boundary.geojson    (overlay outline)
  source/<sad>/image_extent.geojson    (canvas bounds)
  source/<sad>/rod_places.geojson      (POIs with rossetti_category)
  derived/<sad>/buildings_enriched.gpkg  (anchors = cluster_id 2)

OUTPUT
  derived/<sad>/anchor_field_halos.{png,svg}

USAGE
  python module_06d_v2_field_halos.py --source <dir> --derived <dir>
       [--cluster N] [--ring-widths 50,100,150,200]
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
from matplotlib.patches import Polygon as MplPolygon, Patch
from shapely.geometry import Point, MultiPolygon
from shapely.affinity import scale as shapely_scale


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


def buffer_polygon_meters(poly, distance: float):
    """Buffer a polygon by `distance` meters."""
    return poly.buffer(distance, resolution=16)


def dominant_program_in_ring(poly, ring_inner_poly, places: gpd.GeoDataFrame):
    """
    Find the dominant Rossetti category among POIs inside `poly` but
    outside `ring_inner_poly` (i.e., in the ring-shaped band).
    Returns (category, count) or (None, 0).
    """
    if ring_inner_poly is None:
        ring = poly
    else:
        ring = poly.difference(ring_inner_poly)
    if ring.is_empty:
        return None, 0
    
    in_ring = places[places.geometry.within(ring)]
    if len(in_ring) == 0:
        return None, 0
    
    counts = Counter(in_ring['rossetti_category'].dropna().tolist())
    if not counts:
        return None, 0
    dominant, count = counts.most_common(1)[0]
    return dominant, count


def render_field_halos(
    anchors: gpd.GeoDataFrame,
    places: gpd.GeoDataFrame,
    sad_boundary,
    canvas_polygon,
    sad_name: str,
    ring_widths: list[float],
    out_png: Path,
    out_svg: Path,
):
    fig, ax = plt.subplots(figsize=(11, 11))
    
    # Canvas outline
    cx, cy = canvas_polygon.exterior.coords.xy
    ax.fill(cx, cy, facecolor='#fafafa', edgecolor='#ccc',
            linewidth=0.5, zorder=0)
    
    # SAD boundary overlay
    if sad_boundary is not None:
        sad_poly = to_polygon(sad_boundary)
        sx, sy = sad_poly.exterior.coords.xy
        ax.plot(sx, sy, color='#444', linewidth=2, linestyle='-',
                zorder=10, label='SAD boundary')
    
    # For each anchor, draw the halos OUTWARD-IN so inner halos overlay
    # outer halos (so the building footprint stays on top)
    anchor_summaries = []
    for idx, row in anchors.iterrows():
        poly = to_polygon(row.geometry)
        anchor_id = row.get('building_id', f'anchor_{idx}')
        anchor_name = row.get('name', '')
        if anchor_name is None or (isinstance(anchor_name, float) and np.isnan(anchor_name)):
            anchor_name = ''
        anchor_name = str(anchor_name).strip()
        if not anchor_name or anchor_name == 'nan':
            anchor_name = anchor_id
        
        ring_buffers = [None]  # innermost reference (the building footprint itself)
        for w in ring_widths:
            ring_buffers.append(buffer_polygon_meters(poly, w))
        
        # Draw each ring from outermost to innermost
        ring_summary = []
        for i in range(len(ring_widths), 0, -1):
            outer = ring_buffers[i]
            inner = ring_buffers[i - 1] if i > 0 else None
            dom, count = dominant_program_in_ring(outer, inner, places)
            ring_distance = ring_widths[i - 1]
            
            if dom is None:
                # Empty ring - render with faint gray
                color = '#dddddd'
                alpha = 0.10
            else:
                color = PROGRAM_COLORS.get(dom, '#888')
                # Inner rings (closer to building) more opaque
                alpha = 0.5 - 0.07 * (i - 1)
                alpha = max(0.15, alpha)
            
            # Draw the ring (annulus)
            ring_shape = outer if inner is None else outer.difference(inner)
            if hasattr(ring_shape, 'geoms'):
                geoms = list(ring_shape.geoms)
            elif ring_shape.is_empty:
                continue
            else:
                geoms = [ring_shape]
            
            for g in geoms:
                if not hasattr(g, 'exterior'):
                    continue
                # Force 2D coords (some sources have z, MplPolygon wants x,y)
                ext = [(c[0], c[1]) for c in g.exterior.coords]
                patch = MplPolygon(ext, closed=True, facecolor=color,
                                   edgecolor='none', alpha=alpha,
                                   zorder=2 + (len(ring_widths) - i))
                ax.add_patch(patch)
                # Draw interior holes
                for interior in g.interiors:
                    hole_coords = [(c[0], c[1]) for c in interior.coords]
                    hole = MplPolygon(hole_coords, closed=True,
                                      facecolor='#fafafa',
                                      edgecolor='none',
                                      zorder=2 + (len(ring_widths) - i) + 0.5)
                    ax.add_patch(hole)
            
            ring_summary.append({
                'distance_m': ring_distance,
                'dominant_program': dom,
                'poi_count': count,
            })
        
        # Anchor building itself on top
        bx, by = poly.exterior.coords.xy
        ax.fill(bx, by, facecolor='#2a2a2a', edgecolor='#000',
                linewidth=0.8, zorder=20)
        
        # Centroid label
        c = poly.centroid
        ax.annotate(
            anchor_name if anchor_name else anchor_id,
            xy=(c.x, c.y), xytext=(0, 0),
            textcoords='offset points',
            ha='center', va='center', fontsize=7, fontweight='bold',
            color='white', zorder=21,
        )
        
        anchor_summaries.append({
            'building_id': anchor_id,
            'name': anchor_name,
            'rings': ring_summary,
        })
    
    # Legend for program colors
    legend_handles = [
        Patch(facecolor=PROGRAM_COLORS[c], edgecolor='#222',
              label=c.replace('_', ' '))
        for c in ROSSETTI_ORDER
    ]
    if sad_boundary is not None:
        from matplotlib.lines import Line2D
        legend_handles.append(Line2D([], [], color='#444', linewidth=2,
                                       label='SAD boundary'))
    ax.legend(handles=legend_handles, loc='lower left', fontsize=8,
              framealpha=0.95, ncol=2, title='dominant program in ring',
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
        f"{sad_name} — anchor relational field map\n"
        f"halos: 0-{int(ring_widths[0])}m / {int(ring_widths[0])}-{int(ring_widths[1])}m / "
        f"...colored by dominant program in band",
        fontsize=11, pad=12, loc='left',
    )
    
    fig.savefig(out_png, dpi=180, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)
    
    return anchor_summaries


def main():
    parser = argparse.ArgumentParser(
        description="Anchor relational field map with halos")
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--derived', type=Path, required=True)
    parser.add_argument('--cluster', type=int, default=0,
                        help='Cluster ID to treat as anchors (default 2)')
    parser.add_argument('--ring-widths', type=str, default='50,100,150,200',
                        help='Comma-separated ring outer distances in meters')
    args = parser.parse_args()
    
    ring_widths = [float(x) for x in args.ring_widths.split(',')]
    
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
    
    sad_boundary_path = args.source / 'sad_boundary.geojson'
    sad_boundary = None
    if sad_boundary_path.exists():
        sad_gdf = gpd.read_file(sad_boundary_path)
        try:
            sad_boundary = sad_gdf.union_all()
        except AttributeError:
            sad_boundary = sad_gdf.unary_union
    
    # Reproject everything to metric
    metric_crs = buildings.estimate_utm_crs()
    buildings = buildings.to_crs(metric_crs)
    places = places.to_crs(metric_crs)
    extent_gdf = extent_gdf.to_crs(metric_crs)
    canvas_polygon = to_polygon(extent_gdf.geometry.iloc[0])
    if sad_boundary is not None:
        sad_boundary = gpd.GeoSeries([sad_boundary], crs=sad_gdf.crs)\
                          .to_crs(metric_crs).iloc[0]
    
    anchors_df = buildings  # reference for auto-detection
    if args.cluster == 0:
        args.cluster = detect_anchor_cluster(anchors_df)
        print(f"  auto-detected anchor cluster: {args.cluster} "
              f"(largest median building area)")
    
    anchors = buildings[buildings['cluster_id'] == args.cluster]
    if len(anchors) == 0:
        raise SystemExit(f"no buildings in cluster {args.cluster}")
    print(f"  {len(anchors)} anchors in cluster {args.cluster}")
    
    profile_path = args.derived / 'district_profile.json'
    sad_name = args.derived.name
    if profile_path.exists():
        sad_name = json.loads(profile_path.read_text()).get('sad_name', sad_name)
    
    out_png = args.derived / 'anchor_field_halos.png'
    out_svg = args.derived / 'anchor_field_halos.svg'
    summaries = render_field_halos(
        anchors, places, sad_boundary, canvas_polygon, sad_name,
        ring_widths, out_png, out_svg,
    )
    
    out_json = args.derived / 'anchor_field_halos.json'
    out_json.write_text(json.dumps({
        'sad_name': sad_name, 'cluster': args.cluster,
        'ring_widths_m': ring_widths,
        'anchors': summaries,
    }, indent=2))
    
    print(f"[OK] wrote {out_png.name}")
    print(f"[OK] wrote {out_svg.name}")
    print(f"[OK] wrote {out_json.name}")


if __name__ == '__main__':
    main()
