"""
program_structure.py - does program have STRUCTURE inside SADs?

Program failed as a district discriminator (separation 0.006). The useful design
question is different: WITHIN a district, does program depend on where a building
sits (interior vs exterior zone) or what form it is (anchor vs fine-grain)? If so,
program is proposable CONDITIONAL on form/zone -- "retail concentrates in the
interior near large-footprint anchors" -- even if it can't tell one district from
another.

Tests among PROGRAMMED buildings only (removes the ~79% coverage dilution):
  - program coverage overall and by zone
  - program mix by zone (interior / exterior) + separation
  - program mix by form family + separation

USAGE
  python program_structure.py --graphs-dir ..\\data\\_graphs
"""
from __future__ import annotations
import argparse, glob
from pathlib import Path

import numpy as np
import pandas as pd

from vgae_train import sad_from_name
from graph_builder import ROSSETTI_BUCKETS


def separation(M, groups):
    df = pd.DataFrame(M, columns=ROSSETTI_BUCKETS); df['g'] = groups
    tab = df.groupby('g')[ROSSETTI_BUCKETS].mean()
    overall = M.mean(axis=0)
    return float((tab - overall).abs().mean(axis=1).mean()), tab.round(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--graphs-dir', type=Path, required=True)
    a = ap.parse_args()

    files = sorted(glob.glob(str(a.graphs_dir / '*_norm.npz')))
    names = list(np.load(files[0], allow_pickle=True)['feature_names'])
    pin = [names.index(f'prog__{b}') for b in ROSSETTI_BUCKETS]
    zi = names.index('zone__interior') if 'zone__interior' in names else None

    # form labels (per graph_name, node) from M24
    fl = pd.read_parquet(a.graphs_dir / '_form' / 'form_labels.parquet')
    form_by_graph = {g: sub.sort_values('node')['form_type'].to_numpy()
                     for g, sub in fl.groupby('graph_name')}

    PROG, ZONE, FORM = [], [], []
    for f in files:
        d = np.load(f, allow_pickle=True); X = d['X']
        gn = Path(f).stem.replace('_norm', '')
        PROG.append(X[:, pin])
        ZONE.append(np.where(X[:, zi] > 0.5, 'interior', 'exterior') if zi is not None
                    else np.array(['?'] * len(X)))
        fb = form_by_graph.get(gn, np.full(len(X), -1))
        FORM.append(fb[:len(X)] if len(fb) >= len(X) else np.full(len(X), -1))
    PROG = np.vstack(PROG); ZONE = np.concatenate(ZONE); FORM = np.concatenate(FORM)

    prog_mass = PROG.sum(axis=1)
    programmed = prog_mass > 0
    print(f'buildings: {len(PROG)}   programmed: {programmed.mean():.1%}')
    if zi is not None:
        for z in ['interior', 'exterior']:
            m = ZONE == z
            if m.any():
                print(f'  coverage {z}: {(programmed & m).sum() / max(m.sum(),1):.1%}')

    P = PROG[programmed]
    print('\n--- among PROGRAMMED buildings ---')

    if zi is not None:
        sep_z, tab_z = separation(P, ZONE[programmed])
        print(f'\nprogram mix by ZONE  (separation {sep_z:.3f}):')
        print(tab_z.to_string())

    valid = FORM[programmed] >= 0
    if valid.any():
        sep_f, tab_f = separation(P[valid], FORM[programmed][valid])
        print(f'\nprogram mix by FORM FAMILY  (separation {sep_f:.3f}):')
        print(tab_f.to_string())
        print('\ndominant program per form family:')
        for fam in tab_f.index:
            top = tab_f.loc[fam].sort_values(ascending=False).head(3)
            print(f'  family {fam}: ' + ', '.join(f'{k} {v:.2f}' for k, v in top.items()))

    print('\n  district-level program separation was 0.006;  >~0.05 here = real '
          'intra-SAD structure program can be conditioned on')


if __name__ == '__main__':
    main()
