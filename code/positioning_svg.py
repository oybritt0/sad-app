#!/usr/bin/env python
"""
positioning_svg.py — render the Philadelphia-vs-corpus positioning figure as a
single editable SVG, with one dot per district coloured by its Rossetti
typology, the corpus median tick, and Philadelphia called out as a diamond.

Reads the same per-SAD JSON the pipeline already wrote — nothing is invented.
Typology comes from the canonical _shared/typologies.json (key: sad_id,
value primary_typology in {Entertainment, Community, Innovation, Sports Park}),
falling back to each district's derived/typology.json.

THE "ORIGINAL 37"
    The pipeline doesn't tag a cohort, so pass the list explicitly:
        --cohort original_37.txt          one sad_id per line (# comments ok)
    Only those districts are drawn as dots. Without --cohort, every district
    that has a typology is drawn, and the script prints a notice so you know
    it's the full set, not the curated 37. Philadelphia is always the diamond.

OUTPUT
    <out>/positioning_typology.svg   (and .png if --png and cairosvg present)

USAGE  (from code\)
    python positioning_svg.py --data-dir ..\\data --cohort original_37.txt
    python positioning_svg.py --data-dir ..\\data --out ..\\data\\_eagles_figs
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import median


# ─── house-style palette (solid hex, no rgba/gradient) ───────────────────────
INK     = '#111111'
MUTED   = '#8A8A8A'
HAIR    = '#CCCCCC'
PAPER   = '#FFFFFF'
PHILLY  = '#004C54'    # Eagles midnight green — the diamond

# One solid hex per typology. Keys match primary_typology in typologies.json.
TYPOLOGY_COLOR = {
    'Entertainment': '#D85A30',   # coral
    'Community':     '#1D9E75',   # teal
    'Innovation':    '#378ADD',   # blue
    'Sports Park':   '#BA7517',   # amber
}
TYPOLOGY_ORDER = ['Entertainment', 'Community', 'Innovation', 'Sports Park']
UNCLASSIFIED   = '#B4B2A9'        # gray dot if a district has no typology


# ─── metric registry ─────────────────────────────────────────────────────────
def g(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# label, file under derived/, getter, higher_is_better, fmt, plain meaning
METRICS = [
    ('Heat-island delta', 'environment/environment_summary.json',
     lambda d: g(d, 'lst_vs_surroundings_c'), False, '{:+.1f} C',
     'Hotter than its surroundings - the parking signature'),
    ('Daytime jobs', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_inside'), True, '{:,.0f}',
     'People working here on a normal day'),
    ('Jobs per resident', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_per_resident'), True, '{:.2f}',
     'Workers vs residents - high means few people live here'),
    ('Transit trips / day', 'transit_los/transit_los_summary.json',
     lambda d: g(d, 'trips_per_day'), True, '{:,.0f}',
     'Scheduled service reaching the district'),
    ('Greenness (NDVI)', 'environment/environment_summary.json',
     lambda d: g(d, 'mean_ndvi'), True, '{:.3f}',
     'Living vegetation by satellite'),
]


def load(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def esc(s) -> str:
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


def load_typologies(data_dir: Path) -> dict:
    """Return {sad_id: primary_typology}. Prefer the canonical file."""
    canon = load(data_dir / '_shared' / 'typologies.json')
    out = {}
    if canon and isinstance(canon.get('districts'), dict):
        for sid, rec in canon['districts'].items():
            out[sid] = (rec or {}).get('primary_typology')
    return out


def typ_for(sad_id, derived_dir: Path, canon: dict):
    if sad_id in canon and canon[sad_id]:
        return canon[sad_id]
    rec = load(derived_dir / 'typology.json')
    return (rec or {}).get('primary_typology')


def read_cohort(path: Path | None) -> set | None:
    if not path:
        return None
    ids = set()
    for line in path.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            ids.add(line)
    return ids


def find_philly(sads, explicit):
    if explicit:
        return explicit if explicit in sads else None
    return next((s for s in sads if 'phila' in s.lower()), None)


# ─── SVG assembly ─────────────────────────────────────────────────────────────

def build_svg(rows, philly_label, n_drawn, cohort_note):
    W = 680
    row_h = 96
    top = 96
    H = top + row_h * len(rows) + 96
    TX0, TX1 = 300, 620          # value track left/right edge

    parts = [
        f'<svg width="100%" viewBox="0 0 {W} {H}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'font-family="JetBrains Mono, SF Mono, Consolas, monospace">',
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="{PAPER}"/>',
        f'<text x="40" y="44" fill="{INK}" font-size="15" '
        f'font-weight="bold">South Philadelphia Sports Complex</text>',
        f'<text x="40" y="64" fill="{MUTED}" font-size="11">'
        f'Positioned against {n_drawn} districts &#183; diamond = Philadelphia'
        f'</text>',
        f'<line x1="40" y1="76" x2="640" y2="76" stroke="{INK}" '
        f'stroke-width="0.8"/>',
    ]

    for i, row in enumerate(rows):
        y = top + i * row_h + 30
        lo = min(row['points'] + [row['pval'], row['median']])
        hi = max(row['points'] + [row['pval'], row['median']])
        span = (hi - lo) or 1.0

        def tx(v):
            return TX0 + (TX1 - TX0) * (v - lo) / span

        # label + meaning (left column)
        parts.append(
            f'<text x="40" y="{y-6}" fill="{INK}" font-size="14" '
            f'font-weight="bold">{esc(row["label"])}</text>')
        parts.append(
            f'<text x="40" y="{y+12}" fill="{MUTED}" font-size="11">'
            f'{esc(row["meaning"])}</text>')

        # track hairline
        parts.append(
            f'<line x1="{TX0}" y1="{y}" x2="{TX1}" y2="{y}" '
            f'stroke="{HAIR}" stroke-width="0.8"/>')

        # typology dots (jitter vertically a touch so coincident values show)
        for j, (val, color) in enumerate(row['dots']):
            x = tx(val)
            dy = -3 if j % 2 else 3
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y+dy}" r="3.2" fill="{color}"/>')

        # median tick
        xm = tx(row['median'])
        parts.append(
            f'<line x1="{xm:.1f}" y1="{y-9}" x2="{xm:.1f}" y2="{y+9}" '
            f'stroke="{INK}" stroke-width="1.4"/>')
        parts.append(
            f'<text x="{xm:.1f}" y="{y-15}" fill="{MUTED}" font-size="11" '
            f'text-anchor="middle">median {esc(row["median_str"])}</text>')

        # Philadelphia diamond
        xp = tx(row['pval'])
        s = 6
        parts.append(
            f'<path d="M{xp:.1f} {y-s} L{xp+s:.1f} {y} '
            f'L{xp:.1f} {y+s} L{xp-s:.1f} {y} Z" fill="{PHILLY}"/>')
        parts.append(
            f'<text x="{xp:.1f}" y="{y+28}" fill="{PHILLY}" font-size="11" '
            f'font-weight="bold" text-anchor="middle">'
            f'Philadelphia {esc(row["value_str"])}  ({esc(row["rank_str"])})'
            f'</text>')

    # legend
    ly = H - 56
    parts.append(f'<line x1="40" y1="{ly-20}" x2="640" y2="{ly-20}" '
                 f'stroke="{HAIR}" stroke-width="0.5"/>')
    lx = 40
    for t in TYPOLOGY_ORDER:
        parts.append(f'<circle cx="{lx}" cy="{ly}" r="3.2" '
                     f'fill="{TYPOLOGY_COLOR[t]}"/>')
        parts.append(f'<text x="{lx+10}" y="{ly+4}" fill="{INK}" '
                     f'font-size="11">{esc(t)}</text>')
        lx += 24 + len(t) * 7 + 28
    parts.append(
        f'<path d="M{lx} {ly-6} L{lx+6} {ly} L{lx} {ly+6} L{lx-6} {ly} Z" '
        f'fill="{PHILLY}"/>')
    parts.append(f'<text x="{lx+12}" y="{ly+4}" fill="{PHILLY}" '
                 f'font-size="11" font-weight="bold">Philadelphia</text>')
    if cohort_note:
        parts.append(f'<text x="40" y="{H-20}" fill="{MUTED}" '
                     f'font-size="10">{esc(cohort_note)}</text>')

    parts.append('</svg>')
    return '\n'.join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--cohort', type=Path, default=None,
                    help='Text file of sad_ids (the original 37), one per line')
    ap.add_argument('--sad', default=None, help='Exact Philly id (else auto)')
    ap.add_argument('--out', type=Path, default=None)
    ap.add_argument('--png', action='store_true',
                    help='Also write PNG (needs: pip install cairosvg)')
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    sads = sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())
    philly = find_philly(sads, args.sad)
    if not philly:
        raise SystemExit('Philadelphia SAD not found; pass --sad <id>.')

    canon = load_typologies(data_dir)
    cohort = read_cohort(args.cohort)
    cohort_note = None
    if cohort:
        missing = [c for c in cohort if c not in sads]
        if missing:
            print(f'  note: {len(missing)} cohort id(s) not found under data-dir '
                  f'(e.g. {missing[0]})')
        cohort_note = f'Dots: the original {len(cohort)} districts, coloured by typology.'
    else:
        print('  no --cohort given: drawing every district with a typology '
              '(not just the original 37).')
        cohort_note = 'Dots: all measured districts (no cohort file supplied).'

    cache = {}

    def get_file(sad, rel):
        key = (sad, rel)
        if key not in cache:
            cache[key] = load(data_dir / sad / 'derived' / rel)
        return cache[key]

    rows = []
    drawn_ids = set()
    for label, rel, getter, hib, fmt, meaning in METRICS:
        pdoc = get_file(philly, rel)
        pval = getter(pdoc) if pdoc else None
        if pval is None:
            continue

        dots, points = [], []
        for s in sads:
            if cohort is not None and s not in cohort:
                continue
            if s == philly:
                continue
            doc = get_file(s, rel)
            if not doc:
                continue
            v = getter(doc)
            if not isinstance(v, (int, float)):
                continue
            t = typ_for(s, data_dir / s / 'derived', canon)
            color = TYPOLOGY_COLOR.get(t, UNCLASSIFIED)
            dots.append((v, color))
            points.append(v)
            drawn_ids.add(s)
        if len(points) < 4:
            continue

        allvals = points + [pval]
        below = sum(1 for v in points if (v < pval) == hib)
        rank = (len(points) + 1) - below if hib else below + 1
        # simpler: rank from the favourable end
        ordered = sorted(allvals, reverse=hib)
        rank = ordered.index(pval) + 1
        med = median(points)
        rows.append({
            'label': label, 'meaning': meaning,
            'pval': pval, 'value_str': fmt.format(pval),
            'median': med, 'median_str': fmt.format(med),
            'points': points, 'dots': dots,
            'rank_str': f'#{rank} of {len(allvals)}',
        })

    if not rows:
        raise SystemExit('No metrics with >=4 districts available. '
                         'Run M20/M22/M21 across the cohort first.')

    out_dir = (args.out or data_dir / '_eagles_figs').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    svg = build_svg(rows, philly, len(drawn_ids), cohort_note)
    svg_path = out_dir / 'positioning_typology.svg'
    svg_path.write_text(svg, encoding='utf-8')
    print(f'  wrote {svg_path}  [{len(rows)} metrics, {len(drawn_ids)} dots]')

    if args.png:
        try:
            import cairosvg
            cairosvg.svg2png(bytestring=svg.encode('utf-8'),
                             write_to=str(out_dir / 'positioning_typology.png'),
                             output_width=1360)
            print(f'  wrote {out_dir / "positioning_typology.png"}')
        except Exception as e:
            print(f'  PNG skipped ({e}); the SVG is the deliverable.')


if __name__ == '__main__':
    main()
