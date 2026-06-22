"""
module_06a_v2_cluster_population_matrix.py

Variant of 06a v1: instead of showing each cluster's median building, this
version shows a SAMPLE of buildings from the cluster in each cell. The
sample size in each cell scales with the POI percentage in that
cluster-program combination.

Visually: each cluster-program cell becomes a small grid of N silhouettes,
where N is proportional to log(POI %). This shows BOTH morphological
variety within a cluster AND the program weighting.

INPUT
  derived/<sad>/buildings_enriched.gpkg
  derived/<sad>/cluster_program_crosstab.json

OUTPUT
  derived/<sad>/cluster_program_morphology_v2.png
  derived/<sad>/cluster_program_morphology_v2.svg

USAGE
  python module_06a_v2_cluster_population_matrix.py --derived <dir>
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
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import Polygon, MultiPolygon


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


def to_polygon(geom):
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def normalize_polygon(poly, target_size: float = 1.0):
    coords = list(poly.exterior.coords)
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span == 0:
        return [(0, 0)]
    scale = target_size / span
    return [((c[0] - cx) * scale, (c[1] - cy) * scale) for c in coords]


def sample_buildings_per_cluster(buildings: gpd.GeoDataFrame, cid: int,
                                   max_n: int = 16, rng: np.random.Generator = None
                                   ) -> list:
    """
    Get up to max_n building silhouettes from a cluster, biased toward
    representative sizes (sorted by closeness to median area). Returns
    list of normalized polygon coord-lists.
    """
    rng = rng or np.random.default_rng(42)
    cluster_bldgs = buildings[buildings['cluster_id'] == cid].copy()
    if len(cluster_bldgs) == 0:
        return []
    
    # Pick the building closest to the median area + random sampling
    median_area = cluster_bldgs['area_m2'].median()
    cluster_bldgs['_dist'] = (cluster_bldgs['area_m2'] - median_area).abs()
    cluster_bldgs = cluster_bldgs.sort_values('_dist')
    
    n_sample = min(max_n, len(cluster_bldgs))
    # Take the top N closest to median; if cluster is larger than max_n,
    # randomize a bit to show variety
    if len(cluster_bldgs) > max_n * 2:
        # Take half from median-typical, half random
        typical = cluster_bldgs.head(max_n // 2)
        rest = cluster_bldgs.iloc[max_n // 2:].sample(
            n=min(max_n - len(typical), len(cluster_bldgs) - len(typical)),
            random_state=42,
        )
        sample = pd_concat_keep_order([typical, rest])
    else:
        sample = cluster_bldgs.head(n_sample)
    
    return [normalize_polygon(to_polygon(g)) for g in sample.geometry]


def pd_concat_keep_order(dfs):
    import pandas as pd
    return pd.concat(dfs, ignore_index=False)


def draw_cell_grid(ax, silhouettes: list, n_to_show: int, color: str,
                    edgecolor: str = '#222'):
    """
    Arrange n_to_show silhouettes in a small grid inside the cell.
    """
    if n_to_show <= 0 or not silhouettes:
        return
    # Arrange in a square-ish grid
    cols = int(np.ceil(np.sqrt(n_to_show)))
    rows = int(np.ceil(n_to_show / cols))
    
    silhouette_size = 0.9 / max(cols, rows)
    
    for i in range(n_to_show):
        if i >= len(silhouettes):
            break
        col = i % cols
        row = i // cols
        # Position center of this cell-within-cell
        x_offset = (col + 0.5) / cols - 0.5
        y_offset = 0.5 - (row + 0.5) / rows
        
        scaled = [(x * silhouette_size + x_offset,
                   y * silhouette_size + y_offset)
                  for x, y in silhouettes[i]]
        patch = MplPolygon(scaled, closed=True,
                            facecolor=color, edgecolor=edgecolor,
                            linewidth=0.4, alpha=0.95)
        ax.add_patch(patch)


def _lighten(hex_color: str, blend: float):
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255
    return (r + (1 - r) * blend, g + (1 - g) * blend, b + (1 - b) * blend)


def render_matrix(buildings: gpd.GeoDataFrame, crosstab: dict, sad_name: str,
                   out_png: Path, out_svg: Path):
    metric_crs = buildings.estimate_utm_crs()
    buildings = buildings.to_crs(metric_crs)
    
    cluster_keys = sorted(
        [k for k in crosstab.keys() if k.startswith('cluster_')],
        key=lambda k: int(k.split('_')[1]),
    )
    n_clusters = len(cluster_keys)
    n_programs = len(ROSSETTI_ORDER)
    
    # Precompute building samples per cluster
    rng = np.random.default_rng(42)
    cluster_samples: dict[int, list] = {}
    cluster_counts: dict[int, int] = {}
    for ck in cluster_keys:
        cid = int(ck.split('_')[1])
        silhouettes = sample_buildings_per_cluster(buildings, cid, max_n=16, rng=rng)
        cluster_samples[cid] = silhouettes
        cluster_counts[cid] = int(crosstab[ck]['n_buildings'])
    
    # ─── Figure layout ─────────────────────────────────────────────────
    n_cols = n_programs + 1
    fig, axes = plt.subplots(
        n_clusters, n_cols,
        figsize=(1.6 * n_cols + 1.5, 1.6 * n_clusters + 1.5),
        gridspec_kw={'wspace': 0.06, 'hspace': 0.2,
                     'width_ratios': [1.8] + [1] * n_programs},
    )
    if n_clusters == 1:
        axes = axes.reshape(1, -1)
    
    # Column headers
    headers = ['  Cluster sample']
    headers.extend([
        b.replace('_', ' ').replace('retail food entertainment',
                                     'retail/F&B/ent')
        for b in ROSSETTI_ORDER
    ])
    
    for col, label in enumerate(headers):
        axes[0, col].text(0.5, 1.18, label, ha='center', va='bottom',
                          transform=axes[0, col].transAxes,
                          fontsize=9, fontweight='bold',
                          color='#222')
    
    # ─── Populate cells ─────────────────────────────────────────────────
    for row, ck in enumerate(cluster_keys):
        cid = int(ck.split('_')[1])
        c = crosstab[ck]
        n_buildings = c['n_buildings']
        program_pct = c.get('program_pct', {})
        silhouettes = cluster_samples.get(cid, [])
        
        # Reference column: cluster sample
        ax = axes[row, 0]
        ax.set_xlim(-0.55, 0.55)
        ax.set_ylim(-0.55, 0.55)
        ax.set_aspect('equal')
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        if silhouettes:
            # Show all samples in the cluster reference cell
            n_show = min(len(silhouettes), 16)
            draw_cell_grid(ax, silhouettes, n_show, '#444', '#222')
        ax.text(-0.5, 0.5, f"c{cid}",
                fontsize=10, fontweight='bold', va='top', ha='left',
                color='#222')
        ax.text(0.5, -0.5, f"n={n_buildings:,}",
                fontsize=7, va='bottom', ha='right', color='#555')
        
        # Program columns
        for ci, bucket in enumerate(ROSSETTI_ORDER):
            ax = axes[row, ci + 1]
            ax.set_xlim(-0.55, 0.55)
            ax.set_ylim(-0.55, 0.55)
            ax.set_aspect('equal')
            ax.set_xticks([]); ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
            bg_color = PROGRAM_COLORS.get(bucket, '#999')
            ax.set_facecolor(_lighten(bg_color, 0.92))
            
            pct = program_pct.get(bucket, 0.0)
            if pct < 0.005 or not silhouettes:
                continue
            
            # n_to_show scales as log of POI %
            # 1% → 1 silhouette
            # 10% → ~4
            # 50% → ~9
            # 100% → 16
            log_size = np.log10(pct * 100 + 1)  # 0 to ~2
            n_to_show = max(1, min(16, int(np.round(log_size * 7))))
            
            draw_cell_grid(ax, silhouettes, n_to_show, bg_color, '#222')
            
            ax.text(0.5, -0.5, f"{pct*100:.0f}%",
                    fontsize=7, va='bottom', ha='right',
                    color='#222', fontweight='bold')
    
    fig.suptitle(
        f"{sad_name} — cluster × program morphology  "
        f"(sample of cluster buildings, count log-scaled by POI %)",
        fontsize=12, y=0.99,
    )
    fig.subplots_adjust(top=0.93, left=0.02, right=0.98, bottom=0.03)
    
    fig.savefig(out_png, dpi=180, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Cluster × program morphology matrix (population)")
    parser.add_argument('--derived', type=Path, required=True)
    args = parser.parse_args()
    
    enriched_path = args.derived / 'buildings_enriched.gpkg'
    crosstab_path = args.derived / 'cluster_program_crosstab.json'
    if not enriched_path.exists():
        raise SystemExit(f"missing {enriched_path}")
    if not crosstab_path.exists():
        raise SystemExit(f"missing {crosstab_path}")
    
    buildings = gpd.read_file(enriched_path, layer='buildings')
    crosstab = json.loads(crosstab_path.read_text())
    
    profile_path = args.derived / 'district_profile.json'
    sad_name = args.derived.name
    if profile_path.exists():
        sad_name = json.loads(profile_path.read_text()).get('sad_name', sad_name)
    
    out_png = args.derived / 'cluster_program_morphology_v2.png'
    out_svg = args.derived / 'cluster_program_morphology_v2.svg'
    render_matrix(buildings, crosstab, sad_name, out_png, out_svg)
    
    print(f"[OK] wrote {out_png.name}")
    print(f"[OK] wrote {out_svg.name}")


if __name__ == '__main__':
    main()
