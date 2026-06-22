"""
form_profile.py  (Module 25 - per-district form fingerprints + form retrieval)

Turns the validated global form families (form_cluster.py / M24) into:
  - a readable per-district FORM FINGERPRINT: the composition of morphological
    families that make up each SAD (an interpretive layer for designers/leadership)
  - FORM-BASED RETRIEVAL: which districts are most alike in built form -- a second,
    interpretable retrieval channel complementing the latent retrieval (M19)

No retrain: reads the labels M24 already produced. Form is the channel the data
supports (separation 0.069 vs program 0.006), so this is the honest signature.

INPUT   data/_graphs/_form/{form_labels.parquet, form_clusters.json}
OUTPUT  data/_graphs/_form/
          form_profiles.csv     district x family-share + named columns
          form_neighbors.csv     top-k form-similar districts per district
          (readable summary to stdout)

USAGE
  python form_profile.py --graphs-dir ..\\data\\_graphs --query 32_District-Detroit --k 5
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize
from sklearn.metrics.pairwise import cosine_similarity


def family_name(p: dict) -> str:
    """Short human-readable descriptor from a family's geometric profile."""
    size = 'large' if p['area_m2'] >= 800 else ('small' if p['area_m2'] < 80 else 'mid')
    tags = [size]
    if p.get('bbox_elongation', 1) >= 2.3:
        tags.append('elongated')
    if p.get('vertex_count', 0) >= 14:
        tags.append('complex')
    elif p.get('compactness', 0) >= 0.72:
        tags.append('compact')
    return '-'.join(tags)


def short(sad_id: str) -> str:
    parts = sad_id.split('_')
    return parts[1] if len(parts) > 1 else sad_id


def main():
    ap = argparse.ArgumentParser(description='Per-district form fingerprints (M25)')
    ap.add_argument('--graphs-dir', type=Path, required=True)
    ap.add_argument('--query', type=str, default=None)
    ap.add_argument('--k', type=int, default=5)
    a = ap.parse_args()

    form_dir = a.graphs_dir / '_form'
    labels = pd.read_parquet(form_dir / 'form_labels.parquet')
    clusters = json.loads((form_dir / 'form_clusters.json').read_text())
    K = clusters['k']
    profiles = clusters['profiles']

    # disambiguate duplicate descriptors with an index suffix
    raw_names = [family_name(p) for p in profiles]
    seen, names = {}, []
    for nm in raw_names:
        seen[nm] = seen.get(nm, 0) + 1
        names.append(f'{nm}_{seen[nm]}' if raw_names.count(nm) > 1 else nm)

    # per-district family composition
    comp = (labels.groupby('sad_id')['form_type']
            .value_counts(normalize=True).unstack(fill_value=0)
            .reindex(columns=range(K), fill_value=0))
    comp.columns = names
    comp = comp.round(3)
    comp.to_csv(form_dir / 'form_profiles.csv')

    # form-based retrieval (cosine over composition vectors)
    V = normalize(comp.to_numpy())
    S = cosine_similarity(V); np.fill_diagonal(S, -np.inf)
    ids = comp.index.to_list()
    nrows = []
    for i, sad in enumerate(ids):
        for rank, j in enumerate(np.argsort(S[i])[::-1][:a.k], 1):
            nrows.append(dict(sad_id=sad, rank=rank, neighbor=ids[j],
                              similarity=round(float(S[i, j]), 4)))
    pd.DataFrame(nrows).to_csv(form_dir / 'form_neighbors.csv', index=False)

    print(f'form families ({K}):')
    for i, nm in enumerate(names):
        p = profiles[i]
        print(f'  {i} {nm:18s} area {p["area_m2"]:.0f}m2, elong {p["bbox_elongation"]:.2f}, '
              f'verts {p["vertex_count"]:.1f}')

    print(f'\n[OK] {len(comp)} district form fingerprints -> {form_dir/"form_profiles.csv"}')

    if a.query:
        m = [s for s in ids if a.query.lower() in s.lower()]
        if not m:
            print(f'  no district matching "{a.query}"'); return
        q = m[0]
        top = comp.loc[q].sort_values(ascending=False).head(4)
        print(f'\n{q} form fingerprint:')
        for nm, v in top.items():
            print(f'    {nm:18s} {v:.0%}')
        print(f'  most form-similar districts:')
        sub = pd.DataFrame(nrows); sub = sub[sub.sad_id == q].sort_values('rank')
        for _, r in sub.iterrows():
            print(f'    {r["rank"]}. {short(r["neighbor"]):28s} cos={r["similarity"]:.3f}')


if __name__ == '__main__':
    main()
