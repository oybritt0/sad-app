"""
module_8b_census_field.py

Computes a 2D PCA projection of economic indicators across all SADs and
years, producing coordinates that drive the census-driven Field view.

USAGE
    python module_8b_census_field.py --data-dir <data_dir>

OUTPUT
    <data_dir>/_compare_ui/census_pca.json

DESIGN
- Features: 4 economic indicators per (district, year)
    median_household_income_pop_weighted
    median_home_value_pop_weighted
    median_gross_rent_pop_weighted
    unemployment_rate
- Fixed basis: PCA computed across ALL (district x year) observations so
  the axes are stable for trajectory comparison. A single district's
  movement through years is meaningful because every year uses the same
  basis.
- Z-score normalize each feature before PCA so no single dollar-scaled
  variable dominates.
- Observations with any missing feature are dropped (with a count printed
  at the end so the operator knows how much data was excluded).
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np


ECONOMIC_FIELDS = [
    'median_household_income_pop_weighted',
    'median_home_value_pop_weighted',
    'median_gross_rent_pop_weighted',
    'unemployment_rate',
]
SHORT_LABELS = ['income', 'home_value', 'gross_rent', 'unemployment']


def _safe_float(v):
    try:
        f = float(v)
        if math.isfinite(f):
            return f
    except (TypeError, ValueError):
        pass
    return None


def main():
    p = argparse.ArgumentParser(
        description="Build the census-driven Field PCA projection.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument('--data-dir', type=Path, required=True,
                   help='Corpus data folder containing per-SAD subfolders')
    args = p.parse_args()

    if not args.data_dir.is_dir():
        sys.exit(f"not a directory: {args.data_dir}")

    # Gather all (district, year) observations with complete features
    observations = []
    missing_counts = {'no_ts_file': 0, 'incomplete_year': 0}

    for sad_dir in sorted(args.data_dir.iterdir()):
        if not sad_dir.is_dir() or sad_dir.name.startswith('_'):
            continue
        ts_path = sad_dir / 'derived' / 'census_timeseries.json'
        if not ts_path.exists():
            missing_counts['no_ts_file'] += 1
            continue
        try:
            ts = json.loads(ts_path.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"  [warn] could not parse {ts_path}: {e}")
            continue

        sad_id = ts.get('sad_id', sad_dir.name)
        sad_name = ts.get('sad_name', sad_id)
        typology = ts.get('typology') or ts.get('primary_typology') or ''
        years = ts.get('years_pulled', [])
        summaries = ts.get('summaries', {})

        for year in years:
            summary = summaries.get(str(year), {})
            feats = [_safe_float(summary.get(f)) for f in ECONOMIC_FIELDS]
            if any(v is None for v in feats):
                missing_counts['incomplete_year'] += 1
                continue
            observations.append({
                'sad_id': sad_id,
                'sad_name': sad_name,
                'typology': typology,
                'year': int(year),
                'features': feats,
            })

    if not observations:
        sys.exit("no observations with complete economic features found")

    print(f"  observations: {len(observations)} "
          f"(skipped {missing_counts['incomplete_year']} year-rows with missing features, "
          f"{missing_counts['no_ts_file']} SADs with no time-series file)")

    n_districts = len({o['sad_id'] for o in observations})
    print(f"  districts represented: {n_districts}")

    # Build feature matrix
    X = np.array([o['features'] for o in observations], dtype=float)

    # Z-score standardization across the whole corpus
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    if any(s == 0 for s in stds):
        sys.exit("a feature has zero variance - cannot z-score normalize")
    Z = (X - means) / stds

    # PCA via SVD (Z is already centered because z-scoring subtracts the mean)
    U, s, Vt = np.linalg.svd(Z, full_matrices=False)
    scores = U * s              # PC scores: (n_obs, n_features)
    var_explained = (s ** 2) / (s ** 2).sum()

    print(f"  PC1 variance explained: {var_explained[0]:.3f}")
    print(f"  PC2 variance explained: {var_explained[1]:.3f}")
    print(f"  PC1 + PC2 cumulative:   {var_explained[:2].sum():.3f}")
    print(f"  PC1 loadings: " + ", ".join(
        f"{lbl}={val:+.2f}" for lbl, val in zip(SHORT_LABELS, Vt[0])))
    print(f"  PC2 loadings: " + ", ".join(
        f"{lbl}={val:+.2f}" for lbl, val in zip(SHORT_LABELS, Vt[1])))

    # Build the coords structure: {sad_id: {sad_name, typology, positions: {year: [pc1, pc2]}}}
    coords = {}
    for i, o in enumerate(observations):
        sid = o['sad_id']
        if sid not in coords:
            coords[sid] = {
                'sad_name': o['sad_name'],
                'typology': o['typology'],
                'positions': {},
            }
        coords[sid]['positions'][str(o['year'])] = [
            float(scores[i, 0]),
            float(scores[i, 1]),
        ]

    bounds = {
        'pc1_min': float(scores[:, 0].min()),
        'pc1_max': float(scores[:, 0].max()),
        'pc2_min': float(scores[:, 1].min()),
        'pc2_max': float(scores[:, 1].max()),
    }

    output = {
        'metric_keys': SHORT_LABELS,
        'metric_full_names': ECONOMIC_FIELDS,
        'years': sorted({o['year'] for o in observations}),
        'n_observations': len(observations),
        'n_districts': n_districts,
        'standardization': {
            'means': means.tolist(),
            'stds':  stds.tolist(),
        },
        'pca': {
            'pc1_loadings': dict(zip(SHORT_LABELS, [float(v) for v in Vt[0]])),
            'pc2_loadings': dict(zip(SHORT_LABELS, [float(v) for v in Vt[1]])),
            'pc1_variance_explained': float(var_explained[0]),
            'pc2_variance_explained': float(var_explained[1]),
        },
        'bounds': bounds,
        'coords': coords,
    }

    out_dir = args.data_dir / '_compare_ui'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'census_pca.json'
    out_path.write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(f"\n  wrote {out_path}")


if __name__ == '__main__':
    main()
