#!/usr/bin/env python
"""metric_strips.py — one SVG per metric. Every district in the cohort is a
vertical tick along the axis, coloured by Rossetti typology; ~8 districts get
a named label (Philadelphia, the two extremes, the median, and a handful of
recognisable peers). The rest stay unlabelled ticks so the strip reads cleanly.

Two label styles are written per metric into subfolders rotated/ and
staggered/ so you can compare:
  rotated    name turned 90 deg, hanging straight down from its own tick
  staggered  horizontal name on one of up to 4 stacked levels under its tick

Reads only the per-SAD JSON the pipeline already wrote. Typology comes from
_shared/typologies.json (primary_typology), falling back to derived/typology.json.

USAGE  (from code\\)
    python metric_strips.py --data-dir ..\\data --cohort original_37.txt
    python metric_strips.py --data-dir ..\\data --cohort original_37.txt \\
        --peers "Power and Light District,Aquilini Centre,The Star"
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import median


# ─── house style (solid hex only) ────────────────────────────────────────────
INK, MUTED, HAIR, PAPER = '#111111', '#8A8A8A', '#CCCCCC', '#FFFFFF'
PHILLY = '#004C54'
TYPOLOGY_COLOR = {
    'Entertainment': '#D85A30', 'Community': '#1D9E75',
    'Innovation': '#378ADD', 'Sports Park': '#BA7517',
}
TYPOLOGY_ORDER = ['Entertainment', 'Community', 'Innovation', 'Sports Park']
UNCLASSIFIED = '#B4B2A9'


def g(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# key, label, file, getter, higher_is_better, fmt, meaning
METRICS = [
    ('heat_island', 'Heat-island delta', 'environment/environment_summary.json',
     lambda d: g(d, 'lst_vs_surroundings_c'), False, '{:+.1f} C',
     'Hotter than its surroundings - the parking signature'),
    ('daytime_jobs', 'Daytime jobs', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_inside'), True, '{:,.0f}',
     'People working here on a normal day'),
    ('jobs_per_resident', 'Jobs per resident', 'jobs/jobs_summary.json',
     lambda d: g(d, 'jobs_per_resident'), True, '{:.2f}',
     'Workers vs residents - high means few people live here'),
    ('transit_trips', 'Transit trips / day', 'transit_los/transit_los_summary.json',
     lambda d: g(d, 'trips_per_day'), True, '{:,.0f}',
     'Scheduled service reaching the district'),
    ('greenness_ndvi', 'Greenness (NDVI)', 'environment/environment_summary.json',
     lambda d: g(d, 'mean_ndvi'), True, '{:.3f}',
     'Living vegetation by satellite'),
]


def load(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def nice(sad_id: str) -> str:
    parts = sad_id.split('_')
    if len(parts) >= 3:
        name = parts[1].replace('-', ' ')
        loc = parts[-1]
        if '-' in loc:
            *city, st = loc.split('-')
            loc = ' '.join(city).replace('-', ' ') + ', ' + st
        return f'{name} ({loc})'
    return sad_id.replace('_', ' ')


def load_typologies(data_dir: Path) -> dict:
    canon = load(data_dir / '_shared' / 'typologies.json')
    out = {}
    if canon and isinstance(canon.get('districts'), dict):
        for sid, rec in canon['districts'].items():
            out[sid] = (rec or {}).get('primary_typology')
    return out


def typ_for(sad_id, derived_dir, canon):
    if canon.get(sad_id):
        return canon[sad_id]
    rec = load(derived_dir / 'typology.json')
    return (rec or {}).get('primary_typology')


def read_cohort(path):
    if not path:
        return None
    ids = set()
    for line in Path(path).read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            ids.add(line)
    return ids


# ─── label selection ──────────────────────────────────────────────────────────

def pick_labeled(entries, philly_id, median_val, peer_names, max_labels):
    """entries: list of dicts with sad_id, name, val. Return set of sad_ids
    to label: Philadelphia + min + max + nearest-median + recognisable peers,
    capped at max_labels."""
    by_id = {e['sad_id']: e for e in entries}
    chosen = set()
    if philly_id in by_id:
        chosen.add(philly_id)
    smin = min(entries, key=lambda e: e['val'])
    smax = max(entries, key=lambda e: e['val'])
    chosen.add(smin['sad_id'])
    chosen.add(smax['sad_id'])
    med_e = min(entries, key=lambda e: abs(e['val'] - median_val))
    chosen.add(med_e['sad_id'])
    # recognisable peers by name fragment (case-insensitive contains)
    for e in entries:
        if len(chosen) >= max_labels:
            break
        for pn in peer_names:
            if pn and pn.lower() in e['name'].lower():
                chosen.add(e['sad_id'])
                break
    return chosen


# ─── SVG for one metric ───────────────────────────────────────────────────────

def build_strip(label, meaning, entries, philly_id, hib, fmt, labeled_ids,
                label_style='staggered'):
    W = 680
    AX_Y = 150                      # axis vertical position
    AX0, AX1 = 60, 620              # axis left/right
    entries = sorted(entries, key=lambda e: e['val'])
    vals = [e['val'] for e in entries]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    med = median(vals)

    def ax(v):
        return AX0 + (AX1 - AX0) * (v - lo) / span

    # geometry of label columns: leaders go DOWN to names below the axis.
    # We lay labelled names left-to-right by axis position, spacing them on a
    # baseline row, each connected back to its tick with an elbow leader.
    labeled = [e for e in entries if e['sad_id'] in labeled_ids]
    labeled.sort(key=lambda e: e['val'])

    n_lab = len(labeled)
    # longest label drives how much room rotated/staggered names need
    longest = max((len(e['name']) for e in labeled), default=20)
    if label_style == 'rotated':
        names_band = 12 + longest * 6     # vertical names hang below the axis
    else:                                  # staggered horizontal, up to 4 levels
        names_band = 12 + 4 * 24
    H = AX_Y + 40 + names_band + 56        # axis + names + legend band
    parts = [
        f'<svg width="100%" viewBox="0 0 {W} {H}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="{PAPER}"/>',
        f'<text x="40" y="46" fill="{INK}" font-size="16" font-weight="bold">'
        f'{esc(label)}</text>',
        f'<text x="40" y="68" fill="{MUTED}" font-size="12">{esc(meaning)}</text>',
        f'<text x="40" y="86" fill="{MUTED}" font-size="11">'
        f'{len(entries)} districts &#183; one tick each, coloured by typology'
        f'</text>',
    ]

    # axis line
    parts.append(f'<line x1="{AX0}" y1="{AX_Y}" x2="{AX1}" y2="{AX_Y}" '
                 f'stroke="{INK}" stroke-width="1"/>')
    # min / max value captions at the ends
    parts.append(f'<text x="{AX0}" y="{AX_Y+24}" fill="{MUTED}" font-size="11" '
                 f'text-anchor="start">{esc(fmt.format(lo))}</text>')
    parts.append(f'<text x="{AX1}" y="{AX_Y+24}" fill="{MUTED}" font-size="11" '
                 f'text-anchor="end">{esc(fmt.format(hi))}</text>')
    # direction hint
    good = 'higher &#8594;' if hib else '&#8592; cooler / lower is better'
    parts.append(f'<text x="{(AX0+AX1)/2}" y="{AX_Y-30}" fill="{MUTED}" '
                 f'font-size="11" text-anchor="middle">{good}</text>')

    # median tick (above axis)
    xm = ax(med)
    parts.append(f'<line x1="{xm:.1f}" y1="{AX_Y-12}" x2="{xm:.1f}" '
                 f'y2="{AX_Y+12}" stroke="{INK}" stroke-width="1.2" '
                 f'stroke-dasharray="2 2"/>')
    parts.append(f'<text x="{xm:.1f}" y="{AX_Y-16}" fill="{MUTED}" '
                 f'font-size="11" text-anchor="middle">median {esc(fmt.format(med))}</text>')

    # every district tick
    for e in entries:
        x = ax(e['val'])
        is_phil = e['sad_id'] == philly_id
        color = PHILLY if is_phil else e['color']
        if is_phil:
            s = 7
            parts.append(f'<path d="M{x:.1f} {AX_Y-s} L{x+s:.1f} {AX_Y} '
                         f'L{x:.1f} {AX_Y+s} L{x-s:.1f} {AX_Y} Z" '
                         f'fill="{PHILLY}"/>')
        else:
            parts.append(f'<line x1="{x:.1f}" y1="{AX_Y-7}" x2="{x:.1f}" '
                         f'y2="{AX_Y+7}" stroke="{color}" stroke-width="2.4"/>')

    # labels sit near their own tick, so no long diagonals cross the figure.
    # Two styles: 'rotated' (vertical name hanging straight down from the
    # tick) and 'staggered' (horizontal name on one of three stacked levels
    # with a short straight vertical leader).
    if labeled:
        if label_style == 'rotated':
            for e in labeled:
                tx = ax(e['val'])
                is_phil = e['sad_id'] == philly_id
                col = PHILLY if is_phil else INK
                wfont = 'bold' if is_phil else 'normal'
                ty = AX_Y + 22
                parts.append(
                    f'<line x1="{tx:.1f}" y1="{AX_Y+8}" x2="{tx:.1f}" '
                    f'y2="{ty-4:.1f}" stroke="{HAIR}" stroke-width="0.6"/>')
                name_lab = f'{e["name"]}  {fmt.format(e["val"])}'
                parts.append(
                    f'<text x="{tx:.1f}" y="{ty:.1f}" fill="{col}" '
                    f'font-size="11" font-weight="{wfont}" text-anchor="start" '
                    f'transform="rotate(90 {tx:.1f} {ty:.1f})">{esc(name_lab)}</text>')
        else:
            # staggered horizontal: walk left-to-right and place each label on
            # the lowest level whose last occupant clears it horizontally, so
            # close neighbours step down instead of overlapping on one row.
            levels_y = [AX_Y + 30, AX_Y + 54, AX_Y + 78, AX_Y + 102]
            last_right = [-1e9] * len(levels_y)
            ordered = sorted(labeled, key=lambda e: ax(e['val']))
            for e in ordered:
                tx = ax(e['val'])
                est_w = (len(e['name']) + 6) * 6      # rough text width
                lvl = 0
                for li in range(len(levels_y)):
                    if tx - 4 > last_right[li]:
                        lvl = li
                        break
                else:
                    lvl = len(levels_y) - 1
                last_right[lvl] = tx + est_w
                ly_ = levels_y[lvl]
                is_phil = e['sad_id'] == philly_id
                col = PHILLY if is_phil else INK
                wfont = 'bold' if is_phil else 'normal'
                parts.append(
                    f'<line x1="{tx:.1f}" y1="{AX_Y+8}" x2="{tx:.1f}" '
                    f'y2="{ly_-9:.1f}" stroke="{HAIR}" stroke-width="0.6"/>')
                parts.append(
                    f'<circle cx="{tx:.1f}" cy="{AX_Y+8}" r="1.4" fill="{HAIR}"/>')
                parts.append(
                    f'<text x="{tx:.1f}" y="{ly_:.1f}" fill="{col}" '
                    f'font-size="11" font-weight="{wfont}" '
                    f'text-anchor="start">{esc(e["name"])}  '
                    f'<tspan fill="{MUTED}" font-size="10">'
                    f'{esc(fmt.format(e["val"]))}</tspan></text>')

    # legend
    ly = H - 24
    lx = 40
    parts.append(f'<line x1="40" y1="{ly-20}" x2="640" y2="{ly-20}" '
                 f'stroke="{HAIR}" stroke-width="0.5"/>')
    for t in TYPOLOGY_ORDER:
        parts.append(f'<line x1="{lx}" y1="{ly}" x2="{lx}" y2="{ly-10}" '
                     f'stroke="{TYPOLOGY_COLOR[t]}" stroke-width="2.4"/>')
        parts.append(f'<text x="{lx+8}" y="{ly}" fill="{INK}" '
                     f'font-size="11">{esc(t)}</text>')
        lx += 16 + len(t) * 7 + 24
    parts.append(f'<path d="M{lx} {ly-6} L{lx+6} {ly} L{lx} {ly+6} '
                 f'L{lx-6} {ly} Z" fill="{PHILLY}"/>')
    parts.append(f'<text x="{lx+12}" y="{ly}" fill="{PHILLY}" font-size="11" '
                 f'font-weight="bold">Philadelphia</text>')

    parts.append('</svg>')
    return '\n'.join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--cohort', type=Path, default=None)
    ap.add_argument('--sad', default=None)
    ap.add_argument('--peers', default='Power and Light District,Aquilini Centre,'
                    'The Star,Hollywood Park,Downtown East',
                    help='Comma-separated name fragments to label if present')
    ap.add_argument('--max-labels', type=int, default=8)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    sads = sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())
    philly = args.sad or next((s for s in sads if 'phila' in s.lower()), None)
    if not philly:
        raise SystemExit('Philadelphia SAD not found; pass --sad <id>.')

    canon = load_typologies(data_dir)
    cohort = read_cohort(args.cohort)
    if cohort is None:
        print('  no --cohort: using every district with a typology.')
    peer_names = [p.strip() for p in args.peers.split(',') if p.strip()]

    out_dir = (args.out or data_dir / '_eagles_figs').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cache = {}

    def get_file(sad, rel):
        key = (sad, rel)
        if key not in cache:
            cache[key] = load(data_dir / sad / 'derived' / rel)
        return cache[key]

    wrote = 0
    for key, label, rel, getter, hib, fmt, meaning in METRICS:
        entries = []
        for s in sads:
            if cohort is not None and s not in cohort and s != philly:
                continue
            doc = get_file(s, rel)
            if not doc:
                continue
            v = getter(doc)
            if not isinstance(v, (int, float)):
                continue
            t = typ_for(s, data_dir / s / 'derived', canon)
            entries.append({'sad_id': s, 'name': nice(s), 'val': v,
                            'color': TYPOLOGY_COLOR.get(t, UNCLASSIFIED)})
        if len(entries) < 4 or philly not in {e['sad_id'] for e in entries}:
            print(f'  skip {label}: <4 districts or Philadelphia missing')
            continue
        med = median([e['val'] for e in entries])
        labeled = pick_labeled(entries, philly, med, peer_names, args.max_labels)
        for style in ('rotated', 'staggered'):
            svg = build_strip(label, meaning, entries, philly, hib, fmt,
                              labeled, label_style=style)
            sub = out_dir / style
            sub.mkdir(parents=True, exist_ok=True)
            path = sub / f'strip_{key}.svg'
            path.write_text(svg, encoding='utf-8')
        print(f'  wrote strip_{key}.svg  [{len(entries)} ticks, '
              f'{len(labeled)} labels]  (rotated + staggered)')
        wrote += 1

    print(f'\n{wrote} metric(s) x 2 styles in: {out_dir}')
    print(f'  compare: {out_dir / "rotated"}  vs  {out_dir / "staggered"}')


if __name__ == '__main__':
    main()
