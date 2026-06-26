"""
structure_match.py  -  NSI structure-occupancy matching for the typology fit

The structure-channel mirror of program_match.py. Same 8 Rossetti buckets, same
z-scored distance, same typology_fit shape, so it slots in beside the program
channel as a SECOND panel without touching the validated program numbers.

WHERE THE VECTOR COMES FROM
  corpus side : each district's newest derived/structures/nsi_structures_*.json
                -> occupancy_share.shares_sqft  (written by nsi_corpus_enrich.py)
  drawn side  : a live NSI POST for the drawn polygon, clipped to it, summarized
                the same way (reuses nsi_corpus_enrich so both sides are identical)

WHAT IT IS, HONESTLY (carried over from the corpus eval)
  - NSI is sport-blind: HAZUS has no sport class, so the sport bucket is ~0 on
    both sides. Sport stays a program-channel signal. The driver map below never
    leans on sport.
  - NSI separates the four typologies slightly better than program-alone
    (0.0543 vs 0.0397) but is not a replacement; it is a residential-occupancy
    channel that disagrees with program productively (towers vs anchors).
  - This is a SEPARATE channel by design. It does not modify program_match.

USE
  import structure_match
  structure_match.register(app, data_dir)     # adds POST /analyze_structures
  python-qgis-ltr.bat structure_match.py --data-dir ..\\data        # corpus dump
  python-qgis-ltr.bat structure_match.py --selftest                 # offline check
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import numpy as np

# Reuse the corpus enrichment's bucket list, mapping, and live-pull helpers so
# there is one source of truth for the structure read.
from nsi_corpus_enrich import (ROSSETTI_BUCKETS, bbox_fc, post_nsi, features_of,
                               parse_structures, summarize)

FOUR_TYPOLOGIES = ['Entertainment', 'Community', 'Innovation', 'Sports Park']

# Which occupancy buckets characteristically drive each typology, for the plain
# language "why" only (NOT used to compute the score; that is a neighbour vote).
# Sport is intentionally absent: NSI cannot see it.
_TYPOLOGY_DRIVERS = {
    'Entertainment': ['retail_food_entertainment', 'hotel'],
    'Community':     ['residential', 'open_space'],
    'Innovation':    ['office', 'residential'],
    'Sports Park':   ['parking', 'open_space', 'other'],
}


def _core(s):
    s = str(s).lower()
    s = re.sub(r'^\d+[_-]', '', s)
    s = re.sub(r'[_-][a-z]{2}$', '', s)
    return re.sub(r'[^a-z0-9]', '', s)


def _newest(d: Path, pattern: str):
    cands = sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def structure_vector_from_file(nsi_path: Path):
    """occupancy_share.shares_sqft -> {bucket: share}, or None if unavailable/empty."""
    try:
        d = json.loads(nsi_path.read_text())
    except Exception:
        return None
    if not d.get('available', True):
        return None
    ss = (d.get('occupancy_share') or {}).get('shares_sqft')
    if not ss or d.get('n_in_boundary', 0) == 0:
        return None
    if sum(float(v) for v in ss.values()) <= 0:
        return None
    return {b: float(ss.get(b, 0.0)) for b in ROSSETTI_BUCKETS}


# ---- corpus side -----------------------------------------------------------

class StructureCorpus:
    """Structure-occupancy vectors for every district with an NSI file."""
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.records = []
        names = self._names_from_manifest()
        typ = self._typologies_from_shared()
        for d in sorted(self.data_dir.iterdir()):
            if not (d.is_dir() and not d.name.startswith('_') and d.name[:2].isdigit()):
                continue
            f = _newest(d / 'derived' / 'structures', 'nsi_structures_*.json')
            if not f:
                continue
            vec = structure_vector_from_file(f)
            if not vec:
                continue
            t = typ.get(_core(d.name), {})
            self.records.append({
                'sad_id': d.name,
                'sad_name': names.get(d.name, {}).get('name', d.name),
                'typology': names.get(d.name, {}).get('typology'),
                'primary_typology': t.get('primary'),
                'secondary_typology': t.get('secondary'),
                'region': names.get(d.name, {}).get('region'),
                'vec': vec,
            })
        if not self.records:
            raise ValueError('no nsi_structures_*.json with shares found in corpus '
                             '(run nsi_corpus_enrich.py --data --apply first)')
        M = np.array([[r['vec'][b] for b in ROSSETTI_BUCKETS] for r in self.records])
        self.mean = M.mean(axis=0)
        self.std = np.where(M.std(axis=0) > 0, M.std(axis=0), 1.0)

    def _typologies_from_shared(self):
        out = {}
        tj = self.data_dir / '_shared' / 'typologies.json'
        if tj.exists():
            try:
                doc = json.loads(tj.read_text())
                districts = doc.get('districts', doc)
                for k, v in districts.items():
                    if isinstance(v, dict):
                        out[_core(k)] = {
                            'primary': v.get('primary_typology') or v.get('primary'),
                            'secondary': v.get('secondary_typology') or v.get('secondary'),
                        }
            except Exception:
                pass
        return out

    def _names_from_manifest(self):
        man = self.data_dir / '_compare_ui' / 'compare_manifest.json'
        out = {}
        if man.exists():
            try:
                doc = json.loads(man.read_text())
                for r in doc.get('sads', []):
                    out[r.get('sad_id')] = {'name': r.get('sad_name'),
                                            'typology': r.get('typology'),
                                            'region': r.get('region')}
            except Exception:
                pass
        return out

    def _z(self, vec):
        v = np.array([vec[b] for b in ROSSETTI_BUCKETS])
        return (v - self.mean) / self.std

    def rank(self, vec, k: int = 8):
        dz = self._z(vec)
        out = []
        for r in self.records:
            cz = self._z(r['vec'])
            d = float(np.sqrt(np.sum((dz - cz) ** 2)) / np.sqrt(len(ROSSETTI_BUCKETS)))
            out.append({'sad_id': r['sad_id'], 'sad_name': r['sad_name'],
                        'typology': r['typology'], 'region': r['region'],
                        'distance': round(d, 3),
                        'structure': {b: round(r['vec'][b], 3) for b in ROSSETTI_BUCKETS}})
        out.sort(key=lambda x: x['distance'])
        return out[:k]


def typology_fit(vec, corpus: StructureCorpus, k: int = 7):
    """Closeness-weighted percent fit to each of the four typologies, identical in
    shape to program_match.typology_fit. Neighbours vote primary (full) + secondary
    (half), weighted 1/(distance+eps), over the structure-occupancy distance."""
    dz = corpus._z(vec)
    scored = []
    for r in corpus.records:
        cz = corpus._z(r['vec'])
        d = float(np.sqrt(np.sum((dz - cz) ** 2)) / np.sqrt(len(ROSSETTI_BUCKETS)))
        scored.append((d, r))
    scored.sort(key=lambda x: x[0])
    topk = scored[:k]

    weights = {t: 0.0 for t in FOUR_TYPOLOGIES}
    eps = 1e-3
    for d, r in topk:
        w = 1.0 / (d + eps)
        prim = r.get('primary_typology') or r.get('typology')
        sec = r.get('secondary_typology')
        if prim in weights:
            weights[prim] += w
        if sec in weights:
            weights[sec] += 0.5 * w
    tot = sum(weights.values()) or 1.0
    pct = {t: round(100.0 * weights[t] / tot, 1) for t in FOUR_TYPOLOGIES}

    ranked = sorted(pct.items(), key=lambda kv: kv[1], reverse=True)
    top_typ = ranked[0][0]
    drivers = _TYPOLOGY_DRIVERS.get(top_typ, [])
    present = [(b, round(vec.get(b, 0.0), 3)) for b in drivers if vec.get(b, 0.0) > 0.02]
    present.sort(key=lambda x: x[1], reverse=True)
    why = ', '.join(f'{b.replace("_", " ")} {int(round(v * 100))}%' for b, v in present)

    return {
        'percent_by_typology': pct,
        'ranked': ranked,
        'top_typology': top_typ,
        'why': why or 'mixed occupancy with no single dominant use',
        'sport_blind': True,
        'neighbors': [{'sad_id': r['sad_id'], 'sad_name': r['sad_name'],
                       'primary': r.get('primary_typology') or r.get('typology'),
                       'secondary': r.get('secondary_typology'),
                       'distance': round(d, 3)} for d, r in topk],
    }


# ---- drawn side (live NSI pull, reuses nsi_corpus_enrich) -------------------

def pull_drawn_structure_vector(geometry: dict, timeout: int = 180):
    """Live NSI POST for a drawn polygon -> shares_sqft, clipped to the polygon."""
    from shapely.geometry import shape
    geom = shape(geometry)
    if geom.is_empty:
        return None, {}
    bbox, fc = bbox_fc(geom)
    resp = post_nsi(fc, timeout=timeout)
    feats = features_of(resp)
    if not feats:
        return None, {'n_returned': 0, 'n_in_boundary': 0}
    structures, _ = parse_structures(feats, geom)
    occ_share, _hist, tot_n, _tot_sq = summarize(structures)
    if tot_n == 0 or not occ_share.get('sqft_field_present'):
        # fall back to count shares if sqft missing
        ss = occ_share['shares_count'] if tot_n else None
    else:
        ss = occ_share['shares_sqft']
    if not ss or sum(ss.values()) <= 0:
        return None, {'n_returned': len(feats), 'n_in_boundary': tot_n}
    return {b: float(ss.get(b, 0.0)) for b in ROSSETTI_BUCKETS}, {
        'n_returned': len(feats), 'n_in_boundary': tot_n}


def analyze_polygon_structure(geometry: dict, corpus: StructureCorpus, k: int = 8):
    vec, meta = pull_drawn_structure_vector(geometry)
    if not vec:
        return {'ok': False, 'error': 'No NSI structures found in that polygon '
                '(US only; outside the US returns nothing).'}
    return {
        'ok': True,
        'method': 'structure_occupancy_nsi_v1',
        'n_structures': meta.get('n_in_boundary'),
        'structure': {b: round(vec[b], 3) for b in ROSSETTI_BUCKETS},
        'matches': corpus.rank(vec, k=k),
        'typology_fit': typology_fit(vec, corpus, k=min(k, 7)),
        'note': ('NSI structure-occupancy shares (sqft-weighted). Sport reads ~0 '
                 '(HAZUS has no sport class); read alongside the program channel.'),
    }


# ---- register on the Flask app ---------------------------------------------

def register(app, data_dir: Path):
    from flask import request, jsonify
    corpus = StructureCorpus(data_dir)
    print(f'  structure corpus: {len(corpus.records)} districts with NSI shares')

    @app.route('/analyze_structures', methods=['POST', 'OPTIONS'])
    def analyze_structures():
        if request.method == 'OPTIONS':
            return ('', 204)
        body = request.get_json(force=True)
        geom = body.get('geometry')
        if not geom:
            return jsonify({'ok': False, 'error': 'No geometry in request.'}), 400
        try:
            res = analyze_polygon_structure(geom, corpus, k=int(body.get('k', 8)))
            return jsonify(res), (200 if res.get('ok') else 422)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'ok': False, 'error': str(e)}), 500

    return corpus


# ---- offline self-test -----------------------------------------------------

def selftest():
    import tempfile
    import datetime
    root = Path(tempfile.mkdtemp()) / 'data'
    (root / '_shared').mkdir(parents=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    spec = {
        '32_District-Detroit_Detroit-MI': ('Entertainment', 'Innovation',
            {'retail_food_entertainment': .81, 'residential': .07, 'office': .04, 'hotel': .05, 'other': .03}),
        '07_Comm-A_Columbus-OH': ('Community', None,
            {'residential': .60, 'retail_food_entertainment': .20, 'open_space': .15, 'other': .05}),
        '13_Innov-B_Austin-TX': ('Innovation', None,
            {'office': .58, 'residential': .27, 'retail_food_entertainment': .15}),
        '11_Ent-A_Nashville-TN': ('Entertainment', 'Sports Park',
            {'retail_food_entertainment': .60, 'hotel': .25, 'other': .15}),
        '20_Comm-B_Columbus-OH': ('Community', 'Innovation',
            {'residential': .55, 'office': .20, 'open_space': .15, 'other': .10}),
    }
    typ = {'districts': {}}
    for sid, (p, s, nsi) in spec.items():
        der = root / sid / 'derived' / 'structures'
        der.mkdir(parents=True)
        typ['districts'][sid] = {'primary_typology': p, 'secondary_typology': s}
        (der / f'nsi_structures_{stamp}.json').write_text(json.dumps(
            {'available': True, 'n_in_boundary': 40,
             'occupancy_share': {'shares_sqft': {b: nsi.get(b, 0.0) for b in ROSSETTI_BUCKETS}}}))
    (root / '_shared' / 'typologies.json').write_text(json.dumps(typ))

    corpus = StructureCorpus(root)
    print('SELFTEST (offline)\n')
    print(f'corpus districts: {len(corpus.records)}')
    # hold out Columbus-Community and see what the channel says
    held = next(r for r in corpus.records if r['sad_id'].startswith('07_'))
    fit = typology_fit(held['vec'], corpus, k=4)
    print(f'\nheld-out {held["sad_id"]} (known {held["primary_typology"]}):')
    print(f'  percent_by_typology: {fit["percent_by_typology"]}')
    print(f'  top: {fit["top_typology"]}   why: {fit["why"]}')
    print(f'  neighbors: {[n["sad_id"] for n in fit["neighbors"]]}')
    checks = [
        ('typology_fit has 4 keys', set(fit['percent_by_typology']) == set(FOUR_TYPOLOGIES)),
        ('percents sum ~100', abs(sum(fit['percent_by_typology'].values()) - 100.0) < 0.5),
        ('ranked is sorted desc', all(fit['ranked'][i][1] >= fit['ranked'][i + 1][1]
                                      for i in range(len(fit['ranked']) - 1))),
        ('sport_blind flagged', fit.get('sport_blind') is True),
        ('residential-heavy held-out reads Community top', fit['top_typology'] == 'Community'),
        ('rank returns neighbors', len(corpus.rank(held['vec'], k=3)) == 3),
    ]
    print('\n  checks:')
    ok = True
    for label, passed in checks:
        print(f'    [{"PASS" if passed else "FAIL"}] {label}')
        ok = ok and passed
    print(f'\n  SELFTEST {"PASSED" if ok else "FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='NSI structure-occupancy match / typology fit')
    ap.add_argument('--data-dir', type=Path, default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if not a.data_dir:
        ap.error('pass --data-dir <data root> or --selftest')
    corpus = StructureCorpus(a.data_dir.resolve())
    print(f'corpus districts with structure vectors: {len(corpus.records)}\n')
    print(f'{"district":<46}' + ''.join(f'{b[:5]:>7}' for b in ROSSETTI_BUCKETS))
    for r in corpus.records:
        print(f'{r["sad_id"]:<46}' + ''.join(f'{r["vec"][b]:>7.2f}' for b in ROSSETTI_BUCKETS))
