"""
structure_vs_program_eval.py  -- is the NSI structure read better than what we have?

Runs AFTER the corpus NSI pull (nsi_corpus_enrich.py --data --apply) has written
each district's derived/structures/nsi_structures_*.json. Reads, per district:

  NSI      : newest derived/structures/nsi_structures_*.json -> occupancy_share.shares_sqft
  PROGRAM  : newest derived/program_mix_corrected_*.json      -> shares_after
             (floor-area + residential-seed + anchor = "what we have")
             fallback if no corrected file: overture_places.json POI histogram shares
  LABELS   : _shared/typologies.json (primary / secondary)

Compares the two reads three ways, all in the project's own validated terms:

  1. RESIDENTIAL HEADLINE - where the NSI structure read finds housing the
     floor-area read misses (the whole reason to add NSI). Per district:
     program residential% vs NSI residential sqft%, and the delta.

  2. TYPOLOGY SEPARATION - mean |group-mean minus corpus-mean| across the 8
     buckets, grouped by known primary typology (the same score consolidate uses).
     Higher = the four typologies sit in more distinct regions.

  3. LEAVE-ONE-OUT HYBRID PLACEMENT (k=7) - the validated retrieval target. Hold
     each district out, rank the rest by 8-bucket distance, vote neighbour
     primaries; HARD = nearest shares the known primary, HYBRID = predicted is the
     known primary OR secondary. Run for PROGRAM-alone and NSI-alone on the SAME
     district set, so it is a fair head-to-head.

HONEST FRAME (not a prediction): NSI is sport-blind (HAZUS has no sport class), so
expect it to LOSE on sport-driven distinctions and possibly WIN on residential
(Community) ones. "Better" is read off the three numbers, not assumed. Either way
the result is a deliverable: it tells you whether NSI is a replacement, a
complementary residential-truth channel, or neither.

Writes nothing unless --out (CSV for Miro). No network.

USAGE (QGIS bundled interpreter):
  python-qgis-ltr.bat structure_vs_program_eval.py --data-dir ..\\data
  python-qgis-ltr.bat structure_vs_program_eval.py --data-dir ..\\data --full
  python-qgis-ltr.bat structure_vs_program_eval.py --data-dir ..\\data --out ..\\data\\structure_vs_program.csv
  python-qgis-ltr.bat structure_vs_program_eval.py --selftest
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROSSETTI_BUCKETS = ['sport', 'residential', 'hotel', 'retail_food_entertainment',
                    'office', 'parking', 'open_space', 'other']

CANON = {'entertainment': 'Entertainment', 'community': 'Community',
         'innovation': 'Innovation', 'sports park': 'Sports Park',
         'sports tourism': 'Sports Park'}

DEFAULT_K = 7


def canon(t):
    return CANON.get((t or '').strip().lower(), (t or '').title() or None)


def core(s):
    s = str(s).lower()
    s = re.sub(r'^\d+[_-]', '', s)
    s = re.sub(r'[_-][a-z]{2}$', '', s)
    return re.sub(r'[^a-z0-9]', '', s)


def lj(p):
    try:
        if p and p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return None


def newest(derived: Path, pattern: str):
    cands = sorted(derived.glob(pattern), key=lambda d: d.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


# ---- program shares ("what we have") --------------------------------------

def _hist_bucket(cat):
    c = (cat or '').lower()

    def has(*ks):
        return any(k in c for k in ks)
    if has('bar', 'restaurant', 'grill', 'pub', 'cafe', 'coffee', 'lounge',
           'nightclub', 'pizza', 'food', 'bakery', 'brewery', 'winery', 'diner',
           'eatery', 'deli'):
        return 'retail_food_entertainment'
    if has('hotel', 'motel', 'hostel', 'lodging', 'resort', 'bed_and_breakfast'):
        return 'hotel'
    if has('parking'):
        return 'parking'
    if has('software', 'advertising', 'corporate', 'coworking', 'professional',
           'consulting', 'law_firm', 'accounting', 'financial', 'insurance',
           'real_estate', 'office', 'agency'):
        return 'office'
    if has('apartment', 'residential', 'condo', 'housing'):
        return 'residential'
    if has('stadium', 'arena', 'sports', 'gym', 'fitness', 'golf', 'recreation',
           'athletic', 'bowling', 'skating'):
        return 'sport'
    if has('park', 'garden', 'plaza', 'playground', 'trail', 'greenway', 'botanical'):
        return 'open_space'
    if has('music', 'concert', 'theatre', 'theater', 'cinema', 'museum', 'gallery',
           'art', 'entertainment', 'casino', 'shop', 'store', 'market', 'retail',
           'mall', 'boutique', 'clothing'):
        return 'retail_food_entertainment'
    return 'other'


def program_shares(derived: Path):
    """Return (shares_dict, source_tag) or (None, None)."""
    pm = newest(derived, 'program_mix_corrected_*.json')
    d = lj(pm)
    if d and d.get('shares_after'):
        sa = d['shares_after']
        return {b: float(sa.get(b, 0.0)) for b in ROSSETTI_BUCKETS}, 'corrected'
    op = lj(derived / 'overture_places.json')
    hist = (op or {}).get('category_histogram')
    if hist:
        c = Counter()
        for cat, n in hist.items():
            c[_hist_bucket(cat)] += n
        tot = sum(c.values()) or 1
        return {b: c.get(b, 0.0) / tot for b in ROSSETTI_BUCKETS}, 'histogram'
    return None, None


def nsi_shares(derived: Path):
    f = newest(derived / 'structures', 'nsi_structures_*.json')
    d = lj(f)
    if not d or not d.get('available', True):
        return None
    occ = d.get('occupancy_share') or {}
    ss = occ.get('shares_sqft')
    if not ss or d.get('n_in_boundary', 0) == 0:
        return None
    if sum(float(v) for v in ss.values()) <= 0:
        return None
    return {b: float(ss.get(b, 0.0)) for b in ROSSETTI_BUCKETS}


def nsi_meta(derived: Path):
    """Structure count and median story count from the NSI file, or None."""
    f = newest(derived / 'structures', 'nsi_structures_*.json')
    d = lj(f)
    if not d or not d.get('available', True):
        return None
    stories = [s.get('num_story') for s in (d.get('structures') or [])
               if isinstance(s.get('num_story'), (int, float))]
    med = float(np.median(stories)) if stories else None
    return {'n': d.get('n_in_boundary'), 'med_story': med}


def overture_buildings_meta(derived: Path):
    """What we have: Overture building count + typical story count (mean h / 3.5)."""
    bh = lj(derived / 'building_heights_overture.json') or {}
    n = bh.get('n_buildings')
    mh = bh.get('mean_height_m_area_wtd')
    typ_story = round(mh / 3.5, 1) if isinstance(mh, (int, float)) and mh else None
    return {'n': n, 'typ_story': typ_story} if (n or typ_story) else None


# ---- labels ---------------------------------------------------------------

def read_labels(data: Path):
    prim, sec = {}, {}
    doc = lj(data / '_shared' / 'typologies.json')
    if doc:
        districts = doc.get('districts', doc)
        for k, v in districts.items():
            if isinstance(v, dict):
                prim[core(k)] = canon(v.get('primary_typology') or v.get('primary'))
                s = v.get('secondary_typology') or v.get('secondary')
                if s:
                    sec[core(k)] = canon(s)
    return prim, sec


# ---- metrics --------------------------------------------------------------

def separation(M: np.ndarray, labels: list) -> float:
    overall = M.mean(axis=0)
    groups = defaultdict(list)
    for i, lab in enumerate(labels):
        if lab:
            groups[lab].append(i)
    if len(groups) < 2:
        return float('nan')
    per_group = []
    for lab, idx in groups.items():
        gmean = M[idx].mean(axis=0)
        per_group.append(np.abs(gmean - overall).mean())
    return float(np.mean(per_group))


def loo_hybrid(M: np.ndarray, prim: list, sec: list, k: int):
    """Leave-one-out: return (hard_frac, hybrid_frac, n)."""
    mu = M.mean(axis=0)
    sd = np.where(M.std(axis=0) > 0, M.std(axis=0), 1.0)
    Z = (M - mu) / sd
    labeled = [i for i, p in enumerate(prim) if p]
    n = len(labeled)
    if n < 3:
        return float('nan'), float('nan'), n
    hard = hyb = 0
    for i in labeled:
        d = np.sqrt(((Z - Z[i]) ** 2).sum(axis=1)) / np.sqrt(Z.shape[1])
        d[i] = np.inf
        order = np.argsort(d)
        nearest = order[0]
        topk = order[:k]
        known = {prim[i], sec[i]} - {None}
        if prim[nearest] == prim[i]:
            hard += 1
        votes = [prim[j] for j in topk]
        if any(v in known for v in votes):
            hyb += 1
    return hard / n, hyb / n, n


# ---- driver ---------------------------------------------------------------

def collect(data: Path):
    prim_l, sec_l = read_labels(data)
    rows = []
    for d in sorted(data.iterdir()):
        if not (d.is_dir() and d.name[:2].isdigit()):
            continue
        derived = d / 'derived'
        if not derived.exists():
            continue
        prog, psrc = program_shares(derived)
        nsi = nsi_shares(derived)
        c = core(d.name)
        rows.append({
            'sad_id': d.name,
            'prog': prog, 'prog_src': psrc, 'nsi': nsi,
            'nsi_meta': nsi_meta(derived),
            'our_meta': overture_buildings_meta(derived),
            'primary': prim_l.get(c), 'secondary': sec_l.get(c),
        })
    return rows


def fmt_pct(x):
    return f'{x:.1%}' if x is not None else '   -'


def run(data: Path, full: bool, out: Path | None):
    rows = collect(data)
    both = [r for r in rows if r['prog'] and r['nsi']]
    print(f'districts scanned: {len(rows)}')
    print(f'  with program shares : {sum(1 for r in rows if r["prog"])}')
    print(f'  with NSI shares     : {sum(1 for r in rows if r["nsi"])}')
    print(f'  with BOTH (head-to-head set) : {len(both)}\n')
    if not both:
        print('No districts have both reads yet. Run nsi_corpus_enrich.py --data '
              '--apply first (or wait for the corpus pull to land).')
        return

    # 1) residential headline
    print('--- residential share: program (what we have) vs NSI structure read ---')
    print(f'  {"district":<42}{"prog":>8}{"NSI":>8}{"delta":>9}  flag')
    csv_rows = []
    for r in sorted(both, key=lambda r: r['sad_id']):
        pr = r['prog']['residential']
        nr = r['nsi']['residential']
        delta = nr - pr
        flag = 'NSI finds housing' if delta >= 0.05 else (
               'program higher' if delta <= -0.05 else 'agree')
        print(f'  {r["sad_id"]:<42}{pr:>8.1%}{nr:>8.1%}{delta:>+9.1%}  {flag}')
        csv_rows.append({'sad_id': r['sad_id'], 'known_primary': r['primary'] or '-',
                         'prog_resi': round(pr, 4), 'nsi_resi': round(nr, 4),
                         'resi_delta': round(delta, 4), 'flag': flag})

    # 1b) raw building-data coverage: NSI structures vs Overture footprints we have
    cov = [r for r in rows if r['nsi_meta'] and r['our_meta']]
    if cov:
        print('\n--- building data: what we have (Overture) vs NSI structures ---')
        print(f'  {"district":<42}{"our bldgs":>10}{"NSI strs":>10}'
              f'{"our story":>10}{"NSI story":>10}')
        for r in sorted(cov, key=lambda r: r['sad_id']):
            om, nm = r['our_meta'], r['nsi_meta']
            ob = om['n'] if om['n'] is not None else '-'
            nb = nm['n'] if nm['n'] is not None else '-'
            os_ = f'{om["typ_story"]:.1f}' if om['typ_story'] is not None else '-'
            ns_ = f'{nm["med_story"]:.1f}' if nm['med_story'] is not None else '-'
            print(f'  {r["sad_id"]:<42}{str(ob):>10}{str(nb):>10}{os_:>10}{ns_:>10}')
        print('  (our story = Overture mean height / 3.5; NSI story = median num_story. '
              'Footprints and parcel-structures are not identical units; read as coverage.)')

    if full:
        print('\n--- full 8-bucket shares (P=program, N=NSI) ---')
        for r in sorted(both, key=lambda r: r['sad_id']):
            print(f'\n  {r["sad_id"]}  (program source: {r["prog_src"]})')
            print('    ' + ''.join(f'{b[:5]:>9}' for b in ROSSETTI_BUCKETS))
            print('  P ' + ''.join(f'{r["prog"][b]:>9.1%}' for b in ROSSETTI_BUCKETS))
            print('  N ' + ''.join(f'{r["nsi"][b]:>9.1%}' for b in ROSSETTI_BUCKETS))

    # 2 + 3) typology separation and LOO hybrid placement, same district set
    Mp = np.array([[r['prog'][b] for b in ROSSETTI_BUCKETS] for r in both])
    Mn = np.array([[r['nsi'][b] for b in ROSSETTI_BUCKETS] for r in both])
    prim = [r['primary'] for r in both]
    sec = [r['secondary'] for r in both]

    sep_p = separation(Mp, prim)
    sep_n = separation(Mn, prim)
    print('\n--- typology separation (higher separates the 4 types better) ---')
    print(f'  program-alone : {sep_p:.4f}')
    print(f'  NSI-alone     : {sep_n:.4f}')
    if sep_p == sep_p and sep_n == sep_n:
        print(f'  winner: {"NSI" if sep_n > sep_p else "program"} '
              f'(by {abs(sep_n - sep_p):.4f})')

    base = Counter(p for p in prim if p)
    n_lab = sum(base.values())
    maj = base.most_common(1)[0][1] / n_lab if n_lab else float('nan')
    hp, hyp, n = loo_hybrid(Mp, prim, sec, DEFAULT_K)
    hn, hyn, _ = loo_hybrid(Mn, prim, sec, DEFAULT_K)
    print(f'\n--- leave-one-out placement (k={DEFAULT_K}, n={n}, majority baseline {maj:.1%}) ---')
    print(f'  {"channel":<16}{"hard":>9}{"hybrid":>9}')
    print(f'  {"program-alone":<16}{hp:>9.1%}{hyp:>9.1%}')
    print(f'  {"NSI-alone":<16}{hn:>9.1%}{hyn:>9.1%}')

    # verdict
    print('\n--- verdict ---')
    housing = [r['sad_id'] for r in both if r['nsi']['residential'] - r['prog']['residential'] >= 0.05]
    print(f'  NSI raises residential by >=5 pts in {len(housing)}/{len(both)} districts '
          '(structure read catching towers the floor-area read missed).')
    print('  NSI sport share is ~0 by construction; sport stays a program-channel signal.')
    better_sep = 'NSI' if (sep_n == sep_n and sep_p == sep_p and sep_n > sep_p) else 'program'
    better_hyb = 'NSI' if (hyn == hyn and hyp == hyp and hyn > hyp) else 'program'
    if better_sep == 'program' and better_hyb == 'program':
        print('  On typology separation AND placement, program-alone wins -> NSI is a '
              'COMPLEMENTARY residential-truth channel, not a replacement. Add it as '
              'its own channel; do not fuse it into program.')
    elif better_sep == 'NSI' and better_hyb == 'NSI':
        print('  NSI-alone wins on BOTH -> structure occupancy is the stronger typology '
              'signal. Worth weighting NSI heavily, or leading with it.')
    else:
        print('  Split decision (separation and placement disagree) -> keep both as '
              'separate channels and let the disagreement be visible; that gap is the '
              'analytically useful part.')

    if out:
        with out.open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)
        print(f'\n[wrote] {out}  ({len(csv_rows)} rows for Miro)')


# ---- offline self-test ----------------------------------------------------

def selftest():
    """Build a synthetic mini-corpus on disk and run the full eval, no network."""
    import tempfile
    import datetime
    root = Path(tempfile.mkdtemp()) / 'data'
    (root / '_shared').mkdir(parents=True)

    # 6 districts across 3 typologies; NSI bumps residential for Community ones
    spec = {
        '32_District-Detroit_Detroit-MI': ('Entertainment', 'Innovation',
            {'sport': .05, 'residential': .17, 'retail_food_entertainment': .45,
             'office': .20, 'parking': .08, 'hotel': .05},
            {'residential': .30, 'retail_food_entertainment': .35, 'office': .20,
             'parking': .10, 'hotel': .05}),
        '05_Innov-A_Boston-MA': ('Innovation', 'Community',
            {'office': .55, 'residential': .20, 'retail_food_entertainment': .20, 'other': .05},
            {'office': .50, 'residential': .35, 'retail_food_entertainment': .15}),
        '07_Comm-A_Columbus-OH': ('Community', 'Entertainment',
            {'residential': .30, 'retail_food_entertainment': .35, 'open_space': .25, 'other': .10},
            {'residential': .60, 'retail_food_entertainment': .20, 'open_space': .15, 'other': .05}),
        '09_Comm-B_Cary-NC': ('Community', None,
            {'residential': .28, 'retail_food_entertainment': .32, 'open_space': .30, 'other': .10},
            {'residential': .62, 'retail_food_entertainment': .18, 'open_space': .15, 'other': .05}),
        '11_Ent-A_Nashville-TN': ('Entertainment', 'Sports Park',
            {'retail_food_entertainment': .55, 'hotel': .20, 'sport': .10, 'parking': .15},
            {'retail_food_entertainment': .50, 'hotel': .25, 'parking': .20, 'other': .05}),
        '13_Innov-B_Austin-TX': ('Innovation', None,
            {'office': .60, 'residential': .18, 'retail_food_entertainment': .17, 'other': .05},
            {'office': .52, 'residential': .33, 'retail_food_entertainment': .15}),
    }
    typ = {'districts': {}}
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    rng = __import__('random').Random(7)
    for sid, (p, s, prog, nsi) in spec.items():
        der = root / sid / 'derived'
        (der / 'structures').mkdir(parents=True)
        typ['districts'][sid] = {'primary_typology': p, 'secondary_typology': s}
        (der / f'program_mix_corrected_{stamp}.json').write_text(json.dumps(
            {'shares_after': {b: prog.get(b, 0.0) for b in ROSSETTI_BUCKETS}}))
        # synthetic structures so coverage (count + median story) has data
        structs = [{'num_story': rng.choice([1, 1, 2, 3, 4, 8])} for _ in range(50)]
        (der / 'structures' / f'nsi_structures_{stamp}.json').write_text(json.dumps(
            {'available': True, 'n_in_boundary': 50, 'structures': structs,
             'occupancy_share': {'shares_sqft': {b: nsi.get(b, 0.0) for b in ROSSETTI_BUCKETS}}}))
        (der / 'building_heights_overture.json').write_text(json.dumps(
            {'n_buildings': rng.randint(40, 80), 'mean_height_m_area_wtd': rng.uniform(8, 45)}))
    (root / '_shared' / 'typologies.json').write_text(json.dumps(typ))

    print('SELFTEST (offline synthetic corpus)\n')
    run(root, full=False, out=None)
    print('\n  [if the three sections printed with numbers, the eval wiring is sound]')
    return 0


def main():
    ap = argparse.ArgumentParser(description='NSI structure read vs program read')
    ap.add_argument('--data-dir', type=Path, default=None)
    ap.add_argument('--full', action='store_true', help='print full 8-bucket per-district')
    ap.add_argument('--out', type=Path, default=None, help='CSV for Miro')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if not a.data_dir:
        ap.error('pass --data-dir <data root> or --selftest')
    run(a.data_dir.resolve(), a.full, a.out)


if __name__ == '__main__':
    main()
