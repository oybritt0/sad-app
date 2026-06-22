"""
module_12_amenity_density.py

Per-category amenity density visualizations for a SAD. Computes a
true isotropic Gaussian kernel density estimate over the SAD area
and renders dark-background heatmaps.

REWRITE NOTES (v3)
    v2 used scipy.stats.gaussian_kde which scales bandwidth by the
    data's covariance matrix — for non-isotropic POI distributions
    this stretched the heatmap differently in different directions and
    blurred the relationship between POI clusters and visible hotspots.
    v3 uses a hand-rolled isotropic KDE: each POI contributes a
    Gaussian kernel of the exact same physical width regardless of how
    the point cloud is distributed. Bandwidth is now in feet and
    defaults to 250 ft, producing tight localized hotspots per the
    user's preferred read.

    Heatmap alpha dropped from 0.78 -> 0.55 so buildings remain
    clearly legible. Building palette brightened upstream in
    canvas_render. Imperial units throughout the CLI, displayed
    labels, and summary JSON. Scale bar + north arrow added.

INPUTS
    source/sad_boundary.geojson    framing
    source/image_extent.geojson    canvas bbox
    source/buildings.geojson       full-canvas context
    source/highways.geojson        full-canvas context
    --places-file <path>           ROD POI geojson

OUTPUTS
    derived/amenity_density/<category>.png
    derived/amenity_density/<category>.svg
    derived/amenity_density/amenity_density_summary.json
    derived/amenity_density/amenity_points.geojson
"""
from __future__ import annotations
import argparse
import io
import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).parent))
import canvas_render as cr


# ─── Rossetti category rollup ────────────────────────────────────────────────

_PARKING_SUBCATS = {
    'parking', 'parking_garage', 'parking_lot',
}
_RESIDENTIAL_SUBCATS = {
    'condominium', 'apartment', 'housing_authority',
    'residential_building', 'service_apartment',
}
_HOTEL_SUBCATS = {
    'hotel', 'motel', 'inn', 'lodge', 'resort', 'lodging',
    'bed_and_breakfast',
}
_SPORT_SUBCATS = {
    'stadium_arena', 'baseball_stadium', 'hockey_arena',
    'basketball_stadium', 'football_stadium', 'sports_complex',
    'baseball_field', 'soccer_field', 'race_track', 'golf_course',
    'tennis_court', 'swimming_pool',
    'gym', 'fitness_center', 'sport_or_fitness_facility',
    'sport_or_recreation_club', 'yoga_studio', 'fitness_trainer',
    'martial_arts_club', 'dance_studio', 'boot_camp',
    'bowling_alley', 'ice_skating_rink', 'skate_park',
    'rock_climbing_spot', 'mountain_bike_trail',
    'professional_sport_team', 'amateur_sport_league',
    'sports_clubs_and_leagues',
}
_OPEN_SPACE_SUBCATS = {
    'park', 'dog_park', 'public_plaza', 'public_fountain',
    'community_center', 'public_space',
}
_TOP_LEVEL_TO_ROSSETTI = {
    'food_and_drink':            'retail_food_entertainment',
    'shopping':                  'retail_food_entertainment',
    'arts_and_entertainment':    'retail_food_entertainment',
    'lodging':                   'hotel',
    'services_and_business':     'office',
    'health_care':               'office',
    'sports_and_recreation':     'sport',
    'community_and_government':  'other',
    'travel_and_transportation': 'other',
    'cultural_and_historic':     'other',
    'education':                 'other',
    'lifestyle_services':        'other',
    'geographic_entities':       'other',
}


def rollup_category(primary_category, top_level) -> str:
    pc = (primary_category or '').lower().strip()
    if pc in _PARKING_SUBCATS:     return 'parking'
    if pc in _RESIDENTIAL_SUBCATS: return 'residential'
    if pc in _HOTEL_SUBCATS:       return 'hotel'
    if pc in _SPORT_SUBCATS:       return 'sport'
    if pc in _OPEN_SPACE_SUBCATS:  return 'open_space'
    tl = (top_level or '').lower().strip()
    return _TOP_LEVEL_TO_ROSSETTI.get(tl, 'other')


