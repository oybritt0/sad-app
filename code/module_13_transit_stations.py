"""
module_13_transit_stations.py

Transit stations from OpenStreetMap via Overpass API, classified by
tier (major / entrance-or-tram / bus stops) and rendered as a bubble
map echoing KPF Seven Dials page 13.

REWRITE NOTES (v3)
    - PNG legend restored (was dropped in v2 rewrite)
    - Scale bar + north arrow added to PNG and SVG
    - ROD cross-check match radius expressed in feet (250 ft default)
    - All other layer structure unchanged from v2
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import requests
import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).parent))
import canvas_render as cr


# ─── Config ──────────────────────────────────────────────────────────────────

OVERPASS_URL    = 'https://overpass-api.de/api/interpreter'
USER_AGENT      = 'SAD-Pipeline/1.0 (ROSSETTI architectural research)'
QUERY_TIMEOUT   = 60
HTTP_TIMEOUT    = 90
RETRY_ATTEMPTS  = 3
RETRY_BACKOFF_S = 8

# Cross-check radius default in feet
DEFAULT_MATCH_RADIUS_FT = 250.0

TIER_STYLE = {
    1: {'r_svg': 14.0, 'color': '#f1c50c', 'opacity': 0.55,
        'edge': '#ffffff', 'edge_w': 1.2, 'label': 'Major station'},
    2: {'r_svg': 7.5,  'color': '#5b9bd5', 'opacity': 0.55,
        'edge': '#ffffff', 'edge_w': 0.8, 'label': 'Entrance / tram stop'},
    3: {'r_svg': 3.0,  'color': '#9aa0a8', 'opacity': 0.55,
        'edge': None,      'edge_w': 0,   'label': 'Bus stop'},
}

OVERPASS_QUERY = """
[out:json][timeout:{timeout}];
(
  node["railway"="station"]({bbox});
  way["railway"="station"]({bbox});
  node["public_transport"="station"]({bbox});
  way["public_transport"="station"]({bbox});
  node["amenity"="bus_station"]({bbox});
  way["amenity"="bus_station"]({bbox});
  node["railway"="subway_entrance"]({bbox});
  node["railway"="tram_stop"]({bbox});
  node["highway"="bus_stop"]({bbox});
);
out center tags;
"""


# ─── Overpass ───────────────────────────────────────────────────────────────

def query_overpass(bbox_wgs):
    bbox_str = ','.join(f'{v:.6f}' for v in bbox_wgs)
    query = OVERPASS_QUERY.format(timeout=QUERY_TIMEOUT, bbox=bbox_str)
    last_err = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(OVERPASS_URL, data={'data': query},
                                 headers={'User-Agent': USER_AGENT},
                                 timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get('elements', [])
            last_err = RuntimeError(f'HTTP {resp.status_code}: {resp.text[:200]}')
        except (requests.RequestException, ValueError) as e:
            last_err = e
        if attempt < RETRY_ATTEMPTS:
            sleep = RETRY_BACKOFF_S * attempt
            print(f"  Overpass attempt {attempt} failed ({last_err}); "
                  f"retrying in {sleep}s...")
            time.sleep(sleep)
    raise RuntimeError(f'Overpass query failed after {RETRY_ATTEMPTS} '
                       f'attempts: {last_err}')


def classify_tier(tags):
    if tags.get('railway') == 'station':            return 1, 'rail_station'
    if tags.get('public_transport') == 'station':   return 1, 'public_transport_station'
    if tags.get('amenity') == 'bus_station':        return 1, 'bus_station'
    if tags.get('railway') == 'subway_entrance':    return 2, 'subway_entrance'
    if tags.get('railway') == 'tram_stop':          return 2, 'tram_stop'
    if tags.get('highway') == 'bus_stop':           return 3, 'bus_stop'
    return 3, 'unknown'


def parse_elements(elements):
    records = []
    for el in elements:
        tags = el.get('tags', {}) or {}
        if el.get('type') == 'node':
            lat, lon = el.get('lat'), el.get('lon')
        else:
            center = el.get('center', {}) or {}
            lat, lon = center.get('lat'), center.get('lon')
        if lat is None or lon is None:
            continue
        tier, kind = classify_tier(tags)
        records.append({
            'osm_id': el.get('id'),
            'osm_type': el.get('type'),
            'tier': tier,
            'kind': kind,
            'name': tags.get('name'),
            'network': tags.get('network'),
            'operator': tags.get('operator'),
            'geometry': Point(lon, lat),
        })
    if not records:
        return gpd.GeoDataFrame(
            columns=['osm_id', 'osm_type', 'tier', 'kind', 'name',
                     'network', 'operator', 'geometry'],
            geometry='geometry', crs='EPSG:4326')
    return gpd.GeoDataFrame(records, geometry='geometry', crs='EPSG:4326')


# ─── ROD cross-check ─────────────────────────────────────────────────────────

def cross_check_rod(stations_m, places_path, metric_crs,
                    match_radius_ft=DEFAULT_MATCH_RADIUS_FT):
    if places_path is None or not places_path.exists():
        return {'skipped': True, 'reason': 'no places file provided'}
    try:
        places = gpd.read_file(places_path)
    except Exception as e:
        return {'skipped': True, 'reason': f'failed to load places: {e}'}
    if 'primary_category' not in places.columns:
        return {'skipped': True, 'reason': 'no primary_category column'}
    transit_subcats = {
        'subway_station', 'train_station', 'transit_station',
        'metro_station', 'light_rail_station', 'bus_station',
        'bus_stop', 'public_transit',
    }
    rod_transit = places[places['primary_category']
                         .str.lower().isin(transit_subcats)].copy()
    if rod_transit.empty:
        return {'osm_only': int(len(stations_m)), 'rod_only': 0,
                'matched_pairs': 0,
                'note': 'no transit-categorized POIs in ROD file'}
    rod_transit = rod_transit.to_crs(metric_crs)
    match_radius_m = cr.ft_to_m(match_radius_ft)
    osm_majors = stations_m[stations_m['tier'].isin([1, 2])]
    if osm_majors.empty:
        return {'osm_only': 0, 'rod_only': int(len(rod_transit)),
                'matched_pairs': 0,
                'note': 'no tier-1/2 OSM stations to match against'}
    osm_buffered = osm_majors.copy()
    osm_buffered['geometry'] = osm_majors.buffer(match_radius_m)
    joined = gpd.sjoin(rod_transit, osm_buffered, how='left',
                       predicate='intersects')
    matched_rod = joined[joined.index_right.notna()].index.unique()
    unmatched_rod = rod_transit[~rod_transit.index.isin(matched_rod)]
    rod_buffered = rod_transit.copy()
    rod_buffered['geometry'] = rod_transit.buffer(match_radius_m)
    joined2 = gpd.sjoin(osm_majors, rod_buffered, how='left',
                        predicate='intersects')
    matched_osm = joined2[joined2.index_right.notna()].index.unique()
    unmatched_osm = osm_majors[~osm_majors.index.isin(matched_osm)]
    return {
        'match_radius_ft': match_radius_ft,
        'match_radius_m': match_radius_m,
        'osm_tier12_total': int(len(osm_majors)),
        'rod_transit_total': int(len(rod_transit)),
        'matched_pairs': int(len(matched_osm)),
        'osm_only': int(len(unmatched_osm)),
        'rod_only': int(len(unmatched_rod)),
    }


# ─── SVG renderer ────────────────────────────────────────────────────────────

def render_svg(stations_m, ctx, out_svg, sad_id):
    tx, svg_w, svg_h, scale, plot_area = cr.make_transform(
        ctx['canvas_bbox'], svg_width=1100, margin=40, chrome_height=170)

    with open(out_svg, 'w', encoding='utf-8') as fh:
        cr.write_svg_open(fh, svg_w, svg_h,
                          title=f'Transit Stations - {sad_id}')

        cr.write_context_layers(fh, ctx, tx)

        # Stations: tier 3 first (under), tier 1 last (on top)
        for tier in [3, 2, 1]:
            sub = stations_m[stations_m['tier'] == tier] if not stations_m.empty \
                  else stations_m
            s = TIER_STYLE[tier]
            layer_id = f'transit_tier_{tier}'
            attrs = (f'fill="{s["color"]}" opacity="{s["opacity"]}"')
            if s['edge']:
                attrs += f' stroke="{s["edge"]}" stroke-width="{s["edge_w"]}"'
            else:
                attrs += ' stroke="none"'
            fh.write(f'  <g id="{layer_id}" {attrs}>\n')
            if sub is not None and not sub.empty:
                for _, row in sub.iterrows():
                    px, py = tx(row.geometry.x, row.geometry.y)
                    fh.write(f'    <circle cx="{px:.1f}" cy="{py:.1f}" '
                             f'r="{s["r_svg"]:.1f}"/>\n')
            fh.write('  </g>\n')

        # Tier-1 labels in their own layer
        fh.write('  <g id="transit_tier_1_labels" font-family="sans-serif" '
                 f'font-size="11" font-weight="700" fill="{cr.TEXT_COLOR}">\n')
        if not stations_m.empty:
            t1 = stations_m[stations_m['tier'] == 1]
            for _, row in t1.iterrows():
                name = row.get('name')
                if name:
                    px, py = tx(row.geometry.x, row.geometry.y)
                    fh.write(f'    <text x="{px + 16}" y="{py - 8}">'
                             f'{str(name).replace("&","&amp;")}</text>\n')
        fh.write('  </g>\n')

        cr.write_sad_boundary(fh, ctx['sad_geom_m'], tx)

        # Chrome: title
        n_t1 = int((stations_m['tier'] == 1).sum()) if not stations_m.empty else 0
        n_t2 = int((stations_m['tier'] == 2).sum()) if not stations_m.empty else 0
        n_t3 = int((stations_m['tier'] == 3).sum()) if not stations_m.empty else 0
        subtitle = (f"{n_t1} major \u00b7 {n_t2} entrance/tram \u00b7 "
                    f"{n_t3} bus stops \u00b7 OSM via Overpass")
        cr.write_title(fh, 'Transit Stations', subtitle, plot_area)

        # Chrome: legend (always present)
        legend_left = plot_area['left'] + 8
        legend_top = plot_area['bottom'] + 28
        fh.write('  <g id="chrome_legend" font-family="sans-serif" '
                 f'font-size="10" fill="{cr.TEXT_DIM}">\n')
        for i, tier in enumerate([1, 2, 3]):
            s = TIER_STYLE[tier]
            cy = legend_top + i * 20
            r = max(s['r_svg'] * 0.7, 3)
            attrs = f'fill="{s["color"]}" opacity="{s["opacity"]}"'
            if s['edge']:
                attrs += f' stroke="{s["edge"]}" stroke-width="{s["edge_w"]}"'
            fh.write(f'    <circle cx="{legend_left + 14}" cy="{cy}" '
                     f'r="{r:.1f}" {attrs}/>\n')
            fh.write(f'    <text x="{legend_left + 36}" y="{cy + 4}">'
                     f'{s["label"]}</text>\n')
        fh.write('  </g>\n')

        # Chrome: scale bar + north arrow
        cr.write_scale_bar(fh, plot_area, scale)
        cr.write_north_arrow(fh, plot_area)

        cr.write_svg_close(fh)


# ─── PNG renderer ────────────────────────────────────────────────────────────

def render_png(stations_m, ctx, out_png, sad_id):
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

    for tier in [3, 2, 1]:
        sub = stations_m[stations_m['tier'] == tier] if not stations_m.empty \
              else stations_m
        if sub is None or sub.empty:
            continue
        s = TIER_STYLE[tier]
        size_pt2 = (s['r_svg'] * 1.5) ** 2 * 4
        ax.scatter([g.x for g in sub.geometry],
                   [g.y for g in sub.geometry],
                   s=size_pt2, c=s['color'], alpha=s['opacity'],
                   edgecolors=s['edge'] if s['edge'] else 'none',
                   linewidths=s['edge_w'], zorder=5 + tier)

    if not stations_m.empty:
        for _, row in stations_m[stations_m['tier'] == 1].iterrows():
            name = row.get('name')
            if name:
                ax.annotate(str(name), (row.geometry.x, row.geometry.y),
                            xytext=(10, 10), textcoords='offset points',
                            color=cr.TEXT_COLOR, fontsize=9,
                            fontweight='bold', zorder=10)

    gpd.GeoSeries([ctx['sad_geom_m']]).boundary.plot(
        ax=ax, color=cr.BOUNDARY_COLOR, linewidth=cr.BOUNDARY_WIDTH,
        linestyle=(0, (10, 6)), zorder=8)

    # Chrome on the plot: scale bar + north arrow
    cr.draw_scale_bar_mpl(ax, ctx['canvas_bbox'])
    cr.draw_north_arrow_mpl(ax, ctx['canvas_bbox'])

    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)

    # Title and legend in axes coordinates (chrome)
    ax.text(0.02, 0.98, 'Transit Stations', transform=ax.transAxes,
            color=cr.TEXT_COLOR, fontsize=15, fontweight='bold',
            va='top', ha='left', family='sans-serif')
    n_t1 = int((stations_m['tier'] == 1).sum()) if not stations_m.empty else 0
    n_t2 = int((stations_m['tier'] == 2).sum()) if not stations_m.empty else 0
    n_t3 = int((stations_m['tier'] == 3).sum()) if not stations_m.empty else 0
    subtitle = (f"{n_t1} major \u00b7 {n_t2} entrance/tram \u00b7 "
                f"{n_t3} bus stops \u00b7 OSM via Overpass")
    ax.text(0.02, 0.94, subtitle, transform=ax.transAxes,
            color=cr.TEXT_DIM, fontsize=10,
            va='top', ha='left', family='sans-serif')

    # Legend — manual scatter in axes coords (RESTORED IN v3)
    legend_y = 0.16
    for tier in [1, 2, 3]:
        s = TIER_STYLE[tier]
        size_legend = max(s['r_svg'] * 1.2, 4) ** 2 * 3
        ax.scatter([0.04], [legend_y], s=size_legend, c=s['color'],
                   alpha=s['opacity'],
                   edgecolors=s['edge'] if s['edge'] else 'none',
                   linewidths=s['edge_w'], transform=ax.transAxes,
                   zorder=20, clip_on=False)
        ax.text(0.07, legend_y, s['label'], transform=ax.transAxes,
                color=cr.TEXT_DIM, fontsize=10, va='center', ha='left')
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

def compute(source_dir, derived_dir, places_path,
            cross_check=True,
            match_radius_ft=DEFAULT_MATCH_RADIUS_FT):
    sad_id = source_dir.parent.name
    print(f"Transit stations for {sad_id}...")

    print("  loading canvas context...")
    ctx = cr.load_canvas_context(source_dir)
    metric_crs = ctx['metric_crs']
    canvas_wgs = (gpd.GeoSeries([ctx['canvas_geom_m']], crs=metric_crs)
                     .to_crs('EPSG:4326').iloc[0])
    bbox_wgs = canvas_wgs.bounds
    minx, miny, maxx, maxy = bbox_wgs
    bbox_overpass = (miny, minx, maxy, maxx)

    print(f"  querying Overpass for bbox "
          f"({miny:.4f},{minx:.4f}) -> ({maxy:.4f},{maxx:.4f})")
    elements = query_overpass(bbox_overpass)
    stations_wgs = parse_elements(elements)
    print(f"  retrieved {len(stations_wgs)} elements from OSM")

    stations_m = stations_wgs.to_crs(metric_crs) if not stations_wgs.empty \
                 else stations_wgs

    tier_counts = stations_m['tier'].value_counts().to_dict() if not stations_m.empty else {}
    kind_counts = stations_m['kind'].value_counts().to_dict() if not stations_m.empty else {}
    print(f"  tier counts: " +
          ', '.join(f"T{t}={n}" for t, n in sorted(tier_counts.items())))

    out_dir = derived_dir / 'transit'
    out_dir.mkdir(parents=True, exist_ok=True)
    if not stations_wgs.empty:
        stations_wgs.to_file(out_dir / 'transit_stations.geojson',
                             driver='GeoJSON')

    cross_summary = {}
    if cross_check and places_path is not None:
        cross_summary = cross_check_rod(stations_m, places_path, metric_crs,
                                         match_radius_ft=match_radius_ft)
        if cross_summary and not cross_summary.get('skipped'):
            print(f"  ROD cross-check: {cross_summary.get('matched_pairs')} "
                  f"matched, {cross_summary.get('osm_only')} OSM-only, "
                  f"{cross_summary.get('rod_only')} ROD-only "
                  f"(radius {match_radius_ft:.0f} ft)")

    out_png = out_dir / 'transit_visualization.png'
    out_svg = out_dir / 'transit_visualization.svg'
    render_svg(stations_m, ctx, out_svg, sad_id)
    render_png(stations_m, ctx, out_png, sad_id)
    print(f"  wrote transit_visualization.png/.svg")

    summary = {
        'sad_id': sad_id,
        'source': 'OpenStreetMap via Overpass API',
        'query_bbox_wgs84': {
            'min_lat': miny, 'min_lon': minx,
            'max_lat': maxy, 'max_lon': maxx,
        },
        'total_stations': int(len(stations_wgs)),
        'tier_counts': {f'tier_{k}': int(v) for k, v in tier_counts.items()},
        'kind_counts': {k: int(v) for k, v in kind_counts.items()},
        'rod_cross_check': cross_summary,
        'tier_definitions': {
            'tier_1': 'major station (rail/metro/bus terminal)',
            'tier_2': 'subway entrance or tram stop',
            'tier_3': 'individual bus stop',
        },
    }
    (out_dir / 'transit_summary.json').write_text(json.dumps(summary, indent=2))
    print(f"  wrote transit_summary.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--derived', type=Path, required=True)
    ap.add_argument('--places-file', type=str, default='',
                    help='Optional ROD POI file for cross-validation. '
                         'Empty string = skip cross-check.')
    ap.add_argument('--no-cross-check', action='store_true')
    ap.add_argument('--match-radius-ft', type=float,
                    default=DEFAULT_MATCH_RADIUS_FT,
                    help=f'ROD-OSM match radius in feet '
                         f'(default {DEFAULT_MATCH_RADIUS_FT})')
    args = ap.parse_args()
    places_path = Path(args.places_file).resolve() if args.places_file else None
    compute(args.source.resolve(), args.derived.resolve(),
            places_path, cross_check=not args.no_cross_check,
            match_radius_ft=args.match_radius_ft)


if __name__ == '__main__':
    main()
