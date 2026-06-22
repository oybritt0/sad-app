"""
build_compare_manifest.py

Aggregates everything the SAD comparison tool needs into ONE file:
    data/_compare_ui/compare_manifest.json

WHY
  The comparison app (separate from the viewer) needs a single, front-end-
  friendly index: one record per SAD carrying its identity, census context
  (SAD blocks + metro + positioning), program mix, amenity / transit /
  walkshed / proximity / centrality metrics, its morphometric feature vector
  and PCA coordinates, and paths to the artifacts (place card, comparison
  charts) the dashboard will display. This mirrors how the viewer is driven by
  data/_ui/manifest.json — the front-end stays dumb, the pipeline is the
  source of truth.

DESIGN
  - DEFENSIVE: every artifact is optional. A SAD missing M12 or a metro
    summary still produces a record; the gap is recorded in `coverage` rather
    than raising. This tolerates the real, uneven state of the corpus (some
    SADs lack M4, the Canadian ones lack US census entirely, etc.).
  - PATH-TOLERANT: artifacts are looked up across candidate locations
    (e.g. program_summary.json lives at derived/ OR derived/per_sad/<sad>/).
  - SELF-REPORTING: prints a coverage table so this doubles as a "what's
    been computed for which SAD" audit. No separate check script needed.
  - ADDITIVE: reads only; touches nothing the viewer or pipeline depend on.

USAGE
  python build_compare_manifest.py --data-dir ..\\data
  # optional: --out ..\\data\\_compare_ui\\compare_manifest.json

OUTPUT
  data/_compare_ui/compare_manifest.json
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

# Directories under data/ that are NOT SADs.
NON_SAD_DIRS = {'_ui', '_comparisons', '_compare_ui', 'source', 'derived'}

# Canadian province/territory suffixes in the SAD folder names — these lack
# US Census coverage (M4/M4b can't run on them via pygris).
CANADIAN_SUFFIXES = ('-ON', '-QC', '-BC', '-AB', '-MB', '-SK', '-NS', '-NB',
                     '-NL', '-PE', '-NT', '-YT', '-NU')

# Metric groups surfaced to the comparison UI. Each entry names the source
# artifact and the fields pulled from it. Kept declarative so adding a metric
# later is a one-line change.
# (group, label, artifact_key, [field paths within that artifact])
METRICS_CATALOG = [
    ('census',    'Median household income', 'census',   'median_household_income_pop_weighted'),
    ('census',    'Median age',              'census',   'median_age_pop_weighted'),
    ('census',    'Renter-occupied %',       'census',   'pct_renter_occupied'),
    ('census',    "Bachelor's+ %",           'census',   'pct_bachelors_or_higher'),
    ('census',    'Estimated population',    'census',   'estimated_population'),
    ('program',   'Total POIs',              'program',  'total'),
    ('amenity',   'POIs in SAD',             'amenity',  'total_points_in_sad'),
    ('transit',   'Transit stations',        'transit',  'total_stations'),
    ('walkshed',  '10-min walkshed (acres)', 'walkshed', 'walkshed_10min_acres'),
    ('centrality','Street nodes',            'centrality','nodes'),
    ('centrality','Street edges',            'centrality','edges'),
]


# ─── small helpers ───────────────────────────────────────────────────────────

def safe_json(path: Path):
    """Load JSON or return None — never raises on missing/garbled files."""
    try:
        if path and path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"    ! could not read {path.name}: {e}")
    return None


def first_existing(*candidates: Path):
    """Return the first candidate path that exists, else None."""
    for c in candidates:
        if c and c.exists():
            return c
    return None


def rel_to(data_dir: Path, path: Path) -> str | None:
    """Path relative to the data/ root, forward-slashed for the web."""
    if path is None:
        return None
    try:
        return path.relative_to(data_dir).as_posix()
    except ValueError:
        return path.as_posix()


def read_csv_indexed(path: Path) -> dict[str, dict[str, str]]:
    """Read a CSV whose first column is the row index -> {idx: {col: val}}."""
    out = {}
    if not path or not path.exists():
        return out
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return out
        cols = header[1:]
        for row in reader:
            if not row:
                continue
            idx = row[0]
            out[idx] = {c: v for c, v in zip(cols, row[1:])}
    return out


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─── corpus-level: the M8 embedding dir ──────────────────────────────────────

def find_latest_embedding_dir(data_dir: Path) -> Path | None:
    """The dir of the most recent vibe_embedding_summary.json anywhere under
    data/_comparisons/ — robust to naming (embedding_<ts> from standalone M8,
    district_embedding from the batch pipeline, etc.)."""
    comp = data_dir / '_comparisons'
    if not comp.exists():
        return None
    summaries = list(comp.rglob('vibe_embedding_summary.json'))
    if not summaries:
        return None
    return max(summaries, key=lambda p: p.stat().st_mtime).parent


def load_embedding(emb_dir: Path) -> dict:
    """
    Pull the corpus morphometric embedding: feature names, per-SAD raw and
    normalized vectors, PCA coordinates, explained variance, distance matrix.
    All keyed by sad_id. Everything optional.
    """
    if emb_dir is None:
        return {}
    summary = safe_json(emb_dir / 'vibe_embedding_summary.json') or {}

    raw = read_csv_indexed(emb_dir / 'feature_matrix.csv')
    norm = read_csv_indexed(emb_dir / 'feature_matrix_normalized.csv')
    pca = read_csv_indexed(emb_dir / 'pca_coords.csv')
    dist = read_csv_indexed(emb_dir / 'distance_matrix.csv')

    # Feature matrices are numeric; coerce so the UI gets numbers, not strings.
    def numify(table):
        return {sad: {k: (to_float(v) if to_float(v) is not None else v)
                      for k, v in cols.items()}
                for sad, cols in table.items()}
    raw = numify(raw)
    norm = numify(norm)

    # PCA coords -> {sad_id: [pc1, pc2, (pc3)]}
    projections_pca = {}
    for sad_id, cols in pca.items():
        vals = [to_float(v) for v in cols.values()]
        vals = [v for v in vals if v is not None]
        if vals:
            projections_pca[sad_id] = vals[:3]

    # distance matrix -> {sad_id: {other_id: dist}}
    distance = {}
    for a, cols in dist.items():
        distance[a] = {b: to_float(v) for b, v in cols.items()}

    return {
        'source_dir': emb_dir.name,
        'feature_names': summary.get('feature_names', []),
        'feature_count': summary.get('feature_count'),
        'include_demographics': summary.get('include_demographics'),
        'pca_explained_variance': summary.get('pca_explained_variance', []),
        'top_distinguishing_features': summary.get('top_distinguishing_features'),
        'projections': {'pca': projections_pca},
        'distance_matrix': distance,
        '_raw_by_sad': raw,
        '_norm_by_sad': norm,
    }


def find_comparison_summary(data_dir: Path):
    """M7's corpus comparison_summary.json + its chart dir, if present."""
    comp = data_dir / '_comparisons'
    if not comp.exists():
        return None, None
    hits = sorted(comp.rglob('comparison_summary.json'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        return None, None
    return safe_json(hits[0]), hits[0].parent


def find_place_card(data_dir: Path, sad_id: str) -> Path | None:
    """M10 writes <out>/place_cards/<sad_id>_place_card.png somewhere in data/."""
    hits = sorted(data_dir.rglob(f'{sad_id}_place_card.png'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


# ─── per-SAD record ──────────────────────────────────────────────────────────

def _coords_iter(geom):
    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'Point':
        yield c
    elif t in ('LineString', 'MultiPoint'):
        yield from c
    elif t in ('Polygon', 'MultiLineString'):
        for ring in c:
            yield from ring
    elif t == 'MultiPolygon':
        for poly in c:
            for ring in poly:
                yield from ring
    elif t == 'GeometryCollection':
        for g in geom.get('geometries', []):
            yield from _coords_iter(g)


def centroid_and_bbox(boundary_path: Path):
    """Bbox center + bbox [minlon,minlat,maxlon,maxlat] from a boundary GeoJSON."""
    if not boundary_path or not boundary_path.exists():
        return None, None
    try:
        gj = json.loads(boundary_path.read_text(encoding='utf-8'))
    except Exception:
        return None, None
    feats = gj.get('features') if gj.get('type') == 'FeatureCollection' else [gj]
    xs, ys = [], []
    for f in feats or []:
        geom = f.get('geometry', f)
        for x, y in _coords_iter(geom):
            xs.append(x); ys.append(y)
    if not xs:
        return None, None
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2], bbox


def census_basis_for(sad_id: str, has_census: bool) -> str:
    if sad_id.endswith(CANADIAN_SUFFIXES):
        return 'needs-statcan'
    return 'us-census' if has_census else 'unknown'


_UNSPEC_TYP = {'', 'unspecified', 'unknown', 'none', 'n/a', 'na', 'tbd', 'null'}


def _real_typ(*vals):
    for v in vals:
        if v is not None and str(v).strip().lower() not in _UNSPEC_TYP:
            return str(v).strip()
    return None


def _display_name(sad_id: str, raw_name) -> str:
    """A human name for the manifest. Drawn districts save a generic
    'Drawn district' name and carry their location in the 3rd id segment, so
    derive a real name (e.g. '43_Drawn-district_Ann-Arbor' -> 'Ann Arbor')."""
    parts = sad_id.split('_')
    raw = str(raw_name or '').strip()
    drawn = ('drawn' in sad_id.lower()) or raw.lower().replace('-', ' ') == 'drawn district'
    if drawn:
        loc = (parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else '')).replace('-', ' ').strip()
        if loc:
            return loc
    if raw and raw != sad_id:
        return raw
    return (parts[1] if len(parts) > 1 else sad_id).replace('-', ' ')


def _load_parcels(derived: Path):
    """Per-SAD Regrid parcel summary (module_23). Returns None if absent."""
    p = derived / 'parcels' / 'parcels_summary.json'
    if not p.exists(): return None
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return None

def build_sad_record(data_dir: Path, sad_dir: Path, embedding: dict, shared_typ: dict | None = None) -> dict:
    sad_id = sad_dir.name
    derived = sad_dir / 'derived'
    source = sad_dir / 'source'

    manifest = safe_json(derived / 'manifest.json') or {}
    typology_json = safe_json(derived / 'typology.json') or {}
    # canonical fallback (keyed by sad_id) written by setup_typologies.py
    shared_t = (shared_typ or {}).get(sad_id, {}) if shared_typ else {}
    census = safe_json(derived / 'census_summary.json')
    metro = safe_json(derived / 'census_metro_summary.json')
    municipal = safe_json(derived / 'census_municipal_summary.json')
    _centroid, _bbox = centroid_and_bbox(source / 'sad_boundary.geojson')

    program = safe_json(first_existing(
        derived / 'program_summary.json',
        derived / 'per_sad' / sad_id / 'program_summary.json',
    ))
    amenity = safe_json(first_existing(
        derived / 'amenity_density' / 'amenity_density_summary.json',
    ))
    transit = safe_json(first_existing(
        derived / 'transit' / 'transit_summary.json',
    ))
    walkshed = safe_json(first_existing(
        derived / 'walkshed' / 'walkshed_summary.json',
    ))
    proximity = safe_json(first_existing(
        derived / 'feature_program_proximity'
        / 'feature_program_proximity_summary.json',
    ))
    centrality = safe_json(first_existing(
        derived / 'street_centrality_summary.json',
    ))

    place_card = find_place_card(data_dir, sad_id)

    # Flatten the one walkshed budget the catalog references (10-min).
    walkshed_flat = {}
    if walkshed and isinstance(walkshed.get('walksheds'), list):
        for w in walkshed['walksheds']:
            if w.get('minutes') == 10:
                walkshed_flat['walkshed_10min_acres'] = w.get('area_acres')
        walkshed_flat['budgets'] = walkshed['walksheds']

    # Embedding vectors for this SAD (may be absent if M8 skipped it).
    raw_vec = embedding.get('_raw_by_sad', {}).get(sad_id)
    norm_vec = embedding.get('_norm_by_sad', {}).get(sad_id)
    pca_xy = embedding.get('projections', {}).get('pca', {}).get(sad_id)

    region = None
    if metro and isinstance(metro.get('metro'), dict):
        region = metro['metro'].get('cbsa_name')

    has_census = census is not None

    record = {
        'sad_id': sad_id,
        'sad_name': _display_name(sad_id, manifest.get('sad_name')),
        'typology': (_real_typ(typology_json.get('primary_typology'),
                               shared_t.get('primary_typology'),
                               manifest.get('typology'))
                     or 'unspecified'),
        'secondary_typology': _real_typ(typology_json.get('secondary_typology'),
                                        shared_t.get('secondary_typology')),
        'parcels': _load_parcels(derived),
        'anchor_venue': manifest.get('anchor_venue'),
        'region': region,
        'census_basis': census_basis_for(sad_id, has_census),
        'census': {
            'sad': census,
            'municipal': (municipal or {}).get('scope_summaries', {}).get('municipal')
                         if municipal else None,
            'municipality': (municipal or {}).get('municipality') if municipal else None,
            'metro': (metro or {}).get('scope_summaries', {}).get('metro')
                     if metro else None,
            'sad_vs_metro': (metro or {}).get('sad_vs_metro') if metro else None,
        },
        'centroid': _centroid,
        'bbox': _bbox,
        'created': (source / 'extent.json').exists(),
        'program': program,
        'amenity': {
            'total_points_in_sad': (amenity or {}).get('total_points_in_sad'),
            'category_counts': (amenity or {}).get('category_counts'),
            'context_layers': (amenity or {}).get('context_layers'),
        } if amenity else None,
        'transit': {
            'total_stations': (transit or {}).get('total_stations'),
            'tier_counts': (transit or {}).get('tier_counts'),
            'kind_counts': (transit or {}).get('kind_counts'),
        } if transit else None,
        'walkshed': walkshed_flat or None,
        'proximity': proximity,
        'centrality': centrality,
        'features': {
            'raw': raw_vec,
            'normalized': norm_vec,
            'pca': pca_xy,
        },
        'artifacts': {
            'place_card_png': rel_to(data_dir, place_card),
            'sad_boundary': rel_to(data_dir, first_existing(
                source / 'sad_boundary.geojson')),
            'census_geojson': rel_to(data_dir, first_existing(
                derived / 'census_blockgroups.geojson')),
        },
        'coverage': {
            'manifest': bool(manifest),
            'census_sad': census is not None,
            'census_metro': metro is not None,
            'program': program is not None,
            'amenity': amenity is not None,
            'transit': transit is not None,
            'walkshed': bool(walkshed),
            'proximity': proximity is not None,
            'centrality': centrality is not None,
            'embedding': raw_vec is not None,
            'pca': pca_xy is not None,
            'place_card': place_card is not None,
        },
    }
    return record


# ─── orchestration ───────────────────────────────────────────────────────────

def list_sads(data_dir: Path) -> list[Path]:
    out = []
    for d in sorted(data_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name in NON_SAD_DIRS or d.name.startswith('_'):
            continue
        if (d / 'source').exists() or (d / 'derived').exists():
            out.append(d)
    return out


def build(data_dir: Path, out_path: Path) -> Path:
    data_dir = data_dir.resolve()
    print(f"Aggregating compare manifest from {data_dir}")

    emb_dir = find_latest_embedding_dir(data_dir)
    print(f"  embedding dir: {emb_dir.name if emb_dir else '(none found)'}")
    embedding = load_embedding(emb_dir)

    shared_typ = safe_json(data_dir / '_shared' / 'typologies.json') or {}
    print(f"  canonical typologies: {len(shared_typ)} entries"
          + ("" if shared_typ else "  (none — run setup_typologies.py if typology is blank)"))

    comparison_summary, comparison_dir = find_comparison_summary(data_dir)
    if comparison_dir:
        print(f"  M7 comparison dir: {comparison_dir.name}")

    sad_dirs = list_sads(data_dir)
    print(f"  SADs discovered: {len(sad_dirs)}")

    records = [build_sad_record(data_dir, d, embedding, shared_typ) for d in sad_dirs]

    # Strip the private helper keys before publishing.
    pub_embedding = {k: v for k, v in embedding.items()
                     if not k.startswith('_')}

    manifest = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'data_dir': str(data_dir),
        'n_sads': len(records),
        'metrics_catalog': [
            {'group': g, 'label': lbl, 'source': src, 'field': fld}
            for (g, lbl, src, fld) in METRICS_CATALOG
        ],
        'embedding': pub_embedding,
        'corpus_comparison': {
            'summary': comparison_summary,
            'chart_dir': rel_to(data_dir, comparison_dir),
        },
        'sads': records,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, default=str))

    print_coverage(records)
    print(f"\n  wrote {out_path}")
    return out_path


def print_coverage(records: list[dict]):
    """A compact table: rows = SADs, columns = artifact presence."""
    cols = ['census_sad', 'census_metro', 'program', 'amenity', 'transit',
            'walkshed', 'proximity', 'centrality', 'embedding', 'place_card']
    short = ['cen', 'metro', 'prog', 'amen', 'trans',
             'walk', 'prox', 'cent', 'embed', 'card']
    print("\n  Coverage (+ present / . missing):")
    print("    " + " ".join(f"{s:>5}" for s in short) + "   SAD")
    totals = {c: 0 for c in cols}
    for r in records:
        cov = r['coverage']
        cells = []
        for c in cols:
            ok = bool(cov.get(c))
            totals[c] += 1 if ok else 0
            cells.append('+' if ok else '.')
        print("    " + " ".join(f"{x:>5}" for x in cells) + f"   {r['sad_id']}")
    n = len(records)
    print("    " + " ".join(f"{totals[c]:>5}" for c in cols)
          + f"   (of {n} SADs)")


def main():
    ap = argparse.ArgumentParser(
        description="Aggregate per-SAD + corpus outputs into compare_manifest.json")
    ap.add_argument('--data-dir', type=Path, required=True,
                    help='The data/ root (contains the numbered SAD folders)')
    ap.add_argument('--out', type=Path, default=None,
                    help='Output path (default: <data-dir>/_compare_ui/compare_manifest.json)')
    args = ap.parse_args()
    out = args.out or (args.data_dir / '_compare_ui' / 'compare_manifest.json')
    build(args.data_dir, out)


if __name__ == '__main__':
    main()
