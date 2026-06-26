"""
nsi_corpus_enrich.py  (structure enrichment, STEP 1: one-time corpus-wide NSI pull)

The corpus has zero structure enrichment. This is the one-time pass that gives
every existing district a FEMA/USACE National Structure Inventory read, written
per district as:

    <sad>/derived/structures/nsi_structures_<stamp>.json

Each file carries, per in-boundary structure: HAZUS occupancy code (occtype),
damage category (st_damcat = occupancy class), square footage (sqft), story count
(num_story), a story-count height proxy (est_height_m = num_story x 3.5; Overture
holds the measured height), foundation height (found_ht, a FLOOD attribute, NOT
building height), building type (bldgtype), the mapped Rossetti bucket, and
lon/lat. Plus an occupancy-share summary (sqft-weighted, the channel vector; and
count-weighted for cross-check) and the raw occtype histogram for audit.

This is a grandparent of program_match: same 8 Rossetti buckets, same vector
shape, so the structure channel built later (step 3) is apples-to-apples with
the program channel and BOTH sides of any comparison carry the field.

WHAT NSI SEES (and does not):
  - SEES residential/office/retail occupancy by structure, parcel-modeled, so it
    catches the residential towers POIs are blind to. That is the whole point.
  - Single-occupancy-per-structure: a residential tower over ground retail reads
    as ONE occupancy. NSI under-reads vertical mix; it is a structure read, not a
    floor-area read.
  - HAZUS has NO sport/stadium class. A venue lands in COM8 (entertainment/rec),
    GOV, or other, never 'sport'. So the NSI 'sport' share is structurally ~0 for
    every district, Sports Park included. NSI does not see the anchor as sport;
    that signal stays with the program channel (anchor_overrides). Kept in the
    8-bucket vector for schema parity, flagged sport_blind, never claimed.
  - RES4 is Temporary Lodging (hotels/motels) -> 'hotel', not residential. The
    Detroit seed folded RES4 into residential; corrected here.

Boundary, not canvas: the seed posted the 3 km canvas bbox. This posts the
bounding rectangle of source/sad_boundary.geojson, then clips returned structures
to the actual boundary polygon by point-in-polygon, so shares reflect the
district, not the rectangle. Expect Detroit's 23.3% (canvas) to move; the clipped
number is the honest, program-comparable one.

MODES
  --derived <one district derived/>   single district (default: Detroit folder 32)
       dry-run  : real NSI pull, prints the share table + a key-dump of the first
                  structures (so we confirm field names against the live response),
                  writes nothing.
       --apply  : pull + write that district's nsi_structures_<stamp>.json.
  --data <data root>                   whole corpus, Detroit first
       dry-run  : prints the PLAN only (eligible vs skipped + reasons), no network.
       --apply  : pull + write every eligible district; per-district failures are
                  logged and skipped, never abort the loop. Log -> 00_Admin.
  --selftest                           offline mapping/clip/share check, no network.

Never overwrites (stamped, new-named). GeoJSON not involved (writes JSON).

USAGE (QGIS bundled interpreter):
  $bat = "C:\\Program Files\\QGIS 3.40.11\\bin\\python-qgis-ltr.bat"
  & $bat nsi_corpus_enrich.py --selftest
  & $bat nsi_corpus_enrich.py --derived "C:\\Users\\jmeyers\\Desktop\\Detroit_Test\\data\\32_District-Detroit_Detroit-MI\\derived"
  & $bat nsi_corpus_enrich.py --derived "...\\32_..\\derived" --apply
  & $bat nsi_corpus_enrich.py --data "C:\\Users\\jmeyers\\Desktop\\Detroit_Test\\data"
  & $bat nsi_corpus_enrich.py --data "...\\data" --apply
"""
from __future__ import annotations
import argparse
import datetime
import json
import time
import urllib.request
from collections import Counter
from pathlib import Path

from shapely.geometry import shape, Point, mapping
from shapely.ops import unary_union

NSI_URL = 'https://nsi.sec.usace.army.mil/nsiapi/structures'

ROSSETTI_BUCKETS = ['sport', 'residential', 'hotel', 'retail_food_entertainment',
                    'office', 'parking', 'open_space', 'institutional', 'other']

