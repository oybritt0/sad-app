"""
module_07_cross_sad_compare.py

Cross-SAD comparison: takes 2+ SADs from the data directory and produces
analytical visualizations that put them side-by-side. The moment where
the per-SAD pipeline becomes a typology framework.

Outputs go to data/_comparisons/<timestamp>/ to keep them separate from
the per-SAD derived folders.

VISUALIZATIONS PRODUCED
  1. morphology_radar.{png,svg}
       Normalized radar chart with one polygon per SAD across key
       morphological metrics (coverage, fractal dim, components, median
       area, tree skew, anchor count).
  2. program_signature_compare.{png,svg}
       Grouped bars showing each SAD's Rossetti category percentages
       side-by-side. Shows the programmatic "DNA" of each district.
  3. boundary_effect_compare.{png,svg}
       Grouped bars showing interior-vs-exterior percentage-point
       differences per Rossetti category, per SAD. Shows which
       categories define each SAD against its surroundings.
  4. anchor_summary.{png,svg}
       Table-style figure summarizing anchor counts, median areas,
       dominant programs, and inferred archetypes per SAD.
  5. comparison_summary.json
       Numerical synthesis of all the above for downstream analysis.

USAGE
  python module_07_cross_sad_compare.py --data-dir <path> \\
      --sads <sad_id_1> <sad_id_2> ... [--out <output_dir>]
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D


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

# Distinct SAD colors for radar/bar plots (chosen for contrast, not by typology)
SAD_PALETTE = ['#0b6b8a', '#c44536', '#3d8a4f', '#7c5e9c', '#d4a93a', '#6a4e7c']


def safe_load_json(path: Path):
    """Returns dict or None if file missing/invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def gather_sad_data(data_dir: Path, sad_id: str) -> dict:
    """Read all available analytical outputs for one SAD."""
    derived = data_dir / sad_id / 'derived'
    
    profile = safe_load_json(derived / 'district_profile.json') or {}
    cv = safe_load_json(derived / 'cv_metrics.json') or {}
    program = safe_load_json(derived / 'program_summary.json') or {}
    crosstab = safe_load_json(derived / 'cluster_program_crosstab.json') or {}
    polar = safe_load_json(derived / 'anchor_polar_plots.json') or {}
    interior_ext = safe_load_json(derived / 'interior_exterior_signature.json') or {}
    connectivity = safe_load_json(derived / 'anchor_connectivity.json') or {}
    phylogeny = safe_load_json(derived / 'building_phylogeny.json') or {}
    manifest = safe_load_json(derived / 'manifest.json') or {}
    
    # Display name
    name = profile.get('sad_name', manifest.get('sad_name', sad_id))
    
    # Anchor metrics from polar plots (if available)
    anchors = polar.get('anchors', [])
    anchor_count = len(anchors)
    anchor_areas = [a.get('area_m2', 0) for a in anchors if a.get('area_m2', 0) > 0]
    median_anchor_area = float(np.median(anchor_areas)) if anchor_areas else 0
    max_anchor_area = max(anchor_areas) if anchor_areas else 0
    
    # Phylogeny / clustering stats
    cluster_sizes = phylogeny.get('cluster_sizes', {})
    tree_skew = phylogeny.get('tree_shape_skew', None)
    
    # Coverage etc from cv_metrics.json
    field = cv.get('field', {})
    
    return {
        'sad_id': sad_id,
        'name': name,
        'typology': profile.get('typology') or manifest.get('typology', 'unknown'),
        'anchor_venue': profile.get('anchor_venue') or
                         manifest.get('anchor_venue', 'unknown'),
        'building_count': int(cv.get('building_count',
                                       manifest.get('building_count', 0))),
        'median_area_m2': float(cv.get('median_area_m2', 0)),
        'coverage': float(field.get('coverage', 0)),
        'fractal_dimension': float(field.get('fractal_dimension', 0)),
        'component_count': int(field.get('component_count', 0)),
        'tree_skew': float(tree_skew) if tree_skew else None,
        'anchor_count': anchor_count,
        'median_anchor_area_m2': median_anchor_area,
        'max_anchor_area_m2': max_anchor_area,
        'rossetti_percentages_all':
            program.get('rossetti_percentages', {}),
        'interior_rossetti':
            interior_ext.get('categories', []),  # list of {category, interior_pct, exterior_pct, difference}
        'cluster_sizes': cluster_sizes,
        'anchors': anchors,
        'connectivity_edges': connectivity.get('edges', []),
    }


