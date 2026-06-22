"""
graph_builder.py  (Module 17 - SAD -> attributed urban graph)

Turns each SAD's buildings_enriched.gpkg (the M5 integration output) into an
attributed graph for graph representation learning:

    node           = one building
    node features  = 21 shape metrics (M2b)
                     + per-building 8-bucket program mix (M3/M5)
                     + inherited block-group demographics (M4/M5)
                     + zone flag (interior/exterior)
    edge           = spatial adjacency between buildings (Delaunay-pruned default)
    edge features  = [centroid_distance_m, footprint_gap_m]

This keeps form <-> program <-> demographics RELATED inside one structure,
instead of averaging them into district_profile.to_vector().

GRANULARITY
    --level district   one graph per SAD       (37 graphs; retrieval / graph-level embedding)
    --level tile       induced subgraphs on a metric grid
                       (37 x dozens; the training set for graph *generation*)

OUTPUTS  (derived/<sad>/graph/  for one SAD;  data/_graphs/  for --all)
    <name>.npz             X float32 [N,F], edge_index int64 [2,E],
                           edge_attr float32 [E,2]
    <name>_nodes.parquet   per-node metadata (building_id, dominant_program,
                           zone, cluster_id, tile_id, centroid x/y)
    <name>_meta.json       counts, crs, edge method/params, feature schema
    --all also writes data/_graphs/_corpus/{node_scaler.json, graph_index.json}
    and corpus-z-scored *_norm.npz

USAGE
    python graph_builder.py --derived ..\\data\\derived\\per_sad\\district_detroit
    python graph_builder.py --all --data-dir ..\\data --level tile --tile-size-m 1000
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial import Delaunay, cKDTree


# ─── Schema constants (mirror M2b / M5) ────────────────────────────────────

SHAPE_FEATURES = [
    'area_m2', 'perimeter_m', 'equivalent_diameter_m', 'max_diameter_m',
    'mrr_long_side_m', 'mrr_short_side_m',
    'compactness', 'hull_ratio', 'solidity', 'roughness',
    'bbox_elongation', 'mrr_elongation', 'eccentricity',
    'vertex_count',
    'hu_1', 'hu_2', 'hu_3', 'hu_4', 'hu_5', 'hu_6', 'hu_7',
]
# Size-like features are heavy-tailed; log1p before standardizing.
SIZE_FEATURES = {
    'area_m2', 'perimeter_m', 'equivalent_diameter_m', 'max_diameter_m',
    'mrr_long_side_m', 'mrr_short_side_m',
}
# Hu moments span many orders of magnitude and can be negative -> signed log.
HU_FEATURES = {'hu_1', 'hu_2', 'hu_3', 'hu_4', 'hu_5', 'hu_6', 'hu_7'}

ROSSETTI_BUCKETS = [
    'sport', 'residential', 'hotel', 'retail_food_entertainment',
    'office', 'parking', 'open_space', 'other',
]

# Inherited block-group columns M5 leaves on each building (keep what exists).
DEMO_COLS = [
    'total_pop', 'median_age', 'median_household_income',
    'median_home_value', 'median_gross_rent',
    'race_white', 'race_black', 'race_asian', 'hispanic',
    'edu_bachelors', 'edu_masters', 'owner_occupied', 'renter_occupied',
]
# Counts and dollar amounts are heavy-tailed; log1p them. median_age stays raw.
LOG_DEMO = {d for d in DEMO_COLS if d != 'median_age'}


# ─── Load ──────────────────────────────────────────────────────────────────

def load_enriched(derived_dir: Path) -> gpd.GeoDataFrame:
    """Read buildings_enriched.gpkg and restore the ';'-joined program lists."""
    gdf = gpd.read_file(derived_dir / 'buildings_enriched.gpkg', layer='buildings')
    for col in ('programs_inside', 'programs_adjacent'):
        if col in gdf.columns:
            gdf[col] = gdf[col].fillna('').apply(
                lambda s: [p for p in str(s).split(';') if p]
            )
        else:
            gdf[col] = [[] for _ in range(len(gdf))]
    if 'building_id' not in gdf.columns:
        gdf['building_id'] = [f'b{i:05d}' for i in range(len(gdf))]
    return gdf


# ─── Node features ──────────────────────────────────────────────────────────

def _program_mix(prog_lists: pd.Series) -> np.ndarray:
    """Per-building program share over the 8 Rossetti buckets (rows sum to 1
    where any program is present, else all zeros)."""
    idx = {b: i for i, b in enumerate(ROSSETTI_BUCKETS)}
    out = np.zeros((len(prog_lists), len(ROSSETTI_BUCKETS)), dtype='float32')
    for r, progs in enumerate(prog_lists):
        for p in progs:
            if p in idx:
                out[r, idx[p]] += 1.0
    totals = out.sum(axis=1, keepdims=True)
    nz = totals.squeeze(-1) > 0
    out[nz] = out[nz] / totals[nz]
    return out


def build_node_features(gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, list[str]]:
    """Assemble the [N, F] node-feature matrix and the ordered feature names.
    Continuous features are RAW here; corpus-level standardization happens in
    the --all path so stats are fit across all 37, not per-district."""
    cols: list[np.ndarray] = []
    names: list[str] = []

    # 1) shape block (log1p the size-like ones)
    for f in SHAPE_FEATURES:
        if f in gdf.columns:
            v = pd.to_numeric(gdf[f], errors='coerce').to_numpy('float64')
        else:
            v = np.zeros(len(gdf))
        v = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)
        if f in SIZE_FEATURES:
            v = np.log1p(np.clip(v, 0, None))
        elif f in HU_FEATURES:
            v = np.sign(v) * np.log10(np.abs(v) + 1e-12)
        cols.append(v.astype('float32'))
        names.append(f'shape__{f}')

    # 2) program block: 8-bucket share + log1p(inside count) magnitude.
    #    INSIDE = what the building is.
    mix = _program_mix(gdf['programs_inside'])
    for i, b in enumerate(ROSSETTI_BUCKETS):
        cols.append(mix[:, i]); names.append(f'prog__{b}')
    if 'place_count_inside' in gdf.columns:
        pc = pd.to_numeric(gdf['place_count_inside'], errors='coerce').fillna(0).to_numpy()
    else:
        pc = np.zeros(len(gdf))
    cols.append(np.log1p(pc).astype('float32')); names.append('prog__count_inside_log')

    # 2b) ADJACENT program context = what is within 10m. Denser than INSIDE
    #     (most buildings have no POI inside but do have one nearby), kept as a
    #     SEPARATE block so "is" vs "near" are not conflated.
    mix_adj = _program_mix(gdf['programs_adjacent'])
    for i, b in enumerate(ROSSETTI_BUCKETS):
        cols.append(mix_adj[:, i]); names.append(f'prog_adj__{b}')
    if 'place_count_adjacent' in gdf.columns:
        pca = pd.to_numeric(gdf['place_count_adjacent'], errors='coerce').fillna(0).to_numpy()
    else:
        pca = np.zeros(len(gdf))
    cols.append(np.log1p(pca).astype('float32')); names.append('prog_adj__count_adjacent_log')

    # 3) demographics block — FIXED schema. Always emit all DEMO_COLS so every
    # node vector is identical width across the corpus. SADs lacking a census
    # column (e.g. the Canadian districts: no US ACS) get zeros, plus a
    # demo__present flag so a learned model can tell "missing" from "zero".
    any_demo = np.zeros(len(gdf), dtype='float32')
    for d in DEMO_COLS:
        if d in gdf.columns:
            v = pd.to_numeric(gdf[d], errors='coerce').to_numpy('float64')
            any_demo = np.maximum(any_demo, (~np.isnan(v)).astype('float32'))
            v = np.nan_to_num(v, nan=0.0)
        else:
            v = np.zeros(len(gdf))
        if d in LOG_DEMO:
            v = np.log1p(np.clip(v, 0, None))
        cols.append(v.astype('float32')); names.append(f'demo__{d}')
    cols.append(any_demo); names.append('demo__present')

    # 4) zone flag
    if 'zone' in gdf.columns:
        z = (gdf['zone'].astype(str) == 'interior').to_numpy().astype('float32')
    else:
        z = np.zeros(len(gdf), dtype='float32')
    cols.append(z); names.append('zone__interior')

    X = np.column_stack(cols).astype('float32')
    return X, names


# ─── Edges ───────────────────────────────────────────────────────────────────

def _centroids_metric(gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, gpd.GeoSeries]:
    metric = gdf.estimate_utm_crs()
    geom_m = gdf.geometry.to_crs(metric)
    cent = geom_m.centroid
    coords = np.column_stack([cent.x.to_numpy(), cent.y.to_numpy()])
    return coords, geom_m


def build_edges(gdf: gpd.GeoDataFrame, method: str = 'delaunay',
                max_len_m: float = 120.0, k: int = 6,
                radius_m: float = 80.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (edge_index [2,E], edge_attr [E,2]) as an UNDIRECTED graph stored
    with both directions. edge_attr = [centroid_dist_m, footprint_gap_m]."""
    n = len(gdf)
    coords, geom_m = _centroids_metric(gdf)
    if n < 2:
        return np.zeros((2, 0), 'int64'), np.zeros((0, 2), 'float32')

    pairs: set[tuple[int, int]] = set()
    if method == 'delaunay' and n >= 4:
        tri = Delaunay(coords)
        for s in tri.simplices:
            for a in range(3):
                for b in range(a + 1, 3):
                    i, j = int(s[a]), int(s[b])
                    pairs.add((min(i, j), max(i, j)))
    elif method == 'knn':
        tree = cKDTree(coords)
        kk = min(k + 1, n)
        _, nbr = tree.query(coords, k=kk)
        for i in range(n):
            for j in nbr[i][1:]:
                j = int(j)
                pairs.add((min(i, j), max(i, j)))
    elif method == 'radius':
        tree = cKDTree(coords)
        for i, j in tree.query_pairs(radius_m):
            pairs.add((min(i, j), max(i, j)))
    else:  # delaunay fallback for tiny n
        tree = cKDTree(coords)
        _, nbr = tree.query(coords, k=min(k + 1, n))
        for i in range(n):
            for j in np.atleast_1d(nbr[i])[1:]:
                j = int(j)
                pairs.add((min(i, j), max(i, j)))

    # prune by centroid distance + measure footprint gap
    src, dst, attr = [], [], []
    geoms = geom_m.to_numpy()
    for i, j in pairs:
        cd = float(np.hypot(*(coords[i] - coords[j])))
        if method == 'delaunay' and cd > max_len_m:
            continue
        gap = float(geoms[i].distance(geoms[j]))
        for a, b in ((i, j), (j, i)):
            src.append(a); dst.append(b); attr.append([cd, gap])

    edge_index = np.array([src, dst], dtype='int64') if src else np.zeros((2, 0), 'int64')
    edge_attr = np.array(attr, dtype='float32') if attr else np.zeros((0, 2), 'float32')
    return edge_index, edge_attr