# HAZUS occupancy-code prefix -> Rossetti bucket. Longest prefix wins (COM10
# before COM1 before COM; RES4 before RES). See WHAT NSI SEES in the docstring
# for the sport_blind and RES4->hotel rationale.
OCC_TO_BUCKET = {
    'RES4':  'hotel',                       # temporary lodging (hotel/motel)
    'RES':   'residential',                 # RES1/2/3A-F/5/6
    'COM10': 'parking',                     # parking
    'COM1':  'retail_food_entertainment',   # retail trade
    'COM2':  'retail_food_entertainment',   # wholesale (coarse)
    'COM3':  'retail_food_entertainment',   # personal/repair services
    'COM4':  'office',                      # professional/technical/business
    'COM5':  'office',                      # banks
    'COM6':  'other',                       # hospital
    'COM7':  'office',                      # medical office/clinic
    'COM8':  'retail_food_entertainment',   # entertainment & recreation
    'COM9':  'retail_food_entertainment',   # theaters
    'COM':   'retail_food_entertainment',   # other COM fallback
    'IND':   'other',
    'AGR':   'open_space',
    'REL':   'institutional',               # religious / worship
    'GOV':   'institutional',               # government / civic
    'EDU':   'institutional',               # schools / colleges
    'PUB':   'institutional',               # public assembly / civic
}
_OCC_KEYS = sorted(OCC_TO_BUCKET, key=len, reverse=True)

# Canadian folder suffixes: NSI is USACE / US only. Mirrors build_compare_manifest.
CANADIAN_SUFFIXES = ('-ON', '-QC', '-BC', '-AB', '-MB', '-SK', '-NS', '-NB',
                     '-NL', '-PE', '-NT', '-YT', '-NU')

DEFAULT_DERIVED = Path(
    r'C:\Users\jmeyers\Desktop\Detroit_Test\data'
    r'\32_District-Detroit_Detroit-MI\derived'
)


def bucket_for(occ: str) -> str:
    occ = str(occ or '').upper()
    for key in _OCC_KEYS:
        if occ.startswith(key):
            return OCC_TO_BUCKET[key]
    return 'other'


def _num(v):
    try:
        x = float(v)
        return x if x == x else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _props(f):
    if isinstance(f, dict):
        p = f.get('properties')
        return p if isinstance(p, dict) else f
    return {}


def _lonlat(f, p):
    g = f.get('geometry') if isinstance(f, dict) else None
    if isinstance(g, dict) and g.get('type') == 'Point' and g.get('coordinates'):
        c = g['coordinates']
        x, y = _num(c[0]), _num(c[1])
        if x is not None and y is not None:
            return x, y
    x = _num(p.get('x') if p.get('x') is not None else
             (p.get('lon') if p.get('lon') is not None else p.get('longitude')))
    y = _num(p.get('y') if p.get('y') is not None else
             (p.get('lat') if p.get('lat') is not None else p.get('latitude')))
    return x, y


# ---- boundary -------------------------------------------------------------

def load_boundary(sad_dir: Path):
    """source/sad_boundary.geojson -> a single shapely geometry, or None."""
    f = sad_dir / 'source' / 'sad_boundary.geojson'
    if not f.exists():
        return None
    try:
        gj = json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return None
    t = gj.get('type')
    if t == 'FeatureCollection':
        geoms = [shape(ft['geometry']) for ft in gj.get('features', [])
                 if ft.get('geometry')]
        if not geoms:
            return None
        return geoms[0] if len(geoms) == 1 else unary_union(geoms)
    if t == 'Feature':
        return shape(gj['geometry']) if gj.get('geometry') else None
    return shape(gj)


def bbox_fc(geom):
    minx, miny, maxx, maxy = geom.bounds
    ring = [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
    fc = {'type': 'FeatureCollection', 'features': [{
        'type': 'Feature', 'properties': {},
        'geometry': {'type': 'Polygon', 'coordinates': [ring]}}]}
    return (minx, miny, maxx, maxy), fc


# ---- NSI ------------------------------------------------------------------

def post_nsi(fc: dict, timeout: int = 180, retries: int = 2) -> dict:
    body = json.dumps(fc).encode('utf-8')
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                NSI_URL, data=body,
                headers={'Content-Type': 'application/json',
                         'Accept': 'application/json'},
                method='POST')
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:  # network hiccup; retry a couple times
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last


def features_of(resp):
    if isinstance(resp, dict):
        return resp.get('features', [])
    if isinstance(resp, list):
        return resp
    return []