# â”€â”€â”€ Visualization 1: Morphology radar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def render_morphology_radar(all_sad_data: list[dict], out_png: Path, out_svg: Path):
    """Normalized radar chart across key morphological metrics."""
    # Metrics to compare (key, label, higher_is)
    metrics = [
        ('coverage', 'coverage'),
        ('fractal_dimension', 'fractal\ndim.'),
        ('component_count', 'components'),
        ('median_area_m2', 'median\narea (mÂ²)'),
        ('anchor_count', 'anchor\ncount'),
        ('max_anchor_area_m2', 'max\nanchor (mÂ²)'),
        ('building_count', 'total\nbuildings'),
    ]
    
    # Extract values, normalize each metric across SADs to [0, 1]
    matrix = []
    for sad in all_sad_data:
        row = [sad.get(k, 0) for k, _ in metrics]
        matrix.append(row)
    matrix = np.array(matrix, dtype=float)
    
    # Min-max normalize each column. Avoid division by zero.
    normed = np.zeros_like(matrix)
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        col_min, col_max = col.min(), col.max()
        rng = col_max - col_min
        if rng > 0:
            normed[:, j] = (col - col_min) / rng
        else:
            normed[:, j] = 0.5
    
    n_metrics = len(metrics)
    angles = np.linspace(0, 2 * np.pi, n_metrics, endpoint=False).tolist()
    angles_closed = angles + [angles[0]]
    
    fig, ax = plt.subplots(figsize=(9, 9), subplot_kw=dict(polar=True))
    
    for i, sad in enumerate(all_sad_data):
        vals = normed[i].tolist() + [normed[i][0]]
        color = SAD_PALETTE[i % len(SAD_PALETTE)]
        ax.plot(angles_closed, vals, color=color, linewidth=2.0,
                label=sad['name'])
        ax.fill(angles_closed, vals, color=color, alpha=0.13)
    
    # Add raw value labels at each spoke
    ax.set_xticks(angles)
    ax.set_xticklabels([m[1] for m in metrics], fontsize=10)
    ax.set_yticks([0.25, 0.5, 0.75])
    ax.set_yticklabels(['0.25', '0.50', '0.75'], fontsize=7, color='#888')
    ax.set_ylim(0, 1.05)
    ax.grid(linewidth=0.5, alpha=0.5)
    
    ax.legend(loc='upper right', bbox_to_anchor=(1.30, 1.10), fontsize=9,
              frameon=True)
    
    fig.suptitle(
        "Morphology profile: cross-SAD comparison\n"
        "(each metric min-max normalized across the SADs shown)",
        fontsize=11.5, y=0.97,
    )
    
    # Raw values table at the bottom
    fig.text(0.5, 0.02,
              _build_radar_value_table(all_sad_data, metrics),
              fontsize=8, family='monospace', ha='center', va='bottom',
              color='#333')
    
    fig.subplots_adjust(bottom=0.20, top=0.92)
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def _build_radar_value_table(all_sad_data, metrics):
    """Compact raw-value table to print beneath the radar."""
    headers = ['SAD'] + [m[1].replace('\n', ' ') for m in metrics]
    rows = [headers]
    for sad in all_sad_data:
        row = [sad['name'][:18]]
        for k, _ in metrics:
            v = sad.get(k, 0)
            if isinstance(v, float):
                if v < 10:
                    row.append(f"{v:.3f}")
                elif v < 1000:
                    row.append(f"{v:.0f}")
                else:
                    row.append(f"{v:,.0f}")
            else:
                row.append(f"{v:,}")
        rows.append(row)
    # Format as monospace columns
    col_widths = [max(len(str(row[c])) for row in rows)
                   for c in range(len(headers))]
    lines = []
    for ri, row in enumerate(rows):
        line = '  '.join(str(cell).rjust(col_widths[ci])
                          for ci, cell in enumerate(row))
        lines.append(line)
        if ri == 0:
            lines.append('-' * len(line))
    return '\n'.join(lines)


