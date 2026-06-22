"""
retemplate.py  (Module 23 - retrieval-template generation)

The robust generation route. Instead of sampling node latents from the prior
(which collapses to one average node), it takes a REAL comparable tile as a
canvas -- its actual building footprints and positions from
buildings_enriched.gpkg -- encodes its real, structured node latents, then
re-decodes them under a TARGET condition (typology + demographics). The drawing
keeps the real urban structure; the generation drives program and grain.

  encode(real tile, its own condition)  ->  structured latents mu
  decode(mu, TARGET condition)          ->  re-programmed node attributes
  render real footprints recoloured by the new program (footprints optionally
  rescaled toward the new decoded size)

This avoids both failure modes of the schematic route: no prior-sample collapse
(latents are real), no blob (geometry is real).

INPUT   graphs-dir: <template>_norm.npz + <template>_nodes.parquet  (M17)
        data-dir:   <sad>/derived/buildings_enriched.gpkg            (M5)
        graphs-dir/_cmodel/{cvae.pt, condition_scaler.json}          (M21)
        graphs-dir/_corpus/node_scaler.json                          (M17)
OUTPUT  graphs-dir/_gen/retemplate_<template>_<typology>.png  (+ .csv)

USAGE
  python retemplate.py --graphs-dir ..\\data\\_graphs --data-dir ..\\data \\
      --template 32_District-Detroit_Detroit-MI__0_0 --typology innovation \\
      --income 90000 --median-age 31 --pct-bachelors 0.6 --rescale
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import torch
from shapely.affinity import scale as shp_scale

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Polygon as MplPoly

from vgae_train import sad_from_name
from cvae_train import CondGraphVAE, make_condition, find_profiles, read_typologies, DEMO_BRIEF
from generate import inverse_transform, load_scaler
from graph_builder import ROSSETTI_BUCKETS
from place import PROGRAM_COLORS, CREAM, NAVY, dominant_program


def condition_for_sad(sad_id, data_dir, cscaler):
    """Build a sad's TRUE condition: real typology + its demographics."""
    typ = read_typologies(data_dir).get(sad_id, 'unspecified')
    ov = {}
    profs = find_profiles(data_dir)
    if sad_id in profs:
        d = json.loads(profs[sad_id].read_text()).get('demographics', {}) or {}
        ov = {k: d.get(k) for k in DEMO_BRIEF}
    return make_condition(cscaler, typ, ov)


def real_polygons(sad_id, building_ids, data_dir):
    """Real footprints for the template's nodes, in metric CRS, ordered to match."""
    gpkgs = list((data_dir).rglob('buildings_enriched.gpkg'))
    path = next((g for g in gpkgs if sad_id in g.parts or sad_id == g.parent.parent.name), None)
    if path is None:
        path = next((g for g in gpkgs if sad_id in str(g)), None)
    gdf = gpd.read_file(path, layer='buildings')
    gdf = gdf.to_crs(gdf.estimate_utm_crs())
    gdf = gdf.set_index('building_id').reindex(building_ids)
    return gdf.geometry


