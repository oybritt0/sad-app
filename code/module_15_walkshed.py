"""
module_15_walkshed.py

Pedestrian isochrone (walkshed) computation from the SAD centroid.
Renders smooth polycurve outlines per time interval echoing KPF Seven
Dials page 23.

REWRITE NOTES (v3)
    - Walking speed in mph (default 3.0 mph, US planning convention
      per FHWA/AASHTO; international convention 5 km/h ~ 3.1 mph so
      this matches established practice closely)
    - All distances displayed in feet
    - Scale bar + north arrow added to PNG and SVG chrome

METHODOLOGY
    Walking speed: 3 mph default (US planning convention, FHWA).
        - 5 min  = 1,320 ft (~402 m)
        - 10 min = 2,640 ft (~805 m)
        - 15 min = 3,960 ft (~1,207 m)

    Network filter: OSM `highway` types — footway, cycleway, path,
        pedestrian, steps, residential, service, living_street,
        unclassified, tertiary, secondary. Excludes motorway, trunk.

    Graph: LineString broken into vertex-pair edges, weighted by
        Euclidean segment length in local UTM. Endpoints snapped to
        a 0.5m grid (~1.6 ft) so shared intersections merge.

    Shortest path: networkx single_source_dijkstra_path_length.

    Polygon: shapely.concave_hull(MultiPoint(reachable_nodes),
        ratio=0.25) with erosion-dilation smoothing (buffer +25m
        then -15m for rounded corners).

INPUTS
    source/sad_boundary.geojson      framing
    source/image_extent.geojson      canvas bbox
    source/highways.geojson          street network
    source/buildings.geojson         context (optional)

OUTPUTS
    derived/walkshed/walksheds.geojson
    derived/walkshed/walkshed_visualization.png + .svg
    derived/walkshed/walkshed_summary.json
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
import networkx as nx
from shapely.geometry import LineString, MultiLineString, Point, MultiPoint
from shapely.ops import unary_union
from shapely import concave_hull

sys.path.insert(0, str(Path(__file__).parent))
import canvas_render as cr


# ─── Config ──────────────────────────────────────────────────────────────────

WALKABLE_HIGHWAYS = {
    'footway', 'cycleway', 'path', 'pedestrian', 'steps',
    'residential', 'service', 'living_street', 'unclassified',
    'tertiary', 'secondary', 'tertiary_link', 'secondary_link',
    'primary', 'primary_link',
}
EXCLUDE_HIGHWAYS = {
    'motorway', 'trunk', 'motorway_link', 'trunk_link',
}
DEFAULT_WALKING_SPEED_MPH = 3.0     # US planning convention (FHWA/AASHTO)
DEFAULT_MINUTES = [5, 10, 15]
SNAP_GRID_M = 0.5

CONCAVE_RATIO = 0.7
SMOOTHING_M = 60.0

# Per-walkshed styling (closer rings more saturated)
WALKSHED_STYLE = {
    5:  {'fill': '#4dd0e1', 'fill_opacity': 0.25,
         'stroke': '#80deea', 'stroke_opacity': 0.95,
         'stroke_width': 2.0, 'stroke_dasharray': '2 4'},
    10: {'fill': '#4dd0e1', 'fill_opacity': 0.12,
         'stroke': '#9adbe2', 'stroke_opacity': 0.75,
         'stroke_width': 1.6, 'stroke_dasharray': '2 6'},
    15: {'fill': '#4dd0e1', 'fill_opacity': 0.06,
         'stroke': '#b3e3e8', 'stroke_opacity': 0.55,
         'stroke_width': 1.3, 'stroke_dasharray': '2 8'},
}
def walkshed_style(minutes):
    if minutes in WALKSHED_STYLE:
        return WALKSHED_STYLE[minutes]
    base_alpha = max(0.05, 0.3 - 0.015 * minutes)
    return {'fill': '#4dd0e1', 'fill_opacity': base_alpha,
            'stroke': '#9adbe2', 'stroke_opacity': base_alpha * 3,
            'stroke_width': max(1.0, 2.5 - 0.08 * minutes),
            'stroke_dasharray': f'2 {min(10, 4 + minutes//4)}'}

ORIGIN_COLOR = '#ffffff'


# ─── Network ────────────────────────────────────────────────────────────────

def snap(x, y, grid=SNAP_GRID_M):
    return (round(x / grid) * grid, round(y / grid) * grid)


def filter_walkable(highways_gdf):
    col = None
    for c in ['highway', 'type', 'highway_type']:
        if c in highways_gdf.columns:
            col = c
            break
    if col is None:
        return highways_gdf
    h = highways_gdf[col].astype(str).str.lower()
    keep = h.isin(WALKABLE_HIGHWAYS) & ~h.isin(EXCLUDE_HIGHWAYS)
    return highways_gdf[keep].copy()


def build_graph(highways_m):
    G = nx.Graph()
    for geom in highways_m.geometry:
        if geom is None or geom.is_empty:
            continue
        lines = [geom] if geom.geom_type == 'LineString' \
                else list(geom.geoms) if geom.geom_type == 'MultiLineString' \
                else []
        for line in lines:
            coords = list(line.coords)
            for i in range(len(coords) - 1):
                a = snap(coords[i][0], coords[i][1])
                b = snap(coords[i+1][0], coords[i+1][1])
                if a == b:
                    continue
                dist = math.hypot(b[0] - a[0], b[1] - a[1])
                if G.has_edge(a, b):
                    if dist < G[a][b]['weight']:
                        G[a][b]['weight'] = dist
                else:
                    G.add_edge(a, b, weight=dist)
    return G


def find_nearest_node(G, point):
    nodes = np.array(list(G.nodes))
    if len(nodes) == 0:
        raise ValueError("Graph is empty -- no walkable streets in SAD")
    d2 = (nodes[:, 0] - point.x) ** 2 + (nodes[:, 1] - point.y) ** 2
    idx = int(np.argmin(d2))
    return tuple(nodes[idx]), math.sqrt(d2[idx])


def compute_reachable(G, origin_node, budget_m):
    return nx.single_source_dijkstra_path_length(
        G, origin_node, cutoff=budget_m, weight='weight')


def build_walkshed_polygon(reachable, origin_node,
                           concave_ratio=CONCAVE_RATIO,
                           smoothing_m=SMOOTHING_M):
    nodes = list(reachable.keys())
    nodes.append(tuple(origin_node))
    if len(nodes) < 3:
        return Point(origin_node).buffer(smoothing_m)
    points = MultiPoint(nodes)
    try:
        hull = concave_hull(points, ratio=concave_ratio)
    except Exception:
        hull = points.convex_hull
    if smoothing_m > 0:
        smooth = hull.buffer(smoothing_m, resolution=8).buffer(
            -smoothing_m * 0.35, resolution=8)
        if smooth.is_empty:
            smooth = hull.buffer(smoothing_m, resolution=8)
        return smooth
    return hull


# ─── SVG renderer ────────────────────────────────────────────────────────────

def render_svg(walksheds, ctx, origin_m, out_svg, sad_id, walking_speed_mph):
    tx, svg_w, svg_h, scale, plot_area = cr.make_transform(
        ctx['canvas_bbox'], svg_width=1100, margin=40, chrome_height=170)

    with open(out_svg, 'w', encoding='utf-8') as fh:
        cr.write_svg_open(fh, svg_w, svg_h,
                          title=f'Walkshed - {sad_id}')

        cr.write_context_layers(fh, ctx, tx)

        # Walksheds outer-first
        for w in sorted(walksheds, key=lambda x: -x['minutes']):
            s = walkshed_style(w['minutes'])
            layer_id = f'walkshed_{w["minutes"]}min'
            d = cr.polygon_to_path(w['polygon'], tx)
            if not d:
                fh.write(f'  <g id="{layer_id}"></g>\n')
                continue
            fh.write(f'  <g id="{layer_id}">\n')
            fh.write(f'    <path d="{d}" fill="{s["fill"]}" '
                     f'fill-opacity="{s["fill_opacity"]}" '
                     f'stroke="none"/>\n')
            fh.write(f'    <path d="{d}" fill="none" '
                     f'stroke="{s["stroke"]}" '
                     f'stroke-opacity="{s["stroke_opacity"]}" '
                     f'stroke-width="{s["stroke_width"]}" '
                     f'stroke-dasharray="{s["stroke_dasharray"]}" '
                     f'stroke-linecap="round" '
                     f'stroke-linejoin="round"/>\n')
            fh.write('  </g>\n')

        cr.write_sad_boundary(fh, ctx['sad_geom_m'], tx)

        # Origin marker
        ox, oy = tx(origin_m.x, origin_m.y)
        fh.write('  <g id="origin_marker" '
                 f'stroke="{ORIGIN_COLOR}" stroke-width="2.2" '
                 'stroke-linecap="round">\n')
        fh.write(f'    <line x1="{ox-8}" y1="{oy-8}" '
                 f'x2="{ox+8}" y2="{oy+8}"/>\n')
        fh.write(f'    <line x1="{ox-8}" y1="{oy+8}" '
                 f'x2="{ox+8}" y2="{oy-8}"/>\n')
        fh.write('  </g>\n')

        # Chrome: title
        mins_str = ' \u00b7 '.join(f"{w['minutes']} min"
                                    for w in sorted(walksheds, key=lambda x: x['minutes']))
        cr.write_title(fh, 'Pedestrian Walkshed',
                       f"{mins_str} \u00b7 {walking_speed_mph:.1f} mph walking speed",
                       plot_area)

        # Chrome: legend
        legend_left = plot_area['left'] + 8
        legend_top = plot_area['bottom'] + 28
        fh.write('  <g id="chrome_legend" font-family="sans-serif" '
                 f'font-size="10" fill="{cr.TEXT_DIM}">\n')
        for i, w in enumerate(sorted(walksheds, key=lambda x: x['minutes'])):
            s = walkshed_style(w['minutes'])
            y = legend_top + i * 18
            fh.write(f'    <circle cx="{legend_left + 10}" cy="{y - 3}" r="8" '
                     f'fill="{s["fill"]}" '
                     f'fill-opacity="{s["fill_opacity"] * 2.5}" '
                     f'stroke="{s["stroke"]}" '
                     f'stroke-opacity="{s["stroke_opacity"]}" '
                     f'stroke-width="{s["stroke_width"]}" '
                     f'stroke-dasharray="{s["stroke_dasharray"]}"/>\n')
            budget_ft = cr.m_to_ft(w['budget_m'])
            fh.write(f'    <text x="{legend_left + 26}" y="{y}">'
                     f"{w['minutes']}-min walk ({budget_ft:,.0f} ft)"
                     '</text>\n')
        fh.write('  </g>\n')

        # Chrome: scale bar + north arrow
        cr.write_scale_bar(fh, plot_area, scale)
        cr.write_north_arrow(fh, plot_area)

        cr.write_svg_close(fh)


# ─── PNG renderer ────────────────────────────────────────────────────────────

def render_png(walksheds, ctx, origin_m, out_png, sad_id, walking_speed_mph):
    minx, miny, maxx, maxy = ctx['canvas_bbox']
    aspect = (maxy - miny) / max(maxx - minx, 1e-9)
    fig_w = 11
    fig_h = fig_w * aspect + 1.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor(cr.BG_COLOR)
    ax.set_facecolor(cr.BG_COLOR)

    if ctx.get('streets_outside') is not None and not ctx['streets_outside'].empty:
        ctx['streets_outside'].plot(ax=ax, color=cr.STREET_OUTSIDE,
                                     linewidth=cr.STREET_OUTSIDE_WIDTH, zorder=1)
    if ctx.get('buildings_outside') is not None and not ctx['buildings_outside'].empty:
        ctx['buildings_outside'].plot(ax=ax, color=cr.BUILDING_OUTSIDE,
                                       edgecolor='none', linewidth=0, zorder=2)
    if ctx.get('streets_inside') is not None and not ctx['streets_inside'].empty:
        ctx['streets_inside'].plot(ax=ax, color=cr.STREET_INSIDE,
                                    linewidth=cr.STREET_INSIDE_WIDTH, zorder=3)
    if ctx.get('buildings_inside') is not None and not ctx['buildings_inside'].empty:
        ctx['buildings_inside'].plot(ax=ax, color=cr.BUILDING_INSIDE,
                                      edgecolor='none', linewidth=0, zorder=4)

    for w in sorted(walksheds, key=lambda x: -x['minutes']):
        s = walkshed_style(w['minutes'])
        poly_series = gpd.GeoSeries([w['polygon']])
        poly_series.plot(ax=ax, facecolor=s['fill'],
                         alpha=s['fill_opacity'],
                         edgecolor='none', zorder=5)
        dash = [float(x) for x in s['stroke_dasharray'].split()]
        poly_series.boundary.plot(
            ax=ax, color=s['stroke'], alpha=s['stroke_opacity'],
            linewidth=s['stroke_width'], linestyle=(0, dash),
            zorder=6)

    gpd.GeoSeries([ctx['sad_geom_m']]).boundary.plot(
        ax=ax, color=cr.BOUNDARY_COLOR, linewidth=cr.BOUNDARY_WIDTH,
        linestyle=(0, (10, 6)), zorder=8)

    ax.scatter([origin_m.x], [origin_m.y], s=80,
               c=ORIGIN_COLOR, marker='x', linewidths=2.2, zorder=10)

    cr.draw_scale_bar_mpl(ax, ctx['canvas_bbox'])
    cr.draw_north_arrow_mpl(ax, ctx['canvas_bbox'])

    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)

    ax.text(0.02, 0.98, 'Pedestrian Walkshed', transform=ax.transAxes,
            color=cr.TEXT_COLOR, fontsize=15, fontweight='bold',
            va='top', ha='left', family='sans-serif')
    mins_str = ' \u00b7 '.join(f"{w['minutes']} min"
                                for w in sorted(walksheds, key=lambda x: x['minutes']))
    ax.text(0.02, 0.94,
            f"{mins_str} \u00b7 {walking_speed_mph:.1f} mph walking speed",
            transform=ax.transAxes, color=cr.TEXT_DIM, fontsize=10,
            va='top', ha='left', family='sans-serif')

    # Legend in axes coords
    legend_y = 0.14
    for w in sorted(walksheds, key=lambda x: x['minutes']):
        s = walkshed_style(w['minutes'])
        dash = [float(x) for x in s['stroke_dasharray'].split()]
        ax.scatter([0.04], [legend_y], s=200, c=s['fill'],
                   alpha=min(s['fill_opacity'] * 2.5, 0.8),
                   edgecolors=s['stroke'], linewidths=s['stroke_width'],
                   transform=ax.transAxes, zorder=20, clip_on=False)
        budget_ft = cr.m_to_ft(w['budget_m'])
        ax.text(0.07, legend_y,
                f"{w['minutes']}-min walk ({budget_ft:,.0f} ft)",
                transform=ax.transAxes, color=cr.TEXT_DIM,
                fontsize=10, va='center', ha='left')
        legend_y -= 0.04

    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore',
            message='This figure includes Axes that are not compatible')
        fig.tight_layout(pad=1.5)
        fig.savefig(out_png, dpi=150, facecolor=cr.BG_COLOR,
                    bbox_inches='tight', pad_inches=0.15)
    plt.close(fig)


# ─── Driver ──────────────────────────────────────────────────────────────────

def compute(source_dir, derived_dir, minutes,
            origin_lat=None, origin_lng=None,
            walking_speed_mph=DEFAULT_WALKING_SPEED_MPH,
            concave_ratio=CONCAVE_RATIO,
            smoothing_m=SMOOTHING_M):
    sad_id = source_dir.parent.name
    print(f"Walkshed for {sad_id}...")

    print("  loading canvas context...")
    ctx = cr.load_canvas_context(source_dir)
    metric_crs = ctx['metric_crs']
    sad_geom_m = ctx['sad_geom_m']

    # Convert mph to meters/minute for internal computation
    # 1 mph = 5280 ft/hour = 88 ft/min = 26.8224 m/min
    speed_m_per_min = walking_speed_mph * 5280 * cr.M_PER_FT / 60.0
    print(f"  walking speed: {walking_speed_mph:.1f} mph "
          f"({speed_m_per_min:.1f} m/min, {walking_speed_mph * 88:.0f} ft/min)")

    highways_path = source_dir / 'highways.geojson'
    if not highways_path.exists():
        print(f"  ERROR: {highways_path} not found -- cannot compute walkshed")
        return
    highways = gpd.read_file(highways_path)
    highways_m = highways.to_crs(metric_crs)
    walkable_m = filter_walkable(highways_m)
    print(f"  {len(walkable_m)} walkable street segments "
          f"({len(highways_m) - len(walkable_m)} non-walkable filtered out)")
    if walkable_m.empty:
        print("  ERROR: no walkable streets")
        return

    print("  building network graph...")
    G = build_graph(walkable_m)
    print(f"  graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    if origin_lat is not None and origin_lng is not None:
        origin_m = (gpd.GeoSeries([Point(origin_lng, origin_lat)], crs='EPSG:4326')
                       .to_crs(metric_crs).iloc[0])
        origin_source = f'user-specified ({origin_lat:.6f}, {origin_lng:.6f})'
    else:
        origin_m = sad_geom_m.centroid
        origin_source = 'SAD centroid'
    print(f"  origin: {origin_source} -> metric ({origin_m.x:.1f}, {origin_m.y:.1f})")

    origin_node, snap_dist = find_nearest_node(G, origin_m)
    print(f"  origin snapped to network node {snap_dist:.1f} m "
          f"({cr.m_to_ft(snap_dist):.0f} ft) away")

    walksheds = []
    for m in sorted(minutes):
        budget_m = speed_m_per_min * m
        reachable = compute_reachable(G, origin_node, budget_m)
        polygon = build_walkshed_polygon(reachable, origin_node,
                                         concave_ratio=concave_ratio,
                                         smoothing_m=smoothing_m)
        walksheds.append({
            'minutes': m, 'budget_m': budget_m,
            'budget_ft': cr.m_to_ft(budget_m),
            'reachable_nodes': len(reachable),
            'polygon': polygon, 'area_m2': polygon.area,
        })
        print(f"  {m}-min walkshed: {len(reachable)} nodes, "
              f"budget {cr.m_to_ft(budget_m):,.0f} ft, "
              f"area {polygon.area * cr.FT_PER_M ** 2 / 43560:.2f} acres")

    out_dir = derived_dir / 'walkshed'
    out_dir.mkdir(parents=True, exist_ok=True)

    wshed_records = [{
        'minutes': w['minutes'],
        'budget_ft': w['budget_ft'],
        'budget_m': w['budget_m'],
        'reachable_nodes': w['reachable_nodes'],
        'area_m2': w['area_m2'],
        'area_acres': w['area_m2'] * cr.FT_PER_M ** 2 / 43560,
        'geometry': w['polygon'],
    } for w in walksheds]
    wshed_gdf = gpd.GeoDataFrame(wshed_records, geometry='geometry',
                                  crs=metric_crs).to_crs('EPSG:4326')
    wshed_gdf.to_file(out_dir / 'walksheds.geojson', driver='GeoJSON')

    render_svg(walksheds, ctx, origin_m,
               out_dir / 'walkshed_visualization.svg', sad_id,
               walking_speed_mph)
    render_png(walksheds, ctx, origin_m,
               out_dir / 'walkshed_visualization.png', sad_id,
               walking_speed_mph)
    print(f"  wrote walkshed_visualization.png/.svg")

    canvas_bounds = ctx['canvas_geom_m'].bounds
    canvas_extent_m = max(canvas_bounds[2] - canvas_bounds[0],
                          canvas_bounds[3] - canvas_bounds[1])
    max_budget = max(w['budget_m'] for w in walksheds) if walksheds else 0
    extent_warning = (max_budget * 2) > canvas_extent_m

    summary = {
        'sad_id': sad_id,
        'methodology': {
            'walking_speed_mph': walking_speed_mph,
            'walking_speed_ft_per_min': walking_speed_mph * 88,
            'walking_speed_m_per_min': speed_m_per_min,
            'snap_grid_m': SNAP_GRID_M,
            'snap_grid_ft': cr.m_to_ft(SNAP_GRID_M),
            'shortest_path_algorithm': 'networkx single_source_dijkstra',
            'polygon_method': ('shapely.concave_hull(MultiPoint(reachable), '
                               f'ratio={concave_ratio}) then '
                               f'buffer(+{smoothing_m}).buffer(-{smoothing_m * 0.6}) '
                               'smoothing'),
            'concave_ratio': concave_ratio,
            'smoothing_m': smoothing_m,
            'smoothing_ft': cr.m_to_ft(smoothing_m),
            'walkable_highway_types': sorted(WALKABLE_HIGHWAYS),
            'excluded_highway_types': sorted(EXCLUDE_HIGHWAYS),
        },
        'origin': {
            'source': origin_source,
            'snap_distance_m': float(snap_dist),
            'snap_distance_ft': cr.m_to_ft(float(snap_dist)),
        },
        'metric_crs': metric_crs,
        'graph': {
            'nodes': G.number_of_nodes(),
            'edges': G.number_of_edges(),
            'walkable_segments_in_geojson': int(len(walkable_m)),
        },
        'walksheds': [
            {
                'minutes': w['minutes'],
                'budget_ft': w['budget_ft'],
                'budget_m': w['budget_m'],
                'reachable_nodes': w['reachable_nodes'],
                'area_acres': w['area_m2'] * cr.FT_PER_M ** 2 / 43560,
                'area_m2': w['area_m2'],
            } for w in walksheds
        ],
        'canvas_extent_ft': cr.m_to_ft(canvas_extent_m),
        'canvas_extent_m': canvas_extent_m,
        'canvas_clip_warning': (
            f'Longest walkshed budget ({cr.m_to_ft(max_budget):,.0f} ft) '
            f'may exceed half the canvas extent '
            f'({cr.m_to_ft(canvas_extent_m):,.0f} ft). The walkshed '
            f'polygon may be clipped at the canvas edge.'
            if extent_warning else None
        ),
    }
    (out_dir / 'walkshed_summary.json').write_text(json.dumps(summary, indent=2))
    print(f"  wrote walkshed_summary.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--derived', type=Path, required=True)
    ap.add_argument('--minutes', type=int, nargs='+',
                    default=DEFAULT_MINUTES)
    ap.add_argument('--origin-lat', type=float, default=None)
    ap.add_argument('--origin-lng', type=float, default=None)
    ap.add_argument('--walking-speed-mph', type=float,
                    default=DEFAULT_WALKING_SPEED_MPH,
                    help=f'Walking speed in mph '
                         f'(default {DEFAULT_WALKING_SPEED_MPH}, '
                         'US planning standard per FHWA)')
    ap.add_argument('--concave-ratio', type=float, default=CONCAVE_RATIO)
    ap.add_argument('--smoothing-m', type=float, default=SMOOTHING_M)
    args = ap.parse_args()
    compute(args.source.resolve(), args.derived.resolve(),
            minutes=args.minutes,
            origin_lat=args.origin_lat, origin_lng=args.origin_lng,
            walking_speed_mph=args.walking_speed_mph,
            concave_ratio=args.concave_ratio,
            smoothing_m=args.smoothing_m)


if __name__ == '__main__':
    main()
