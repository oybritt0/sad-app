#!/usr/bin/env python
"""
impervious_figure.py — standalone "ground plane" visual for one SAD: a single
proportional bar splitting hard surface (building/pavement) from vegetated/open,
with the headline percentage and the source citation. House style: Arial, flat,
solid hex.

Reads built_up_pct from derived/environment/environment_summary.json (M22).

OUTPUT
    <out>/impervious_ground_plane.svg

USAGE  (from code\\)
    python impervious_figure.py --data-dir ..\\data
    python impervious_figure.py --data-dir ..\\data --sad 59_Drawn-district_Philadelphia
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

HARD_FILL, HARD_TXT = '#993C1D', '#FAECE7'      # coral 600 / coral 50
GREEN_FILL, GREEN_TXT = '#1D9E75', '#0F6E56'    # teal 400 / teal 600
INK, MUTED, HAIR, PAPER = '#111111', '#8A8A8A', '#CCCCCC', '#FFFFFF'


def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sad', default=None)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    sads = sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())
    philly = args.sad or next((s for s in sads if 'phila' in s.lower()), None)
    if not philly:
        raise SystemExit('SAD not found; pass --sad <id>.')

    env_path = data_dir / philly / 'derived' / 'environment' / 'environment_summary.json'
    env = json.loads(env_path.read_text())
    hard = env.get('built_up_pct')
    if hard is None:
        raise SystemExit('built_up_pct missing — run M22 first.')
    soft = round(100 - hard, 1)

    # bar geometry
    X0, X1, BY, BH = 40, 640, 92, 44
    full_w = X1 - X0
    hard_w = full_w * hard / 100.0
    split = X0 + hard_w

    parts = [
        f'<svg width="100%" viewBox="0 0 680 300" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="680" height="300" fill="{PAPER}"/>',
        f'<text x="40" y="44" fill="{INK}" font-size="16" font-weight="bold">'
        f'Almost the entire district is hard surface</text>',
        f'<text x="40" y="66" fill="{MUTED}" font-size="12">'
        f'Share of ground that is building or pavement, by satellite land cover</text>',
        f'<rect x="{X0}" y="{BY}" width="{hard_w:.1f}" height="{BH}" fill="{HARD_FILL}"/>',
        f'<rect x="{split:.1f}" y="{BY}" width="{X1-split:.1f}" height="{BH}" '
        f'fill="{GREEN_FILL}"/>',
        f'<text x="{X0+4}" y="{BY+28}" fill="{HARD_TXT}" font-size="22" '
        f'font-weight="bold">{hard:.1f}%</text>',
        f'<text x="{X0+4}" y="{BY+66}" fill="{HARD_FILL}" font-size="12">'
        f'hard surface &#8212; building or pavement</text>',
        f'<text x="{X1}" y="{BY+66}" fill="{GREEN_TXT}" font-size="12" '
        f'text-anchor="end">{soft:.1f}% vegetated / open</text>',
        f'<line x1="40" y1="196" x2="640" y2="196" stroke="{HAIR}" stroke-width="0.5"/>',
        f'<text x="40" y="222" fill="{INK}" font-size="13">'
        f'Every point of that hard surface you deck or green is developable land &#8212;</text>',
        f'<text x="40" y="240" fill="{INK}" font-size="13">'
        f'and the direct lever on the heat-island and stormwater load.</text>',
        f'<text x="40" y="276" fill="{MUTED}" font-size="10">'
        f'Source: ESA WorldCover 2021 (10 m built-up), via Microsoft Planetary '
        f'Computer. Rossetti+HOK SAD analysis, 2026.</text>',
        '</svg>',
    ]
    out_dir = (args.out or data_dir / '_eagles_figs').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / 'impervious_ground_plane.svg'
    path.write_text('\n'.join(parts), encoding='utf-8')
    print(f'  wrote {path}  ({hard:.1f}% hard / {soft:.1f}% open)')


if __name__ == '__main__':
    main()