def parse_structures(feats, boundary):
    """Clip to the boundary polygon and pull the attributes we keep."""
    kept = []
    n_no_coord = 0
    for f in feats:
        p = _props(f)
        lon, lat = _lonlat(f, p)
        if lon is None or lat is None:
            n_no_coord += 1
            continue
        if not boundary.contains(Point(lon, lat)):
            continue
        occ = p.get('occtype') or p.get('st_damcat') or ''
        ns = _num(p.get('num_story'))
        kept.append({
            'occtype': str(p.get('occtype') or ''),
            'st_damcat': str(p.get('st_damcat') or ''),
            'sqft': _num(p.get('sqft')),
            'num_story': ns,
            'est_height_m': round(ns * 3.5, 1) if ns else None,  # story-count proxy; Overture has measured height
            'found_ht_foundation_ft': _num(p.get('found_ht')),  # flood attr, not bldg height
            'bldgtype': str(p.get('bldgtype') or ''),
            'bucket': bucket_for(occ),
            'lon': lon, 'lat': lat,
        })
    return kept, n_no_coord


def summarize(structures):
    by_sqft = Counter()
    by_count = Counter()
    occ_hist = Counter()
    sqft_missing = 0
    for s in structures:
        bk = s['bucket']
        by_count[bk] += 1
        occ_hist[(s['occtype'] or s['st_damcat'] or '?')[:8]] += 1
        sq = s.get('sqft')
        if sq and sq > 0:
            by_sqft[bk] += sq
        else:
            sqft_missing += 1
    tot_sq = float(sum(by_sqft.values()))
    tot_n = int(sum(by_count.values()))
    shares_sqft = {b: round(by_sqft[b] / tot_sq, 4) if tot_sq else 0.0
                   for b in ROSSETTI_BUCKETS}
    shares_count = {b: round(by_count[b] / tot_n, 4) if tot_n else 0.0
                    for b in ROSSETTI_BUCKETS}
    return {
        'shares_sqft': shares_sqft,      # the structure-channel vector
        'shares_count': shares_count,    # cross-check
        'n_by_bucket': {b: int(by_count[b]) for b in ROSSETTI_BUCKETS},
        'total_sqft': tot_sq,
        'sqft_field_present': tot_sq > 0,
        'structures_missing_sqft': sqft_missing,
    }, dict(occ_hist), tot_n, tot_sq


def print_shares(occ_share, tot_n, tot_sq):
    print(f'  in-boundary structures: {tot_n}   total sqft: {tot_sq:,.0f}'
          f'{"  [no sqft field -> count-only]" if not occ_share["sqft_field_present"] else ""}')
    print(f'  {"bucket":<26}{"count":>8}{"cnt%":>8}{"sqft%":>9}')
    ss, sc, nb = occ_share['shares_sqft'], occ_share['shares_count'], occ_share['n_by_bucket']
    for b in ROSSETTI_BUCKETS:
        print(f'  {b:<26}{nb[b]:>8}{sc[b]:>8.1%}{ss[b]:>9.1%}')
    print(f'  {"(sport is structurally 0: HAZUS has no sport class)":<26}')


def build_record(sad_id, bbox, structures, occ_share, occ_hist, n_returned, n_no_coord):
    return {
        'method': 'nsi_structure_occupancy_v1',
        'source': 'usace_nsi',
        'nsi_url': NSI_URL,
        'pulled_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'sad_id': sad_id,
        'available': True,
        'boundary_clipped': True,
        'bbox_posted': list(bbox),
        'n_structures_returned': int(n_returned),
        'n_in_boundary': len(structures),
        'n_dropped_no_coord': int(n_no_coord),
        'sport_blind': True,         # HAZUS has no sport class; see docstring
        'res4_as_hotel': True,       # RES4 = temporary lodging -> hotel
        'occupancy_share': occ_share,
        'occtype_histogram': occ_hist,
        'structures': structures,
    }


def write_record(derived: Path, record: dict, dry: bool):
    out_dir = derived / 'structures'
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    dst = out_dir / f'nsi_structures_{stamp}.json'
    if dry:
        print(f'  [DRY-RUN] would write: {dst}')
        return dst
    out_dir.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise SystemExit(f'[FATAL] refusing to overwrite {dst}')
    dst.write_text(json.dumps(record, indent=2))
    print(f'  [WROTE] {dst}')
    return dst


# ---- one district ---------------------------------------------------------