def main():
    ap = argparse.ArgumentParser(description='Retrieval-template generation (M23)')
    ap.add_argument('--graphs-dir', type=Path, required=True)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--template', type=str, required=True)
    ap.add_argument('--typology', type=str, required=True)
    ap.add_argument('--income', type=float, default=None)
    ap.add_argument('--median-age', type=float, default=None)
    ap.add_argument('--population', type=float, default=None)
    ap.add_argument('--pct-bachelors', type=float, default=None)
    ap.add_argument('--rescale', action='store_true',
                    help='rescale footprints toward the new decoded size')
    ap.add_argument('--mono', action='store_true')
    a = ap.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    cm = a.graphs_dir / '_cmodel'
    ckpt = torch.load(cm / 'cvae.pt', map_location=device, weights_only=False)
    cscaler = json.loads((cm / 'condition_scaler.json').read_text())
    node_scaler = load_scaler(a.graphs_dir)
    model = CondGraphVAE(**ckpt['cfg']).to(device)
    model.load_state_dict(ckpt['state_dict']); model.eval()

    # resolve template (allow substring)
    npzs = sorted(a.graphs_dir.glob('*_norm.npz'))
    match = next((p for p in npzs if p.stem.replace('_norm', '') == a.template), None) \
        or next((p for p in npzs if a.template.lower() in p.stem.lower()), None)
    if match is None:
        raise SystemExit(f'no template tile matching "{a.template}"')
    name = match.stem.replace('_norm', '')
    sad = sad_from_name(name)
    d = np.load(match, allow_pickle=True)
    X = torch.tensor(d['X'], dtype=torch.float32).clamp_(-10, 10).to(device)
    ei = torch.tensor(d['edge_index'], dtype=torch.long).to(device)
    meta = pd.read_parquet(a.graphs_dir / f'{name}_nodes.parquet')

    c_src = torch.tensor(condition_for_sad(sad, a.data_dir, cscaler), device=device).repeat(X.size(0), 1)
    overrides = dict(total_population=a.population, median_household_income=a.income,
                     median_age=a.median_age, pct_bachelors_or_higher=a.pct_bachelors)
    c_tgt = torch.tensor(make_condition(cscaler, a.typology, overrides),
                         device=device).repeat(X.size(0), 1)

    with torch.no_grad():
        _, mu, _ = model.encode(X, c_src, ei, sample=False)   # real structured latents
        xhat = model.dec(mu, c_tgt)                           # re-decode toward target
    df = inverse_transform(xhat.cpu().numpy(), node_scaler)

    polys = real_polygons(sad, meta['building_id'].tolist(), a.data_dir)

    # render real geometry recoloured (and optionally resized) by re-decoded attrs
    fig, ax = plt.subplots(figsize=(9, 9), dpi=150)
    fig.patch.set_facecolor(CREAM); ax.set_facecolor('white' if a.mono else CREAM)
    used = set()
    for i, geom in enumerate(polys):
        if geom is None or geom.is_empty:
            continue
        g = geom
        if a.rescale and 'shape__area_m2' in df.columns and g.area > 1:
            f = float(np.clip(np.sqrt(max(df['shape__area_m2'].iloc[i], 1) / g.area), 0.3, 3.0))
            g = shp_scale(g, xfact=f, yfact=f, origin='centroid')
        prog = dominant_program(df.iloc[i]); used.add(prog)
        color = 'black' if a.mono else PROGRAM_COLORS.get(prog, '#C9C2B4')
        for poly in (g.geoms if g.geom_type == 'MultiPolygon' else [g]):
            xs, ys = poly.exterior.xy
            ax.add_patch(MplPoly(np.column_stack([xs, ys]), closed=True,
                                 facecolor=color, edgecolor='none', zorder=2))
    ax.set_aspect('equal'); ax.autoscale_view()
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_color('#CCC4B6')
    ax.set_title(f'{sad}\nreal fabric re-programmed as "{a.typology}"', color=NAVY, fontsize=12, pad=12)
    if not a.mono:
        leg = [Patch(facecolor=PROGRAM_COLORS[p], label=p.replace('_', ' '))
               for p in ROSSETTI_BUCKETS if p in used]
        ax.legend(handles=leg, frameon=False, fontsize=8, loc='upper right')

    out = a.graphs_dir / '_gen'; out.mkdir(parents=True, exist_ok=True)
    stem = f'retemplate_{name}_{a.typology.lower()}'
    df.insert(0, 'building_id', meta['building_id'].values)
    df.to_csv(out / f'{stem}.csv', index=False)
    png = out / (f'{stem}_mono.png' if a.mono else f'{stem}.png')
    fig.tight_layout(); fig.savefig(png, facecolor=CREAM); plt.close(fig)

    pcols = [f'prog__{b}' for b in ROSSETTI_BUCKETS if f'prog__{b}' in df.columns]
    mix = df[pcols].mean().sort_values(ascending=False)
    print(f'[OK] re-programmed {sad} ({len(polys)} real buildings) as {a.typology}')
    print('  new mean program mix: ' + ', '.join(
        f'{c.split("__")[1]} {v:.2f}' for c, v in mix.head(4).items()))
    print(f'  -> {png}')


if __name__ == '__main__':
    main()
