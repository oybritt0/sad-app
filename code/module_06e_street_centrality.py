#!/usr/bin/env python3
"""
module_06e_street_centrality.py

Compute street betweenness centrality (Freeman 1977) on the SAD's road
network. Each street segment receives a centrality score: a higher score
means more shortest paths between pairs of nodes pass through that
segment — a topological measure of "structural importance for through-
movement."

This is the measure popularised in urban analysis by Hillier's space
syntax group and packaged for designers via Decoding Spaces (Bauhaus-
Universität Weimar, the toolbox the KPF deck references). It is a
network-shape property: it does not reflect actual traffic counts or
land use; it answers "if movement followed shortest paths between every
pair of points in the network, which segments would carry the most of
it?"

NETWORK CONSTRUCTION
    Input: source/highways.geojson (line geometries).
    Filtered to the vehicular subset (motorway through service,
    living_street, unclassified) — footways, cycleways, paths, steps are
    excluded.

    By default the network is *also* clipped to the SAD boundary
    (--no-clip-analysis to opt out): only road segments that intersect
    the SAD polygon are included in the graph. This focuses the
    centrality on the SAD's internal structure and avoids the
    fragmentation we'd otherwise see at the canvas-edge dead-ends. The
    cost is that streets which are merely local within the SAD but
    globally important regionally won't show as such — for this kind of
    "within-district structure" reading, that's the right trade.

    Each LineString is one edge. Endpoints are snapped at 0.5 m tolerance
    to bridge minor floating-point gaps. Edge weight is segment length
    in meters; centrality is computed weighted.

CENTRALITY VARIANTS
    centrality_global   Edge betweenness on the (SAD-clipped) network.
                        Computed at the segment level, but the SVG
                        visualization aggregates to the mean per named
                        street so a single arterial reads as one
                        continuous color (no segment-to-segment flicker).

VISUALIZATION
    Output is framed to the SAD bbox, dark background, with all streets
    and buildings clipped to the SAD polygon. Streets are colored on a
    blue → pale yellow → red gradient by their named-street mean
    centrality. Each named street is its own SVG group (e.g.
    `<g id="street-Maryland-Avenue">`), so in Illustrator they can be
    selected and edited as logical units. Unnamed segments are grouped
    in `streets-unnamed`.

OUTPUTS
    derived/<sad>/street_centrality.geojson
        Every vehicular street within the canvas extent, with columns:
        highway, name, oneway, lanes, maxspeed, surface, length_m,
        centrality_global, within_sad. CRS WGS84. The `within_sad` flag
        is convenient for QGIS users who want to filter the visualization
        to the SAD subset.

    derived/<sad>/street_centrality.svg
        Visualization framed to the SAD bounding box. Streets and
        buildings are clipped to the SAD polygon; outside the polygon
        nothing is rendered. Streets are colored by centrality value on
        a blue→yellow→red gradient (low to high) with stroke width also
        scaled subtly by value. Buildings within the SAD appear as a
        dark grey context layer on a near-black background. A horizontal
        gradient legend with "Low / High" labels sits at the bottom-left.

USAGE
    python module_06e_street_centrality.py
        --source  data/<sad_id>/source
        --derived data/derived/per_sad/<sad_id>
        [--snap-tolerance 0.5]   meters (default 0.5)
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from pathlib import Path

import geopandas as gpd


# ─── Network filtering ───────────────────────────────────────────────

VEHICULAR_HIGHWAYS = {
    'motorway', 'motorway_link',
    'trunk', 'trunk_link',
    'primary', 'primary_link',
    'secondary', 'secondary_link',
    'tertiary', 'tertiary_link',
    'residential', 'unclassified', 'living_street', 'service',
}

# OSM columns we preserve in the output (everything else is dropped).
KEEP_TAGS = ['highway', 'name', 'oneway', 'lanes', 'maxspeed', 'surface']

# Visualization style. The output is clipped to the SAD boundary and uses
# a color gradient (low=blue, mid=yellow, high=red) over a dark background,
# echoing standard space-syntax centrality visualizations. The Nolli map
# stays B&W/grey — these have different purposes (figure-ground vs analytical
# overlay).
SVG_BG = '#1a1a1a'
SVG_BUILDING = '#333333'
SVG_SKELETON = '#5a5a5a'         # uniform colour for unnamed minor segments
SVG_SKELETON_WIDTH = 0.8
SVG_BOUNDARY = '#f5d76e'         # warm bright yellow — clearly visible on dark bg
SVG_BOUNDARY_WIDTH = 2.4
SVG_BOUNDARY_DASH = '10 6'
SVG_LEGEND_TEXT = '#cccccc'
SVG_MIN_STROKE = 1.0
SVG_MAX_STROKE = 3.5
# Blue → pale yellow → red gradient stops (RdYlBu_r-inspired)
COLOR_STOPS = [
    (0.00, (44, 123, 182)),
    (0.50, (255, 255, 191)),
    (1.00, (215, 25, 28)),
]


# ─── IO helpers ──────────────────────────────────────────────────────

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
    image_extent = read_optional(source_dir / 'image_extent.geojson')
    if image_extent is not None and 'utm_crs' in image_extent.columns:
        v = image_extent['utm_crs'].iloc[0]
        if isinstance(v, str) and v.startswith('EPSG:'):
            return v, image_extent
    c = boundary.geometry.iloc[0].centroid
    zone = int((c.x + 180) / 6) + 1
    crs = f"EPSG:{32600 + zone}" if c.y >= 0 else f"EPSG:{32700 + zone}"
    return crs, image_extent


def _explode_line(geom):
    """LineString or MultiLineString -> list of simple LineStrings."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == 'LineString':
        return [geom]
    if geom.geom_type == 'MultiLineString':
        return list(geom.geoms)
    return []