def enrich_one(derived: Path, dry: bool, dump_keys: int = 3):
    sad_dir = derived.parent
    sad_id = sad_dir.name
    print(f'\n=== {sad_id} ===')

    if sad_id.endswith(CANADIAN_SUFFIXES):
        print('  [skip] Canadian district: NSI is US only. Writing availability stub.')
        rec = {'method': 'nsi_structure_occupancy_v1', 'source': 'usace_nsi',
               'sad_id': sad_id, 'available': False, 'reason': 'outside_us_nsi',
               'pulled_at': datetime.datetime.now().isoformat(timespec='seconds')}
        write_record(derived, rec, dry)
        return 'skip_canadian'

    boundary = load_boundary(sad_dir)
    if boundary is None or boundary.is_empty:
        print('  [skip] no source/sad_boundary.geojson (or empty). No file written.')
        return 'skip_no_boundary'

    bbox, fc = bbox_fc(boundary)
    print(f'  boundary bbox: {tuple(round(v, 5) for v in bbox)}')
    print(f'  POST {NSI_URL} ...')
    try:
        resp = post_nsi(fc)
    except Exception as e:
        print(f'  [FAIL] NSI request failed: {e}')
        return 'fail_request'

    feats = features_of(resp)
    print(f'  NSI returned {len(feats)} structures (bbox)')
    if dump_keys and feats:
        print(f'  --- property keys of first {min(dump_keys, len(feats))} (confirm field names) ---')
        for f in feats[:dump_keys]:
            p = _props(f)
            print(f'    keys: {sorted(p.keys())}')
            print(f'    occtype={p.get("occtype")!r} st_damcat={p.get("st_damcat")!r} '
                  f'sqft={p.get("sqft")!r} num_story={p.get("num_story")!r} '
                  f'found_ht={p.get("found_ht")!r} bldgtype={p.get("bldgtype")!r}')
    if not feats:
        print('  [FAIL] zero structures returned.')
        return 'fail_empty'

    structures, n_no_coord = parse_structures(feats, boundary)
    occ_share, occ_hist, tot_n, tot_sq = summarize(structures)
    print_shares(occ_share, tot_n, tot_sq)
    if tot_n == 0:
        print('  [note] no structures fell inside the boundary; writing empty record.')

    rec = build_record(sad_id, bbox, structures, occ_share, occ_hist,
                        len(feats), n_no_coord)
    write_record(derived, rec, dry)
    return 'ok'


# ---- corpus ---------------------------------------------------------------

def list_districts(data: Path):
    sads = [d for d in data.iterdir()
            if d.is_dir() and d.name[:2].isdigit()]
    sads.sort(key=lambda d: (not d.name.startswith('32_'), d.name))  # Detroit first
    return sads


def plan_corpus(data: Path):
    rows = []
    for d in list_districts(data):
        der = d / 'derived'
        if d.name.endswith(CANADIAN_SUFFIXES):
            rows.append((d.name, 'will write stub (Canadian, NSI US-only)'))
        elif load_boundary(d) is None:
            rows.append((d.name, 'SKIP no sad_boundary.geojson'))
        else:
            rows.append((d.name, 'will pull + write'))
    return rows


def run_corpus(data: Path, dry: bool):
    if dry:
        print(f'data : {data}\nmode : DRY-RUN (plan only, no network, no writes)\n')
        rows = plan_corpus(data)
        for name, what in rows:
            print(f'  {name:<46} {what}')
        n_pull = sum(1 for _, w in rows if w.startswith('will pull'))
        print(f'\n{n_pull} districts would be pulled, {len(rows) - n_pull} stub/skip.')
        print('[DRY-RUN] re-run with --apply to execute. Detroit (32) goes first.')
        return

    admin = data / '00_Admin'
    admin.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    log = admin / f'nsi_corpus_enrich_{stamp}.log'
    lines = []

    def L(msg):
        print(msg)
        lines.append(msg)

    L(f'data : {data}\nmode : APPLY')
    sads = list_districts(data)
    counts = Counter()
    for i, d in enumerate(sads, 1):
        L(f'\n[{i}/{len(sads)}] {d.name}')
        try:
            status = enrich_one(d / 'derived', dry=False, dump_keys=0)
        except SystemExit as e:
            status = 'fail_overwrite'
            L(f'  {e}')
        except Exception as e:
            status = 'fail_exception'
            L(f'  [FAIL] {e}')
        counts[status] += 1
    L('\n--- summary ---')
    for k, v in counts.most_common():
        L(f'  {k:<18} {v}')
    log.write_text('\n'.join(lines), encoding='utf-8')
    print(f'\nlog: {log}')


# ---- offline self-test ----------------------------------------------------

