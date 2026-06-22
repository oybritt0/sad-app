"""
module_09_thematic_scatters.py

Produces multiple 2D scatter plots placing SADs on interpretable axis pairs:
morphology, program, scale, anchor structure, axiality, demographics.

Each chart is a simple "where does this SAD sit on these two specific
dimensions?" — far more legible to non-specialists than PCA's abstract
principal components.

INPUTS
  Option A (preferred): --embedding-dir <path>
    Reads vibe_embedding_summary.json from an existing M8 output folder.
  Option B: --data-dir <path> --sads <id1> <id2> ...
    Runs the M8 feature extraction fresh.

OUTPUTS (in <output-dir>/scatters/)
  morphology_grid.{png,svg}      Coverage×density, median area×max anchor,
                                  axiality×cluster diversity
  program_grid.{png,svg}         Office×retail, sport×hotel, open-space×coverage
  anchor_grid.{png,svg}          Anchor count×concentration, anchor count×area
  scale_grid.{png,svg}           SAD area×building count, total built area×coverage
  cross_domain_grid.{png,svg}    Coverage×office %, axiality×coverage,
                                  anchor concentration×program diversity
  demographics_grid.{png,svg}    Pop density×income, bachelor's×diversity,
                                  pop density×home value  (if census available)
  composite_overview.{png,svg}   All charts in one large grid
  individual SVGs per chart in scatters/individual/

USAGE
  python module_09_thematic_scatters.py --embedding-dir \\
      <path-to-data/_comparisons/embedding_YYYYMMDD_HHMM/>
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


SAD_PALETTE = ['#1B2845', '#D97757', '#5C8B89', '#7C5E9C', '#D4A93A', '#6A4E7C']


# ─── Chart definitions (data-driven) ───────────────────────────────────
# Each chart: x feature, y feature, axis labels, title, optional units

CHART_DEFS = {
    'morphology': [
        {
            'x': 'morph_coverage', 'y': 'morph_density_per_km2',
            'x_label': 'Built coverage (building area / SAD area)',
            'y_label': 'Building density (buildings per km²)',
            'title': 'Density vs coverage',
            'subtitle': 'How packed is the fabric?',
        },
        {
            'x': 'morph_median_area_m2', 'y': 'anchor_max_area_ratio',
            'x_label': 'Median building area (m²)',
            'y_label': 'Max anchor as fraction of total built area',
            'title': 'Typical vs anchor scale',
            'subtitle': 'Many small buildings or a few giants?',
            'x_log': True,
        },
        {
            'x': 'morph_orientation_R', 'y': 'morph_cluster_diversity',
            'x_label': 'Axiality (Rayleigh R; 0 = random, 1 = perfectly aligned)',
            'y_label': 'Cluster diversity (Shannon entropy of form clusters)',
            'title': 'Planned alignment vs morphological variety',
            'subtitle': 'Grid logic vs organic form',
        },
    ],
    'program': [
        {
            'x': 'prog_pct_office', 'y': 'prog_pct_retail_food_entertainment',
            'x_label': 'Office (% of SAD-interior POIs)',
            'y_label': 'Retail / F&B / entertainment (%)',
            'title': 'Corporate vs consumer character',
            'subtitle': 'What do people DO inside this district?',
        },
        {
            'x': 'prog_pct_sport', 'y': 'prog_pct_hotel',
            'x_label': 'Sport (%)',
            'y_label': 'Hotel (%)',
            'title': 'Venue-driven character',
            'subtitle': 'Tourism + spectacle infrastructure',
        },
        {
            'x': 'prog_pct_open_space', 'y': 'morph_coverage',
            'x_label': 'Open space (%)',
            'y_label': 'Built coverage',
            'title': 'Open space vs built density',
            'subtitle': 'Inverse relationship?',
        },
    ],
    'anchor': [
        {
            'x': 'anchor_count_inside', 'y': 'anchor_max_area_ratio',
            'x_label': 'Number of anchors inside SAD',
            'y_label': 'Max anchor as fraction of total built area',
            'title': 'Anchor count vs anchor dominance',
            'subtitle': 'One mega-anchor or many distributed ones?',
        },
        {
            'x': 'anchor_size_concentration', 'y': 'prog_diversity',
            'x_label': 'Anchor size concentration (top-1 / top-3 area)',
            'y_label': 'Program diversity (Shannon entropy of POIs)',
            'title': 'Anchor dominance vs program mix',
            'subtitle': 'Does a single anchor narrow the program?',
        },
    ],
    'scale': [
        {
            'x': 'sad_area_km2', 'y': 'buildings_inside_count',
            'x_label': 'SAD area (km²)',
            'y_label': 'Buildings inside SAD',
            'title': 'Scale of the district',
            'subtitle': 'Geographic vs building count',
        },
    ],
    'cross_domain': [
        {
            'x': 'morph_coverage', 'y': 'prog_pct_office',
            'x_label': 'Built coverage',
            'y_label': 'Office (% of POIs)',
            'title': 'Density × corporate concentration',
            'subtitle': 'Are office-heavy SADs always dense?',
        },
        {
            'x': 'morph_orientation_R', 'y': 'morph_coverage',
            'x_label': 'Axiality (orientation alignment)',
            'y_label': 'Built coverage',
            'title': 'Master plan signature',
            'subtitle': 'Planned districts tend toward aligned + dense',
        },
        {
            'x': 'morph_area_log_skew', 'y': 'anchor_max_area_ratio',
            'x_label': 'Building-area skew (heavy tail = anchors)',
            'y_label': 'Max anchor / total built area',
            'title': 'Anchor signature, two readings',
            'subtitle': 'Does skew predict anchor dominance?',
        },
    ],
    'demographics': [
        {
            'x': 'demo_population_density', 'y': 'demo_median_hh_income',
            'x_label': 'Population density (people/km²)',
            'y_label': 'Median household income ($)',
            'title': 'Density × wealth',
            'subtitle': 'Who lives near the district?',
            'demo_only': True,
        },
        {
            'x': 'demo_pct_bachelors_plus', 'y': 'demo_racial_diversity',
            'x_label': "Bachelor's degree or higher (%)",
            'y_label': 'Racial diversity (Shannon entropy)',
            'title': 'Education × racial diversity',
            'subtitle': 'Demographic complexity',
            'demo_only': True,
        },
        {
            'x': 'demo_population_density', 'y': 'demo_median_home_value',
            'x_label': 'Population density (people/km²)',
            'y_label': 'Median home value ($)',
            'title': 'Density × home values',
            'subtitle': 'Real estate market signal',
            'demo_only': True,
        },
    ],
}


# ─── Helpers ───────────────────────────────────────────────────────────

def load_from_embedding_summary(emb_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Read vibe_embedding_summary.json and return (feature_df, name_lookup)."""
    summary_path = emb_dir / 'vibe_embedding_summary.json'
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing {summary_path}. Run M8 first.")
    
    summary = json.loads(summary_path.read_text())
    
    # Build name lookup
    name_lookup = {s['sad_id']: s['name'] for s in summary['sads']}
    
    # Build raw feature matrix from the records
    df = pd.DataFrame(summary['feature_matrix']).set_index('sad_id')
    
    # Also pull sad_area_km2 and buildings_inside_count from the sad metadata
    # (these are non-feature columns but useful for scale charts)
    meta = {s['sad_id']: s for s in summary['sads']}
    df['sad_area_km2'] = df.index.map(lambda sid: meta[sid].get('sad_area_km2', 0))
    df['buildings_inside_count'] = df.index.map(
        lambda sid: meta[sid].get('buildings_inside_count', 0))
    
    return df, name_lookup