# ─── Graph construction ──────────────────────────────────────────────

def build_graph(roads_m, snap_tolerance):
    """
    Build a networkx MultiGraph from road LineStrings.

    Nodes are snapped endpoints (tuples of rounded x,y in meters).
    Edges carry `length` (meters) and the row index they came from.

    Returns:
        G                    networkx.MultiGraph
        feature_edge_map     dict { roads_m.index value -> [(u,v,key), ...] }
    """
    import networkx as nx
    G = nx.MultiGraph()
    feature_edge_map = {}

    def snap(x, y):
        return (round(x / snap_tolerance) * snap_tolerance,
                round(y / snap_tolerance) * snap_tolerance)

    for idx, geom in zip(roads_m.index, roads_m.geometry):
        edges_for_this = []
        for line in _explode_line(geom):
            coords = list(line.coords)
            if len(coords) < 2:
                continue
            u = snap(*coords[0])
            v = snap(*coords[-1])
            if u == v:
                continue  # zero-length or closed loop with same endpoints
            length = float(line.length)
            key = G.add_edge(u, v, length=length, idx=idx)
            edges_for_this.append((u, v, key))
        if edges_for_this:
            feature_edge_map[idx] = edges_for_this

    return G, feature_edge_map


def _graph_summary(G):
    import networkx as nx
    comps = list(nx.connected_components(G))
    comp_sizes = sorted((len(c) for c in comps), reverse=True)
    return {
        'nodes': G.number_of_nodes(),
        'edges': G.number_of_edges(),
        'components': len(comps),
        'largest_component_nodes': comp_sizes[0] if comp_sizes else 0,
        'orphan_components_2_or_fewer': sum(1 for s in comp_sizes if s <= 2),
    }


# ─── Centrality ──────────────────────────────────────────────────────

def compute_global_centrality(G):
    """
    Edge betweenness centrality, weighted by length, normalised.

    Computed only on the largest connected component to avoid pathological
    values from isolated stubs. Edges outside the largest component get
    centrality 0.
    """
    import networkx as nx
    if G.number_of_edges() == 0:
        return {}
    comps = list(nx.connected_components(G))
    if not comps:
        return {}
    largest = max(comps, key=len)
    H = G.subgraph(largest).copy()
    c = nx.edge_betweenness_centrality(H, weight='length', normalized=True)
    # nx returns (u,v) for Graph or (u,v,key) for MultiGraph
    return c


