"""
retrieve.py  (Module 19 - comparable-settings retrieval over the learned latent)

Reads the VGAE graph embeddings (vgae_train.py / M18) and turns them into the
retrieval engine: "given this place, which others has the model seen that are
most alike?" Tile embeddings are mean-pooled to one vector per SAD, then
nearest-neighbour search runs in the learned 16-d space.

This is the "recognize comparable settings, geographies, urban contexts" piece
of the tool, grounded in a learned relational latent rather than the
hand-engineered M8 point.

INPUT   graph_embeddings.csv  (columns: sad_id, name, z0..z{D-1})
OUTPUT  (in same dir or --out-dir)
          district_vectors.csv        one mean-pooled vector per SAD
          similarity_matrix.csv        full SAD x SAD cosine similarity
          neighbors.csv                top-k comparable SADs per SAD
          projection.csv               2D coords (PCA or UMAP) + cluster id
          projection.png               labelled scatter (deck-grade)

USAGE
  python retrieve.py --csv ..\\data\\_graphs\\_model\\graph_embeddings.csv
  python retrieve.py --csv ...\\graph_embeddings.csv --query 32_District-Detroit --k 5
  python retrieve.py --csv ...\\graph_embeddings.csv --method umap --clusters 5
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Rossetti palette
NAVY = '#1B2845'; CORAL = '#D97757'; CREAM = '#F4EFE6'
CLUSTER_COLORS = ['#1B2845', '#D97757', '#5C8B89', '#7C5E9C', '#D4A93A',
                  '#6A4E7C', '#B05535', '#3B6D6B']


def z_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith('z') and c[1:].isdigit()]


def district_vectors(df: pd.DataFrame) -> pd.DataFrame:
    """Mean-pool tile embeddings to one vector per SAD."""
    zc = z_cols(df)
    return df.groupby('sad_id')[zc].mean()


def short_name(sad_id: str) -> str:
    """'32_District-Detroit_Detroit-MI' -> 'District-Detroit'."""
    parts = sad_id.split('_')
    return parts[1] if len(parts) > 1 else sad_id


def neighbors_table(vecs: pd.DataFrame, k: int) -> pd.DataFrame:
    V = normalize(vecs.to_numpy())
    S = cosine_similarity(V)
    np.fill_diagonal(S, -np.inf)
    rows = []
    ids = vecs.index.to_list()
    for i, sad in enumerate(ids):
        order = np.argsort(S[i])[::-1][:k]
        for rank, j in enumerate(order, 1):
            rows.append(dict(sad_id=sad, rank=rank,
                             neighbor=ids[j], similarity=round(float(S[i, j]), 4)))
    return pd.DataFrame(rows)


def project(vecs: pd.DataFrame, method: str, clusters: int, seed: int):
    V = vecs.to_numpy()
    if method == 'umap':
        try:
            import umap
            xy = umap.UMAP(n_neighbors=min(10, len(V) - 1), min_dist=0.3,
                           random_state=seed).fit_transform(V)
        except Exception as e:
            print(f'  UMAP unavailable ({e}); falling back to PCA')
            xy = PCA(n_components=2, random_state=seed).fit_transform(V)
    else:
        xy = PCA(n_components=2, random_state=seed).fit_transform(V)
    lab = KMeans(n_clusters=min(clusters, len(V)), n_init=10,
                 random_state=seed).fit_predict(V)
    return xy, lab


def plot_projection(vecs, xy, lab, method, out_png):
    fig, ax = plt.subplots(figsize=(12, 9), dpi=150)
    fig.patch.set_facecolor(CREAM); ax.set_facecolor(CREAM)
    for c in np.unique(lab):
        m = lab == c
        ax.scatter(xy[m, 0], xy[m, 1], s=140,
                   color=CLUSTER_COLORS[c % len(CLUSTER_COLORS)],
                   edgecolor='white', linewidth=1.2, zorder=3,
                   label=f'group {c + 1}')
    for (x, y), sad in zip(xy, vecs.index):
        ax.annotate(short_name(sad), (x, y), fontsize=8, color=NAVY,
                    xytext=(6, 3), textcoords='offset points', zorder=4)
    ax.set_title(f'SAD corpus in the learned VGAE latent ({method.upper()} of 16-d)',
                 color=NAVY, fontsize=14, pad=14)
    for s in ax.spines.values(): s.set_color('#CCC4B6')
    ax.tick_params(colors='#9A9080')
    ax.legend(frameon=False, fontsize=9, loc='best')
    fig.tight_layout(); fig.savefig(out_png, facecolor=CREAM); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description='Comparable-settings retrieval (M19)')
    ap.add_argument('--csv', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, default=None)
    ap.add_argument('--query', type=str, default=None,
                    help='sad_id (or unique substring) to print neighbours for')
    ap.add_argument('--k', type=int, default=5)
    ap.add_argument('--method', choices=['pca', 'umap'], default='pca')
    ap.add_argument('--clusters', type=int, default=5)
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    df = pd.read_csv(a.csv)
    out = a.out_dir or a.csv.parent
    out.mkdir(parents=True, exist_ok=True)

    vecs = district_vectors(df)
    vecs.to_csv(out / 'district_vectors.csv')

    S = cosine_similarity(normalize(vecs.to_numpy()))
    pd.DataFrame(S, index=vecs.index, columns=vecs.index).to_csv(out / 'similarity_matrix.csv')

    nbrs = neighbors_table(vecs, a.k)
    nbrs.to_csv(out / 'neighbors.csv', index=False)

    xy, lab = project(vecs, a.method, a.clusters, a.seed)
    proj = pd.DataFrame({'sad_id': vecs.index, 'x': xy[:, 0], 'y': xy[:, 1], 'cluster': lab})
    proj.to_csv(out / 'projection.csv', index=False)
    plot_projection(vecs, xy, lab, a.method, out / 'projection.png')

    print(f'[OK] {len(vecs)} district vectors, {a.method.upper()} projection, '
          f'{a.clusters} clusters -> {out}')

    if a.query:
        match = [s for s in vecs.index if a.query.lower() in s.lower()]
        if not match:
            print(f'  no SAD matching "{a.query}"'); return
        q = match[0]
        sub = nbrs[nbrs.sad_id == q].sort_values('rank')
        print(f'\n  Nearest to {q}:')
        for _, r in sub.iterrows():
            print(f'    {r["rank"]}. {r["neighbor"]:48s} cos={r["similarity"]:.3f}')


if __name__ == '__main__':
    main()
