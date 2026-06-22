"""
compare_manifest_bands.py

Augments the per-SAD feature vectors in compare_manifest.json with the
more-than-human (mth_) and economic (eco_) bands, so the FIELD viewer's
in-browser PCA-3D re-projects the EXPANDED vector — without re-running
Module 8 and without any dependency on Module 06.

It is stdlib-only (no pandas/numpy) to match build_compare_manifest.py's
defensive, dependency-free style. It mirrors the band logic in
module_08_bands_ext.py, inlined here so the manifest builder stays
self-contained.

HOW THE FIELD GETS THE DATA
  build_compare_manifest.py copies Module 8's feature vectors into each
  record's features.raw / features.normalized. compare.js fetches the
  manifest and runs its own pca3() + nearest-neighbour links over those
  vectors. So appending bands to features.{raw,normalized} HERE is all the
  field needs — the projection and links recompute in the browser.

BAND-AWARENESS
  --bands {human,ecology,economic,combined}  (default combined)
    human    -> only Module 8's existing columns (reproduces the CURRENT field)
    combined -> Module 8 columns + mth_ + eco_
    ecology  -> only mth_   |   economic -> only eco_
  Regenerate the manifest with the band set you want; that is how you flip
  the field between human-centric and more-than-human projections.

NORMALIZATION
  Module 8's normalized columns are z-scored across the corpus. The new bands
  are z-scored here the same way (per-feature mean/std across all SADs that
  have the feature), so the expanded normalized vector is internally
  consistent and no single large-magnitude raw feature dominates the PCA.
  Missing values (a SAD not yet enriched) -> 0 after z-scoring, exactly the
  M8 convention, so one un-enriched SAD never voids a band.

──────────────────────────────────────────────────────────────────────────
PATCH build_compare_manifest.py  (three small edits)
──────────────────────────────────────────────────────────────────────────
1) Top, after the existing imports:
       import compare_manifest_bands as cmb

2) In main(), add the flag:
       ap.add_argument('--bands', default='combined',
                       choices=list(cmb.BAND_SETS),
                       help='Feature bands for the field vector (default combined)')
   and pass it down:
       build(args.data_dir, out, bands=args.bands)

3) In build(...), change the signature to accept bands and, right AFTER
   `records = [build_sad_record(...) for d in sad_dirs]`, insert:
       records = cmb.augment_records(records, embedding, data_dir, bands)
   (def build(data_dir, out_path, bands='combined'):)

That is the whole integration. Nothing else changes; if Module 24/25 haven't
been run for any SAD, augment_records is a no-op and the field is unchanged.

HONEST CAVEAT
  This assumes compare.js feeds record.features.normalized (or .raw) into its
  pca3(). The manifest plainly carries those as the morphometric vector, so
  that is almost certainly the wiring — but I have not seen the line in
  compare.js that assembles `vectors`. If it instead builds the vector from a
  hardcoded list of metric fields, send me that line and I'll match it. Both
  raw and normalized are written here, so either choice is covered.
"""
from __future__ import annotations
import json
import statistics
from pathlib import Path


# ─── Band registry (mirrors module_08_bands_ext) ─────────────────────────────

BAND_PREFIXES = {
    'morphology':   ('morph_', 'wave_', 'iex_', 'nolli_'),
    'program':      ('program_', 'prog_', 'cat_'),
    'anchor':       ('anchor_',),
    'demographics': ('demo_', 'census_', 'acs_'),
    'ecology':      ('mth_',),
    'economic':     ('eco_',),
}
BAND_SETS = {
    'human':    ('morphology', 'program', 'anchor', 'demographics'),
    'ecology':  ('ecology',),
    'economic': ('economic',),
    'combined': ('morphology', 'program', 'anchor', 'demographics',
                 'ecology', 'economic'),
}


def resolve_bands(name: str):
    if name not in BAND_SETS:
        raise SystemExit(f"--bands must be one of {list(BAND_SETS)}")
    return BAND_SETS[name]


def _belongs(feature_name: str, band: str) -> bool:
    return any(feature_name.startswith(p) for p in BAND_PREFIXES.get(band, ()))


def filter_bands(features: dict, selected_bands) -> dict:
    """Keep features whose prefix maps to a selected band. Un-prefixed legacy
    Module 8 columns (e.g. area_m2) count as 'human'."""
    selected = set(selected_bands)
    human_on = bool({'morphology', 'program', 'anchor', 'demographics'} & selected)
    keep = {}
    for k, v in features.items():
        recognized = any(_belongs(k, b) for b in BAND_PREFIXES)
        if not recognized:
            if human_on:
                keep[k] = v
            continue
        if any(_belongs(k, b) for b in selected):
            keep[k] = v
    return keep


