"""
typology_signal.py  (diagnostic) - does typology predict building program?

Reads the REAL node features (raw, un-standardized) across the corpus, groups
buildings by their SAD's typology, and reports the mean program mix per
typology. This decides the conditioning question:

  - If typologies show clearly different program profiles  -> the signal exists;
    weak conditional output is a MODEL problem (retrain: lower beta, more epochs).
  - If typologies look about the same                      -> the signal isn't
    there; typology is the wrong thing to condition on (condition on target
    program mix or demographics/density instead).

USAGE
  python typology_signal.py --graphs-dir ..\\data\\_graphs --data-dir ..\\data
"""
from __future__ import annotations
import argparse, glob, json
from pathlib import Path

import numpy as np
import pandas as pd

from vgae_train import sad_from_name
from cvae_train import read_typologies
from graph_builder import ROSSETTI_BUCKETS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--graphs-dir', type=Path, required=True)
    ap.add_argument('--data-dir', type=Path, required=True)
    a = ap.parse_args()

    typ_of = read_typologies(a.data_dir)
    if not typ_of or set(typ_of.values()) <= {'unspecified'}:
        print('WARNING: no real typology labels found.')
        print('  Run setup_typologies.py --xlsx SAD_Typologies.xlsx --data-dir '
              f'{a.data_dir} first, then re-run this.\n')

    # raw (un-normalized) graphs: program shares already in [0,1]
    files = [f for f in sorted(glob.glob(str(a.graphs_dir / '*.npz')))
             if not f.endswith('_norm.npz')]
    in_cols = [f'prog__{b}' for b in ROSSETTI_BUCKETS]
    aj_cols = [f'prog_adj__{b}' for b in ROSSETTI_BUCKETS]

    typs, INS, ADJ = [], [], []
    for f in files:
        d = np.load(f, allow_pickle=True)
        names = list(d['feature_names']); X = d['X']
        sad = sad_from_name(Path(f).stem.replace('_norm', ''))
        ii = [names.index(c) for c in in_cols if c in names]
        ai = [names.index(c) for c in aj_cols if c in names]
        INS.append(X[:, ii] if ii else np.zeros((len(X), 8)))
        ADJ.append(X[:, ai] if ai else np.zeros((len(X), 8)))
        typs += [typ_of.get(sad, 'unspecified')] * len(X)
    typs = np.array(typs); INS = np.vstack(INS); ADJ = np.vstack(ADJ)
    has_adj = bool(ADJ.any())

    def renorm(M):
        s = M.sum(1, keepdims=True); s[s == 0] = 1; return M / s
    COMB = renorm(INS + ADJ) if has_adj else INS

    def separation(M):
        df = pd.DataFrame(M, columns=ROSSETTI_BUCKETS); df['t'] = typs
        tab = df.groupby('t')[ROSSETTI_BUCKETS].mean()
        return float((tab - M.mean(axis=0)).abs().mean(axis=1).mean()), tab.round(3)

    print(f'buildings per typology:\n{pd.Series(typs).value_counts().to_string()}\n')
    cov_in = float((INS.sum(1) > 0).mean())
    cov_cb = float(((INS.sum(1) > 0) | (ADJ.sum(1) > 0)).mean()) if has_adj else cov_in
    print(f'program coverage:  inside {cov_in:.1%}'
          + (f'   inside+adjacent {cov_cb:.1%}' if has_adj else
             '   (no prog_adj block found - rebuild corpus with updated graph_builder)'))

    sep_in, _ = separation(INS)
    print(f'\nseparation INSIDE-only:    {sep_in:.3f}')
    if has_adj:
        sep_aj, _ = separation(ADJ)
        sep_cb, tab_cb = separation(COMB)
        print(f'separation ADJACENT-only:  {sep_aj:.3f}')
        print(f'separation COMBINED:       {sep_cb:.3f}')
        print('\ncombined mean program share by typology:')
        print(tab_cb.to_string())
        print('\ndominant (combined) per typology:')
        for t in tab_cb.index:
            top = tab_cb.loc[t].sort_values(ascending=False).head(3)
            print(f'  {t:16s} ' + ', '.join(f'{k} {v:.2f}' for k, v in top.items()))
    print('\n  >~0.05 = real signal the model can learn;  <~0.02 = little to condition on')


if __name__ == '__main__':
    main()