def has_demographic_features(df: pd.DataFrame) -> bool:
    return any(c.startswith('demo_') for c in df.columns)


def build_palette(n: int):
    """A set of distinct colors for up to ~40 districts."""
    base = list(plt.cm.tab20.colors) + list(plt.cm.tab20b.colors)
    return [base[i % len(base)] for i in range(n)]


def _is_dark(rgb) -> bool:
    r, g, b = rgb[:3]
    return (0.299 * r + 0.587 * g + 0.114 * b) < 0.58


def build_identity(df: pd.DataFrame, name_lookup: dict):
    """Stable number + color per district, ordered alphabetically by name.
    The SAME number/color is used on every chart so districts can be tracked
    across the whole set via the shared legend."""
    ordering = sorted(df.index, key=lambda sid: str(name_lookup.get(sid, sid)).lower())
    palette = build_palette(len(ordering))
    number = {sid: i + 1 for i, sid in enumerate(ordering)}
    color = {sid: palette[i] for i, sid in enumerate(ordering)}
    return ordering, number, color


def make_legend_handles(identity, name_lookup):
    ordering, number, color = identity
    handles = [Line2D([0], [0], marker='o', linestyle='none',
                      markerfacecolor=color[sid], markeredgecolor='white',
                      markeredgewidth=0.6, markersize=7) for sid in ordering]
    labels = [f"{number[sid]}  {name_lookup.get(sid, sid)}" for sid in ordering]
    return handles, labels