def _get_top_level(hierarchy):
    if hierarchy is None:
        return None
    try:
        if hasattr(hierarchy, '__len__') and len(hierarchy) > 0:
            return str(hierarchy[0])
    except Exception:
        pass
    return None


# ─── Module config ───────────────────────────────────────────────────────────

CATEGORY_LABELS = {
    'retail_food_entertainment': 'Retail \u00b7 Food \u00b7 Entertainment',
    'office':                    'Office \u00b7 Services',
    'hotel':                     'Hotel \u00b7 Lodging',
    'sport':                     'Sport \u00b7 Recreation',
    'open_space':                'Open Space \u00b7 Civic',
    'residential':               'Residential',
    'parking':                   'Parking',
    'other':                     'Other',
}

MIN_POINTS_PER_CATEGORY = 5
DEFAULT_BANDWIDTH_FT = 250.0    # ~76 m — tight, localized hotspots
DEFAULT_GRID_SIZE = 200

HEATMAP_CMAP = 'magma'
HEATMAP_ALPHA_MAX = 0.55     # was 0.78 — buildings now read clearly through
LOW_DENSITY_CUTOFF_PCTL = 25
POINT_COLOR = '#ffffff'
POINT_RADIUS_SVG = 2.0
POINT_OPACITY = 0.7


# ─── IO + classification ─────────────────────────────────────────────────────

