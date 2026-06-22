"""
diagram.py  (Module 27 - anchor-centric relational diagram)

Renders a generated attributed graph (generate / cvae_form / retemplate output)
as what it ACTUALLY is: a relational diagram, not a sited plan. The anchor (the
largest mass, standing in for the venue) sits at the centre; every other building
is placed on a ring by its graph-distance (adjacency hops) from the anchor,
coloured by program and sized by decoded area. Edges show adjacency.

This is the honest face of the model: it shows program + adjacency + scale-by-
distance-from-anchor, and hides the made-up x/y positions the figure-ground
implied. It also previews V1 -- where the anchor becomes a true fixed input
rather than the largest generated node.

INPUT   _gen/<scenario>.csv  (+ _edges.csv)
OUTPUT  _gen/<scenario>_diagram.png   (+ prints program mix by ring)

USAGE
  python diagram.py --csv ..\\data\\_graphs\\_gen\\fgen_32_120.csv
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from graph_builder import ROSSETTI_BUCKETS
from place import PROGRAM_COLORS, CREAM, NAVY, dominant_program


def main():
    ap = argparse.ArgumentParser(description='Anchor-centric relational diagram (M27)')
    ap.add_argument('--csv', type=Path, required=True)
    ap.add_argument('--ring-gap', type=float, default=1.0)
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    nodes = pd.read_csv(a.csv)
    edge_path = a.csv.with_name(a.csv.stem + '_edges.csv')
    edges = pd.read_csv(edge_path) if edge_path.exists() else pd.DataFrame(columns=['src', 'dst'])

    area = dict(zip(nodes['node'], nodes.get('shape__area_m2', pd.Series([200] * len(nodes)))))
    prog = {int(r['node']): dominant_program(r) for _, r in nodes.iterrows()}

    G = nx.Graph(); G.add_nodes_from(nodes['node'].astype(int))
    G.add_edges_from(edges[['src', 'dst']].astype(int).itertuples(index=False, name=None))

    anchor = int(max(area, key=area.get))            # largest mass = venue proxy
    hops = nx.single_source_shortest_path_length(G, anchor) if anchor in G else {anchor: 0}
    maxh = max(hops.values()) if hops else 0

    rings = defaultdict(list)
    for nd in nodes['node'].astype(int):
        rings[hops.get(nd, maxh + 1)].append(nd)

    rng = np.random.default_rng(a.seed)
    pos = {}
    for ring, members in rings.items():
        if ring == 0:
            pos[members[0]] = (0.0, 0.0); continue
        R = ring * a.ring_gap
        base = rng.uniform(0, 2 * np.pi)
        for i, nd in enumerate(members):
            ang = base + 2 * np.pi * i / len(members) + rng.uniform(-0.05, 0.05)
            pos[nd] = (R * np.cos(ang), R * np.sin(ang))

    fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
    fig.patch.set_facecolor(CREAM); ax.set_facecolor(CREAM)
    # guide rings
    for ring in range(1, maxh + 2):
        ax.add_patch(plt.Circle((0, 0), ring * a.ring_gap, fill=False,
                                color='#D8D0C0', lw=0.8, zorder=0))
    # edges
    for u, v in G.edges():
        if u in pos and v in pos:
            ax.plot([pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]],
                    color='#C9C2B4', lw=0.4, zorder=1)
    # nodes sized by sqrt(area)
    used = set()
    for nd in nodes['node'].astype(int):
        if nd == anchor:
            continue
        x, y = pos[nd]; p = prog[nd]; used.add(p)
        s = float(np.clip(np.sqrt(max(area[nd], 1)) * 1.5, 20, 600))
        ax.scatter(x, y, s=s, color=PROGRAM_COLORS.get(p, '#C9C2B4'),
                   edgecolor='white', linewidth=0.5, zorder=3)
    # anchor
    ax.scatter(0, 0, s=900, color=NAVY, edgecolor='white', linewidth=2, zorder=4, marker='s')
    ax.annotate('anchor / venue', (0, -0.32 * a.ring_gap), color=NAVY, fontsize=9,
                ha='center', va='top', zorder=5, fontweight='bold')

    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])
    lim = (maxh + 1.5) * a.ring_gap
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
    for s in ax.spines.values(): s.set_color('#CCC4B6')
    ax.set_title(f'{a.csv.stem}  -  program & adjacency around the anchor',
                 color=NAVY, fontsize=13, pad=12)
    leg = [Patch(facecolor=PROGRAM_COLORS[p], label=p.replace('_', ' '))
           for p in ROSSETTI_BUCKETS if p in used]
    ax.legend(handles=leg, frameon=False, fontsize=9, loc='upper right')

    out = a.csv.with_name(a.csv.stem + '_diagram.png')
    fig.tight_layout(); fig.savefig(out, facecolor=CREAM); plt.close(fig)

    # program mix by distance band (the relational gradient)
    print(f'anchor = node {anchor} (area {area[anchor]:.0f} m2); {maxh} adjacency rings')
    print('program mix by ring (distance from anchor):')
    for ring in sorted(rings):
        ms = rings[ring]
        if ring == 0:
            continue
        cnt = pd.Series([prog[n] for n in ms]).value_counts(normalize=True)
        top = ', '.join(f'{k} {v:.0%}' for k, v in cnt.head(3).items())
        print(f'  ring {ring} ({len(ms)} bldgs): {top}')
    print(f'[OK] -> {out}')


if __name__ == '__main__':
    main()