# ─── Rendering ─────────────────────────────────────────────────────────

def render_scatter(ax, df: pd.DataFrame, identity, chart_def: dict,
                   point_size: int = 150, number_size: float = 6.5,
                   axis_label_size: int = 11, tick_size: int = 9,
                   show_numbers: bool = True):
    """Render a single scatter chart: small numbered points, no inline names."""
    ordering, number, color = identity
    xkey, ykey = chart_def['x'], chart_def['y']

    if xkey not in df.columns or ykey not in df.columns:
        ax.text(0.5, 0.5, f'data missing\n({xkey} or {ykey})',
                ha='center', va='center', transform=ax.transAxes,
                fontsize=9, color='#aaa')
        ax.set_xticks([]); ax.set_yticks([])
        return

    for sid in ordering:
        if sid not in df.index:
            continue
        row = df.loc[sid]
        x, y = row[xkey], row[ykey]
        if pd.isna(x) or pd.isna(y):
            continue
        c = color[sid]
        ax.scatter(x, y, s=point_size, facecolor=[c], edgecolors='white',
                   linewidth=1.1, zorder=3, alpha=0.95)
        if show_numbers:
            ax.text(x, y, str(number[sid]), fontsize=number_size,
                    ha='center', va='center', zorder=4, fontweight='bold',
                    color='white' if _is_dark(c) else '#15203a')

    if chart_def.get('x_log'):
        ax.set_xscale('log')
    if chart_def.get('y_log'):
        ax.set_yscale('log')

    ax.set_xlabel(chart_def['x_label'], fontsize=axis_label_size)
    ax.set_ylabel(chart_def['y_label'], fontsize=axis_label_size)

    # Tight padding so points spread across the plot instead of bunching center
    xs = pd.to_numeric(df[xkey], errors='coerce').dropna().values
    ys = pd.to_numeric(df[ykey], errors='coerce').dropna().values
    if len(xs) and not chart_def.get('x_log'):
        xr = xs.max() - xs.min()
        px = max(xr * 0.06, abs(xs.max()) * 0.04, 1e-6)
        ax.set_xlim(xs.min() - px, xs.max() + px)
    if len(ys) and not chart_def.get('y_log'):
        yr = ys.max() - ys.min()
        py = max(yr * 0.06, abs(ys.max()) * 0.04, 1e-6)
        ax.set_ylim(ys.min() - py, ys.max() + py)

    ax.grid(alpha=0.25, linestyle='--', linewidth=0.5)
    ax.set_axisbelow(True)
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    ax.tick_params(labelsize=tick_size)