def attach_to_features(roads_m, feature_edge_map, centrality):
    """
    For each input feature, take the mean centrality across the edges it
    was split into (in practice one edge per feature, but handle multi).
    """
    values = []
    for idx in roads_m.index:
        edges = feature_edge_map.get(idx, [])
        if not edges:
            values.append(0.0)
            continue
        vals = []
        for (u, v, key) in edges:
            vals.append(centrality.get((u, v, key), 0.0))
        values.append(sum(vals) / len(vals) if vals else 0.0)
    return values


# ─── SVG visualization ───────────────────────────────────────────────

def make_transform(bbox, size, margin):
    xmin, ymin, xmax, ymax = bbox
    bw, bh = xmax - xmin, ymax - ymin
    span = max(bw, bh)
    inner = size - 2 * margin
    scale = inner / span
    ox = margin + (inner - bw * scale) / 2.0
    oy = margin + (inner - bh * scale) / 2.0

    def tx(x, y):
        return (ox + (x - xmin) * scale,
                (size - oy) - (y - ymin) * scale)
    return tx


def _line_d(geom, tx):
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
        return ''.join(_line_d(g, tx) for g in geom.geoms)
    return ''


def _polygon_d(geom, tx):
    """Polygon or MultiPolygon as a single SVG 'd' string."""
    if geom is None or geom.is_empty:
        return ''
    def _ring(coords):
        parts = []
        for i, c in enumerate(coords):
            x, y = c[0], c[1]
            sx, sy = tx(x, y)
            parts.append(f"{'M' if i == 0 else 'L'}{sx:.1f},{sy:.1f}")
        parts.append('Z')
        return ''.join(parts)
    if geom.geom_type == 'Polygon':
        d = _ring(list(geom.exterior.coords))
        for hole in geom.interiors:
            d += _ring(list(hole.coords))
        return d
    if geom.geom_type == 'MultiPolygon':
        return ''.join(_polygon_d(p, tx) for p in geom.geoms)
    return ''


def _safe_intersection(geom, sad):
    """Clip geom to the SAD polygon, returning None on empty/error."""
    if geom is None or geom.is_empty or sad is None:
        return None
    try:
        clipped = geom.intersection(sad)
    except Exception:
        try:
            clipped = geom.buffer(0).intersection(sad.buffer(0))
        except Exception:
            return None
    return None if clipped.is_empty else clipped


def centrality_color(value, vmin, vmax):
    """Three-stop blue→yellow→red gradient. Returns hex string."""
    if vmax <= vmin:
        return '#888888'
    t = (value - vmin) / (vmax - vmin)
    t = max(0.0, min(1.0, t))
    for i in range(len(COLOR_STOPS) - 1):
        t0, c0 = COLOR_STOPS[i]
        t1, c1 = COLOR_STOPS[i + 1]
        if t <= t1 + 1e-9:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0
            r = int(round(c0[0] + (c1[0] - c0[0]) * f))
            g = int(round(c0[1] + (c1[1] - c0[1]) * f))
            b = int(round(c0[2] + (c1[2] - c0[2]) * f))
            return f'#{r:02x}{g:02x}{b:02x}'
    return '#888888'


def stroke_width_for(value, vmin, vmax):
    if vmax <= vmin:
        return SVG_MIN_STROKE
    lv = math.log1p(value - vmin)
    lmax = math.log1p(vmax - vmin)
    t = lv / lmax if lmax > 0 else 0
    return SVG_MIN_STROKE + t * (SVG_MAX_STROKE - SVG_MIN_STROKE)


