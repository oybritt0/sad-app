#!/usr/bin/env python
"""
make_eagles_figures.py — render presentation figures for the South
Philadelphia Sports Complex from the real corpus data the pipeline wrote.

This is the comparative layer the viewer doesn't give you: it reads every
district's jobs / environment / transit / cv summaries, then draws where
Philadelphia sits against the full set. Three outputs, SVG + 300-dpi PNG:

  positioning_vs_corpus.{svg,png}   one bullet row per metric: corpus spread,
                                    median tick, Philadelphia diamond, value +
                                    percentile. Colour-coded by where Philly
                                    lands (concerning / neutral / favourable).
  heat_island_callout.{svg,png}     the +7.7C headline as a big-number card.
  transit_capacity_callout.{svg,png}busiest-node departures / headway / routes.

House style: monospace, flat, hairline rules, solid hex fills only — no
rounded corners, shadows, rgba, or gradients. SVG text stays editable in
Illustrator (no path conversion).

USAGE  (from code\, after M20/M21/M22 have run for Philly)
    python make_eagles_figures.py --data-dir ..\\data
    python make_eagles_figures.py --data-dir ..\\data --out ..\\data\\_eagles_figs
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ─── House style ─────────────────────────────────────────────────────────────
matplotlib.rcParams.update({
    'font.family':     'monospace',
    'font.monospace':  ['JetBrains Mono', 'SF Mono', 'Consolas',
                        'DejaVu Sans Mono'],
    'svg.fonttype':    'none',     # keep text as text for Illustrator
    'axes.linewidth':  0.6,
    'figure.dpi':      100,
})

PAPER   = '#FFFFFF'   # solid background
INK     = '#111111'   # primary text / median
MUTED   = '#8A8A8A'   # corpus marks
HAIR    = '#CCCCCC'   # hairline rules
FAVOUR  = '#2E7D32'   # Philly in a favourable position (>=60th pctile)
NEUTRAL = '#111111'   # middling (40-60th)
CONCERN = '#C8102E'   # concerning (<40th)
PHILLY  = '#004C54'   # Eagles midnight green (used on the callout cards)


def band_colour(pct: float) -> str:
    if pct != pct:           # NaN
        return NEUTRAL
    if pct >= 60:
        return FAVOUR
    if pct < 40:
        return CONCERN
    return NEUTRAL


# ─── Data access ─────────────────────────────────────────────────────────────

def load(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def g(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def find_philly(data_dir: Path, explicit):
    sads = sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())
    if explicit:
        return (explicit if explicit in sads else None), sads
    for s in sads:
        if 'phila' in s.lower():
            return s, sads
    return None, sads


# label, file, getter, higher_is_better, fmt
METRICS = [
    ('Heat-island delta vs surroundings',
     'environment/environment_summary.json',
     lambda d: g(d, 'lst_vs_surroundings_c'), False, '{:+.1f} C'),
    ('Daytime jobs inside district',
     'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_inside'), True, '{:,.0f}'),
    ('Jobs per resident',
     'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_per_resident'), True, '{:.2f}'),
    ('Transit trips / day',
     'transit_los/transit_los_summary.json',
     lambda d: g(d, 'trips_per_day'), True, '{:,.0f}'),
    ('Greenness (mean NDVI)',
     'environment/environment_summary.json',
     lambda d: g(d, 'mean_ndvi'), True, '{:.3f}'),
    ('Built coverage',
     'cv_metrics.json',
     lambda d: g(d, 'field', 'coverage'), True, '{:.2f}'),
]


def pct_rank(value, vals, higher_is_better):
    clean = [v for v in vals if isinstance(v, (int, float))]
    if not clean or value is None:
        return float('nan')
    below = sum(1 for v in clean if v < value)
    pr = 100.0 * below / len(clean)
    return pr if higher_is_better else 100.0 - pr


def collect(data_dir, philly, sads):
    """Return list of rows with everything needed to draw a metric."""
    cache = {}

    def get_file(sad, rel):
        key = (sad, rel)
        if key not in cache:
            cache[key] = load(data_dir / sad / 'derived' / rel)
        return cache[key]

    rows = []
    for label, rel, getter, hib, fmt in METRICS:
        pdoc = get_file(philly, rel)
        pval = getter(pdoc) if pdoc else None
        if pval is None:
            continue
        corpus = []
        for s in sads:
            doc = get_file(s, rel)
            if doc:
                try:
                    v = getter(doc)
                    if isinstance(v, (int, float)):
                        corpus.append(v)
                except Exception:
                    pass
        if len(corpus) < 5:
            continue
        rows.append({
            'label': label, 'pval': pval, 'corpus': corpus,
            'median': median(corpus),
            'pct': pct_rank(pval, corpus, hib),
            'value_str': fmt.format(pval),
        })
    return rows


# ─── Figure 1: positioning bullets ───────────────────────────────────────────

def fig_positioning(rows, philly, n_sads, out_base):
    n = len(rows)
    fig_h = 1.6 + 0.95 * n
    fig, ax = plt.subplots(figsize=(12, fig_h))
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis('off')

    # track geometry in axes fraction
    TX0, TX1 = 0.34, 0.78          # value track left/right
    top, bot = 0.84, 0.12
    ys = [top - (top - bot) * (i / max(n - 1, 1)) for i in range(n)]

    # header
    ax.text(0.0, 0.96, 'SOUTH PHILADELPHIA SPORTS COMPLEX',
            transform=ax.transAxes, color=INK, fontsize=13, fontweight='bold',
            ha='left', va='bottom')
    ax.text(1.0, 0.96, f'vs {n_sads} districts',
            transform=ax.transAxes, color=MUTED, fontsize=10,
            ha='right', va='bottom')
    ax.plot([0, 1], [0.935, 0.935], transform=ax.transAxes,
            color=INK, lw=0.8, solid_capstyle='butt')

    for row, y in zip(rows, ys):
        lo, hi = min(row['corpus']), max(row['corpus'])
        span = (hi - lo) or 1.0

        def tx(v):
            return TX0 + (TX1 - TX0) * (v - lo) / span

        # track hairline
        ax.plot([TX0, TX1], [y, y], transform=ax.transAxes,
                color=HAIR, lw=0.8, solid_capstyle='butt')
        # corpus marks
        for v in row['corpus']:
            x = tx(v)
            ax.plot([x, x], [y - 0.012, y + 0.012], transform=ax.transAxes,
                    color=MUTED, lw=0.7, solid_capstyle='butt')
        # median tick
        xm = tx(row['median'])
        ax.plot([xm, xm], [y - 0.022, y + 0.022], transform=ax.transAxes,
                color=INK, lw=1.4, solid_capstyle='butt')
        ax.text(xm, y - 0.040, 'median', transform=ax.transAxes,
                color=MUTED, fontsize=7, ha='center', va='top')
        # Philadelphia diamond
        col = band_colour(row['pct'])
        xp = tx(row['pval'])
        ax.scatter([xp], [y], transform=ax.transAxes, marker='D',
                   s=80, color=col, zorder=5, linewidths=0)

        # left label
        ax.text(0.0, y, row['label'], transform=ax.transAxes,
                color=INK, fontsize=10.5, ha='left', va='center')
        # right value + percentile
        pct = row['pct']
        pct_str = f'{pct:.0f}th pctile' if pct == pct else 'n/a'
        ax.text(0.84, y + 0.018, row['value_str'], transform=ax.transAxes,
                color=col, fontsize=11, fontweight='bold',
                ha='left', va='center')
        ax.text(0.84, y - 0.022, pct_str, transform=ax.transAxes,
                color=MUTED, fontsize=8.5, ha='left', va='center')

    # legend / footnote
    ax.text(0.0, 0.02,
            'diamond = Philadelphia   |   ticks = the other districts   |   '
            'green favourable / red concerning vs the set',
            transform=ax.transAxes, color=MUTED, fontsize=8,
            ha='left', va='bottom')

    fig.tight_layout()
    for ext in ('svg', 'png'):
        fig.savefig(f'{out_base}.{ext}', facecolor=PAPER,
                    dpi=300, bbox_inches='tight')
    plt.close(fig)


# ─── Big-number callout cards ────────────────────────────────────────────────

def fig_card(big, big_col, eyebrow, sub_lines, out_base):
    fig, ax = plt.subplots(figsize=(6, 3.4))
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis('off')
    # hairline frame (square corners)
    ax.plot([0.02, 0.98, 0.98, 0.02, 0.02],
            [0.04, 0.04, 0.96, 0.96, 0.04],
            transform=ax.transAxes, color=HAIR, lw=0.8,
            solid_capstyle='butt', solid_joinstyle='miter')
    ax.text(0.08, 0.80, eyebrow, transform=ax.transAxes, color=MUTED,
            fontsize=10, ha='left', va='center')
    ax.text(0.08, 0.54, big, transform=ax.transAxes, color=big_col,
            fontsize=40, fontweight='bold', ha='left', va='center')
    y = 0.30
    for line in sub_lines:
        ax.text(0.08, y, line, transform=ax.transAxes, color=INK,
                fontsize=10, ha='left', va='center')
        y -= 0.10
    fig.tight_layout()
    for ext in ('svg', 'png'):
        fig.savefig(f'{out_base}.{ext}', facecolor=PAPER,
                    dpi=300, bbox_inches='tight')
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--sad', default=None, help='Exact Philly id (else auto)')
    ap.add_argument('--out', type=Path, default=None,
                    help='Output dir (default <data>/_eagles_figs)')
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    philly, sads = find_philly(data_dir, args.sad)
    if not philly:
        raise SystemExit('Philadelphia SAD not found; pass --sad <id>.')
    out_dir = (args.out or data_dir / '_eagles_figs').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = collect(data_dir, philly, sads)
    if rows:
        fig_positioning(rows, philly, len(sads), out_dir / 'positioning_vs_corpus')
        print(f'  wrote positioning_vs_corpus.(svg|png)  [{len(rows)} metrics]')
    else:
        print('  no metrics available — run M20/M22/M21 first')

    # Heat card
    env = load(data_dir / philly / 'derived' / 'environment'
               / 'environment_summary.json')
    if env and g(env, 'lst_vs_surroundings_c') is not None:
        delta = g(env, 'lst_vs_surroundings_c')
        sub = [f"district {g(env,'mean_summer_lst_c')} C  vs  "
               f"{g(env,'surroundings_lst_c')} C around it"]
        # add percentile context if we drew it
        hr = next((r for r in rows if r['label'].startswith('Heat')), None)
        if hr and hr['pct'] == hr['pct']:
            sub.append(f"{hr['pct']:.0f}th percentile of {len(sads)} districts "
                       f"(median {hr['median']:+.1f} C)")
        sub.append('driven by surface parking — the case for decking + canopy')
        fig_card(f'{delta:+.1f} C', CONCERN, 'HOTTER THAN ITS SURROUNDINGS',
                 sub, out_dir / 'heat_island_callout')
        print('  wrote heat_island_callout.(svg|png)')

    # Transit card
    tr = load(data_dir / philly / 'derived' / 'transit_los'
              / 'transit_los_summary.json')
    if tr and g(tr, 'busiest_stop_departures_per_day') is not None:
        dep = g(tr, 'busiest_stop_departures_per_day')
        head = g(tr, 'busiest_node_combined_headway_min')
        routes = g(tr, 'routes_serving')
        modes = ', '.join(g(tr, 'modes') or [])
        sub = [f"~{head} min combined headway at the busiest node"
               if head else '',
               f"{routes} routes serving the district ({modes})",
               'baseline weekday only — game-day event service is additional']
        fig_card(f'{dep:,}/day', PHILLY, 'DEPARTURES AT THE BUSIEST NODE',
                 [s for s in sub if s], out_dir / 'transit_capacity_callout')
        print('  wrote transit_capacity_callout.(svg|png)')

    print(f'\nFigures in: {out_dir}')


if __name__ == '__main__':
    main()