def render_chart_group(charts: list, df: pd.DataFrame, identity,
                       name_lookup: dict, group_name: str,
                       out_png: Path, out_svg: Path):
    """A row of charts for one theme, with one shared numbered legend below."""
    n = len(charts)
    if n == 0:
        return
    fig, axes = plt.subplots(1, n, figsize=(6.3 * n, 7.6))
    if n == 1:
        axes = [axes]

    for ax, chart in zip(axes, charts):
        render_scatter(ax, df, identity, chart, point_size=120, number_size=6,
                       axis_label_size=11, tick_size=9)
        ax.text(0, 1.075, chart['title'], transform=ax.transAxes,
                fontsize=12.5, fontweight='bold', color='#1B2845',
                ha='left', va='bottom')
        if 'subtitle' in chart:
            ax.text(0, 1.02, chart['subtitle'], transform=ax.transAxes,
                    fontsize=9.5, color='#888', style='italic',
                    ha='left', va='bottom')

    fig.suptitle(f'SAD positioning — {group_name}', fontsize=15, y=1.0,
                 x=0.01, ha='left', fontweight='bold', color='#1B2845')

    handles, labels = make_legend_handles(identity, name_lookup)
    ncol = min(6, max(3, (len(labels) + 6) // 7))
    fig.legend(handles, labels, loc='lower center', ncol=ncol, fontsize=8,
               frameon=False, bbox_to_anchor=(0.5, -0.015),
               handletextpad=0.4, columnspacing=1.2, labelspacing=0.4)

    fig.tight_layout(rect=[0, 0.14, 1, 0.93])
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def render_composite_overview(all_charts: list, df: pd.DataFrame, identity,
                              name_lookup: dict, out_png: Path, out_svg: Path,
                              cols: int = 3):
    """Big grid of every chart for the deck, with one shared legend below."""
    n = len(all_charts)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 5.6, rows * 5.4))
    axes_flat = axes.flatten() if rows * cols > 1 else [axes]

    for i, (group, chart) in enumerate(all_charts):
        ax = axes_flat[i]
        render_scatter(ax, df, identity, chart, point_size=85, number_size=5,
                       axis_label_size=9, tick_size=7.5)
        ax.text(0, 1.05, chart['title'], transform=ax.transAxes,
                fontsize=10.5, fontweight='bold', color='#1B2845',
                ha='left', va='bottom')
        ax.text(1.0, 1.05, group.upper(), transform=ax.transAxes,
                fontsize=7.5, color='#D97757', ha='right', va='bottom',
                fontweight='bold')

    for j in range(n, len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle('SAD positioning — thematic overview', fontsize=18, y=1.0,
                 x=0.01, ha='left', fontweight='bold', color='#1B2845')

    handles, labels = make_legend_handles(identity, name_lookup)
    ncol = min(7, max(4, (len(labels) + 5) // 6))
    fig.legend(handles, labels, loc='lower center', ncol=ncol, fontsize=8,
               frameon=False, bbox_to_anchor=(0.5, -0.01),
               handletextpad=0.4, columnspacing=1.2, labelspacing=0.4)

    fig.tight_layout(rect=[0, 0.07, 1, 0.97])
    fig.subplots_adjust(hspace=0.42, wspace=0.28)
    fig.savefig(out_png, dpi=160, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def render_individual(charts: list, df: pd.DataFrame, identity,
                      name_lookup: dict, out_dir: Path):
    """One SVG + PNG per chart, numbered points with the legend on the right."""
    out_dir.mkdir(parents=True, exist_ok=True)
    handles, labels = make_legend_handles(identity, name_lookup)
    ncol_leg = 1 if len(labels) <= 18 else 2

    for group, chart in charts:
        fig, ax = plt.subplots(figsize=(12, 6.8))
        render_scatter(ax, df, identity, chart, point_size=165, number_size=7,
                       axis_label_size=11.5, tick_size=9.5)
        ax.text(0, 1.095, chart['title'], transform=ax.transAxes,
                fontsize=15, fontweight='bold', color='#1B2845',
                ha='left', va='bottom')
        if 'subtitle' in chart:
            ax.text(0, 1.035, chart['subtitle'], transform=ax.transAxes,
                    fontsize=11, color='#888', style='italic',
                    ha='left', va='bottom')
        ax.text(1.0, 1.095, group.upper(), transform=ax.transAxes,
                fontsize=9, color='#D97757', fontweight='bold',
                ha='right', va='bottom')

        leg = ax.legend(handles, labels, loc='center left',
                        bbox_to_anchor=(1.02, 0.5), ncol=ncol_leg, fontsize=8,
                        frameon=False, handletextpad=0.4, columnspacing=1.1,
                        labelspacing=0.4, title='Districts', title_fontsize=9.5)
        leg._legend_box.align = 'left'

        fname = f"{group}_{chart['x']}_vs_{chart['y']}"
        fname = fname.replace('morph_', '').replace('prog_pct_', '')\
                     .replace('prog_', 'prog').replace('demo_', 'demo')\
                     .replace('anchor_', 'anc')
        fig.tight_layout()
        fig.savefig(out_dir / f'{fname}.png', dpi=170, bbox_inches='tight')
        fig.savefig(out_dir / f'{fname}.svg', format='svg', bbox_inches='tight')
        plt.close(fig)


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Thematic 2D scatter plots placing SADs on "
                    "interpretable axis pairs")
    parser.add_argument('--embedding-dir', type=Path, default=None,
                        help="Existing M8 output dir to read features from")
    parser.add_argument('--data-dir', type=Path, default=None,
                        help="Data root (used with --sads to recompute fresh)")
    parser.add_argument('--sads', nargs='+', default=None,
                        help="SAD IDs (used with --data-dir)")
    parser.add_argument('--out', type=Path, default=None,
                        help="Output dir (default: <embedding-dir>/scatters/)")
    args = parser.parse_args()
    
    # Load data
    if args.embedding_dir:
        print(f"Loading from {args.embedding_dir}")
        df, name_lookup = load_from_embedding_summary(args.embedding_dir)
        default_out = args.embedding_dir / 'scatters'
    elif args.data_dir and args.sads:
        # Lazy import so module_08 isn't strictly required if user always
        # uses --embedding-dir
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from module_08_district_embedding import gather_sad_features, \
            build_feature_matrix
        
        print(f"Computing features fresh for {len(args.sads)} SADs...")
        sads_data = []
        for sad_id in args.sads:
            sd = gather_sad_features(args.data_dir, sad_id,
                                       include_demographics=True)
            sads_data.append(sd)
            print(f"  {sad_id}: {sd['buildings_inside_count']} buildings inside")
        df, name_lookup = build_feature_matrix(sads_data)
        # Attach metadata columns for scale charts
        df['sad_area_km2'] = [sd['sad_area_km2'] for sd in sads_data]
        df['buildings_inside_count'] = [sd['buildings_inside_count']
                                          for sd in sads_data]
        
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        default_out = args.data_dir / '_comparisons' / \
                       f'embedding_{ts}' / 'scatters'
    else:
        raise SystemExit("Provide either --embedding-dir OR "
                          "(--data-dir AND --sads).")
    
    out_dir = args.out if args.out else default_out
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting to {out_dir}")
    
    identity = build_identity(df, name_lookup)
    has_demo = has_demographic_features(df)
    print(f"Demographic features available: {has_demo}")
    
    # Filter chart definitions to only those with available features
    def filter_charts(chart_list):
        out = []
        for chart in chart_list:
            if chart.get('demo_only') and not has_demo:
                continue
            if chart['x'] not in df.columns or chart['y'] not in df.columns:
                continue
            out.append(chart)
        return out
    
    # Render each thematic group
    all_charts = []
    for group, chart_list in CHART_DEFS.items():
        filtered = filter_charts(chart_list)
        if not filtered:
            continue
        
        for chart in filtered:
            all_charts.append((group, chart))
        
        render_chart_group(
            filtered, df, identity, name_lookup, group,
            out_dir / f'{group}_grid.png',
            out_dir / f'{group}_grid.svg',
        )
        print(f"  [OK] {group}_grid ({len(filtered)} charts)")
    
    # Composite overview
    render_composite_overview(
        all_charts, df, identity, name_lookup,
        out_dir / 'composite_overview.png',
        out_dir / 'composite_overview.svg',
        cols=3,
    )
    print(f"  [OK] composite_overview ({len(all_charts)} charts)")
    
    # Individual charts
    individual_dir = out_dir / 'individual'
    render_individual(all_charts, df, identity, name_lookup, individual_dir)
    print(f"  [OK] individual/ ({len(all_charts)} chart SVGs/PNGs)")
    
    print(f"\nAll outputs in: {out_dir}")
    print(f"Composite: {out_dir / 'composite_overview.svg'}")


if __name__ == '__main__':
    main()
