"""
module_06b_dendrogram_by_program.py

The flagship visualization. Takes the phylogenetic dendrogram (Witt's
RoweBot, Module 2b) and recolors every leaf by the building's dominant
program category (Rossetti rollup, Module 5).

The interpretation is direct: if program clusters within the morphology
tree - e.g., sport leaves all sit together on one branch, residential
leaves all sit together on another - then morphology PREDICTS program.
If colors are scattered across the tree, morphology and program are
independent.

This is the visualization Witt's RoweBot paper hinted at but didn't
produce: morphological similarity colored by functional category. It's
what makes the multi-signal pipeline analytically novel rather than just
mechanically combinatorial.

INPUT
  derived/<sad>/buildings_enriched.gpkg  (cluster_id + dominant_program_inside)
  derived/<sad>/building_linkage.npy     (SciPy linkage matrix)

OUTPUT
  derived/<sad>/dendrogram_by_program.png
  derived/<sad>/dendrogram_by_program.svg

USAGE
  python module_06b_dendrogram_by_program.py --derived <dir>
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from scipy.cluster.hierarchy import dendrogram, leaves_list


# Rossetti palette - distinct colors per program category that read well
# both for accessibility and against the dendrogram tree lines.
PROGRAM_COLORS = {
    'sport':                     '#d62728',  # red — anchor venues
    'residential':               '#2ca02c',  # green
    'hotel':                     '#9467bd',  # purple
    'retail_food_entertainment': '#ff7f0e',  # orange
    'office':                    '#1f77b4',  # blue
    'parking':                   '#8c564b',  # brown
    'open_space':                '#bcbd22',  # yellow-green
    'other':                     '#7f7f7f',  # gray
    None:                        '#dddddd',  # very light gray — no program
}


def render(derived_dir: Path):
    # Load
    buildings = gpd.read_file(derived_dir / 'buildings_enriched.gpkg', layer='buildings')
    Z = np.load(derived_dir / 'building_linkage.npy')
    
    if 'cluster_id' not in buildings.columns:
        raise SystemExit("buildings_enriched.gpkg missing cluster_id - rerun Module 2b")
    if 'dominant_program_inside' not in buildings.columns:
        raise SystemExit("buildings_enriched.gpkg missing dominant_program_inside - rerun Module 5")
    
    # The linkage matrix was built from the clustered subset (cluster_id > 0).
    # Get those buildings in the order they were passed to clustering.
    valid_mask = buildings['cluster_id'].values > 0
    valid_buildings = buildings[valid_mask].reset_index(drop=True)
    n = len(valid_buildings)
    
    leaf_order = leaves_list(Z)  # indices 0..n-1 in dendrogram order
    
    # Get dominant programs in leaf order. Pandas yields NaN for missing,
    # which we normalize to None for lookup against PROGRAM_COLORS.
    raw_progs = valid_buildings['dominant_program_inside'].iloc[leaf_order].values
    leaf_programs = [
        p if (isinstance(p, str) and p) else None
        for p in raw_progs
    ]
    
    # ─── Render the dendrogram ──────────────────────────────────────────
    fig, (ax_tree, ax_strip) = plt.subplots(
        2, 1, figsize=(20, 8),
        gridspec_kw={'height_ratios': [4, 0.4], 'hspace': 0.03},
    )
    
    # Tree, drawn in neutral gray so the program colors below stand out
    dendrogram(
        Z, ax=ax_tree,
        color_threshold=0,  # disable scipy's default cluster coloring
        above_threshold_color='#999999',
        no_labels=True,
    )
    ax_tree.set_ylabel('merge distance (Ward)', fontsize=10)
    
    # Profile info for title
    profile_path = derived_dir / 'district_profile.json'
    sad_name = derived_dir.name
    if profile_path.exists():
        profile = json.loads(profile_path.read_text())
        sad_name = profile.get('sad_name', derived_dir.name)
    
    ax_tree.set_title(
        f"{sad_name} - phylogenetic tree recolored by dominant program inside building\n"
        f"(if program tracks form, colors will cluster on branches; "
        f"if scattered, form and program are independent)",
        fontsize=11, pad=12,
    )
    
    # Strip below the tree: one colored cell per leaf, in dendrogram order.
    strip = np.zeros((1, n, 4))
    for i, prog in enumerate(leaf_programs):
        hex_color = PROGRAM_COLORS.get(prog, PROGRAM_COLORS[None])
        # matplotlib hex -> RGBA
        rgb = matplotlib.colors.to_rgba(hex_color)
        strip[0, i] = rgb
    ax_strip.imshow(strip, aspect='auto', interpolation='nearest')
    ax_strip.set_xticks([])
    ax_strip.set_yticks([])
    ax_strip.set_ylabel('program', fontsize=9, rotation=0, labelpad=40, va='center')
    
    # Align x-axes: dendrogram uses 10*i for leaf positions, imshow uses 0..n-1
    # The dendrogram leaves are positioned at x = 5, 15, 25, ..., 10*n-5.
    ax_tree.set_xlim(-5, 10 * n - 5)
    ax_strip.set_xlim(-0.5, n - 0.5)
    
    # Build a legend showing what each color means, plus per-program counts
    program_counts = {}
    for p in leaf_programs:
        program_counts[p] = program_counts.get(p, 0) + 1
    
    legend_entries = []
    for prog, color in PROGRAM_COLORS.items():
        if prog in program_counts:
            count = program_counts[prog]
            pct = 100 * count / n
            label = (
                f"{prog if prog else 'none'}  ({count:,}, {pct:.1f}%)"
            )
            legend_entries.append(Patch(facecolor=color, edgecolor='gray',
                                         linewidth=0.5, label=label))
    
    ax_tree.legend(
        handles=legend_entries,
        loc='upper left', bbox_to_anchor=(1.005, 1.0),
        fontsize=9, frameon=True, title='dominant program inside',
        title_fontsize=9,
    )
    
    out_png = derived_dir / 'dendrogram_by_program.png'
    out_svg = derived_dir / 'dendrogram_by_program.svg'
    fig.savefig(out_png, dpi=180, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)
    
    # Print summary
    print(f"[OK] wrote {out_png.name}")
    print(f"[OK] wrote {out_svg.name}")
    print(f"  {n} buildings, {len(program_counts)} distinct programs in dendrogram:")
    for prog, count in sorted(program_counts.items(), key=lambda x: -x[1]):
        prog_label = str(prog) if prog else 'none'
        print(f"    {prog_label:30s} {count:5d}  ({100*count/n:5.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Dendrogram colored by dominant program")
    parser.add_argument('--derived', type=Path, required=True,
                        help='Derived dir containing buildings_enriched.gpkg + building_linkage.npy')
    args = parser.parse_args()
    
    if not (args.derived / 'buildings_enriched.gpkg').exists():
        raise SystemExit("missing buildings_enriched.gpkg - run Module 5 first")
    if not (args.derived / 'building_linkage.npy').exists():
        raise SystemExit("missing building_linkage.npy - run Module 2b first")
    
    render(args.derived)


if __name__ == '__main__':
    main()
