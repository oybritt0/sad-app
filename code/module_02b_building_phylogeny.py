"""
module_02b_building_phylogeny.py

Builds the per-SAD building phylogenetic tree - an adaptation of Andrew
Witt's RoweBot (Log 36, 2016) for the smaller scale of a Sports-Anchored
District. Where Witt operated on ~10,000 buildings in central London,
a single SAD typically holds 150-500 buildings, which makes every pairwise
comparison computationally trivial and yields cleaner cluster separation.

METHOD (follows Witt's four-stage pipeline)
    1. Per-shape feature extraction - done by Module 2.
       21 morphological metrics per building (Witt uses 40; we cover the
       major families: size, compactness, elongation, eccentricity, complexity,
       Hu moments). Spatial-context metrics like nearest_neighbor are EXCLUDED
       so the tree is purely about form, matching Witt's framing.
    
    2. Pairwise difference - Euclidean distance in normalized feature space.
       Z-score normalization first so no single metric dominates.
    
    3. Network of relationships - represented as the pairwise distance matrix.
       For visualization we don't draw the full graph (would be illegible);
       we extract the minimum-spanning subtree via hierarchical clustering.
    
    4. Tree extraction + clustering - Ward-linkage agglomerative clustering
       produces a dendrogram; cutting at k=8 yields the form families.
       k is configurable. Witt notes that the dendrogram's shape itself is
       diagnostic - narrow trees mean variety, wide-base trees mean uniformity.

OUTPUTS (in --derived)
    building_features.csv          per-building feature matrix (z-scored)
    building_distance_matrix.npy   NxN pairwise distance (squareform)
    building_linkage.npy           SciPy linkage matrix (Z)
    building_phylogeny.json        summary: cluster sizes + medians + tree shape stats
    building_phylogeny.png         dendrogram visualization
    building_clusters_map.png      figure-ground tinted by cluster
    buildings_clustered.gpkg       buildings with cluster_id column added

USAGE
    python module_02b_building_phylogeny.py \\
        --derived ../data/derived/per_sad/district_detroit \\
        --num-clusters 8
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')  # non-interactive
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from scipy.cluster.hierarchy import linkage, fcluster, dendrogram
from scipy.spatial.distance import pdist, squareform
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest


# Default feature set: pure shape descriptors, no spatial context.
# This mirrors Witt's framing - the tree is about form, not adjacency.
SHAPE_FEATURES = [
    # Size
    'area_m2', 'perimeter_m', 'equivalent_diameter_m', 'max_diameter_m',
    'mrr_long_side_m', 'mrr_short_side_m',
    # Compactness / convexity
    'compactness', 'hull_ratio', 'solidity', 'roughness',
    # Elongation / eccentricity
    'bbox_elongation', 'mrr_elongation', 'eccentricity',
    # Complexity
    'vertex_count',
    # Hu moments (scale + rotation + translation invariant)
    'hu_1', 'hu_2', 'hu_3', 'hu_4', 'hu_5', 'hu_6', 'hu_7',
]


def build_phylogeny(
    buildings_cv: gpd.GeoDataFrame,
    num_clusters: int,
    features: list[str] | None = None,
) -> dict:
    """
    Run the four-stage RoweBot pipeline on one SAD's buildings.
    Returns a dict with linkage, cluster assignments, normalized features,
    and summary statistics.
    """
    features = features or SHAPE_FEATURES
    
    # 1. Extract feature matrix; drop buildings with too many NaNs
    X_df = buildings_cv[features].copy()
    missing_per_row = X_df.isna().sum(axis=1)
    keep = missing_per_row <= len(features) // 4  # tolerate <25% missing per row
    if keep.sum() < len(buildings_cv):
        print(f"  dropped {(~keep).sum()} buildings with >25% missing features")
    
    X_df = X_df[keep].fillna(X_df.median())
    valid_indices = X_df.index.values
    
    if len(X_df) < 3:
        raise ValueError(
            f"Only {len(X_df)} valid buildings - need at least 3 for clustering."
        )
    
    # 2. Normalize. Z-scoring means each feature contributes equally to the
    # distance metric regardless of unit (m vs ratio vs log-moment).
    scaler = StandardScaler()
    X = scaler.fit_transform(X_df.values)
    
    # 3. Pairwise distance + linkage (Ward minimizes within-cluster variance)
    pdists = pdist(X, metric='euclidean')
    Z = linkage(pdists, method='ward')
    
    # 4. Cut tree at requested cluster count
    k = min(num_clusters, len(X_df) - 1)
    clusters = fcluster(Z, t=k, criterion='maxclust')
    
    return {
        'linkage': Z,
        'distance_matrix': squareform(pdists),
        'clusters': clusters,
        'features_normalized': X,
        'features_raw': X_df,
        'valid_indices': valid_indices,
        'num_clusters_actual': int(len(np.unique(clusters))),
        'features_used': features,
    }


def cluster_summary(phylogeny: dict, buildings_cv: gpd.GeoDataFrame) -> dict:
    """Per-cluster descriptive stats for downstream interpretation."""
    clusters = phylogeny['clusters']
    valid_idx = phylogeny['valid_indices']
    features = phylogeny['features_used']
    
    df = buildings_cv.iloc[valid_idx].copy()
    df['cluster_id'] = clusters
    
    summary = {
        'num_clusters': phylogeny['num_clusters_actual'],
        'cluster_sizes': {},
        'cluster_medians': {},
    }
    
    for cid in np.unique(clusters):
        mask = clusters == cid
        summary['cluster_sizes'][int(cid)] = int(mask.sum())
        # Median of raw (non-normalized) features for interpretation
        medians = df[mask][features].median()
        summary['cluster_medians'][int(cid)] = {
            k: float(v) if pd.notna(v) else None for k, v in medians.items()
        }
    return summary


def tree_shape_stats(Z: np.ndarray) -> dict:
    """
    Witt's observation: the SHAPE of the tree is diagnostic.
    - Narrow root + dense top  = wide variety of forms (e.g. Boston)
    - Tall narrow tree         = few dense clusters, variety with few commonalities (City of London)
    - Wide dense base          = strong similarity (Lower Manhattan, uniform parcels)
    
    We summarize this with three numbers:
      max_merge_height:  the longest "branch" in the dendrogram
      merge_height_p90:  the 90th percentile of merge heights
      merge_height_skew: skewness of the merge-height distribution
                         (positive = few tall merges over many short ones)
    """
    from scipy.stats import skew
    heights = Z[:, 2]
    return {
        'max_merge_height': float(heights.max()),
        'merge_height_p50': float(np.percentile(heights, 50)),
        'merge_height_p90': float(np.percentile(heights, 90)),
        'merge_height_skew': float(skew(heights)),
        'interpretation_hint': (
            'High skew + high p90 = wide variety of forms; '
            'low skew + low max = strong shape similarity across the SAD.'
        ),
    }


def plot_dendrogram(Z: np.ndarray, num_clusters: int, title: str, out_path: Path):
    """Render the phylogenetic dendrogram, with the cut-line for cluster assignment."""
    fig, ax = plt.subplots(figsize=(14, 6))
    # Color threshold: the merge height that produces k clusters
    color_threshold = Z[-(num_clusters - 1), 2] if num_clusters > 1 else 0
    dendrogram(
        Z, ax=ax,
        color_threshold=color_threshold,
        above_threshold_color='#888888',
        no_labels=True,
    )
    ax.axhline(color_threshold, color='red', linestyle='--', alpha=0.5,
               label=f'cut at k={num_clusters}')
    ax.set_title(title, fontsize=12)
    ax.set_xlabel('building index (re-ordered by hierarchical similarity)')
    ax.set_ylabel('merge distance (Ward)')
    ax.legend(loc='upper right')
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_cluster_map(buildings_with_clusters: gpd.GeoDataFrame, title: str,
                     out_path: Path, manifest: Manifest):
    """
    Render the figure-ground tinted by cluster_id. Each form family gets its
    own color, making it easy to see which parts of the SAD share morphology.
    """
    # Use a categorical colormap
    n_clusters = int(buildings_with_clusters['cluster_id'].max())
    cmap = plt.colormaps.get_cmap('tab10').resampled(max(n_clusters, 10))
    
    fig, ax = plt.subplots(figsize=(10, 10))
    # Clip to the SAD canvas
    minlon, minlat, maxlon, maxlat = manifest.bbox_geo
    in_frame = buildings_with_clusters.cx[minlon:maxlon, minlat:maxlat]
    
    in_frame.plot(
        column='cluster_id',
        cmap=cmap,
        ax=ax,
        edgecolor='black',
        linewidth=0.2,
        categorical=True,
        legend=True,
        legend_kwds={'title': 'form cluster', 'loc': 'upper right',
                     'frameon': True, 'fontsize': 9},
    )
    ax.set_xlim(minlon, maxlon)
    ax.set_ylim(minlat, maxlat)
    ax.set_aspect('equal')
    ax.set_facecolor('white')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, fontsize=12)
    
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()


def process_sad(derived_dir: Path, num_clusters: int = 8) -> dict:
    # Load inputs
    manifest = Manifest.model_validate_json(
        (derived_dir / 'manifest.json').read_text()
    )
    buildings_cv = gpd.read_file(derived_dir / 'buildings_cv.gpkg', layer='buildings')
    
    print(f"Loading {len(buildings_cv)} buildings for {manifest.sad_id}...")
    print(f"Building phylogeny over {len(SHAPE_FEATURES)} shape features...")
    
    # Build the phylogeny
    phy = build_phylogeny(buildings_cv, num_clusters=num_clusters)
    print(f"  produced {phy['num_clusters_actual']} clusters from "
          f"{len(phy['valid_indices'])} buildings")
    
    # Save artifacts
    np.save(derived_dir / 'building_distance_matrix.npy', phy['distance_matrix'])
    np.save(derived_dir / 'building_linkage.npy', phy['linkage'])
    
    # Persist the normalized feature matrix as CSV (interpretable)
    feat_df = pd.DataFrame(
        phy['features_normalized'],
        columns=phy['features_used'],
        index=phy['valid_indices'],
    )
    feat_df['cluster_id'] = phy['clusters']
    feat_df.to_csv(derived_dir / 'building_features.csv')
    
    # Attach cluster_id to buildings, write out
    buildings_clustered = buildings_cv.copy()
    buildings_clustered['cluster_id'] = -1  # default for dropped buildings
    buildings_clustered.loc[phy['valid_indices'], 'cluster_id'] = phy['clusters']
    buildings_clustered.to_file(
        derived_dir / 'buildings_clustered.gpkg', driver='GPKG', layer='buildings'
    )
    
    # Visualizations
    plot_dendrogram(
        phy['linkage'], phy['num_clusters_actual'],
        title=f"{manifest.sad_id} - building phylogeny "
              f"({len(phy['valid_indices'])} buildings -> "
              f"{phy['num_clusters_actual']} form clusters)",
        out_path=derived_dir / 'building_phylogeny.png',
    )
    plot_cluster_map(
        buildings_clustered[buildings_clustered['cluster_id'] > 0],
        title=f"{manifest.sad_id} - form clusters on figure-ground",
        out_path=derived_dir / 'building_clusters_map.png',
        manifest=manifest,
    )
    
    # JSON summary
    summary = {
        'sad_id': manifest.sad_id,
        'sad_name': manifest.sad_name,
        'typology': manifest.typology,
        'num_buildings_input': len(buildings_cv),
        'num_buildings_clustered': len(phy['valid_indices']),
        'num_features': len(SHAPE_FEATURES),
        'features_used': SHAPE_FEATURES,
        **cluster_summary(phy, buildings_cv),
        'tree_shape': tree_shape_stats(phy['linkage']),
    }
    with open(derived_dir / 'building_phylogeny.json', 'w') as f:
        json.dump(summary, f, indent=2)
    
    return summary


def main():
    parser = argparse.ArgumentParser(description="Per-SAD building phylogenetic tree (RoweBot-style).")
    parser.add_argument('--derived', type=Path, required=True,
                        help='Derived directory for this SAD (must contain buildings_cv.gpkg).')
    parser.add_argument('--num-clusters', type=int, default=8,
                        help='Target number of form-family clusters (default 8).')
    args = parser.parse_args()
    
    summary = process_sad(args.derived, num_clusters=args.num_clusters)
    
    print(f"\n[OK] {summary['sad_id']} phylogeny complete")
    print(f"  buildings clustered: {summary['num_buildings_clustered']}")
    print(f"  form families:       {summary['num_clusters']}")
    print(f"  cluster sizes:       "
          f"{dict(sorted(summary['cluster_sizes'].items()))}")
    print(f"  tree shape (skew):   {summary['tree_shape']['merge_height_skew']:.3f}")


if __name__ == '__main__':
    main()