# â”€â”€â”€ Visualization 2: Program signature comparison â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def render_program_signature_compare(all_sad_data, out_png, out_svg):
    """Grouped bars showing each SAD's Rossetti percentages side-by-side."""
    n_sads = len(all_sad_data)
    n_cats = len(ROSSETTI_ORDER)
    
    fig, ax = plt.subplots(figsize=(13, 6.5))
    
    bar_w = 0.8 / n_sads
    x = np.arange(n_cats)
    
    for i, sad in enumerate(all_sad_data):
        pcts = sad.get('rossetti_percentages_all', {})
        vals = [pcts.get(c, 0) for c in ROSSETTI_ORDER]
        offset = (i - (n_sads - 1) / 2) * bar_w
        bars = ax.bar(x + offset, vals, bar_w,
                       color=SAD_PALETTE[i % len(SAD_PALETTE)],
                       edgecolor='white', linewidth=0.5,
                       label=sad['name'])
        # Label nonzero bars
        for bx, bv in zip(x + offset, vals):
            if bv > 1.5:
                ax.text(bx, bv + 0.6, f'{bv:.0f}', ha='center', va='bottom',
                         fontsize=7, color='#444')
    
    ax.set_xticks(x)
    ax.set_xticklabels(
        [c.replace('_', ' ').replace('retail food entertainment',
                                       'retail/F&B/ent')
         for c in ROSSETTI_ORDER],
        rotation=20, ha='right', fontsize=10,
    )
    ax.set_ylabel('% of POIs in this category (all canvas POIs)', fontsize=10)
    ax.set_title("Program signature: cross-SAD comparison",
                  fontsize=12, loc='left', pad=10)
    ax.legend(loc='upper right', fontsize=9, frameon=True)
    ax.grid(axis='y', linestyle='--', alpha=0.4, linewidth=0.5)
    ax.set_axisbelow(True)
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    
    fig.tight_layout()
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