def selftest():
    """No network. Synthetic NSI-shaped response over a unit-square boundary,
    one feature deliberately outside, to prove mapping + clip + shares."""
    boundary = shape({'type': 'Polygon', 'coordinates':
                      [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]})
    feats = [
        {'geometry': {'type': 'Point', 'coordinates': [0.2, 0.2]},
         'properties': {'occtype': 'RES1-1SNB', 'st_damcat': 'RES', 'sqft': 2000, 'num_story': 2}},
        {'geometry': {'type': 'Point', 'coordinates': [0.3, 0.3]},
         'properties': {'occtype': 'RES3A',     'st_damcat': 'RES', 'sqft': 8000, 'num_story': 6}},
        {'geometry': {'type': 'Point', 'coordinates': [0.4, 0.4]},
         'properties': {'occtype': 'RES4',      'st_damcat': 'RES', 'sqft': 5000, 'num_story': 8}},  # hotel
        {'geometry': {'type': 'Point', 'coordinates': [0.5, 0.5]},
         'properties': {'occtype': 'COM4',      'st_damcat': 'COM', 'sqft': 4000, 'num_story': 10}},  # office
        {'geometry': {'type': 'Point', 'coordinates': [0.6, 0.6]},
         'properties': {'occtype': 'COM8',      'st_damcat': 'COM', 'sqft': 3000, 'num_story': 1}},  # retail (venue would land here)
        {'geometry': {'type': 'Point', 'coordinates': [0.7, 0.7]},
         'properties': {'occtype': 'COM10',     'st_damcat': 'COM', 'sqft': 1000, 'num_story': 1}},  # parking
        {'geometry': {'type': 'Point', 'coordinates': [0.8, 0.8]},
         'properties': {'occtype': 'GOV1',      'st_damcat': 'GOV', 'sqft': 1000, 'num_story': 1}},  # other
        {'geometry': {'type': 'Point', 'coordinates': [9.9, 9.9]},   # OUTSIDE boundary
         'properties': {'occtype': 'RES1',      'st_damcat': 'RES', 'sqft': 99999, 'num_story': 1}},
    ]
    structures, n_no_coord = parse_structures(feats, boundary)
    occ_share, occ_hist, tot_n, tot_sq = summarize(structures)

    print('SELFTEST (offline)')
    print(f'  features posted: {len(feats)}   in-boundary: {tot_n}   '
          f'(1 outside must be dropped)')
    print_shares(occ_share, tot_n, tot_sq)

    ss = occ_share['shares_sqft']
    checks = [
        ('outside feature dropped', tot_n == 7),
        ('RES4 -> hotel (not residential)', bucket_for('RES4') == 'hotel'),
        ('RES1 -> residential', bucket_for('RES1-1SNB') == 'residential'),
        ('RES3A -> residential', bucket_for('RES3A') == 'residential'),
        ('COM4 -> office', bucket_for('COM4') == 'office'),
        ('COM10 -> parking (before COM1)', bucket_for('COM10') == 'parking'),
        ('COM8 -> retail_food_entertainment', bucket_for('COM8') == 'retail_food_entertainment'),
        ('GOV1 -> institutional', bucket_for('GOV1') == 'institutional'),
        ('sport share == 0 (structurally blind)', ss['sport'] == 0.0),
        ('hotel share > 0 (RES4 routed there)', ss['hotel'] > 0.0),
        ('residential sqft = (2000+8000)/24000', abs(ss['residential'] - (10000 / 24000)) < 1e-3),
        ('shares sum to ~1 (within rounding)', abs(sum(ss.values()) - 1.0) < 2e-3),
    ]
    print('\n  checks:')
    ok = True
    for label, passed in checks:
        print(f'    [{"PASS" if passed else "FAIL"}] {label}')
        ok = ok and passed
    print(f'\n  SELFTEST {"PASSED" if ok else "FAILED"}')
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description='Corpus-wide NSI structure enrichment (step 1)')
    ap.add_argument('--derived', type=Path, default=None, help='one district derived/ (default Detroit 32)')
    ap.add_argument('--data', type=Path, default=None, help='data root: enrich every NN_* district')
    ap.add_argument('--apply', action='store_true', help='write (default is dry-run)')
    ap.add_argument('--selftest', action='store_true', help='offline mapping/clip/share check')
    a = ap.parse_args()

    if a.selftest:
        raise SystemExit(selftest())

    dry = not a.apply
    if a.data:
        run_corpus(a.data.resolve(), dry)
    else:
        derived = (a.derived or DEFAULT_DERIVED).resolve()
        print(f'mode : {"DRY-RUN (no writes)" if dry else "APPLY (new-named write)"}')
        enrich_one(derived, dry)


if __name__ == '__main__':
    main()