def _emit_legend(size, margin):
    """Horizontal gradient bar at the bottom-left with low/high labels.

    Built as ~50 small rects rather than an SVG linearGradient because
    flattened rects survive Illustrator round-trips more reliably.
    """
    x = margin + 14
    y = size - margin - 30
    w = 220
    h = 10
    n = 50
    out = ['  <g id="legend">']
    for i in range(n):
        t = i / (n - 1)
        out.append(
            f'    <rect x="{x + i * w / n:.1f}" y="{y:.1f}" '
            f'width="{w / n + 0.5:.2f}" height="{h}" '
            f'fill="{centrality_color(t, 0, 1)}"/>'
        )
    out.append(
        f'    <rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" '
        f'fill="none" stroke="{SVG_LEGEND_TEXT}" stroke-width="0.6"/>'
    )
    out.append(
        f'    <text x="{x:.1f}" y="{y + h + 13:.1f}" '
        f'font-family="sans-serif" font-size="11" fill="{SVG_LEGEND_TEXT}">'
        f'Low</text>'
    )
    out.append(
        f'    <text x="{x + w:.1f}" y="{y + h + 13:.1f}" '
        f'font-family="sans-serif" font-size="11" fill="{SVG_LEGEND_TEXT}" '
        f'text-anchor="end">High</text>'
    )
    out.append(
        f'    <text x="{x + w/2:.1f}" y="{y - 6:.1f}" '
        f'font-family="sans-serif" font-size="11" fill="{SVG_LEGEND_TEXT}" '
        f'text-anchor="middle">Edge Betweenness Centrality</text>'
    )
    out.append('  </g>')
    return '\n'.join(out) + '\n'


def _slug(name: str) -> str:
    """SVG-safe identifier from a street name."""
    import re as _re
    s = _re.sub(r'[^A-Za-z0-9]+', '-', (name or '').strip()).strip('-')
    return s or 'unnamed'


def render_svg(roads_m, centrality_values, sad_geom, buildings_m,
               size=1600, margin=60):
    """
    Render the centrality SVG. Framed to the SAD bbox, with all street
    and building geometries clipped to the SAD polygon. Streets colored
    on a blue → yellow → red gradient. Segments of the same named street
    are grouped together and share a single mean centrality value (color
    + stroke width), so a single arterial reads as one continuous
    coloured ribbon instead of segment-by-segment flicker. Unnamed
    segments live in their own group.
    """
    bbox = sad_geom.bounds
    tx = make_transform(bbox, size, margin)

    # Per-named-street mean centrality. Unnamed segments stay individual.
    name_col = roads_m.get('name')
    by_name: dict = {}
    unnamed = []  # list of (geom, val)
    for i, (geom, val) in enumerate(zip(roads_m.geometry, centrality_values)):
        nm = (name_col.iloc[i] if name_col is not None else None)
        if isinstance(nm, str) and nm.strip():
            by_name.setdefault(nm.strip(), []).append((geom, val))
        else:
            unnamed.append((geom, val))

    # Color/width scales: use the same value distribution the place card
    # will eventually summarize from, which is named-street means plus
    # unnamed-segment individual values.
    all_vals = [sum(v for _, v in items) / len(items)
                for items in by_name.values()] \
        + [v for _, v in unnamed]
    nonzero = [v for v in all_vals if v > 0]
    vmin, vmax = 0.0, (max(nonzero) if nonzero else 1.0)

    out = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>']
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" width="{size}" height="{size}">')
    out.append(f'  <rect id="background" width="{size}" '
               f'height="{size}" fill="{SVG_BG}"/>')

    # Buildings (clipped to SAD) as context
    if buildings_m is not None and not buildings_m.empty:
        out.append(f'  <g id="buildings" fill="{SVG_BUILDING}" stroke="none">')
        for geom in buildings_m.geometry:
            clipped = _safe_intersection(geom, sad_geom)
            if clipped is None:
                continue
            d = _polygon_d(clipped, tx)
            if d:
                out.append(f'    <path d="{d}"/>')
        out.append('  </g>')

    # Network skeleton — every unnamed segment in a uniform muted grey.
    # Drawn before the named-street ribbons so the colored arterials
    # overlay it. This fills in the OSM intersection nodes / sliplanes /
    # turn lanes / driveway stubs that would otherwise show as colored
    # gaps interrupting the arterials.
    if unnamed:
        out.append(f'  <g id="streets-unnamed" fill="none" '
                   f'stroke="{SVG_SKELETON}" '
                   f'stroke-width="{SVG_SKELETON_WIDTH}" '
                   f'stroke-linecap="round" stroke-linejoin="round">')
        for geom, _val in unnamed:
            clipped = _safe_intersection(geom, sad_geom)
            if clipped is None:
                continue
            d = _line_d(clipped, tx)
            if d:
                out.append(f'    <path d="{d}"/>')
        out.append('  </g>')

    # Named streets — one sub-group per street, each with a shared
    # color and stroke-width derived from its mean centrality. Sorted
    # ascending so the highest-centrality streets render LAST (on top
    # of any visual conflicts at intersections).
    out.append('  <g id="streets" fill="none" '
               'stroke-linecap="round" stroke-linejoin="round">')
    sorted_names = sorted(
        by_name.keys(),
        key=lambda n: sum(v for _, v in by_name[n]) / len(by_name[n]))
    for name in sorted_names:
        items = by_name[name]
        mean_val = sum(v for _, v in items) / len(items)
        color = centrality_color(mean_val, vmin, vmax)
        w = stroke_width_for(mean_val, vmin, vmax)
        out.append(f'    <g id="street-{_slug(name)}" '
                   f'stroke="{color}" stroke-width="{w:.2f}">')
        for geom, _ in items:
            clipped = _safe_intersection(geom, sad_geom)
            if clipped is None:
                continue
            d = _line_d(clipped, tx)
            if d:
                out.append(f'      <path d="{d}"/>')
        out.append('    </g>')
    out.append('  </g>')

    # SAD boundary — prominent dashed outline on its own layer, drawn
    # last so it sits clearly on top of all data. Handles Polygon and
    # MultiPolygon both.
    d = _polygon_d(sad_geom, tx)
    if d:
        out.append(f'  <g id="boundary" fill="none" '
                   f'stroke="{SVG_BOUNDARY}" '
                   f'stroke-width="{SVG_BOUNDARY_WIDTH}" '
                   f'stroke-dasharray="{SVG_BOUNDARY_DASH}" '
                   f'stroke-linecap="round" stroke-linejoin="round">'
                   f'<path d="{d}"/></g>')

    out.append(_emit_legend(size, margin))
    out.append('</svg>')
    return '\n'.join(out)


