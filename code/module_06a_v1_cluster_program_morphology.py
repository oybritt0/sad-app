"""
module_06a_v1_cluster_program_morphology.py

The cluster x program crosstab visualized with building silhouettes instead
of color cells. For each (cluster, Rossetti category) pair, draws the
cluster's MEDIAN building (representative of cluster morphology) at a size
proportional to the POI percentage in that cell. Log-scaled for legibility.

Vector SVG output so silhouettes and colors are editable in Illustrator.

INPUT
  derived/<sad>/buildings_enriched.gpkg     (cluster_id, area_m2, geometry)
  derived/<sad>/cluster_program_crosstab.json
  derived/<sad>/building_features.csv       (for cluster centers)

OUTPUT
  derived/<sad>/cluster_program_morphology_v1.png
  derived/<sad>/cluster_program_morphology_v1.svg

USAGE
  python module_06a_v1_cluster_program_morphology.py --derived <dir>
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
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


def find_median_building(buildings_in_cluster: gpd.GeoDataFrame) -> Polygon:
    """
    Return the building whose area is closest to the cluster's median area.
    This gives a morphologically-typical representative of the cluster
    (more representative than the centroid in feature space, which can
    pick weird outliers).
    """
    if len(buildings_in_cluster) == 0:
        return None
    median_area = buildings_in_cluster['area_m2'].median()
    idx = (buildings_in_cluster['area_m2'] - median_area).abs().idxmin()
    return to_polygon(buildings_in_cluster.geometry.loc[idx])


def normalize_polygon(poly: Polygon, target_size: float = 1.0
                       ) -> list[tuple[float, float]]:
    """
    Center polygon at origin and scale so its longest dimension is target_size.
    Returns list of (x, y) coords, ready for matplotlib.
    """
    coords = list(poly.exterior.coords)
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    span = max(width, height)
    if span == 0:
        return [(0, 0)]
    scale = target_size / span
    # Index into the tuple (handles both 2D and 3D geometry)
    return [((c[0] - cx) * scale, (c[1] - cy) * scale) for c in coords]


def render_matrix(buildings: gpd.GeoDataFrame, crosstab: dict, sad_name: str,
                   out_png: Path, out_svg: Path):
    """
    Build the cluster x program morphology matrix.
    """
    # Reproject to metric CRS for area-based comparisons
    metric_crs = buildings.estimate_utm_crs()
    buildings = buildings.to_crs(metric_crs)
    
    cluster_keys = sorted(
        [k for k in crosstab.keys() if k.startswith('cluster_')],
        key=lambda k: int(k.split('_')[1]),
    )
    n_clusters = len(cluster_keys)
    n_programs = len(ROSSETTI_ORDER)
    
    # Precompute median building silhouettes per cluster
    cluster_silhouettes: dict[int, list[tuple[float, float]]] = {}
    cluster_areas: dict[int, float] = {}
    for ck in cluster_keys:
        cid = int(ck.split('_')[1])
        cluster_bldgs = buildings[buildings['cluster_id'] == cid]
        if len(cluster_bldgs) == 0:
            continue
        median_poly = find_median_building(cluster_bldgs)
        if median_poly is None:
            continue
        cluster_silhouettes[cid] = normalize_polygon(median_poly)
        cluster_areas[cid] = float(cluster_bldgs['area_m2'].median())
    
    # ─── Set up the figure grid ────────────────────────────────────────
    # 1 reference column + 8 program columns; one row per cluster
    n_cols = n_programs + 1
    fig, axes = plt.subplots(
        n_clusters, n_cols,
        figsize=(1.1 * n_cols + 1.5, 1.1 * n_clusters + 1.5),
        gridspec_kw={'wspace': 0.05, 'hspace': 0.18,
                     'width_ratios': [1.4] + [1] * n_programs},
    )
    if n_clusters == 1:
        axes = axes.reshape(1, -1)
    
    # Column headers
    headers = ['  Cluster\n  (n buildings)']
    headers.extend([
        b.replace('_', ' ').replace('retail food entertainment',
                                     'retail/F&B/ent')
        for b in ROSSETTI_ORDER
    ])
    
    for col, label in enumerate(headers):
        # Place header above first row's axis
        axes[0, col].text(0.5, 1.35, label, ha='center', va='bottom',
                          transform=axes[0, col].transAxes,
                          fontsize=9, fontweight='bold',
                          color='#222')
    
    # ─── Populate cells ─────────────────────────────────────────────────
    for row, ck in enumerate(cluster_keys):
        cid = int(ck.split('_')[1])
        c = crosstab[ck]
        n_buildings = c['n_buildings']
        n_programs_in_cluster = c['n_programs_inside']
        program_pct = c.get('program_pct', {})
        
        silhouette = cluster_silhouettes.get(cid)
        
        # Reference column (leftmost): cluster's median building at fixed size
        ax = axes[row, 0]
        ax.set_xlim(-0.6, 0.6)
        ax.set_ylim(-0.6, 0.6)
        ax.set_aspect('equal')
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        if silhouette:
            patch = MplPolygon(silhouette, closed=True,
                                facecolor='#444', edgecolor='#222',
                                linewidth=0.6)
            ax.add_patch(patch)
        ax.text(-0.55, 0.55, f"c{cid}",
                fontsize=10, fontweight='bold', va='top', ha='left',
                color='#222')
        ax.text(0.55, -0.55, f"n={n_buildings:,}",
                fontsize=7, va='bottom', ha='right', color='#555')
        
        # Program columns
        for ci, bucket in enumerate(ROSSETTI_ORDER):
            ax = axes[row, ci + 1]
            ax.set_xlim(-0.6, 0.6)
            ax.set_ylim(-0.6, 0.6)
            ax.set_aspect('equal')
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
            
            # Pastel background tinted by program color
            bg_color = PROGRAM_COLORS.get(bucket, '#999')
            ax.set_facecolor(_lighten(bg_color, 0.92))
            
            pct = program_pct.get(bucket, 0.0)
            if pct < 0.005 or not silhouette:
                # Vanishing cell - leave blank
                continue
            
            # Log-scale silhouette size so 1% is still visible
            # log10(pct*100 + 1) → 0 at 0%, ~2 at ~50%
            log_size = np.log10(pct * 100 + 1)  # 0 to ~2
            # Normalize so 50% becomes ~0.45 (visible but not overflowing)
            silhouette_scale = 0.18 + 0.32 * (log_size / 2.0)
            silhouette_scale = min(silhouette_scale, 0.5)
            
            scaled = [(x * silhouette_scale * 2, y * silhouette_scale * 2)
                      for x, y in silhouette]
            patch = MplPolygon(scaled, closed=True,
                                facecolor=bg_color, edgecolor='#222',
                                linewidth=0.5, alpha=0.95)
            ax.add_patch(patch)
            
            # Percentage label
            ax.text(0.55, -0.55, f"{pct*100:.0f}%",
                    fontsize=7, va='bottom', ha='right',
                    color='#222', fontweight='bold')
    
    fig.suptitle(
        f"{sad_name} — cluster × program morphology  (median building, log-scaled by POI %)",
        fontsize=12, y=0.99,
    )
    fig.subplots_adjust(top=0.93, left=0.02, right=0.98, bottom=0.03)
    
    fig.savefig(out_png, dpi=180, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def _lighten(hex_color: str, blend: float) -> tuple[float, float, float]:
    """Blend a hex color toward white. blend=0 returns the color, 1 returns white."""
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255
    return (r + (1 - r) * blend, g + (1 - g) * blend, b + (1 - b) * blend)


def main():
    parser = argparse.ArgumentParser(
        description="Cluster × program morphology matrix (median building)")
    parser.add_argument('--derived', type=Path, required=True)
    args = parser.parse_args()
    
    enriched_path = args.derived / 'buildings_enriched.gpkg'
    crosstab_path = args.derived / 'cluster_program_crosstab.json'
    if not enriched_path.exists():
        raise SystemExit(f"missing {enriched_path} — run Module 5 first")
    if not crosstab_path.exists():
        raise SystemExit(f"missing {crosstab_path} — run Module 5 first")
    
    buildings = gpd.read_file(enriched_path, layer='buildings')
    crosstab = json.loads(crosstab_path.read_text())
    
    profile_path = args.derived / 'district_profile.json'
    sad_name = args.derived.name
    if profile_path.exists():
        sad_name = json.loads(profile_path.read_text()).get('sad_name', sad_name)
    
    out_png = args.derived / 'cluster_program_morphology_v1.png'
    out_svg = args.derived / 'cluster_program_morphology_v1.svg'
    render_matrix(buildings, crosstab, sad_name, out_png, out_svg)
    
    print(f"[OK] wrote {out_png.name}")
    print(f"[OK] wrote {out_svg.name}")


if __name__ == '__main__':
    main()
