"""
diag_residential_audit.py

READ-ONLY diagnostic. Does not edit any pipeline file or output. It answers
one question: how much residential signal is actually present in the
Overture *places* (POI) layer, and how much of what IS there is being
misfiled away from the 'residential' bucket?

It reads one or more rod_places.geojson files (Module 3 output, which
carries primary_category / top_level / rossetti_category / zone) and reports:

  1. primary_category histogram          — the raw vocabulary of the layer
  2. Rossetti rollup counts              — what the pipeline produced
  3. residential hit-rate                — overall and interior-only
  4. residential-looking-but-misfiled    — POIs that read as housing by
                                           primary_category yet landed in
                                           office/other (the office-miscount)
  5. dedup / operational sanity          — quick flag-presence check

It also runs on a RAW ROD export (no rossetti_category column): in that
case it computes the rollup itself with a faithful mirror of Module 3's
logic, so the numbers match what the pipeline would produce.

USAGE
    python diag_residential_audit.py path\\to\\rod_places.geojson
    python diag_residential_audit.py file1.geojson file2.geojson ...
    python diag_residential_audit.py --scan ..\\data\\source\\per_sad
    python diag_residential_audit.py --scan ..\\data --top 30
    python diag_residential_audit.py --json report.json --scan ..\\data

Standard library only (json, argparse, re, pathlib, collections). No
geopandas / fiona needed — GeoJSON is read as plain JSON.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# MIRROR OF module_03_rod_program_extractor.py (rollup logic), used ONLY when a
# file has no rossetti_category column (i.e. a raw export). When the column is
# present we trust it verbatim. Keep these in sync with Module 3 if it changes.
# ─────────────────────────────────────────────────────────────────────────────
PARKING_SUBCATS = {'parking', 'parking_garage', 'parking_lot'}
RESIDENTIAL_SUBCATS = {
    'condominium', 'apartment', 'housing_authority',
    'residential_building', 'service_apartment',
}
HOTEL_SUBCATS = {
    'hotel', 'motel', 'inn', 'lodge', 'resort', 'lodging', 'bed_and_breakfast',
}
SPORT_SUBCATS = {
    'stadium_arena', 'baseball_stadium', 'hockey_arena', 'basketball_stadium',
    'football_stadium', 'sports_complex', 'baseball_field', 'soccer_field',
    'race_track', 'golf_course', 'tennis_court', 'swimming_pool', 'gym',
    'fitness_center', 'sport_or_fitness_facility', 'sport_or_recreation_club',
    'yoga_studio', 'fitness_trainer', 'martial_arts_club', 'dance_studio',
    'boot_camp', 'bowling_alley', 'ice_skating_rink', 'skate_park',
    'rock_climbing_spot', 'mountain_bike_trail', 'professional_sport_team',
    'amateur_sport_league', 'sports_clubs_and_leagues',
}
OPEN_SPACE_SUBCATS = {
    'park', 'dog_park', 'public_plaza', 'public_fountain',
    'community_center', 'public_space',
}
TOP_LEVEL_TO_ROSSETTI = {
    'food_and_drink': 'retail_food_entertainment',
    'shopping': 'retail_food_entertainment',
    'arts_and_entertainment': 'retail_food_entertainment',
    'lodging': 'hotel',
    'services_and_business': 'office',
    'health_care': 'office',
    'sports_and_recreation': 'sport',
    'community_and_government': 'other',
    'travel_and_transportation': 'other',
    'cultural_and_historic': 'other',
    'education': 'other',
    'lifestyle_services': 'other',
    'geographic_entities': 'other',
}


def rollup_category(primary_category, top_level) -> str:
    pc = (primary_category or '').lower().strip()
    if pc in PARKING_SUBCATS:
        return 'parking'
    if pc in RESIDENTIAL_SUBCATS:
        return 'residential'
    if pc in HOTEL_SUBCATS:
        return 'hotel'
    if pc in SPORT_SUBCATS:
        return 'sport'
    if pc in OPEN_SPACE_SUBCATS:
        return 'open_space'
    return TOP_LEVEL_TO_ROSSETTI.get(top_level or '', 'other')


def _top_level_from_hierarchy(hierarchy):
    if hierarchy is None:
        return None
    if isinstance(hierarchy, str):
        return hierarchy
    try:
        if hasattr(hierarchy, '__len__') and len(hierarchy) > 0:
            return str(hierarchy[0])
    except (TypeError, ValueError):
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# "Looks residential" heuristic. Independent of Module 3's recognized set —
# the whole point is to surface housing-ish primaries the pipeline DOESN'T
# recognize. Transparent on purpose: the report prints every primary it
# flagged and how it matched, so the match list can be eyeballed and trusted
# (or corrected).
# ─────────────────────────────────────────────────────────────────────────────

# Substrings that look residential but are NOT — checked first, hard veto.
SUBSTR_EXCLUDE = (
    'warehous', 'courthouse', 'clubhouse', 'house_of', 'steakhouse',
    'alehouse', 'lighthouse', 'powerhouse', 'guesthouse', 'bathhouse',
    'roadhouse', 'smokehouse', 'farmhouse', 'funeral', 'real_estate',
    'household', 'opera_house',
)
# Single tokens (within snake_case) that signal housing.
RES_TOKENS = {
    'apartment', 'apartments', 'condominium', 'condominiums', 'condo',
    'condos', 'dormitory', 'dormitories', 'dorm', 'tenement', 'tenements',
}
# Multi-word compounds (substring match) that signal housing.
RES_COMPOUNDS = (
    'student_housing', 'assisted_living', 'senior_living', 'independent_living',
    'retirement_community', 'retirement_home', 'nursing_home', 'care_home',
    'mobile_home', 'manufactured_home', 'co_living', 'coliving',
    'multi_family', 'multifamily', 'residence_hall', 'public_housing',
    'affordable_housing', 'housing_development', 'housing_authority',
    'housing_cooperative', 'service_apartment', 'serviced_apartment',
    'townhouse', 'townhome', 'gated_community',
)
# Safe broad substrings (after excludes have vetoed the look-alikes).
RES_SUBSTR_OK = ('residential', 'housing', 'dwelling')


def looks_residential(primary):
    """Return (bool, how) where `how` names the matching rule, for audit."""
    p = (primary or '').lower().strip()
    if not p:
        return False, ''
    if any(x in p for x in SUBSTR_EXCLUDE):
        return False, ''
    toks = set(re.split(r'[^a-z0-9]+', p))
    if toks & RES_TOKENS:
        return True, 'token'
    for c in RES_COMPOUNDS:
        if c in p:
            return True, 'compound'
    for s in RES_SUBSTR_OK:
        if s in p:
            return True, 'substr'
    return False, ''


# ─────────────────────────────────────────────────────────────────────────────
# IO
# ─────────────────────────────────────────────────────────────────────────────

def load_features(path: Path):
    """Yield property dicts from a GeoJSON FeatureCollection."""
    with path.open('r', encoding='utf-8') as fh:
        data = json.load(fh)
    feats = data.get('features', []) if isinstance(data, dict) else []
    for f in feats:
        if isinstance(f, dict):
            props = f.get('properties') or {}
            if isinstance(props, dict):
                yield props


def truthy(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        try:
            return bool(int(v))
        except (ValueError, OverflowError):
            return False
    return str(v).strip().lower() in ('true', 't', '1', 'yes', 'y')


# ─────────────────────────────────────────────────────────────────────────────
# Per-file analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze(path: Path) -> dict:
    rows = list(load_features(path))
    n = len(rows)

    primary_hist = Counter()
    rossetti_hist = Counter()
    rossetti_interior = Counter()
    zone_present = False
    n_interior = 0

    # residential-looking but not classified residential -> where did it go?
    misfiled_by_bucket = defaultdict(Counter)   # bucket -> {primary: count}
    res_like_total = Counter()                  # primary -> count (all res-like)
    res_like_caught = Counter()                 # primary -> count caught as residential
    match_how = {}                              # primary -> how it matched

    # sanity flags
    has_dupe_flag = False
    has_keep_flag = False
    has_operational = False
    n_op_true = 0
    n_dupe_true = 0

    for r in rows:
        primary = r.get('primary_category')
        primary_hist[str(primary)] += 1

        # rossetti_category: trust the file; else compute from top_level /
        # taxonomy_hierarchy with the Module-3 mirror.
        ross = r.get('rossetti_category')
        if not ross:
            tl = r.get('top_level')
            if tl is None:
                tl = _top_level_from_hierarchy(r.get('taxonomy_hierarchy'))
            ross = rollup_category(primary, tl)
        ross = str(ross)
        rossetti_hist[ross] += 1

        zone = r.get('zone')
        if zone is not None and zone != 'unknown':
            zone_present = True
            if zone == 'interior':
                n_interior += 1
                rossetti_interior[ross] += 1

        is_res_like, how = looks_residential(primary)
        if is_res_like:
            key = str(primary)
            res_like_total[key] += 1
            match_how[key] = how
            if ross == 'residential':
                res_like_caught[key] += 1
            else:
                misfiled_by_bucket[ross][key] += 1

        # sanity
        if 'dedupe_is_duplicate' in r:
            has_dupe_flag = True
            if truthy(r.get('dedupe_is_duplicate')):
                n_dupe_true += 1
        if 'dedupe_keep' in r:
            has_keep_flag = True
        if 'is_operational' in r:
            has_operational = True
            if truthy(r.get('is_operational')):
                n_op_true += 1

    n_res = rossetti_hist.get('residential', 0)
    n_res_interior = rossetti_interior.get('residential', 0)
    n_misfiled = sum(sum(c.values()) for c in misfiled_by_bucket.values())

    return {
        'path': str(path),
        'sad': path.parent.name,
        'n': n,
        'n_interior': n_interior,
        'zone_present': zone_present,
        'primary_hist': primary_hist,
        'rossetti_hist': rossetti_hist,
        'rossetti_interior': rossetti_interior,
        'n_res': n_res,
        'n_res_interior': n_res_interior,
        'res_like_total': res_like_total,
        'res_like_caught': res_like_caught,
        'misfiled_by_bucket': misfiled_by_bucket,
        'match_how': match_how,
        'n_misfiled': n_misfiled,
        'sanity': {
            'has_dupe_flag': has_dupe_flag,
            'has_keep_flag': has_keep_flag,
            'has_operational': has_operational,
            'n_op_true': n_op_true,
            'n_dupe_true': n_dupe_true,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Text report
# ─────────────────────────────────────────────────────────────────────────────

def bar(n, total, width=30):
    if total <= 0:
        return ''
    return '#' * int(round(width * n / total))


def pct(n, total):
    return (100.0 * n / total) if total else 0.0


def print_report(a: dict, top: int):
    n = a['n']
    print('=' * 72)
    print(f"FILE   {a['path']}")
    print(f"SAD    {a['sad']}")
    print('-' * 72)
    if n == 0:
        print("  (no features)")
        return

    # totals
    if a['zone_present']:
        print(f"  {n} POIs total   |   {a['n_interior']} interior, "
              f"{n - a['n_interior']} exterior")
    else:
        print(f"  {n} POIs total   |   no zone tagging in file")

    # sanity
    s = a['sanity']
    sane = []
    if s['has_operational']:
        sane.append(f"is_operational present: {s['n_op_true']}/{n} true"
                    + ("  (UNVERIFIED export — all-false signature)"
                       if s['n_op_true'] == 0 else ""))
    else:
        sane.append("is_operational: column absent")
    if s['has_dupe_flag']:
        sane.append(f"dedupe_is_duplicate present: {s['n_dupe_true']} flagged")
    elif s['has_keep_flag']:
        sane.append("dedupe_keep present (no explicit duplicate flag)")
    else:
        sane.append("no dedup flags present (already deduped upstream?)")
    print("  sanity: " + " | ".join(sane))

    # primary_category histogram
    print()
    items = a['primary_hist'].most_common()
    shown = items if (top <= 0 or top >= len(items)) else items[:top]
    print(f"  primary_category vocabulary ({len(items)} distinct):")
    for cat, c in shown:
        print(f"    {cat[:34]:34s} {c:6d} ({pct(c, n):5.1f}%) {bar(c, n)}")
    if len(shown) < len(items):
        tail = sum(c for _, c in items[len(shown):])
        print(f"    {'... (' + str(len(items) - len(shown)) + ' more)':34s} "
              f"{tail:6d} ({pct(tail, n):5.1f}%)")

    # rossetti rollup
    print()
    print("  Rossetti rollup:")
    for cat, c in a['rossetti_hist'].most_common():
        print(f"    {cat:28s} {c:6d} ({pct(c, n):5.1f}%) {bar(c, n)}")

    # residential findings
    print()
    print("  RESIDENTIAL FINDINGS")
    print(f"    classified residential : {a['n_res']:6d} "
          f"({pct(a['n_res'], n):5.1f}% of all POIs)")
    if a['zone_present']:
        if a['n_interior']:
            ip = pct(a['n_res_interior'], a['n_interior'])
            print(f"      of which interior    : {a['n_res_interior']:6d} "
                  f"({ip:5.1f}% of interior POIs)")
        else:
            print(f"      of which interior    : {a['n_res_interior']:6d}")

    # residential-looking but misfiled
    misfiled = a['misfiled_by_bucket']
    if a['n_misfiled'] == 0:
        print("    residential-looking POIs misfiled elsewhere: none detected")
    else:
        print(f"    residential-looking POIs MISFILED out of residential: "
              f"{a['n_misfiled']}")
        for bucket in sorted(misfiled, key=lambda b: -sum(misfiled[b].values())):
            tot = sum(misfiled[bucket].values())
            print(f"      -> landed in '{bucket}': {tot}")
            for prim, c in misfiled[bucket].most_common():
                how = a['match_how'].get(prim, '?')
                print(f"           {prim[:30]:30s} {c:5d}  [{how}]")

    # verdict
    print()
    caught_extra = sum(a['res_like_caught'].values())
    res_like = sum(a['res_like_total'].values())
    print("  VERDICT")
    print(f"    Of {res_like} residential-looking POIs, "
          f"{caught_extra} are recognized and {a['n_misfiled']} are misfiled.")
    if a['n_res'] == 0:
        print("    Zero residential POIs in this district. If you know it has "
              "labeled housing,\n    that confirms the *places* layer does not "
              "carry it — a buildings/landuse\n    source is required.")
    elif pct(a['n_res'], n) < 2.0:
        print("    Residential is <2% of POIs — implausibly low for an urban "
              "district.\n    Consistent with the places layer under-"
              "representing housing.")


def print_cross_summary(results):
    if len(results) < 2:
        return
    print('=' * 72)
    print("CROSS-DISTRICT SUMMARY")
    print('-' * 72)
    print(f"  {'sad':28s} {'POIs':>7s} {'res':>6s} {'res%':>6s} "
          f"{'misfiled':>9s}")
    for a in results:
        print(f"  {a['sad'][:28]:28s} {a['n']:7d} {a['n_res']:6d} "
              f"{pct(a['n_res'], a['n']):5.1f}% {a['n_misfiled']:9d}")


# ─────────────────────────────────────────────────────────────────────────────
# JSON report
# ─────────────────────────────────────────────────────────────────────────────

def to_json(results):
    out = []
    for a in results:
        out.append({
            'path': a['path'],
            'sad': a['sad'],
            'n_pois': a['n'],
            'n_interior': a['n_interior'],
            'n_residential': a['n_res'],
            'n_residential_interior': a['n_res_interior'],
            'residential_pct': round(pct(a['n_res'], a['n']), 3),
            'n_residential_like_misfiled': a['n_misfiled'],
            'misfiled_by_bucket': {
                b: dict(c) for b, c in a['misfiled_by_bucket'].items()
            },
            'residential_like_recognized': dict(a['res_like_caught']),
            'primary_category_hist': dict(a['primary_hist']),
            'rossetti_hist': dict(a['rossetti_hist']),
            'sanity': a['sanity'],
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
def collect_paths(args):
    paths = []
    for p in args.paths:
        paths.append(Path(p))
    if args.scan:
        root = Path(args.scan)
        if not root.exists():
            sys.exit(f"--scan path not found: {root}")
        found = sorted(root.rglob('rod_places.geojson'))
        if not found:
            sys.exit(f"no rod_places.geojson under {root}")
        paths.extend(found)
    # de-dup while preserving order
    seen, uniq = set(), []
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def main():
    ap = argparse.ArgumentParser(
        description="Audit residential coverage in ROD/Overture places exports.")
    ap.add_argument('paths', nargs='*',
                    help='one or more rod_places.geojson (or raw export) files')
    ap.add_argument('--scan', type=str, default=None,
                    help='directory to recursively scan for rod_places.geojson')
    ap.add_argument('--top', type=int, default=0,
                    help='limit primary_category histogram to top N (0 = all)')
    ap.add_argument('--json', type=str, default=None,
                    help='write a JSON report to this path instead of text')
    args = ap.parse_args()

    paths = collect_paths(args)
    if not paths:
        ap.error("provide at least one file path or --scan DIR")

    results = []
    for p in paths:
        if not p.exists():
            print(f"  SKIP (not found): {p}", file=sys.stderr)
            continue
        try:
            results.append(analyze(p))
        except Exception as e:  # noqa: BLE001 — diagnostic should never crash hard
            print(f"  SKIP (error reading {p}): {e}", file=sys.stderr)

    if not results:
        sys.exit("no readable files")

    if args.json:
        Path(args.json).write_text(
            json.dumps(to_json(results), indent=2), encoding='utf-8')
        print(f"wrote {args.json}")
        return

    for a in results:
        print_report(a, args.top)
    print_cross_summary(results)


if __name__ == '__main__':
    main()