# ─── Main ────────────────────────────────────────────────────────────

def compute(source_dir: Path, derived_dir: Path, snap_tolerance: float,
            clip_to_sad: bool = True):
    try:
        import networkx as nx
    except ImportError:
        sys.exit("networkx is required. Install with: "
                 "pip install networkx --break-system-packages")

    boundary = read_optional(source_dir / 'sad_boundary.geojson')
    if boundary is None:
        sys.exit(f"sad_boundary.geojson missing under {source_dir}")
    roads = read_optional(source_dir / 'highways.geojson')
    if roads is None:
        sys.exit(f"highways.geojson missing under {source_dir} — "
                 f"copy it from 01_GeoJSONs/05_Highways/ first (the "
                 f"migrate script does this).")

    metric_crs, image_extent = _resolve_metric_crs(source_dir, boundary)
    boundary_m = _to_metric(boundary, metric_crs)
    sad_geom = boundary_m.geometry.iloc[0]
    # Load buildings as a context layer for the SVG (clipped to SAD by
    # render_svg). Missing/empty buildings file is non-fatal.
    buildings = read_optional(source_dir / 'buildings.geojson')
    buildings_m = (_to_metric(buildings, metric_crs)
                   if buildings is not None else None)

    roads_m = _to_metric(roads, metric_crs)
    # Vehicular subset only
    hw = roads_m.get('highway')
    if hw is not None:
        roads_m = roads_m[hw.isin(VEHICULAR_HIGHWAYS)].copy()
    print(f"  vehicular network: {len(roads_m)} features")

    # Clip the analysis to the SAD. Streets that don't touch the SAD
    # polygon are dropped before the graph is built — this focuses the
    # centrality on the district's internal structure and prevents
    # canvas-edge stubs from fragmenting the network.
    if clip_to_sad:
        sad_geom_for_clip = boundary_m.geometry.iloc[0]
        keep = roads_m.geometry.apply(
            lambda g: g is not None and not g.is_empty
                      and g.intersects(sad_geom_for_clip))
        before = len(roads_m)
        roads_m = roads_m[keep].copy()
        print(f"  clipped to SAD: {len(roads_m)} of {before} features kept")

    if roads_m.empty:
        sys.exit("  no vehicular roads after filtering — nothing to compute.")

    # Build graph
    G, feature_edge_map = build_graph(roads_m, snap_tolerance)
    summary = _graph_summary(G)
    print(f"  graph: {summary['nodes']} nodes, {summary['edges']} edges, "
          f"{summary['components']} connected components "
          f"(largest: {summary['largest_component_nodes']} nodes)")
    if summary['components'] > 5:
        print(f"  NOTE: graph has many disconnected components — the "
              f"vehicular network is fragmented. Centrality is computed "
              f"on the largest component only.")

    # Compute global betweenness centrality
    print("  computing edge betweenness centrality (global)...")
    cent = compute_global_centrality(G)
    values = attach_to_features(roads_m, feature_edge_map, cent)

    # Write GeoJSON
    keep = [c for c in KEEP_TAGS if c in roads_m.columns]
    out_gdf = roads_m[keep + ['geometry']].copy()
    out_gdf['length_m'] = roads_m.geometry.length.round(1)
    out_gdf['centrality_global'] = [round(v, 6) for v in values]
    # Mark each feature with whether it intersects the SAD — convenient
    # filter for QGIS users who want to focus on the SAD-only subset.
    out_gdf['within_sad'] = [
        bool(geom is not None and not geom.is_empty
             and geom.intersects(sad_geom))
        for geom in roads_m.geometry
    ]
    out_gdf = out_gdf.to_crs('EPSG:4326')
    out_gj = derived_dir / 'street_centrality.geojson'
    out_gdf.to_file(out_gj, driver='GeoJSON')
    print(f"  wrote {out_gj}")

    # SVG visualization
    svg = render_svg(roads_m, values, sad_geom, buildings_m)
    out_svg = derived_dir / 'street_centrality.svg'
    out_svg.write_text(svg, encoding='utf-8')
    print(f"  wrote {out_svg}")

    # Summary JSON (for the place card later)
    nonzero = [v for v in values if v > 0]
    sumj = {
        'sad_id':         source_dir.parent.name,
        'method':         'edge_betweenness_centrality (Freeman 1977), '
                          'weighted by length, normalized, computed on '
                          'largest connected component',
        'network_size':   summary,
        'feature_count':  len(roads_m),
        'max_centrality': round(max(values), 6) if values else 0.0,
        'mean_centrality': round(sum(values) / len(values), 6) if values else 0.0,
        'mean_centrality_nonzero': round(sum(nonzero) / len(nonzero), 6)
                                    if nonzero else 0.0,
        'caveats': [
            "Network is clipped to the 2km ROD search radius; values "
            "within ~500m of the canvas edge are biased low.",
            "Topological measure only — does not reflect actual traffic "
            "counts, pedestrian flow, or land use.",
            "Local-radius variants (400m, 800m) not yet computed.",
        ],
    }
    sumj_path = derived_dir / 'street_centrality_summary.json'
    sumj_path.write_text(json.dumps(sumj, indent=2))
    print(f"  wrote {sumj_path}")
    return sumj


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('--source',  type=Path, required=True)
    ap.add_argument('--derived', type=Path, required=True)
    ap.add_argument('--snap-tolerance', type=float, default=0.5,
                    help='Endpoint snap tolerance in meters (default 0.5)')
    ap.add_argument('--no-clip-analysis', action='store_true',
                    help='Compute on the full vehicular network in the '
                         'highways file instead of clipping the graph to '
                         'the SAD boundary first. Default behavior clips '
                         'so the centrality reflects the SAD internal '
                         'structure and avoids canvas-edge fragmentation.')
    args = ap.parse_args()

    source = args.source.resolve()
    derived = args.derived.resolve()
    if not source.is_dir():
        sys.exit(f"source not found: {source}")
    derived.mkdir(parents=True, exist_ok=True)

    print(f"Street centrality for {source.parent.name}...")
    compute(source, derived, args.snap_tolerance,
            clip_to_sad=not args.no_clip_analysis)


if __name__ == '__main__':
    main()
