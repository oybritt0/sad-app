"""
compare_board.py  (Module 28 - retrieval-and-overlay instrument)

The honest tool. Given a site (a real SAD for now), it:
  - retrieves its real comparables by FORM fingerprint (M25) -- the channel that
    works, corroborated by the latent retrieval (M19)
  - lays the site and its comparables side by side as REAL figure-grounds drawn
    from buildings_enriched.gpkg (real positions, real footprints -- legible by
    construction, no generated mush)
  - annotates each with form composition, the (sparse) program tendency with its
    coverage caveat, and demographics

No generation: it shows what the data actually supports. Re-graining (retemplate)
is an optional what-if layered on top of this, not the main claim.

INPUT   _form/{form_profiles.csv, form_clusters.json}   (M24/M25)
        <sad>/derived/buildings_enriched.gpkg, district_profile.json
OUTPUT  _gen/board_<target>.png   +  board_<target>.csv

USAGE
  python compare_board.py --graphs-dir ..\\data\\_graphs --data-dir ..\\data --target 32_District-Detroit --k 3
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.preprocessing import normalize
from sklearn.metrics.pairwise import cosine_similarity

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly

from cvae_train import find_profiles
from graph_builder import ROSSETTI_BUCKETS
from form_profile import family_name, short

CREAM = '#F4EFE6'; NAVY = '#1B2845'; CORAL = '#D97757'


def gpkg_for(sad_id, data_dir):
    for g in data_dir.rglob('buildings_enriched.gpkg'):
        if '_graphs' in g.parts:
            continue
        if sad_id == g.parent.parent.name or sad_id in str(g):
            return g
    return None


def district_stats(sad_id, data_dir):
    """Real plan + headline stats for one SAD."""
    path = gpkg_for(sad_id, data_dir)
    gdf = gpd.read_file(path, layer='buildings').to_crs('EPSG:4326')
    gdf = gdf.to_crs(gdf.estimate_utm_crs())
    n = len(gdf)
    med_area = float(pd.to_numeric(gdf.get('area_m2', pd.Series([np.nan])), errors='coerce').median())

    # program tendency among programmed buildings (+ coverage)
    progs = gdf.get('programs_inside', pd.Series([''] * n)).fillna('').astype(str)
    cnt = {b: 0 for b in ROSSETTI_BUCKETS}; programmed = 0
    for s in progs:
        toks = [t for t in s.split(';') if t]
        if toks:
            programmed += 1
        for t in toks:
            if t in cnt:
                cnt[t] += 1
    cov = programmed / max(n, 1)
    tot = sum(cnt.values()) or 1
    prog_top = sorted(((b, c / tot) for b, c in cnt.items() if c), key=lambda x: -x[1])[:3]

    demo = {}
    profs = find_profiles(data_dir)
    if sad_id in profs:
        demo = json.loads(profs[sad_id].read_text()).get('demographics', {}) or {}
    return dict(gdf=gdf, n=n, med_area=med_area, coverage=cov, prog_top=prog_top, demo=demo)


def panel(ax, sad_id, stats, fingerprint, fam_names, is_target):
    gdf = stats['gdf']
    edge = CORAL if is_target else '#CCC4B6'
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        for poly in (geom.geoms if geom.geom_type == 'MultiPolygon' else [geom]):
            xs, ys = poly.exterior.xy
            ax.add_patch(MplPoly(np.column_stack([xs, ys]), closed=True,
                                 facecolor=NAVY, edgecolor='none', zorder=2))
    ax.set_aspect('equal'); ax.autoscale_view(); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(edge); s.set_linewidth(2.5 if is_target else 1.0)

    top_fam = fingerprint.sort_values(ascending=False).head(2)
    fam_txt = ', '.join(f'{c} {v:.0%}' for c, v in top_fam.items())
    prog_txt = ', '.join(f'{b.split("_")[0]} {v:.0%}' for b, v in stats['prog_top']) or 'n/a'
    inc = stats['demo'].get('median_household_income')
    inc_txt = f"${inc/1000:.0f}k" if inc else 'n/a'
    title = short(sad_id) + ('  (SITE)' if is_target else '')
    ax.set_title(title, color=(CORAL if is_target else NAVY), fontsize=12,
                 fontweight='bold' if is_target else 'normal', pad=8)
    ax.set_xlabel(f"{stats['n']} bldgs · med {stats['med_area']:.0f}m²\n"
                  f"form: {fam_txt}\n"
                  f"program ({stats['coverage']:.0%} tagged): {prog_txt}\n"
                  f"median income: {inc_txt}",
                  fontsize=8, color='#5A5347', labelpad=8)


def main():
    ap = argparse.ArgumentParser(description='Retrieval-and-overlay board (M28)')
    ap.add_argument('--graphs-dir', type=Path, required=True)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--target', type=str, required=True)
    ap.add_argument('--k', type=int, default=3)
    a = ap.parse_args()

    form_dir = a.graphs_dir / '_form'
    prof = pd.read_csv(form_dir / 'form_profiles.csv', index_col=0)
    clusters = json.loads((form_dir / 'form_clusters.json').read_text())
    fam_names = [family_name(p) for p in clusters['profiles']]

    ids = list(prof.index)
    match = [s for s in ids if a.target.lower() in s.lower()]
    if not match:
        raise SystemExit(f'no district matching "{a.target}"')
    target = match[0]

    V = normalize(prof.to_numpy()); ti = ids.index(target)
    sims = cosine_similarity(V[ti:ti + 1], V)[0]; sims[ti] = -np.inf
    comps = [ids[j] for j in np.argsort(sims)[::-1][:a.k]]
    panel_ids = [target] + comps

    stats = {s: district_stats(s, a.data_dir) for s in panel_ids}

    fig, axes = plt.subplots(1, len(panel_ids), figsize=(5 * len(panel_ids), 5.8), dpi=150)
    fig.patch.set_facecolor(CREAM)
    if len(panel_ids) == 1:
        axes = [axes]
    for ax, s in zip(axes, panel_ids):
        ax.set_facecolor(CREAM)
        panel(ax, s, stats[s], prof.loc[s], fam_names, s == target)
    fig.suptitle(f'{short(target)} and its closest real comparables (by form)',
                 color=NAVY, fontsize=15, y=0.99)
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    out = a.graphs_dir / '_gen'; out.mkdir(parents=True, exist_ok=True)
    png = out / f'board_{target}.png'
    fig.savefig(png, facecolor=CREAM); plt.close(fig)

    # comparison table
    rows = []
    for s in panel_ids:
        st = stats[s]
        rows.append(dict(sad_id=s, role=('site' if s == target else 'comparable'),
                         form_sim=(1.0 if s == target else round(float(sims[ids.index(s)]), 3)),
                         n_buildings=st['n'], median_area_m2=round(st['med_area'], 1),
                         program_coverage=round(st['coverage'], 3),
                         top_program=(st['prog_top'][0][0] if st['prog_top'] else None)))
    pd.DataFrame(rows).to_csv(out / f'board_{target}.csv', index=False)

    print(f'site: {target}')
    print('closest by form:')
    for s in comps:
        print(f'  {short(s):28s} cos={sims[ids.index(s)]:.3f}  '
              f'{stats[s]["n"]} bldgs, med {stats[s]["med_area"]:.0f}m2')
    print(f'[OK] -> {png}')


if __name__ == '__main__':
    main()
