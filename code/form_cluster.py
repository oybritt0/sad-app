"""
form_cluster.py  (Module 24 - corpus-wide form families + separation diagnostic)

Clusters ALL buildings' shape features into shared morphological families with a
SINGLE global clustering (so 'family 3' means the same form in every district,
unlike M2b's per-SAD cluster_id). Then measures, with the same metric that
killed the program channel, how strongly form-type distribution separates across
districts and typologies -- against ~100% coverage (every building has geometry).

The question this answers: is form-type a live, conditionable/retrievable signal
(unlike program at separation 0.006)? Measure before wiring it into the model.

INPUT   data/_graphs/*_norm.npz        (shape__ features, corpus-standardized)
        data/_graphs/_corpus/node_scaler.json   (to interpret centroids)
        data-dir (for real typology labels)
OUTPUT  data/_graphs/_form/
          form_clusters.json    k, silhouette, interpretable family profiles
          form_labels.parquet   graph_name, node, sad_id, form_type
          separation report (stdout)

USAGE
  python form_cluster.py --graphs-dir ..\\data\\_graphs --data-dir ..\\data
  python form_cluster.py --graphs-dir ..\\data\\_graphs --data-dir ..\\data --k 8
"""
from __future__ import annotations
import argparse, glob, json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from vgae_train import sad_from_name
from cvae_train import read_typologies
from graph_builder import SIZE_FEATURES, HU_FEATURES

# A few interpretable features to print per family (real units)
SHOW = ['area_m2', 'perimeter_m', 'compactness', 'solidity',
        'bbox_elongation', 'vertex_count']


def load_shape_matrix(graphs_dir: Path):
    files = sorted(glob.glob(str(graphs_dir / '*_norm.npz')))
    names = list(np.load(files[0], allow_pickle=True)['feature_names'])
    shp_idx = [i for i, n in enumerate(names) if n.startswith('shape__')]
    shp_names = [names[i] for i in shp_idx]
    X, sad, gname, node = [], [], [], []
    for f in files:
        d = np.load(f, allow_pickle=True)
        Xi = d['X'][:, shp_idx]
        gn = Path(f).stem.replace('_norm', '')
        X.append(Xi)
        sad += [sad_from_name(gn)] * len(Xi)
        gname += [gn] * len(Xi)
        node += list(range(len(Xi)))
    return np.vstack(X), np.array(sad), np.array(gname), np.array(node), shp_names


def choose_k(X, krange, seed):
    samp = X[np.random.default_rng(seed).choice(len(X), min(5000, len(X)), replace=False)]
    best_k, best_s, scores = krange[0], -1, {}
    for k in krange:
        lab = KMeans(n_clusters=k, n_init=5, random_state=seed).fit_predict(samp)
        s = silhouette_score(samp, lab)
        scores[k] = round(float(s), 3)
        if s > best_s:
            best_s, best_k = s, k
    return best_k, best_s, scores


def interpret(centers_std, shp_names, scaler):
    """Inverse-transform standardized centroids to real units for a few features."""
    nm = scaler['feature_names']; center = np.array(scaler['center']); scale = np.array(scaler['scale'])
    full_idx = {n: i for i, n in enumerate(nm)}
    rows = []
    for c in centers_std:
        prof = {}
        for j, sn in enumerate(shp_names):
            base = sn.split('__', 1)[1]
            if base not in SHOW:
                continue
            fi = full_idx[sn]
            x = c[j] * scale[fi] + center[fi]
            if base in SIZE_FEATURES:
                x = float(np.expm1(np.clip(x, 0, 14)))
            elif base in HU_FEATURES:
                x = float(np.sign(x) * 10 ** np.clip(abs(x), 0, 12))
            prof[base] = round(float(x), 2)
        rows.append(prof)
    return pd.DataFrame(rows)[[s for s in SHOW]]


def separation(labels, groups, k):
    """Mean |group form-type distribution - corpus distribution| across groups."""
    df = pd.DataFrame({'g': groups, 'f': labels})
    corpus = np.bincount(labels, minlength=k) / len(labels)
    devs = []
    for _, sub in df.groupby('g'):
        dist = np.bincount(sub['f'], minlength=k) / len(sub)
        devs.append(np.abs(dist - corpus).mean())
    return float(np.mean(devs))


def main():
    ap = argparse.ArgumentParser(description='Corpus form families + diagnostic (M24)')
    ap.add_argument('--graphs-dir', type=Path, required=True)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--k', type=int, default=None, help='families; default auto via silhouette')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    X, sad, gname, node, shp_names = load_shape_matrix(a.graphs_dir)
    scaler = json.loads((a.graphs_dir / '_corpus' / 'node_scaler.json').read_text())
    print(f'clustering {len(X)} buildings on {len(shp_names)} shape features')

    if a.k is None:
        k, sil, scores = choose_k(X, range(4, 11), a.seed)
        print(f'  silhouette by k: {scores}  -> chose k={k}')
    else:
        k = a.k; sil = None

    km = KMeans(n_clusters=k, n_init=10, random_state=a.seed).fit(X)
    labels = km.labels_
    if sil is None:
        s = X[np.random.default_rng(a.seed).choice(len(X), min(5000, len(X)), replace=False)]
        sil = float(silhouette_score(s, km.predict(s)))

    typ_of = read_typologies(a.data_dir)
    typ = np.array([typ_of.get(s, 'unspecified') for s in sad])

    sep_district = separation(labels, sad, k)
    sep_typology = separation(labels, typ, k)

    prof = interpret(km.cluster_centers_, shp_names, scaler)
    sizes = np.bincount(labels, minlength=k) / len(labels)

    print(f'\nform families (k={k}, silhouette={sil:.3f}):')
    for i in range(k):
        p = prof.iloc[i]
        print(f'  family {i} ({sizes[i]:.0%}): area {p.area_m2:.0f}m2, elong {p.bbox_elongation:.2f}, '
              f'compact {p.compactness:.2f}, solidity {p.solidity:.2f}, verts {p.vertex_count:.1f}')

    print(f'\nprogram coverage was ~21%; form coverage: 100% (every building has geometry)')
    print(f'separation across DISTRICTS:  {sep_district:.3f}')
    print(f'separation across TYPOLOGIES: {sep_typology:.3f}')
    print('  (program separation was 0.006-0.009;  >~0.05 = strong, conditionable signal)')

    out = a.graphs_dir / '_form'; out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({'graph_name': gname, 'node': node, 'sad_id': sad,
                  'form_type': labels}).to_parquet(out / 'form_labels.parquet')
    (out / 'form_clusters.json').write_text(json.dumps(dict(
        k=int(k), silhouette=round(sil, 3), feature_names=shp_names,
        centers_std=km.cluster_centers_.tolist(),
        family_share=sizes.tolist(), profiles=prof.to_dict('records'),
        sep_district=round(sep_district, 4), sep_typology=round(sep_typology, 4)), indent=2))
    print(f'\n[OK] labels + clusters -> {out}')


if __name__ == '__main__':
    main()