# â”€â”€â”€ Visualization 3: Boundary effect comparison â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def render_boundary_effect_compare(all_sad_data, out_png, out_svg):
    """Grouped bars showing interior-vs-exterior percentage-point diff per SAD."""
    n_sads = len(all_sad_data)
    n_cats = len(ROSSETTI_ORDER)
    
    fig, ax = plt.subplots(figsize=(13, 6.5))
    
    bar_w = 0.8 / n_sads
    x = np.arange(n_cats)
    
    has_data_for_any_sad = False
    for i, sad in enumerate(all_sad_data):
        ie = {c['category']: c['difference']
              for c in sad.get('interior_rossetti', [])}
        if not ie:
            continue
        has_data_for_any_sad = True
        vals = [ie.get(c, 0) for c in ROSSETTI_ORDER]
        offset = (i - (n_sads - 1) / 2) * bar_w
        ax.bar(x + offset, vals, bar_w,
                color=SAD_PALETTE[i % len(SAD_PALETTE)],
                edgecolor='white', linewidth=0.5,
                label=sad['name'])
        # Annotate substantial differences
        for bx, bv in zip(x + offset, vals):
            if abs(bv) > 3.0:
                offset_y = 0.6 if bv > 0 else -1.2
                ax.text(bx, bv + offset_y, f'{bv:+.1f}',
                         ha='center', va='center', fontsize=7,
                         color='#222', fontweight='bold')
    
    if not has_data_for_any_sad:
        ax.text(0.5, 0.5,
                 "No interior/exterior comparison data available.\n"
                 "Run 06c v2 (interior vs exterior signature) for each SAD first.",
                 transform=ax.transAxes, ha='center', va='center',
                 fontsize=11, color='#666')
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        ax.axhline(0, color='#222', linewidth=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(
            [c.replace('_', ' ').replace('retail food entertainment',
                                          'retail/F&B/ent')
             for c in ROSSETTI_ORDER],
            rotation=20, ha='right', fontsize=10,
        )
        ax.set_ylabel('interior âˆ’ exterior (percentage points)', fontsize=10)
        ax.set_title(
            "Boundary effect: what distinguishes each SAD's interior from its surroundings",
            fontsize=12, loc='left', pad=10,
        )
        ax.legend(loc='upper right', fontsize=9, frameon=True)
        ax.grid(axis='y', linestyle='--', alpha=0.4, linewidth=0.5)
        ax.set_axisbelow(True)
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    
    fig.tight_layout()
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


# â”€â”€â”€ Visualization 4: Anchor summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def render_anchor_summary(all_sad_data, out_png, out_svg):
    """Side-by-side anchor inventory + auto-classified archetypes."""
    n_sads = len(all_sad_data)
    
    fig, axes = plt.subplots(1, n_sads, figsize=(5.5 * n_sads, 8.5))
    if n_sads == 1:
        axes = [axes]
    
    for i, sad in enumerate(all_sad_data):
        ax = axes[i]
        ax.axis('off')
        anchors = sad.get('anchors', [])
        
        title = f"{sad['name']}\n"
        title += f"anchor venue: {sad['anchor_venue']}\n"
        title += f"typology: {sad['typology']}\n"
        title += f"{sad['anchor_count']} anchors detected"
        ax.text(0.5, 0.96, title,
                transform=ax.transAxes, ha='center', va='top',
                fontsize=10, fontweight='bold',
                color=SAD_PALETTE[i % len(SAD_PALETTE)])
        
        y = 0.78
        for a in anchors[:10]:  # cap at 10 per SAD for readability
            name = str(a.get('name') or a.get('building_id', '?'))
            area = a.get('area_m2', 0)
            cells = a.get('cells', [])
            
            # Classify archetype from polar cell pattern
            archetype = classify_anchor_archetype(cells)
            
            ax.text(0.05, y, f"â€¢ {name[:30]}",
                    transform=ax.transAxes, fontsize=9,
                    color='#222', fontweight='bold')
            ax.text(0.05, y - 0.025, f"   {area:,.0f} mÂ² | {archetype}",
                    transform=ax.transAxes, fontsize=8,
                    color='#666', fontstyle='italic')
            y -= 0.07
            if y < 0.05:
                ax.text(0.5, y, f"... (+{sad['anchor_count'] - 10} more)",
                         transform=ax.transAxes, ha='center', fontsize=8,
                         color='#888')
                break
    
    fig.suptitle("Anchor inventory across SADs (auto-archetype from polar field)",
                  fontsize=12, y=0.99)
    
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def classify_anchor_archetype(cells: list[dict]) -> str:
    """
    Auto-classify an anchor's archetype from its polar cells.
    
    Heuristic rules:
      â€¢ 0 cells / very few cells with POIs                  â†’ 'morphological false positive'
      â€¢ cells concentrated in <3 compass sectors             â†’ 'asymmetric stadium'
      â€¢ cells spread evenly across 6+ sectors, retail-dom    â†’ 'distributed casino'
      â€¢ cells dominated by office program                    â†’ 'office tower'
      â€¢ cells dominated by retail but not uniform            â†’ 'retail-front anchor'
      â€¢ mixed program signature, multi-direction             â†’ 'mixed-use anchor'
    """
    if not cells:
        return 'morphological false positive'
    
    sectors_hit = set()
    program_counts = {}
    for c in cells:
        sectors_hit.add(c.get('sector', ''))
        prog = c.get('dominant_program', '')
        program_counts[prog] = program_counts.get(prog, 0) + c.get('count', 0)
    
    if not program_counts:
        return 'morphological false positive'
    
    n_sectors = len(sectors_hit)
    total = sum(program_counts.values())
    if total == 0:
        return 'morphological false positive'
    
    top_prog = max(program_counts, key=program_counts.get)
    top_pct = program_counts[top_prog] / total
    
    if n_sectors <= 2:
        return f'asymmetric ({top_prog})'
    if n_sectors >= 6 and top_pct >= 0.55:
        return f'distributed {top_prog}'
    if top_prog == 'office' and top_pct > 0.5:
        return 'office tower'
    if top_prog == 'retail_food_entertainment' and top_pct > 0.5:
        return 'retail-front anchor'
    if top_prog == 'sport':
        return 'asymmetric stadium'
    return f'mixed-use ({top_prog} dom.)'


# â”€â”€â”€ Main entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    parser = argparse.ArgumentParser(
        description="Cross-SAD comparison visualizations")
    parser.add_argument('--data-dir', type=Path, required=True,
                        help="Root data folder containing one subfolder per SAD")
    parser.add_argument('--sads', nargs='+', required=True,
                        help="List of SAD IDs (subfolder names) to compare")
    parser.add_argument('--out', type=Path, default=None,
                        help="Output dir (default: <data-dir>/_comparisons/<timestamp>)")
    args = parser.parse_args()
    
    # Gather data
    all_sad_data = []
    for sad_id in args.sads:
        sad_path = args.data_dir / sad_id
        if not sad_path.exists():
            print(f"  WARN: {sad_path} not found, skipping")
            continue
        d = gather_sad_data(args.data_dir, sad_id)
        all_sad_data.append(d)
        print(f"  loaded {sad_id}: {d['building_count']:,} buildings, "
              f"{d['anchor_count']} anchors, "
              f"coverage {d['coverage']:.3f}")
    
    if len(all_sad_data) < 2:
        raise SystemExit("Need at least 2 SADs to compare. Found "
                          f"{len(all_sad_data)}.")
    
    # Determine output dir
    if args.out:
        out_dir = args.out
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        out_dir = args.data_dir / '_comparisons' / f'comparison_{ts}'
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting to {out_dir}")
    
    # Render each visualization
    render_morphology_radar(
        all_sad_data,
        out_dir / 'morphology_radar.png',
        out_dir / 'morphology_radar.svg',
    )
    print(f"[OK] morphology_radar.svg")
    
    render_program_signature_compare(
        all_sad_data,
        out_dir / 'program_signature_compare.png',
        out_dir / 'program_signature_compare.svg',
    )
    print(f"[OK] program_signature_compare.svg")
    
    render_boundary_effect_compare(
        all_sad_data,
        out_dir / 'boundary_effect_compare.png',
        out_dir / 'boundary_effect_compare.svg',
    )
    print(f"[OK] boundary_effect_compare.svg")
    
    render_anchor_summary(
        all_sad_data,
        out_dir / 'anchor_summary.png',
        out_dir / 'anchor_summary.svg',
    )
    print(f"[OK] anchor_summary.svg")
    
    # Write numerical synthesis
    summary_path = out_dir / 'comparison_summary.json'
    summary_path.write_text(json.dumps({
        'generated_at': datetime.now().isoformat(),
        'sads_compared': [d['sad_id'] for d in all_sad_data],
        'data': [
            {k: v for k, v in d.items() if k != 'anchors'
                                          and k != 'connectivity_edges'}
            for d in all_sad_data
        ],
        'anchor_archetypes': {
            d['sad_id']: [
                {
                    'building_id': a.get('building_id'),
                    'name': a.get('name'),
                    'area_m2': a.get('area_m2'),
                    'archetype': classify_anchor_archetype(a.get('cells', [])),
                }
                for a in d.get('anchors', [])
            ]
            for d in all_sad_data
        },
    }, indent=2))
    print(f"[OK] comparison_summary.json")
    
    print(f"\nAll outputs in: {out_dir}")


if __name__ == '__main__':
    main()