# ─── Tiling (sub-district granularity) ───────────────────────────────────────

def assign_tiles(gdf: gpd.GeoDataFrame, tile_size_m: float) -> np.ndarray:
    coords, _ = _centroids_metric(gdf)
    if len(coords) == 0:
        return np.array([], dtype=object)
    x0, y0 = coords[:, 0].min(), coords[:, 1].min()
    cx = ((coords[:, 0] - x0) // tile_size_m).astype(int)
    cy = ((coords[:, 1] - y0) // tile_size_m).astype(int)
    return np.array([f'{a}_{b}' for a, b in zip(cx, cy)], dtype=object)


def induced_subgraph(X, edge_index, edge_attr, mask):
    """Re-index nodes selected by boolean mask; keep edges fully inside."""
    keep = np.where(mask)[0]
    remap = {old: new for new, old in enumerate(keep)}
    Xs = X[keep]
    if edge_index.shape[1] == 0:
        return Xs, np.zeros((2, 0), 'int64'), np.zeros((0, edge_attr.shape[1]), 'float32'), keep
    s, d = edge_index
    em = np.array([(a in remap and b in remap) for a, b in zip(s, d)])
    s2 = np.array([remap[a] for a in s[em]], 'int64')
    d2 = np.array([remap[b] for b in d[em]], 'int64')
    return Xs, np.vstack([s2, d2]), edge_attr[em], keep


# ─── Build one SAD ───────────────────────────────────────────────────────────

def infer_sad_id(derived_dir: Path) -> str:
    """Recover the SAD id from the derived-dir path across layouts:
    v2  data/<sad>/derived/        -> parent name
    v1  data/derived/per_sad/<sad> -> own name
    """
    if derived_dir.name == 'derived':
        return derived_dir.parent.name
    return derived_dir.name


def find_enriched(data_dir: Path) -> list[tuple[str, Path]]:
    """Locate every buildings_enriched.gpkg under data_dir (any layout),
    skipping the _graphs output dir. Returns sorted (sad_id, derived_dir)."""
    found = {}
    for gpkg in data_dir.rglob('buildings_enriched.gpkg'):
        if '_graphs' in gpkg.parts:
            continue
        derived_dir = gpkg.parent
        found[infer_sad_id(derived_dir)] = derived_dir
    return sorted(found.items())


def build_sad_graph(derived_dir: Path, edge_method: str, max_len_m: float,
                    k: int, radius_m: float, level: str,
                    tile_size_m: float, min_tile_nodes: int,
                    sad_id: str | None = None) -> list[dict]:
    gdf = load_enriched(derived_dir)
    sad_id = sad_id or infer_sad_id(derived_dir)
    X, names = build_node_features(gdf)
    edge_index, edge_attr = build_edges(gdf, edge_method, max_len_m, k, radius_m)

    coords, _ = _centroids_metric(gdf)
    node_meta = pd.DataFrame({
        'building_id': gdf['building_id'].to_numpy(),
        'dominant_program': gdf.get('dominant_program_inside', pd.Series([None] * len(gdf))).to_numpy(),
        'zone': gdf.get('zone', pd.Series(['unknown'] * len(gdf))).to_numpy(),
        'cluster_id': gdf.get('cluster_id', pd.Series([-1] * len(gdf))).to_numpy(),
        'cx_m': coords[:, 0], 'cy_m': coords[:, 1],
    })

    graphs = []
    if level == 'district':
        node_meta['tile_id'] = sad_id
        graphs.append(dict(name=sad_id, sad_id=sad_id, X=X, edge_index=edge_index,
                           edge_attr=edge_attr, node_meta=node_meta, feature_names=names))
    else:  # tile
        tiles = assign_tiles(gdf, tile_size_m)
        node_meta['tile_id'] = tiles
        for t in pd.unique(tiles):
            mask = tiles == t
            if mask.sum() < min_tile_nodes:
                continue
            Xs, ei, ea, keep = induced_subgraph(X, edge_index, edge_attr, mask)
            graphs.append(dict(name=f'{sad_id}__{t}', sad_id=sad_id,
                               X=Xs, edge_index=ei, edge_attr=ea,
                               node_meta=node_meta.iloc[keep].reset_index(drop=True),
                               feature_names=names))
    return graphs


def save_graph(g: dict, out_dir: Path, suffix: str = '') -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    npz = out_dir / f'{g["name"]}{suffix}.npz'
    np.savez_compressed(npz, X=g['X'], edge_index=g['edge_index'],
                        edge_attr=g['edge_attr'],
                        feature_names=np.array(g['feature_names']),
                        node_ids=g['node_meta']['building_id'].to_numpy())
    g['node_meta'].to_parquet(out_dir / f'{g["name"]}_nodes.parquet')
    meta = dict(name=g['name'], sad_id=g['sad_id'],
                n_nodes=int(g['X'].shape[0]),
                n_edges=int(g['edge_index'].shape[1]),
                n_features=int(g['X'].shape[1]),
                feature_names=g['feature_names'])
    (out_dir / f'{g["name"]}_meta.json').write_text(json.dumps(meta, indent=2))
    return meta


# ─── Optional exports ─────────────────────────────────────────────────────────

def to_pyg(g: dict):
    """Lazy PyG Data export. Requires torch + torch_geometric."""
    import torch
    from torch_geometric.data import Data
    return Data(x=torch.tensor(g['X']),
                edge_index=torch.tensor(g['edge_index']),
                edge_attr=torch.tensor(g['edge_attr']))


def to_networkx(g: dict):
    import networkx as nx
    G = nx.Graph()
    for i in range(g['X'].shape[0]):
        G.add_node(i, **{n: float(v) for n, v in zip(g['feature_names'], g['X'][i])})
    s, d = g['edge_index']
    for a, b, at in zip(s, d, g['edge_attr']):
        G.add_edge(int(a), int(b), centroid_dist=float(at[0]), gap=float(at[1]))
    return G


# ─── Corpus driver (--all): build + fit corpus-level normalization ───────────

def run_corpus(data_dir: Path, **kw):
    out_dir = data_dir / '_graphs'
    sads = find_enriched(data_dir)
    print(f'Found {len(sads)} SADs with buildings_enriched.gpkg')
    if not sads:
        print(f'  (searched recursively under {data_dir.resolve()})')

    all_graphs, index = [], []
    for sad_id, d in sads:
        gs = build_sad_graph(d, kw['edge_method'], kw['max_len_m'], kw['k'],
                             kw['radius_m'], kw['level'], kw['tile_size_m'],
                             kw['min_tile_nodes'], sad_id=sad_id)
        for g in gs:
            meta = save_graph(g, out_dir)
            all_graphs.append(g); index.append(meta)
        print(f'  {sad_id}: {len(gs)} graph(s)')

    if not all_graphs:
        print('No graphs built.'); return

    # corpus-level ROBUST standardization (median / IQR) fit across ALL nodes.
    # Robust scaling + clip resists the heavy tails that blow up training.
    stacked = np.vstack([g['X'] for g in all_graphs])
    center = np.median(stacked, axis=0)
    q1, q3 = np.percentile(stacked, [25, 75], axis=0)
    scale = (q3 - q1); scale[scale < 1e-6] = 1.0
    names = all_graphs[0]['feature_names']
    CLIP = 8.0

    corpus = out_dir / '_corpus'; corpus.mkdir(parents=True, exist_ok=True)
    (corpus / 'node_scaler.json').write_text(json.dumps(
        {'feature_names': names, 'method': 'robust_iqr', 'clip': CLIP,
         'center': center.tolist(), 'scale': scale.tolist(),
         'n_nodes': int(stacked.shape[0])}, indent=2))
    for g in all_graphs:
        Xn = np.clip((g['X'] - center) / scale, -CLIP, CLIP)
        np.savez_compressed(out_dir / f'{g["name"]}_norm.npz', X=Xn.astype('float32'),
                            edge_index=g['edge_index'], edge_attr=g['edge_attr'],
                            feature_names=np.array(names),
                            node_ids=g['node_meta']['building_id'].to_numpy())
    (corpus / 'graph_index.json').write_text(json.dumps(
        {'level': kw['level'], 'edge_method': kw['edge_method'],
         'n_graphs': len(index), 'feature_dim': len(names),
         'total_nodes': int(stacked.shape[0]), 'graphs': index}, indent=2))
    print(f'\n[OK] {len(index)} graphs, {stacked.shape[0]} nodes, '
          f'{len(names)} features -> {out_dir}')


def main():
    ap = argparse.ArgumentParser(description='SAD -> attributed urban graph (M17)')
    ap.add_argument('--derived', type=Path, help='one SAD derived dir')
    ap.add_argument('--all', action='store_true', help='build every SAD in --data-dir')
    ap.add_argument('--data-dir', type=Path, default=Path('../data'))
    ap.add_argument('--level', choices=['district', 'tile'], default='district')
    ap.add_argument('--edge-method', choices=['delaunay', 'knn', 'radius'], default='delaunay')
    ap.add_argument('--max-len-m', type=float, default=120.0)
    ap.add_argument('--k', type=int, default=6)
    ap.add_argument('--radius-m', type=float, default=80.0)
    ap.add_argument('--tile-size-m', type=float, default=1000.0)
    ap.add_argument('--min-tile-nodes', type=int, default=8)
    a = ap.parse_args()

    kw = dict(edge_method=a.edge_method, max_len_m=a.max_len_m, k=a.k,
              radius_m=a.radius_m, level=a.level, tile_size_m=a.tile_size_m,
              min_tile_nodes=a.min_tile_nodes)

    if a.all:
        run_corpus(a.data_dir, **kw)
    elif a.derived:
        gs = build_sad_graph(a.derived, **kw)
        for g in gs:
            m = save_graph(g, a.derived / 'graph')
            print(f'  {m["name"]}: {m["n_nodes"]} nodes, {m["n_edges"]} edges, '
                  f'{m["n_features"]} features')
    else:
        ap.error('pass --derived <dir> or --all')


if __name__ == '__main__':
    main()
