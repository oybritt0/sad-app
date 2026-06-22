"""
module_06c_v2_interior_exterior_signature.py

Side-by-side comparison of Rossetti program mix INSIDE the SAD boundary vs
OUTSIDE (within canvas). The SAD's defining boundary effect, quantified.

Replaces the v1 land-area reconciliation chart (which compared our pipeline
output against a Rossetti reference at a different boundary, making the
disagreement hard to interpret). The v2 chart compares the SAD against its
own surroundings using the boundary-aware zone tagging produced by Module 3.

INPUT
  derived/<sad>/program_summary.json   (must have zone_breakdown - run
                                        Module 3 with sad_boundary present)

OUTPUT
  derived/<sad>/interior_exterior_signature.png   (raster preview)
  derived/<sad>/interior_exterior_signature.svg   (vector for Illustrator)
  derived/<sad>/interior_exterior_signature.json  (numbers)

USAGE
  python module_06c_v2_interior_exterior_signature.py --derived <dir>
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


ROSSETTI_ORDER = [
    'sport', 'residential', 'hotel', 'retail_food_entertainment',
    'office', 'parking', 'open_space', 'other',
]

# Same TAB10-aligned palette as the other 06-series visualizations
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


def render_chart(data: dict, sad_name: str, out_png: Path, out_svg: Path):
    interior = data.get('interior', {}).get('rossetti_percentages', {})
    exterior = data.get('exterior', {}).get('rossetti_percentages', {})
    interior_n = data.get('interior', {}).get('count', 0)
    exterior_n = data.get('exterior', {}).get('count', 0)
    
    int_vals = [interior.get(b, 0.0) for b in ROSSETTI_ORDER]
    ext_vals = [exterior.get(b, 0.0) for b in ROSSETTI_ORDER]
    diffs = [i - e for i, e in zip(int_vals, ext_vals)]
    
    y = np.arange(len(ROSSETTI_ORDER))
    bar_h = 0.35
    
    fig, (ax_main, ax_diff) = plt.subplots(
        1, 2, figsize=(13, 7),
        gridspec_kw={'width_ratios': [3, 1.4], 'wspace': 0.15},
    )
    
    # ─── LEFT panel: side-by-side bars per category ─────────────────────
    bars_int = ax_main.barh(
        y - bar_h/2, int_vals, height=bar_h,
        color=[PROGRAM_COLORS[b] for b in ROSSETTI_ORDER],
        edgecolor='white', linewidth=0.5,
        label=f'Interior ({interior_n:,} POIs)',
    )
    bars_ext = ax_main.barh(
        y + bar_h/2, ext_vals, height=bar_h,
        color=[PROGRAM_COLORS[b] for b in ROSSETTI_ORDER],
        edgecolor='white', linewidth=0.5,
        alpha=0.45,
        label=f'Exterior ({exterior_n:,} POIs)',
    )
    
    for vi, v in enumerate(int_vals):
        if v > 0.4:
            ax_main.text(v + 0.4, y[vi] - bar_h/2, f'{v:.1f}%',
                         va='center', fontsize=8.5, color='#222')
    for vi, v in enumerate(ext_vals):
        if v > 0.4:
            ax_main.text(v + 0.4, y[vi] + bar_h/2, f'{v:.1f}%',
                         va='center', fontsize=8.5, color='#555')
    
    ax_main.set_yticks(y)
    ax_main.set_yticklabels(
        [b.replace('_', ' ').replace('retail food entertainment',
                                       'retail/F&B/ent')
         for b in ROSSETTI_ORDER], fontsize=10,
    )
    ax_main.invert_yaxis()
    ax_main.set_xlabel('% of POIs in zone', fontsize=10)
    ax_main.set_xlim(0, max(int_vals + ext_vals) * 1.18)
    ax_main.set_title(
        f"{sad_name} — program signature, interior vs exterior",
        fontsize=12, pad=10, loc='left',
    )
    ax_main.grid(axis='x', linestyle='--', alpha=0.4, linewidth=0.6)
    ax_main.set_axisbelow(True)
    for spine in ('top', 'right'):
        ax_main.spines[spine].set_visible(False)
    
    # Legend showing interior=solid vs exterior=translucent
    from matplotlib.patches import Patch
    handles = [
        Patch(facecolor='#666', edgecolor='white', label=f'Interior ({interior_n:,} POIs inside SAD)'),
        Patch(facecolor='#666', alpha=0.45, edgecolor='white', label=f'Exterior ({exterior_n:,} POIs in canvas, outside SAD)'),
    ]
    ax_main.legend(handles=handles, loc='lower right', fontsize=9, frameon=True)
    
    # ─── RIGHT panel: difference (interior - exterior) per category ──────
    colors_diff = ['#1f7a3c' if d > 0 else '#a8341e' for d in diffs]
    ax_diff.barh(y, diffs, height=0.55, color=colors_diff,
                  edgecolor='white', linewidth=0.5)
    for vi, d in enumerate(diffs):
        if abs(d) > 0.2:
            offset = 0.4 if d > 0 else -0.4
            ha = 'left' if d > 0 else 'right'
            ax_diff.text(d + offset, y[vi], f'{d:+.1f}',
                         va='center', ha=ha, fontsize=8.5,
                         color='#222')
    ax_diff.axvline(0, color='#222', linewidth=0.8)
    ax_diff.set_yticks(y)
    ax_diff.set_yticklabels([])
    ax_diff.invert_yaxis()
    ax_diff.set_xlabel('interior − exterior\n(percentage points)', fontsize=9)
    ax_diff.set_title('Boundary effect', fontsize=11, pad=10, loc='left')
    ax_diff.grid(axis='x', linestyle='--', alpha=0.4, linewidth=0.6)
    ax_diff.set_axisbelow(True)
    for spine in ('top', 'right', 'left'):
        ax_diff.spines[spine].set_visible(False)
    
    # Symmetric x range
    max_d = max(abs(min(diffs)), abs(max(diffs)))
    ax_diff.set_xlim(-max_d * 1.3, max_d * 1.3)
    
    plt.tight_layout()
    fig.savefig(out_png, dpi=180, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Interior vs exterior program signature comparison")
    parser.add_argument('--derived', type=Path, required=True)
    args = parser.parse_args()
    
    summary_path = args.derived / 'program_summary.json'
    if not summary_path.exists():
        raise SystemExit(f"missing {summary_path} — run Module 3 first")
    summary = json.loads(summary_path.read_text())
    
    zone_breakdown = summary.get('zone_breakdown')
    if not zone_breakdown:
        raise SystemExit(
            "program_summary.json has no zone_breakdown. "
            "Re-run Module 3 with sad_boundary.geojson in source folder."
        )
    
    sad_name = summary.get('sad_name', args.derived.name)
    out_png = args.derived / 'interior_exterior_signature.png'
    out_svg = args.derived / 'interior_exterior_signature.svg'
    out_json = args.derived / 'interior_exterior_signature.json'
    
    render_chart(zone_breakdown, sad_name, out_png, out_svg)
    
    # Companion JSON with the per-category numbers
    out_data = {
        'sad_name': sad_name,
        'interior_count': zone_breakdown['interior']['count'],
        'exterior_count': zone_breakdown['exterior']['count'],
        'categories': [
            {
                'category': b,
                'interior_pct': zone_breakdown['interior']['rossetti_percentages'].get(b, 0.0),
                'exterior_pct': zone_breakdown['exterior']['rossetti_percentages'].get(b, 0.0),
                'difference': (
                    zone_breakdown['interior']['rossetti_percentages'].get(b, 0.0) -
                    zone_breakdown['exterior']['rossetti_percentages'].get(b, 0.0)
                ),
            }
            for b in ROSSETTI_ORDER
        ],
    }
    out_json.write_text(json.dumps(out_data, indent=2))
    
    print(f"[OK] wrote {out_png.name}")
    print(f"[OK] wrote {out_svg.name}")
    print(f"[OK] wrote {out_json.name}")
    print()
    print(f"  {'category':30s} {'interior':>9s} {'exterior':>9s} {'diff':>8s}")
    for row in out_data['categories']:
        print(f"  {row['category']:30s} {row['interior_pct']:>8.1f}% "
              f"{row['exterior_pct']:>8.1f}% {row['difference']:>+7.1f}")


if __name__ == '__main__':
    main()
