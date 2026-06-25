"""
program_match.py  -  program-mix matching for the "Draw a district" tool

Adds the program dimension the census-first server (sad_match_server.py) defers.
Given a drawn polygon it:

  1. Pulls Overture places (POIs) inside the polygon live (same query the corpus
     pull uses: pull_overture_all.py),
  2. Maps each POI's Overture category to one of the 8 ROSSETTI program buckets,
  3. Builds a normalized 8-bucket program vector for the drawn area,
  4. Ranks the existing corpus by program similarity, where each corpus district's
     vector is built the SAME way from its derived/overture_places.json histogram.

Both sides use one method (POI-count shares over the same bucket map), so the
comparison is apples-to-apples.

HONEST LIMIT: this is POI-based, so residential reads low on both sides (POIs only
see ground-floor businesses). That is the same blind spot the corpus floor-area fix
solved for the embedding; reproducing that live on a drawn polygon is the next
increment. This v1 is still a real program match and strictly better than
demographics-only.

USE  - importable:  from program_match import ProgramCorpus, analyze_polygon_program
      - or register a route on an existing Flask app:
            import program_match
            program_match.register(app, data_dir, release="2026-05-20.0")
        then POST {"geometry": <GeoJSON geometry>} to /analyze_program
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np

ROSSETTI_BUCKETS = ['sport', 'residential', 'hotel', 'retail_food_entertainment',
                    'office', 'parking', 'open_space', 'other']

# Overture place category -> ROSSETTI bucket, matched by keyword tokens (Overture
# categories are lowercase tokens like 'restaurant', 'hotel', 'sports_club').
# Checked in this order; first hit wins. Parking before automotive, hotel before
# generic retail, etc.
_BUCKET_RULES = [
    ('parking',                   ['parking', 'garage']),
    ('hotel',                     ['hotel', 'motel', 'hostel', 'lodging',
                                   'accommodation', 'resort', 'bed_and_breakfast',
                                   'inn']),
    ('sport',                     ['sport', 'stadium', 'arena', 'fitness', 'gym',
                                   'athletic', 'golf', 'bowling', 'skating',
                                   'recreation_center', 'climbing', 'martial_arts',
                                   'yoga', 'dance', 'tennis']),
    ('open_space',                ['park', 'garden', 'plaza', 'trail', 'playground',
                                   'nature', 'beach', 'campground', 'botanical']),
    ('office',                    ['office', 'professional', 'financial', 'bank',
                                   'insurance', 'real_estate', 'legal', 'lawyer',
                                   'accounting', 'coworking', 'corporate',
                                   'software', 'consulting', 'advertising',
                                   'architect', 'engineering']),
    ('residential',               ['residential', 'apartment', 'condo', 'housing']),
    ('retail_food_entertainment', ['restaurant', 'bar', 'cafe', 'coffee', 'food',
                                   'eat_and_drink', 'retail', 'shop', 'store',
                                   'shopping', 'market', 'mall', 'brewery',
                                   'winery', 'distillery', 'nightlife', 'club',
                                   'entertainment', 'arts', 'theater', 'theatre',
                                   'cinema', 'movie', 'music', 'gallery', 'museum',
                                   'grocery', 'bakery', 'pub']),
]


def bucket_for(category: str) -> str:
    c = str(category or '').lower()
    if not c:
        return 'other'
    for bucket, toks in _BUCKET_RULES:
        if any(t in c for t in toks):
            return bucket
    return 'other'


def vector_from_histogram(hist: dict) -> dict:
    """{overture_category: count} -> normalized {bucket: share} over 8 buckets."""
    area = {b: 0.0 for b in ROSSETTI_BUCKETS}
    for cat, cnt in (hist or {}).items():
        try:
            area[bucket_for(cat)] += float(cnt)
        except (TypeError, ValueError):
            continue
    tot = sum(area.values())
    if tot <= 0:
        return {b: 0.0 for b in ROSSETTI_BUCKETS}
    return {b: area[b] / tot for b in ROSSETTI_BUCKETS}


# ---- corpus side -----------------------------------------------------------

class ProgramCorpus:
    """Program vectors for every existing district, from its overture_places.json."""
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.records = []
        names = self._names_from_manifest()
        typ = self._typologies_from_shared()
        for d in sorted(self.data_dir.iterdir()):
            if not (d.is_dir() and not d.name.startswith('_') and d.name[:2].isdigit()):
                continue
            places = d / 'derived' / 'overture_places.json'
            if not places.exists():
                continue
            try:
                hist = json.loads(places.read_text()).get('category_histogram', {})
            except Exception:
                continue
            if not hist:
                continue
            t = typ.get(self._core(d.name), {})
            self.records.append({
                'sad_id': d.name,
                'sad_name': names.get(d.name, {}).get('name', d.name),
                'typology': names.get(d.name, {}).get('typology'),
                'primary_typology': t.get('primary'),
                'secondary_typology': t.get('secondary'),
                'region': names.get(d.name, {}).get('region'),
                'vec': vector_from_histogram(hist),
            })
        if not self.records:
            raise ValueError('no overture_places.json with histograms found in corpus')
        # per-bucket mean/std across corpus, for z-scored distance
        M = np.array([[r['vec'][b] for b in ROSSETTI_BUCKETS] for r in self.records])
        self.mean = M.mean(axis=0)
        self.std = np.where(M.std(axis=0) > 0, M.std(axis=0), 1.0)

    @staticmethod
    def _core(s):
        import re
        s = str(s).lower()
        s = re.sub(r'^\d+[_-]', '', s)
        s = re.sub(r'[_-][a-z]{2}$', '', s)
        return re.sub(r'[^a-z0-9]', '', s)

    def _typologies_from_shared(self) -> dict:
        """Read _shared/typologies.json (nested districts schema) -> {core: {primary, secondary}}."""
        out = {}
        tj = self.data_dir / '_shared' / 'typologies.json'
        if tj.exists():
            try:
                doc = json.loads(tj.read_text())
                districts = doc.get('districts', doc)
                for k, v in districts.items():
                    if isinstance(v, dict):
                        out[self._core(k)] = {
                            'primary': v.get('primary_typology') or v.get('primary'),
                            'secondary': v.get('secondary_typology') or v.get('secondary'),
                        }
            except Exception:
                pass
        return out

    def _names_from_manifest(self) -> dict:
        man = self.data_dir / '_compare_ui' / 'compare_manifest.json'
        out = {}
        if man.exists():
            try:
                doc = json.loads(man.read_text())
                for r in doc.get('sads', []):
                    out[r.get('sad_id')] = {
                        'name': r.get('sad_name'),
                        'typology': r.get('typology'),
                        'region': r.get('region'),
                    }
            except Exception:
                pass
        return out

    def _z(self, vec: dict) -> np.ndarray:
        v = np.array([vec[b] for b in ROSSETTI_BUCKETS])
        return (v - self.mean) / self.std

    def rank(self, vec: dict, k: int = 8) -> list[dict]:
        dz = self._z(vec)
        out = []
        for r in self.records:
            cz = self._z(r['vec'])
            d = float(np.sqrt(np.sum((dz - cz) ** 2)) / np.sqrt(len(ROSSETTI_BUCKETS)))
            out.append({'sad_id': r['sad_id'], 'sad_name': r['sad_name'],
                        'typology': r['typology'], 'region': r['region'],
                        'distance': round(d, 3),
                        'program': {b: round(r['vec'][b], 3) for b in ROSSETTI_BUCKETS}})
        out.sort(key=lambda x: x['distance'])
        return out[:k]


# ---- drawn side (live Overture pull) ---------------------------------------

def pull_drawn_histogram(geometry: dict, release: str = '2026-05-20.0') -> dict:
    """Live Overture places query for a drawn polygon -> {category: count}.
    Mirrors pull_overture_all.py: bbox parquet scan, then contains() filter."""
    from collections import Counter
    from shapely.geometry import shape, Point
    import duckdb

    geom = shape(geometry)
    if geom.is_empty:
        return {}
    minx, miny, maxx, maxy = geom.bounds
    con = duckdb.connect()
    for s in ['INSTALL spatial', 'LOAD spatial', 'INSTALL httpfs', 'LOAD httpfs',
              "SET s3_region='us-west-2'"]:
        con.execute(s)
    sql = f"""
        SELECT categories.primary AS category,
               ST_X(geometry) AS lon, ST_Y(geometry) AS lat
        FROM read_parquet(
            's3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*',
            filename=true, hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {minx} AND {maxx}
          AND bbox.ymin BETWEEN {miny} AND {maxy}
    """
    rows = con.execute(sql).fetchall()
    cats = []
    for cat, lon, lat in rows:
        if lon is None or lat is None or not cat:
            continue
        if geom.contains(Point(lon, lat)):
            cats.append(cat)
    return dict(Counter(cats))


FOUR_TYPOLOGIES = ['Entertainment', 'Community', 'Innovation', 'Sports Park']

# Which program buckets characteristically drive each typology, for the "why".
# Used only to explain a score in plain language, not to compute it.
_TYPOLOGY_DRIVERS = {
    'Entertainment': ['retail_food_entertainment', 'sport', 'hotel'],
    'Community':     ['residential', 'open_space', 'retail_food_entertainment'],
    'Innovation':    ['office', 'residential'],
    'Sports Park':   ['sport', 'parking', 'open_space'],
}


def typology_fit(vec: dict, corpus: ProgramCorpus, k: int = 7) -> dict:
    """Closeness-weighted percent fit to each of the four typologies.

    Of the k program-nearest corpus districts, each votes its primary (full weight)
    and secondary (half weight) typology, weighted by 1/(distance+eps). Normalized
    to percentages across the four typologies. Returns the percents, the driving
    program buckets for the top typology, and the neighbors used."""
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

    # rank typologies, build a plain-language "why" for the leader
    ranked = sorted(pct.items(), key=lambda kv: kv[1], reverse=True)
    top_typ = ranked[0][0]
    drivers = _TYPOLOGY_DRIVERS.get(top_typ, [])
    present = [(b, round(vec.get(b, 0.0), 3)) for b in drivers if vec.get(b, 0.0) > 0.02]
    present.sort(key=lambda x: x[1], reverse=True)
    why = ', '.join(f'{b.replace("_", " ")} {int(round(v*100))}%' for b, v in present)

    return {
        'percent_by_typology': pct,
        'ranked': ranked,
        'top_typology': top_typ,
        'why': why or 'mixed program with no single dominant use',
        'neighbors': [{'sad_id': r['sad_id'], 'sad_name': r['sad_name'],
                       'primary': r.get('primary_typology') or r.get('typology'),
                       'secondary': r.get('secondary_typology'),
                       'distance': round(d, 3)} for d, r in topk],
    }


def analyze_polygon_program(geometry: dict, corpus: ProgramCorpus,
                            release: str = '2026-05-20.0', k: int = 8) -> dict:
    hist = pull_drawn_histogram(geometry, release=release)
    if not hist:
        return {'ok': False, 'error': 'No Overture places found in that polygon.'}
    vec = vector_from_histogram(hist)
    return {
        'ok': True,
        'method': 'program_similarity_poi_v1',
        'n_pois': int(sum(hist.values())),
        'program': {b: round(vec[b], 3) for b in ROSSETTI_BUCKETS},
        'matches': corpus.rank(vec, k=k),
        'typology_fit': typology_fit(vec, corpus, k=min(k, 7)),
        'note': ('POI-based program shares (residential reads low; POIs miss '
                 'housing). Floor-area-weighted match is the next increment.'),
    }


# ---- optional: bolt a route onto an existing Flask app ---------------------

def register(app, data_dir: Path, release: str = '2026-05-20.0'):
    from flask import request, jsonify
    corpus = ProgramCorpus(data_dir)
    print(f'  program corpus: {len(corpus.records)} districts with POI histograms')

    @app.route('/analyze_program', methods=['POST', 'OPTIONS'])
    def analyze_program():
        if request.method == 'OPTIONS':
            return ('', 204)
        body = request.get_json(force=True)
        geom = body.get('geometry')
        if not geom:
            return jsonify({'ok': False, 'error': 'No geometry in request.'}), 400
        try:
            res = analyze_polygon_program(geom, corpus, release=release,
                                          k=int(body.get('k', 8)))
            return jsonify(res), (200 if res.get('ok') else 422)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({'ok': False, 'error': str(e)}), 500

    return corpus


# ---- standalone smoke test -------------------------------------------------

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Program-mix corpus check / drawn match')
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--release', default='2026-05-20.0')
    ap.add_argument('--geojson', type=Path, default=None,
                    help='optional drawn polygon GeoJSON to match live')
    a = ap.parse_args()

    corpus = ProgramCorpus(a.data_dir)
    print(f'corpus districts with program vectors: {len(corpus.records)}\n')
    print(f'{"district":<42}' + ''.join(f'{b[:5]:>7}' for b in ROSSETTI_BUCKETS))
    for r in corpus.records:
        print(f'{r["sad_id"]:<42}' +
              ''.join(f'{r["vec"][b]:>7.2f}' for b in ROSSETTI_BUCKETS))

    if a.geojson and a.geojson.exists():
        gj = json.loads(a.geojson.read_text())
        geom = gj.get('geometry') or (gj.get('features', [{}])[0].get('geometry')) or gj
        print('\nlive drawn-polygon match:')
        res = analyze_polygon_program(geom, corpus, release=a.release)
        print(json.dumps(res, indent=2))