def load_places(places_path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(places_path)
    if 'primary_category' not in gdf.columns:
        raise SystemExit(
            f"{places_path} has no 'primary_category' column -- "
            "is this a ROD Overture-formatted places file?")
    if 'taxonomy_hierarchy' not in gdf.columns:
        gdf['taxonomy_hierarchy'] = None
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    return gdf


def classify(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    gdf['top_level'] = gdf['taxonomy_hierarchy'].apply(_get_top_level)
    gdf['category'] = [
        rollup_category(pc, tl)
        for pc, tl in zip(gdf['primary_category'], gdf['top_level'])
    ]
    return gdf


# ─── Isotropic KDE (replaces scipy gaussian_kde) ─────────────────────────────

def isotropic_kde_grid(points_m, bbox, grid_size, bandwidth_m):
    """Sum of isotropic Gaussian kernels at each point.

    Each POI contributes a Gaussian of the SAME physical bandwidth in
    all directions, regardless of how the point cloud is distributed.
    This gives a direct, interpretable heatmap: 'how concentrated are
    POIs within a bandwidth-sized neighborhood'.

    Returns (density, x_centers, y_centers). Density at a single POI is
    1.0; clusters add up.
    """
    xmin, ymin, xmax, ymax = bbox
    xs = np.array([g.x for g in points_m.geometry])
    ys = np.array([g.y for g in points_m.geometry])
    n = len(xs)

    x_centers = np.linspace(xmin, xmax, grid_size)
    y_centers = np.linspace(ymin, ymax, grid_size)
    gx, gy = np.meshgrid(x_centers, y_centers)

    if n == 0:
        return np.zeros_like(gx), x_centers, y_centers

    inv_2sig2 = 1.0 / (2.0 * bandwidth_m * bandwidth_m)
    density = np.zeros_like(gx)
    # Vectorize over points: for each POI add its Gaussian.
    # For typical N (50-500) this is fast in numpy.
    for px, py in zip(xs, ys):
        dist2 = (gx - px) ** 2 + (gy - py) ** 2
        density += np.exp(-dist2 * inv_2sig2)
    return density, x_centers, y_centers


def apply_low_density_cutoff(density, pctl):
    if density is None:
        return None
    out = density.copy().astype(float)
    nonzero = out[out > 0]
    if len(nonzero) == 0:
        return out
    threshold = np.percentile(nonzero, pctl)
    out[out < threshold] = np.nan
    return out


def mask_to_polygon(density, x_centers, y_centers, polygon):
    gx, gy = np.meshgrid(x_centers, y_centers)
    mask = np.array([
        polygon.contains(Point(x, y))
        for x, y in zip(gx.ravel(), gy.ravel())
    ]).reshape(gx.shape)
    out = density.copy()
    out[~mask] = np.nan
    return out


# ─── Heatmap PNG bytes for SVG embedding ────────────────────────────────────

def heatmap_to_png_bytes(density, plot_area, canvas_bbox,
                         cmap=HEATMAP_CMAP, alpha_max=HEATMAP_ALPHA_MAX):
    fig_w_in = plot_area['width'] / 100
    fig_h_in = plot_area['height'] / 100
    fig = plt.figure(figsize=(fig_w_in, fig_h_in), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor('none')
    fig.patch.set_alpha(0)

    if density is not None:
        xmin, ymin, xmax, ymax = canvas_bbox
        if np.isfinite(density).any():
            vmax = np.nanmax(density)
            ax.imshow(density, extent=(xmin, xmax, ymin, ymax),
                      origin='lower', cmap=cmap,
                      vmin=0, vmax=vmax if vmax > 0 else 1,
                      alpha=alpha_max, interpolation='bilinear')
        ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax)
    ax.set_aspect('equal'); ax.axis('off')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, transparent=True,
                bbox_inches=None, pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ─── SVG renderer ────────────────────────────────────────────────────────────

def render_category_svg(category, label, n_points, points_m, density,
                        ctx, bandwidth_ft, out_svg):
    tx, svg_w, svg_h, scale, plot_area = cr.make_transform(
        ctx['canvas_bbox'], svg_width=1100, margin=40, chrome_height=170)

    png_bytes = heatmap_to_png_bytes(density, plot_area, ctx['canvas_bbox'])
    data_uri = cr.png_bytes_to_data_uri(png_bytes)

    with open(out_svg, 'w', encoding='utf-8') as fh:
        cr.write_svg_open(fh, svg_w, svg_h,
                          title=f'Amenity Density - {category}')

        cr.write_context_layers(fh, ctx, tx)
        cr.write_embedded_image(fh, 'density_heatmap', data_uri, plot_area)

        fh.write(f'  <g id="poi_dots" fill="{POINT_COLOR}" stroke="none" '
                 f'opacity="{POINT_OPACITY}">\n')
        for geom in points_m.geometry:
            if geom is None or geom.is_empty:
                continue
            px, py = tx(geom.x, geom.y)
            fh.write(f'    <circle cx="{px:.1f}" cy="{py:.1f}" '
                     f'r="{POINT_RADIUS_SVG}"/>\n')
        fh.write('  </g>\n')

        cr.write_sad_boundary(fh, ctx['sad_geom_m'], tx)

        # Chrome: title
        subtitle = (f"{n_points} location{'s' if n_points != 1 else ''} "
                    f"in SAD \u00b7 KDE bandwidth {bandwidth_ft:.0f} ft")
        cr.write_title(fh, label, subtitle, plot_area)

        # Chrome: legend (density gradient bar)
        legend_left = plot_area['left'] + 8
        legend_top = plot_area['bottom'] + 24
        legend_w = 200
        legend_h = 8
        fh.write('  <g id="chrome_legend" font-family="sans-serif">\n')
        fh.write('    <defs>\n')
        fh.write('      <linearGradient id="density_gradient" '
                 'x1="0%" y1="0%" x2="100%" y2="0%">\n')
        for stop, color in [
            (0.00, '#0d0218'), (0.25, '#3b0f70'),
            (0.50, '#8c2981'), (0.75, '#de4968'),
            (1.00, '#fcfdbf')]:
            fh.write(f'        <stop offset="{stop * 100:.0f}%" '
                     f'stop-color="{color}"/>\n')
        fh.write('      </linearGradient>\n')
        fh.write('    </defs>\n')
        fh.write(f'    <rect x="{legend_left}" y="{legend_top}" '
                 f'width="{legend_w}" height="{legend_h}" '
                 f'fill="url(#density_gradient)" stroke="{cr.TEXT_DIM}" '
                 f'stroke-width="0.5"/>\n')
        fh.write(f'    <text x="{legend_left}" y="{legend_top + legend_h + 14}" '
                 f'font-size="10" fill="{cr.TEXT_DIM}">Low density</text>\n')
        fh.write(f'    <text x="{legend_left + legend_w}" '
                 f'y="{legend_top + legend_h + 14}" '
                 f'text-anchor="end" font-size="10" '
                 f'fill="{cr.TEXT_DIM}">High density</text>\n')
        fh.write('  </g>\n')

        # Chrome: scale bar + north arrow
        cr.write_scale_bar(fh, plot_area, scale)
        cr.write_north_arrow(fh, plot_area)

        cr.write_svg_close(fh)


# ─── PNG renderer ────────────────────────────────────────────────────────────

def render_category_png(category, label, n_points, points_m, density,
                        ctx, bandwidth_ft, out_png):
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

    if density is not None:
        ax.imshow(density, extent=(minx, maxx, miny, maxy),
                  origin='lower', cmap=HEATMAP_CMAP,
                  alpha=HEATMAP_ALPHA_MAX, interpolation='bilinear',
                  zorder=5)

    if not points_m.empty:
        ax.scatter([g.x for g in points_m.geometry],
                   [g.y for g in points_m.geometry],
                   s=4, c=POINT_COLOR, alpha=POINT_OPACITY,
                   linewidths=0, zorder=6)

    gpd.GeoSeries([ctx['sad_geom_m']]).boundary.plot(
        ax=ax, color=cr.BOUNDARY_COLOR, linewidth=cr.BOUNDARY_WIDTH,
        linestyle=(0, (10, 6)), zorder=7)

    # Chrome on the plot
    cr.draw_scale_bar_mpl(ax, ctx['canvas_bbox'])
    cr.draw_north_arrow_mpl(ax, ctx['canvas_bbox'])

    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    ax.text(0.02, 0.98, label, transform=ax.transAxes,
            color=cr.TEXT_COLOR, fontsize=14, fontweight='bold',
            va='top', ha='left', family='sans-serif')
    subtitle = (f"{n_points} location{'s' if n_points != 1 else ''} "
                f"in SAD \u00b7 KDE bandwidth {bandwidth_ft:.0f} ft")
    ax.text(0.02, 0.95, subtitle, transform=ax.transAxes,
            color=cr.TEXT_DIM, fontsize=10,
            va='top', ha='left', family='sans-serif')

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
            bandwidth_ft=DEFAULT_BANDWIDTH_FT,
            grid_size=DEFAULT_GRID_SIZE):
    sad_id = source_dir.parent.name
    print(f"Amenity density for {sad_id}...")

    print("  loading canvas context...")
    ctx = cr.load_canvas_context(source_dir)
    metric_crs = ctx['metric_crs']
    sad_geom_m = ctx['sad_geom_m']
    bandwidth_m = cr.ft_to_m(bandwidth_ft)

    n_b_out = len(ctx['buildings_outside']) if ctx['buildings_outside'] is not None else 0
    n_b_in  = len(ctx['buildings_inside'])  if ctx['buildings_inside']  is not None else 0
    n_s_out = len(ctx['streets_outside'])   if ctx['streets_outside']   is not None else 0
    n_s_in  = len(ctx['streets_inside'])    if ctx['streets_inside']    is not None else 0
    print(f"  context: {n_b_in} bldgs in / {n_b_out} out, "
          f"{n_s_in} streets in / {n_s_out} out")
    print(f"  bandwidth: {bandwidth_ft:.0f} ft ({bandwidth_m:.1f} m)")

    places = load_places(places_path)
    if places.crs is None:
        places = places.set_crs('EPSG:4326')
    places_m = places.to_crs(metric_crs)
    places_m = classify(places_m)
    places_m = gpd.clip(places_m, sad_geom_m)
    print(f"  {len(places_m)} POIs inside SAD")

    bbox_m = ctx['canvas_bbox']

    out_dir = derived_dir / 'amenity_density'
    out_dir.mkdir(parents=True, exist_ok=True)

    counts = places_m['category'].value_counts().to_dict()
    print("  category counts:")
    for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
        label = CATEGORY_LABELS.get(cat, cat)
        marker = '' if n >= MIN_POINTS_PER_CATEGORY else ' (skip)'
        print(f"    {n:>4}  {label}{marker}")

    rendered = []
    for cat, label in CATEGORY_LABELS.items():
        cat_points = places_m[places_m['category'] == cat].copy()
        n = len(cat_points)
        if n < MIN_POINTS_PER_CATEGORY:
            continue

        density, xc, yc = isotropic_kde_grid(
            cat_points, bbox_m, grid_size=grid_size, bandwidth_m=bandwidth_m)
        if density is not None:
            density = apply_low_density_cutoff(density, LOW_DENSITY_CUTOFF_PCTL)
            density = mask_to_polygon(density, xc, yc, sad_geom_m)

        out_png = out_dir / f'{cat}.png'
        out_svg = out_dir / f'{cat}.svg'
        render_category_png(cat, label, n, cat_points, density,
                            ctx, bandwidth_ft, out_png)
        render_category_svg(cat, label, n, cat_points, density,
                            ctx, bandwidth_ft, out_svg)
        rendered.append(cat)
        print(f"  wrote {cat}.png/.svg")

    if not places_m.empty:
        places_out = places_m[['primary_category', 'top_level', 'category',
                                'geometry']].to_crs('EPSG:4326')
        places_out.to_file(out_dir / 'amenity_points.geojson', driver='GeoJSON')

    summary = {
        'sad_id': sad_id,
        'method': ('isotropic gaussian kernel density estimation: each POI '
                   'contributes an isotropic Gaussian kernel of equal '
                   'physical bandwidth; summed on a regular grid over the '
                   'canvas extent, masked to the SAD polygon. Low-density '
                   f'cutoff at percentile {LOW_DENSITY_CUTOFF_PCTL} of '
                   'nonzero values.'),
        'metric_crs': metric_crs,
        'bandwidth_ft': bandwidth_ft,
        'bandwidth_m': bandwidth_m,
        'grid_size': grid_size,
        'low_density_cutoff_pctl': LOW_DENSITY_CUTOFF_PCTL,
        'heatmap_alpha_max': HEATMAP_ALPHA_MAX,
        'total_points_in_sad': int(len(places_m)),
        'category_counts': {k: int(v) for k, v in counts.items()},
        'categories_rendered': rendered,
        'categories_skipped_too_few_points': [
            cat for cat, n in counts.items()
            if n < MIN_POINTS_PER_CATEGORY and cat in CATEGORY_LABELS
        ],
        'context_layers': {
            'buildings_inside_sad':  n_b_in,
            'buildings_outside_sad': n_b_out,
            'streets_inside_sad':    n_s_in,
            'streets_outside_sad':   n_s_out,
        },
    }
    (out_dir / 'amenity_density_summary.json').write_text(
        json.dumps(summary, indent=2))
    print(f"  wrote amenity_density_summary.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--derived', type=Path, required=True)
    ap.add_argument('--places-file', type=Path, required=True)
    ap.add_argument('--bandwidth-ft', type=float, default=DEFAULT_BANDWIDTH_FT,
                    help=f'KDE bandwidth in FEET '
                         f'(default {DEFAULT_BANDWIDTH_FT})')
    ap.add_argument('--grid-size', type=int, default=DEFAULT_GRID_SIZE)
    args = ap.parse_args()
    compute(args.source.resolve(), args.derived.resolve(),
            args.places_file.resolve(),
            bandwidth_ft=args.bandwidth_ft, grid_size=args.grid_size)


if __name__ == '__main__':
    main()
