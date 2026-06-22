"""
place.py  (Module 22 - schematic placement: attributed graph -> figure-ground)

Turns a generated/decoded attributed graph (generate.py / cvae_train.py output)
into a drawn plan: lay nodes out from the predicted adjacency (force-directed),
stamp a footprint per node sized/shaped by its decoded morphology, fill by
dominant program. SCHEMATIC route -- the void/street structure is synthetic.
The retrieval-template route (next module) borrows real structure for realism.
This is enough to SEE a generated scenario as a figure-ground.

NOTE: footprints are axis-aligned because the corpus has no per-building
orientation feature yet. Adding orientation to M17 is the upgrade that lets
footprints rotate to a street grid.

INPUT   _gen/<scenario>.csv        node attrs (shape__area_m2, shape__bbox_elongation, prog__*)
        _gen/<scenario>_edges.csv  predicted adjacency (src,dst)
OUTPUT  _gen/<scenario>_plan.png   program-coloured figure-ground (+ --mono for black/white)

USAGE
  python place.py --csv ..\\data\\_graphs\\_gen\\cgen_innovation_150.csv --extent-m 1000
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx
from shapely.geometry import box
from shapely.affinity import translate

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.patches import Polygon as MplPoly

from graph_builder import ROSSETTI_BUCKETS

CREAM = '#F4EFE6'; NAVY = '#1B2845'
PROGRAM_COLORS = {
    'sport': '#D97757', 'residential': '#5C8B89', 'office': '#1B2845',
    'retail_food_entertainment': '#D4A93A', 'hotel': '#7C5E9C',
    'parking': '#9A9080', 'open_space': '#639922', 'other': '#C9C2B4',
}


def dominant_program(row) -> str:
    shares = {b: row.get(f'prog__{b}', 0.0) for b in ROSSETTI_BUCKETS}
    return max(shares, key=shares.get)


def footprint(area_m2, elong):
    """Axis-aligned rectangle of the given area and aspect ratio, centred at 0."""
    area = float(np.clip(area_m2, 20, 40000))
    e = float(np.clip(elong, 1.0, 6.0))
    w = np.sqrt(area * e); h = np.sqrt(area / e)
    return box(-w / 2, -h / 2, w / 2, h / 2)


def layout(nodes: pd.DataFrame, edges: pd.DataFrame, extent_m: float, seed: int):
    G = nx.Graph(); G.add_nodes_from(nodes['node'].tolist())
    if edges is not None and len(edges):
        G.add_edges_from(edges[['src', 'dst']].itertuples(index=False, name=None))
    pos = nx.spring_layout(G, seed=seed, k=1.2 / np.sqrt(max(len(G), 1)))
    P = np.array([pos[n] for n in nodes['node']])
    # rescale into [margin, extent-margin] square
    m = 0.06 * extent_m
    lo, hi = P.min(0), P.max(0); span = np.where(hi - lo < 1e-9, 1, hi - lo)
    P = m + (P - lo) / span * (extent_m - 2 * m)
    return P


def render(nodes, P, extent_m, out_png, mono, title):
    fig, ax = plt.subplots(figsize=(9, 9), dpi=150)
    fig.patch.set_facecolor(CREAM); ax.set_facecolor(CREAM if not mono else 'white')
    used = set()
    for (_, row), (x, y) in zip(nodes.iterrows(), P):
        fp = translate(footprint(row.get('shape__area_m2', 200),
                                 row.get('shape__bbox_elongation', 1.5)), x, y)
        prog = dominant_program(row)
        color = 'black' if mono else PROGRAM_COLORS.get(prog, '#C9C2B4')
        used.add(prog)
        xs, ys = fp.exterior.xy
        ax.add_patch(MplPoly(np.column_stack([xs, ys]), closed=True,
                             facecolor=color, edgecolor='none', zorder=2))
    ax.set_xlim(0, extent_m); ax.set_ylim(0, extent_m); ax.set_aspect('equal')
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_color('#CCC4B6')
    ax.set_title(title, color=NAVY, fontsize=13, pad=12)
    if not mono:
        leg = [Patch(facecolor=PROGRAM_COLORS[p], label=p.replace('_', ' '))
               for p in ROSSETTI_BUCKETS if p in used]
        ax.legend(handles=leg, frameon=False, fontsize=8, loc='upper right',
                  bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout(); fig.savefig(out_png, facecolor=CREAM); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description='Schematic placement (M22)')
    ap.add_argument('--csv', type=Path, required=True)
    ap.add_argument('--extent-m', type=float, default=None,
                    help='site side in m; if omitted, auto-sized from footprint area')
    ap.add_argument('--coverage', type=float, default=0.22,
                    help='target built coverage for auto extent')
    ap.add_argument('--mono', action='store_true', help='black/white figure-ground')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    nodes = pd.read_csv(a.csv)
    if a.extent_m is None:
        areas = nodes.get('shape__area_m2', pd.Series([200] * len(nodes))).clip(20, 40000)
        a.extent_m = float(np.sqrt(areas.sum() / max(a.coverage, 0.05)))
    edge_path = a.csv.with_name(a.csv.stem + '_edges.csv')
    edges = pd.read_csv(edge_path) if edge_path.exists() else None
    P = layout(nodes, edges, a.extent_m, a.seed)
    out_png = a.csv.with_name(a.csv.stem + ('_plan_mono.png' if a.mono else '_plan.png'))
    render(nodes, P, a.extent_m, out_png,
           a.mono, f'{a.csv.stem}  ({len(nodes)} buildings, {int(a.extent_m)}m)')
    print(f'[OK] {len(nodes)} footprints -> {out_png}')


if __name__ == '__main__':
    main()
