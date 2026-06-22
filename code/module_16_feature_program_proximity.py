"""
module_16_feature_program_proximity.py

Per-SAD analysis of how program mix shifts depending on proximity to
key urban features: transit stations and parks/open space.

For each feature type, computes the program-category share INSIDE a
proximity buffer (e.g. 1,320 ft / 5-min walk of transit) and OUTSIDE
(but still within the SAD), then visualizes the SHIFT as a
Cleveland-style paired dot plot. The shift (in percentage points)
makes the analytical answer immediately legible: "retail/F&B is +14pp
more common near transit, office is -9pp."

KPF AESTHETIC
    Matches M12/M13/M15: dark background, named SVG layers, imperial
    units throughout. PNG via matplotlib, SVG hand-rolled.

INPUTS
    source/sad_boundary.geojson
    source/rod_places.geojson                 POIs with rossetti_category
    source/parks.geojson                      park polygons (optional)
    derived/transit/transit_stations.geojson  from M13 (optional)

OUTPUTS (in derived/feature_program_proximity/)
    transit_proximity.{png,svg}
    parks_proximity.{png,svg}
    feature_program_proximity_summary.json
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from shapely.geometry import Point
from shapely.ops import unary_union


# ─── Unit conversion ─────────────────────────────────────────────────────────

M_PER_FT = 0.3048
FT_PER_M = 1.0 / M_PER_FT


# ─── Config ──────────────────────────────────────────────────────────────────

DEFAULT_TRANSIT_BUFFER_FT = 1320.0   # 5-min walk at 3 mph (FHWA convention)
DEFAULT_PARKS_BUFFER_FT   = 500.0    # ~1 short block

MIN_FAR_POIS = 5    # minimum POIs needed "far" to compute a meaningful share


# ─── Style (matches M12/M13/M15 KPF palette) ───────────────────────────────

BG_COLOR        = '#0a0a0a'
TEXT_COLOR      = '#dcdcdc'
TEXT_DIM        = '#8a8a8a'
GRID_COLOR      = '#2a2a2a'
NEAR_COLOR      = '#f1c50c'      # warm yellow for "near"
FAR_COLOR       = '#5b9bd5'      # cool blue for "far"
SHIFT_POS_COLOR = '#7BB661'      # green for positive shift (more common near)
SHIFT_NEG_COLOR = '#D97757'      # coral for negative shift (less common near)
SHIFT_NEUTRAL   = '#666666'


# Program category palette (matches M10 plan view + M6d v3)
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


# ─── Analysis ────────────────────────────────────────────────────────────────

def compute_proximity_shift(places_in_sad, features_buffer_geom):
    """For each Rossetti program category, compute the share among POIs
    NEAR the features (within buffer) vs FAR (outside buffer but in SAD).
    Returns dict per category with near_pct / far_pct / shift_pp / counts,
    plus aggregate counts.
    """
    if 'rossetti_category' not in places_in_sad.columns:
        return None

    if features_buffer_geom is None or features_buffer_geom.is_empty:
        return None

    is_near = places_in_sad.geometry.apply(
        lambda p: features_buffer_geom.contains(p) if p else False)
    near = places_in_sad[is_near]
    far  = places_in_sad[~is_near]

    n_near = len(near)
    n_far = len(far)
    if n_near < 1 or n_far < MIN_FAR_POIS:
        return {
            'n_near': int(n_near), 'n_far': int(n_far),
            'rows': None,    # not enough data
        }

    rows = []
    for cat in ROSSETTI_ORDER:
        near_count = int((near['rossetti_category'] == cat).sum())
        far_count  = int((far['rossetti_category']  == cat).sum())
        near_pct = (near_count / n_near * 100) if n_near > 0 else 0.0
        far_pct  = (far_count  / n_far  * 100) if n_far  > 0 else 0.0
        rows.append({
            'category':  cat,
            'label':     PROGRAM_LABELS[cat],
            'near_count': near_count,
            'far_count':  far_count,
            'near_pct':   near_pct,
            'far_pct':    far_pct,
            'shift_pp':   near_pct - far_pct,
        })
    return {'n_near': int(n_near), 'n_far': int(n_far), 'rows': rows}


# ─── PNG renderer ────────────────────────────────────────────────────────────

def render_proximity_chart_png(shift_data, title, subtitle,
                                buffer_ft, n_features, feature_label,
                                out_png):
    """Cleveland-style paired dot plot showing the program-mix shift."""
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # No-data case
    if (shift_data is None or shift_data.get('rows') is None or
        n_features == 0):
        if n_features == 0:
            msg = f'(no {feature_label} detected in this SAD)'
        else:
            n_far = shift_data['n_far'] if shift_data else 0
            n_near = shift_data['n_near'] if shift_data else 0
            msg = (f'(not enough POIs to compare:  '
                   f'near={n_near}, far={n_far}, need far >= {MIN_FAR_POIS})')
        ax.text(0.5, 0.5, msg,
                transform=ax.transAxes,
                color=TEXT_DIM, fontsize=12, style='italic',
                ha='center', va='center')
        ax.text(0.02, 0.96, title, transform=ax.transAxes,
                color=TEXT_COLOR, fontsize=15, fontweight='bold',
                ha='left', va='top')
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        fig.savefig(out_png, dpi=150, facecolor=BG_COLOR,
                    bbox_inches='tight', pad_inches=0.2)
        plt.close(fig)
        return

    rows = shift_data['rows']
    # Sort by absolute shift magnitude so most-meaningful rows on top
    rows = sorted(rows, key=lambda r: -abs(r['shift_pp']))

    y_positions = list(range(len(rows)))[::-1]   # top of chart = first row
    labels = [r['label'] for r in rows]
    near_pcts = [r['near_pct'] for r in rows]
    far_pcts  = [r['far_pct']  for r in rows]
    shifts    = [r['shift_pp'] for r in rows]

    # Compute x-axis bounds
    max_pct = max(max(near_pcts), max(far_pcts), 5)
    x_max = max_pct * 1.15

    # Grid lines (vertical, subtle)
    for x in range(0, int(x_max) + 1, 5):
        ax.axvline(x, color=GRID_COLOR, linewidth=0.5, zorder=1)

    # Draw line connecting near<->far for each program
    for y, np_, fp, sh in zip(y_positions, near_pcts, far_pcts, shifts):
        if abs(sh) > 1.5:
            color = SHIFT_POS_COLOR if sh > 0 else SHIFT_NEG_COLOR
            alpha = 0.85
            lw = 2.0
        else:
            color = SHIFT_NEUTRAL
            alpha = 0.5
            lw = 1.2
        x0, x1 = min(np_, fp), max(np_, fp)
        ax.plot([x0, x1], [y, y], color=color,
                linewidth=lw, alpha=alpha, zorder=3,
                solid_capstyle='round')

    # Plot the FAR dots (blue)
    ax.scatter(far_pcts, y_positions, s=110, c=FAR_COLOR,
                edgecolors='white', linewidths=1.4, zorder=5,
                label='Far from ' + feature_label)
    # Plot the NEAR dots (yellow)
    ax.scatter(near_pcts, y_positions, s=110, c=NEAR_COLOR,
                edgecolors='white', linewidths=1.4, zorder=6,
                label=f'Within {int(buffer_ft):,} ft of {feature_label}')

    # Label each row with shift annotation
    for y, sh in zip(y_positions, shifts):
        if abs(sh) >= 0.5:
            sign = '+' if sh > 0 else ''
            color = (SHIFT_POS_COLOR if sh > 0 else SHIFT_NEG_COLOR) \
                    if abs(sh) > 1.5 else SHIFT_NEUTRAL
            ax.text(x_max * 0.995, y, f"{sign}{sh:.0f} pp",
                    color=color, fontsize=10, fontweight='bold',
                    ha='right', va='center', zorder=10)

    # Y-axis: program category labels
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, color=TEXT_COLOR, fontsize=11)
    ax.tick_params(axis='y', colors=TEXT_DIM, length=0)

    # X-axis
    ax.set_xlim(-x_max * 0.02, x_max)
    ax.set_xlabel('Share of POIs (%)', color=TEXT_DIM, fontsize=10)
    ax.tick_params(axis='x', colors=TEXT_DIM, labelsize=9)
    for sp in ('top', 'right', 'left'):
        ax.spines[sp].set_visible(False)
    ax.spines['bottom'].set_color(GRID_COLOR)

    # Title & subtitle
    ax.text(0.02, 1.10, title, transform=ax.transAxes,
            color=TEXT_COLOR, fontsize=15, fontweight='bold',
            ha='left', va='bottom', family='sans-serif')
    ax.text(0.02, 1.04, subtitle, transform=ax.transAxes,
            color=TEXT_DIM, fontsize=10,
            ha='left', va='bottom')

    # Legend
    leg = ax.legend(loc='lower right', fontsize=10, frameon=False,
                     bbox_to_anchor=(1.0, 1.04), ncol=2)
    for txt in leg.get_texts():
        txt.set_color(TEXT_COLOR)

    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor=BG_COLOR,
                bbox_inches='tight', pad_inches=0.2)
    plt.close(fig)


# ─── SVG renderer (hand-rolled named layers) ────────────────────────────────

def render_proximity_chart_svg(shift_data, title, subtitle,
                                buffer_ft, n_features, feature_label,
                                out_svg):
    """Hand-rolled SVG with named layers for editing in Illustrator."""
    svg_w, svg_h = 1100, 650
    margin_l, margin_r, margin_t, margin_b = 200, 80, 110, 70
    plot_w = svg_w - margin_l - margin_r
    plot_h = svg_h - margin_t - margin_b

    with open(out_svg, 'w', encoding='utf-8') as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'viewBox="0 0 {svg_w} {svg_h}" '
                 f'width="{svg_w}" height="{svg_h}">\n')
        fh.write(f'  <title>{title}</title>\n')

        # Background
        fh.write('  <g id="background">\n')
        fh.write(f'    <rect x="0" y="0" width="{svg_w}" height="{svg_h}" '
                 f'fill="{BG_COLOR}"/>\n')
        fh.write('  </g>\n')

        # Title chrome
        fh.write('  <g id="chrome_title" font-family="sans-serif">\n')
        fh.write(f'    <text x="{margin_l}" y="40" font-size="18" '
                 f'font-weight="700" fill="{TEXT_COLOR}">{title}</text>\n')
        sub_esc = subtitle.replace('&', '&amp;').replace('<', '&lt;')
        fh.write(f'    <text x="{margin_l}" y="60" font-size="11" '
                 f'fill="{TEXT_DIM}">{sub_esc}</text>\n')
        fh.write('  </g>\n')

        # No-data path
        if (shift_data is None or shift_data.get('rows') is None or
            n_features == 0):
            if n_features == 0:
                msg = f'(no {feature_label} detected in this SAD)'
            else:
                n_far = shift_data['n_far'] if shift_data else 0
                n_near = shift_data['n_near'] if shift_data else 0
                msg = (f'(not enough POIs to compare: near={n_near}, '
                       f'far={n_far}, need far &gt;= {MIN_FAR_POIS})')
            fh.write('  <g id="message" font-family="sans-serif" '
                     f'font-size="14" fill="{TEXT_DIM}" '
                     'font-style="italic">\n')
            fh.write(f'    <text x="{svg_w/2:.0f}" y="{svg_h/2:.0f}" '
                     f'text-anchor="middle">{msg}</text>\n')
            fh.write('  </g>\n')
            fh.write('</svg>\n')
            return

        rows = shift_data['rows']
        rows = sorted(rows, key=lambda r: -abs(r['shift_pp']))

        near_pcts = [r['near_pct'] for r in rows]
        far_pcts  = [r['far_pct']  for r in rows]
        max_pct = max(max(near_pcts), max(far_pcts), 5)
        x_max = max_pct * 1.15

        def to_svg_x(p):
            return margin_l + (p / x_max) * plot_w
        n_rows = len(rows)
        row_step = plot_h / max(n_rows, 1)
        def to_svg_y(i):
            return margin_t + row_step * (i + 0.5)

        # Vertical grid lines layer
        fh.write('  <g id="chart_grid" stroke="{c}" stroke-width="0.5">\n'
                 .format(c=GRID_COLOR))
        for x_pct in range(0, int(x_max) + 1, 5):
            sx = to_svg_x(x_pct)
            fh.write(f'    <line x1="{sx:.1f}" y1="{margin_t}" '
                     f'x2="{sx:.1f}" y2="{svg_h - margin_b}"/>\n')
        fh.write('  </g>\n')

        # X-axis tick labels
        fh.write('  <g id="chart_x_axis" font-family="sans-serif" '
                 f'font-size="9" fill="{TEXT_DIM}" '
                 'text-anchor="middle">\n')
        for x_pct in range(0, int(x_max) + 1, 5):
            sx = to_svg_x(x_pct)
            fh.write(f'    <text x="{sx:.1f}" y="{svg_h - margin_b + 16}">'
                     f'{x_pct}</text>\n')
        fh.write(f'    <text x="{margin_l + plot_w / 2:.1f}" '
                 f'y="{svg_h - margin_b + 36}" font-size="10" '
                 f'fill="{TEXT_DIM}">Share of POIs (%)</text>\n')
        fh.write('  </g>\n')

        # Y-axis labels
        fh.write('  <g id="chart_y_labels" font-family="sans-serif" '
                 f'font-size="11" fill="{TEXT_COLOR}">\n')
        for i, r in enumerate(rows):
            sy = to_svg_y(i)
            fh.write(f'    <text x="{margin_l - 10}" y="{sy + 4:.1f}" '
                     f'text-anchor="end">{r["label"]}</text>\n')
        fh.write('  </g>\n')

        # Connector lines layer
        fh.write('  <g id="chart_connectors" stroke-linecap="round">\n')
        for i, r in enumerate(rows):
            sy = to_svg_y(i)
            x0 = to_svg_x(min(r['near_pct'], r['far_pct']))
            x1 = to_svg_x(max(r['near_pct'], r['far_pct']))
            sh = r['shift_pp']
            if abs(sh) > 1.5:
                color = SHIFT_POS_COLOR if sh > 0 else SHIFT_NEG_COLOR
                opacity = '0.85'
                lw = 2.0
            else:
                color = SHIFT_NEUTRAL
                opacity = '0.5'
                lw = 1.2
            fh.write(f'    <line x1="{x0:.1f}" y1="{sy:.1f}" '
                     f'x2="{x1:.1f}" y2="{sy:.1f}" '
                     f'stroke="{color}" stroke-opacity="{opacity}" '
                     f'stroke-width="{lw}"/>\n')
        fh.write('  </g>\n')

        # Far dots layer
        fh.write(f'  <g id="chart_far_dots" fill="{FAR_COLOR}" '
                 f'stroke="white" stroke-width="1.4">\n')
        for i, r in enumerate(rows):
            sy = to_svg_y(i)
            sx = to_svg_x(r['far_pct'])
            fh.write(f'    <circle cx="{sx:.1f}" cy="{sy:.1f}" '
                     f'r="6"/>\n')
        fh.write('  </g>\n')

        # Near dots layer
        fh.write(f'  <g id="chart_near_dots" fill="{NEAR_COLOR}" '
                 f'stroke="white" stroke-width="1.4">\n')
        for i, r in enumerate(rows):
            sy = to_svg_y(i)
            sx = to_svg_x(r['near_pct'])
            fh.write(f'    <circle cx="{sx:.1f}" cy="{sy:.1f}" '
                     f'r="6"/>\n')
        fh.write('  </g>\n')

        # Shift annotations layer (right side)
        fh.write('  <g id="chart_shift_labels" font-family="sans-serif" '
                 f'font-size="10" font-weight="700" text-anchor="end">\n')
        annot_x = margin_l + plot_w - 4
        for i, r in enumerate(rows):
            sh = r['shift_pp']
            if abs(sh) < 0.5:
                continue
            sy = to_svg_y(i)
            sign = '+' if sh > 0 else ''
            color = (SHIFT_POS_COLOR if sh > 0 else SHIFT_NEG_COLOR) \
                    if abs(sh) > 1.5 else SHIFT_NEUTRAL
            fh.write(f'    <text x="{annot_x:.1f}" y="{sy + 4:.1f}" '
                     f'fill="{color}">{sign}{sh:.0f} pp</text>\n')
        fh.write('  </g>\n')

        # Legend (top-right)
        leg_y = 90
        leg_x = svg_w - margin_r
        fh.write('  <g id="chrome_legend" font-family="sans-serif" '
                 f'font-size="10" fill="{TEXT_COLOR}">\n')
        # Near
        fh.write(f'    <circle cx="{leg_x - 130}" cy="{leg_y}" '
                 f'r="6" fill="{NEAR_COLOR}" '
                 f'stroke="white" stroke-width="1.4"/>\n')
        fh.write(f'    <text x="{leg_x - 120}" y="{leg_y + 4}">'
                 f'Within {int(buffer_ft):,} ft of {feature_label}</text>\n')
        # Far
        fh.write(f'    <circle cx="{leg_x - 320}" cy="{leg_y + 18}" '
                 f'r="6" fill="{FAR_COLOR}" '
                 f'stroke="white" stroke-width="1.4"/>\n')
        fh.write(f'    <text x="{leg_x - 310}" y="{leg_y + 22}">'
                 f'Far from {feature_label}</text>\n')
        fh.write('  </g>\n')

        fh.write('</svg>\n')


# ─── Driver ──────────────────────────────────────────────────────────────────

def compute(source_dir, derived_dir,
            transit_buffer_ft=DEFAULT_TRANSIT_BUFFER_FT,
            parks_buffer_ft=DEFAULT_PARKS_BUFFER_FT):
    sad_id = source_dir.parent.name
    print(f"Feature program proximity for {sad_id}...")

    # ── Load SAD boundary ─────────────────────────────────────────
    boundary_path = source_dir / 'sad_boundary.geojson'
    if not boundary_path.exists():
        sys.exit(f"missing {boundary_path}")
    sad_gdf = gpd.read_file(boundary_path)
    metric_crs = sad_gdf.estimate_utm_crs()
    sad_gdf_m = sad_gdf.to_crs(metric_crs)
    sad_geom = sad_gdf_m.geometry.iloc[0]

    # ── Load POIs and clip to SAD ─────────────────────────────────
    places_path = source_dir / 'rod_places.geojson'
    if not places_path.exists():
        print(f"  no POIs at {places_path} -- skipping")
        return
    places = gpd.read_file(places_path)
    if 'rossetti_category' not in places.columns:
        print("  ROD POIs missing rossetti_category column -- skipping")
        return
    places_m = places.to_crs(metric_crs)
    places_in_sad = gpd.clip(places_m, sad_geom)
    n_total = len(places_in_sad)
    print(f"  {n_total} POIs in SAD")

    out_dir = derived_dir / 'feature_program_proximity'
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        'sad_id':    sad_id,
        'n_pois':    int(n_total),
        'analyses':  {},
    }

    # ── Analysis 1: Transit proximity ─────────────────────────────
    transit_path = derived_dir / 'transit' / 'transit_stations.geojson'
    transit_buffer_m = transit_buffer_ft * M_PER_FT
    if transit_path.exists():
        transit = gpd.read_file(transit_path)
        if not transit.empty:
            transit_m = transit.to_crs(metric_crs)
            # Use only stations within or near the SAD (within buffer of SAD boundary)
            near_sad = transit_m[transit_m.geometry.apply(
                lambda p: sad_geom.distance(p) <= transit_buffer_m)]
            n_stations = len(near_sad)
            if n_stations > 0:
                stations_buffer = unary_union(
                    near_sad.geometry.buffer(transit_buffer_m).tolist())
                shift_data = compute_proximity_shift(
                    places_in_sad, stations_buffer)
            else:
                shift_data = None
        else:
            n_stations = 0
            shift_data = None
    else:
        print(f"  M13 transit output not found at {transit_path} -- "
              f"transit analysis skipped")
        n_stations = 0
        shift_data = None

    title = 'Program mix shift -- transit proximity'
    if shift_data and shift_data.get('rows'):
        sub = (f"{shift_data['n_near']} POIs within "
               f"{int(transit_buffer_ft):,} ft of "
               f"{n_stations} transit station"
               + ('s' if n_stations != 1 else '') +
               f"  |  {shift_data['n_far']} POIs farther in SAD")
    else:
        sub = (f"{n_stations} transit station"
               + ('s' if n_stations != 1 else '') +
               f" within {int(transit_buffer_ft):,} ft of SAD")
    render_proximity_chart_png(shift_data, title, sub,
                                 transit_buffer_ft, n_stations,
                                 'transit',
                                 out_dir / 'transit_proximity.png')
    render_proximity_chart_svg(shift_data, title, sub,
                                 transit_buffer_ft, n_stations,
                                 'transit',
                                 out_dir / 'transit_proximity.svg')
    print(f"  wrote transit_proximity.png/.svg "
          f"({n_stations} stations, "
          f"near/far = {shift_data['n_near'] if shift_data else 0}/"
          f"{shift_data['n_far'] if shift_data else 0})")
    summary['analyses']['transit'] = {
        'buffer_ft':    transit_buffer_ft,
        'n_features':   int(n_stations),
        'n_near':       int(shift_data['n_near']) if shift_data else 0,
        'n_far':        int(shift_data['n_far']) if shift_data else 0,
        'rows':         shift_data.get('rows') if shift_data else None,
    }

    # ── Analysis 2: Parks proximity ───────────────────────────────
    parks_path = source_dir / 'parks.geojson'
    parks_buffer_m = parks_buffer_ft * M_PER_FT
    if parks_path.exists():
        parks = gpd.read_file(parks_path)
        if not parks.empty:
            parks_m = parks.to_crs(metric_crs)
            parks_clipped = gpd.clip(parks_m, sad_geom)
            n_parks = len(parks_clipped)
            if n_parks > 0:
                parks_buffer = unary_union(
                    parks_clipped.geometry.buffer(parks_buffer_m).tolist())
                shift_data_p = compute_proximity_shift(
                    places_in_sad, parks_buffer)
            else:
                shift_data_p = None
        else:
            n_parks = 0
            shift_data_p = None
    else:
        n_parks = 0
        shift_data_p = None

    title = 'Program mix shift -- park / open-space proximity'
    if shift_data_p and shift_data_p.get('rows'):
        sub = (f"{shift_data_p['n_near']} POIs within "
               f"{int(parks_buffer_ft):,} ft of "
               f"{n_parks} park"
               + ('s' if n_parks != 1 else '') +
               f"  |  {shift_data_p['n_far']} POIs farther in SAD")
    else:
        sub = (f"{n_parks} park"
               + ('s' if n_parks != 1 else '') + " in SAD")
    render_proximity_chart_png(shift_data_p, title, sub,
                                 parks_buffer_ft, n_parks,
                                 'park / open space',
                                 out_dir / 'parks_proximity.png')
    render_proximity_chart_svg(shift_data_p, title, sub,
                                 parks_buffer_ft, n_parks,
                                 'park / open space',
                                 out_dir / 'parks_proximity.svg')
    print(f"  wrote parks_proximity.png/.svg "
          f"({n_parks} parks, "
          f"near/far = {shift_data_p['n_near'] if shift_data_p else 0}/"
          f"{shift_data_p['n_far'] if shift_data_p else 0})")
    summary['analyses']['parks'] = {
        'buffer_ft':    parks_buffer_ft,
        'n_features':   int(n_parks),
        'n_near':       int(shift_data_p['n_near']) if shift_data_p else 0,
        'n_far':        int(shift_data_p['n_far']) if shift_data_p else 0,
        'rows':         shift_data_p.get('rows') if shift_data_p else None,
    }

    summary_path = out_dir / 'feature_program_proximity_summary.json'
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"  wrote {summary_path.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--derived', type=Path, required=True)
    ap.add_argument('--transit-buffer-ft', type=float,
                    default=DEFAULT_TRANSIT_BUFFER_FT,
                    help=f'Transit station buffer in feet '
                         f'(default {DEFAULT_TRANSIT_BUFFER_FT}, '
                         '= 5-min walk at 3 mph)')
    ap.add_argument('--parks-buffer-ft', type=float,
                    default=DEFAULT_PARKS_BUFFER_FT,
                    help=f'Park / open space buffer in feet '
                         f'(default {DEFAULT_PARKS_BUFFER_FT})')
    args = ap.parse_args()
    compute(args.source.resolve(), args.derived.resolve(),
            transit_buffer_ft=args.transit_buffer_ft,
            parks_buffer_ft=args.parks_buffer_ft)


if __name__ == '__main__':
    main()