# ─── Band loaders (stdlib; numeric features only) ─────────────────────────────

def _numeric_features(summary_path: Path) -> dict:
    try:
        if not summary_path.exists():
            return {}
        blob = json.loads(summary_path.read_text(encoding='utf-8'))
    except Exception:                              # noqa: BLE001
        return {}
    feats = blob.get('features', {}) if isinstance(blob, dict) else {}
    return {k: v for k, v in feats.items() if isinstance(v, (int, float))}


def load_ecology_features(derived: Path) -> dict:
    return _numeric_features(derived / 'more_than_human' / 'mth_summary.json')


def load_economic_features(derived: Path) -> dict:
    return _numeric_features(derived / 'economic' / 'economic_prepost_summary.json')


# ─── Corpus z-score (stdlib) ─────────────────────────────────────────────────

def _zscore_corpus(raw_by_sad: dict) -> tuple[dict, list]:
    """raw_by_sad: {sad_id: {feat: value}}. Returns (norm_by_sad, feature_names).
    Per-feature z-score across SADs that have it; missing -> 0."""
    feats = sorted({f for d in raw_by_sad.values() for f in d})
    stats = {}
    for f in feats:
        vals = [d[f] for d in raw_by_sad.values()
                if isinstance(d.get(f), (int, float))]
        if len(vals) >= 2:
            mu = statistics.fmean(vals)
            sd = statistics.pstdev(vals)
            stats[f] = (mu, sd if sd > 1e-12 else 1.0)
    norm_by_sad = {}
    for sad, d in raw_by_sad.items():
        row = {}
        for f in feats:
            if f in stats and isinstance(d.get(f), (int, float)):
                mu, sd = stats[f]
                row[f] = round((d[f] - mu) / sd, 4)
            else:
                row[f] = 0.0                       # missing -> mean (0 after z)
        norm_by_sad[sad] = row
    return norm_by_sad, feats


# ─── Public entry point ──────────────────────────────────────────────────────

def augment_records(records: list[dict], embedding: dict, data_dir: Path,
                    bands: str = 'combined') -> list[dict]:
    """Append mth_/eco_ bands to each record's features.{raw,normalized}, then
    filter every vector (existing + new) to the selected band set. Updates the
    embedding block's feature_names/count for consistency with other panels."""
    selected = resolve_bands(bands)
    data_dir = Path(data_dir)

    # 1) gather new-band raw features per SAD
    new_raw = {}
    n_enriched = 0
    for r in records:
        sad_id = r['sad_id']
        derived = data_dir / sad_id / 'derived'
        block = {**load_ecology_features(derived),
                 **load_economic_features(derived)}
        new_raw[sad_id] = block
        if block:
            n_enriched += 1

    # 2) z-score the new bands across the corpus
    new_norm, new_feats = _zscore_corpus(new_raw)

    # 3) merge into each record, then band-filter both vectors
    for r in records:
        sad_id = r['sad_id']
        feats = r.setdefault('features', {})
        base_raw = dict(feats.get('raw') or {})
        base_norm = dict(feats.get('normalized') or {})
        merged_raw = {**base_raw, **new_raw.get(sad_id, {})}
        merged_norm = {**base_norm, **new_norm.get(sad_id, {})}
        feats['raw'] = filter_bands(merged_raw, selected)
        feats['normalized'] = filter_bands(merged_norm, selected)
        # mark which bands this vector carries (handy for the UI / debugging)
        r.setdefault('coverage', {})['mth'] = bool(load_ecology_features(
            data_dir / sad_id / 'derived'))
        r['coverage']['eco'] = bool(load_economic_features(
            data_dir / sad_id / 'derived'))

    # 4) keep the embedding block's feature list in step (other panels read it)
    existing_names = list(embedding.get('feature_names') or [])
    all_names = {n: 0 for n in existing_names + new_feats}
    kept = sorted(filter_bands(all_names, selected).keys())
    embedding['feature_names'] = kept
    embedding['feature_count'] = len(kept)
    embedding['bands'] = list(selected)
    embedding['bands_selection'] = bands

    print(f"  bands: '{bands}'  ->  {len(kept)} features in field vector "
          f"({n_enriched}/{len(records)} SADs carry mth_/eco_ data)")
    return records
