"""
module_03_rod_program_extractor.py  (revised 2026-05)

Adapter for ROD Search Tool output. Reads a places GeoJSON produced by ROD
(or any equivalent Overture-format export) and produces:

  rod_places.geojson    Same point geometry, with two added columns:
                          - top_level         the Overture top-level category
                          - rossetti_category the 7-bucket Rossetti rollup
  program_summary.json  Aggregate counts and percentages per Rossetti bucket.

REVISION — POI FILTERING (dedup + operational)
This version filters the ROD export to one clean POI set BEFORE any
classification or counting, so every downstream module (05, 06d_v3, 08)
inherits the same cleaned set. See filter_places() for the full rules. In
short:
  - Duplicates (ROD's dedupe_is_duplicate / dedupe_keep flags) are always
    dropped.
  - Non-operational POIs are dropped too — BUT an unverified ROD export
    carries is_operational=False for every row, because ROD's verification
    step (Google Places calls) has not run yet. Filtering such an export
    would drop 100% of POIs, so the operational filter auto-skips with a
    loud warning when it detects that case. Once you run ROD verification,
    the same code path filters normally with no change. Pass
    --keep-non-operational to disable the operational filter explicitly.

CATEGORY ROLLUP LOGIC
ROD exposes Overture's 13 top-level categories plus the hierarchical
taxonomy beneath them. We map both into the 7-bucket scheme used in
Rossetti's 2025 SAD Report donut charts:

  sport                     fitness, stadiums, recreation facilities
  residential               housing (condominium, apartment)
  hotel                     lodging
  retail_food_entertainment retail + dining + commercial entertainment
  office                    business/professional services, health clinics
  parking                   parking facilities specifically
  open_space                parks, plazas, public space
  other                     civic, education, transit, lifestyle services, etc.

Subcategory overrides win over top-level mapping. This matters because:
  - Parks live under `sports_and_recreation` in Overture, but they're
    open_space, not sport. We override on primary_category=='park'.
  - Stadium-class venues (stadium_arena, baseball_stadium, hockey_arena)
    live under `arts_and_entertainment` (along with music venues, dance
    clubs, galleries), but they're sport-class.
  - Parking lives under `travel_and_transportation` alongside transit and
    gas stations, but should be its own bucket.
  - Residential lives under `services_and_business` (alongside
    professional services), but should be its own bucket.

USAGE
    python module_03_rod_program_extractor.py ^
        --places-file "C:\\path\\to\\rod_export.geojson" ^
        --source  "..\\data\\source\\per_sad\\district_detroit" ^
        --derived "..\\data\\derived\\per_sad\\district_detroit"

The script clips places to the SAD's canvas extent by default (reads bbox
from manifest.json). Pass --no-clip to keep all places, including those
outside the canvas.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest


# Subcategory overrides (checked before the top-level fallback). Membership
# is matched against primary_category.lower().strip(). Sets, not lists,
# because lookup is constant-time and 4,000-place datasets are common.

PARKING_SUBCATS = {
    'parking', 'parking_garage', 'parking_lot',
}

RESIDENTIAL_SUBCATS = {
    'condominium', 'apartment', 'housing_authority',
    'residential_building', 'service_apartment',
}

HOTEL_SUBCATS = {
    'hotel', 'motel', 'inn', 'lodge', 'resort', 'lodging',
    'bed_and_breakfast',
}

SPORT_SUBCATS = {
    # Anchor sports venues
    'stadium_arena', 'baseball_stadium', 'hockey_arena',
    'basketball_stadium', 'football_stadium', 'sports_complex',
    # Sport-specific facilities
    'baseball_field', 'soccer_field', 'race_track', 'golf_course',
    'tennis_court', 'swimming_pool',
    # Fitness
    'gym', 'fitness_center', 'sport_or_fitness_facility',
    'sport_or_recreation_club', 'yoga_studio', 'fitness_trainer',
    'martial_arts_club', 'dance_studio', 'boot_camp',
    # Recreation
    'bowling_alley', 'ice_skating_rink', 'skate_park',
    'rock_climbing_spot', 'mountain_bike_trail',
    # Organizations
    'professional_sport_team', 'amateur_sport_league',
    'sports_clubs_and_leagues',
}

OPEN_SPACE_SUBCATS = {
    'park', 'dog_park', 'public_plaza', 'public_fountain',
    'community_center', 'public_space',
}


# Top-level fallback mapping (applied when no subcategory override matches)
TOP_LEVEL_TO_ROSSETTI = {
    'food_and_drink':            'retail_food_entertainment',
    'shopping':                  'retail_food_entertainment',
    'arts_and_entertainment':    'retail_food_entertainment',
    'lodging':                   'hotel',
    'services_and_business':     'office',
    'health_care':               'office',
    'sports_and_recreation':     'sport',
    'community_and_government':  'other',
    'travel_and_transportation': 'other',
    'cultural_and_historic':     'other',
    'education':                 'other',
    'lifestyle_services':        'other',
    'geographic_entities':       'other',
}


def rollup_category(primary_category, top_level) -> str:
    """
    Map an Overture (primary_category, top_level) pair to a Rossetti
    7-bucket label (plus 'other'). Subcategory overrides take precedence
    over the top-level mapping.
    """
    pc = (primary_category or '').lower().strip()

    if pc in PARKING_SUBCATS:
        return 'parking'
    # Residential is NO LONGER derived from POIs. Overture residential POIs are
    # too sparse/inconsistent to map honestly. Residential is sourced from the
    # OSM `building` tag in Module 3b (per_layer.residential_buildings) and
    # injected into the program features in Module 8. These subcategories roll
    # up to 'other' so no POI-based residential percentage is emitted.
    if pc in RESIDENTIAL_SUBCATS:
        return 'other'
    if pc in HOTEL_SUBCATS:
        return 'hotel'
    if pc in SPORT_SUBCATS:
        return 'sport'
    if pc in OPEN_SPACE_SUBCATS:
        return 'open_space'

    return TOP_LEVEL_TO_ROSSETTI.get(top_level or '', 'other')


def _get_top_level(hierarchy):
    """
    Extract the first element of a taxonomy_hierarchy value. Tolerates
    nulls, numpy arrays, Python lists, and zero-length sequences.
    """
    if hierarchy is None:
        return None
    try:
        if hasattr(hierarchy, '__len__') and len(hierarchy) > 0:
            return str(hierarchy[0])
    except (TypeError, ValueError):
        pass
    return None


# ─── POI filtering (dedup + operational) ──────────────────────────────

def _truthy(series):
    """
    Coerce a column to clean booleans, tolerating real bools, 0/1, and
    "true"/"false" strings. GeoJSON should round-trip booleans cleanly,
    but exports vary, so this is defensive.
    """
    def one(v):
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
    return series.map(one)


def filter_places(gdf, *, drop_duplicates: bool = True,
                  require_operational: bool = True):
    """
    Remove duplicate and (optionally) non-operational POIs from a ROD
    export so every downstream module shares one clean set.

    DEDUP — always applied when drop_duplicates is True. ROD flags
    duplicates via dedupe_is_duplicate / dedupe_keep. There is no use case
    for keeping duplicates in the analysis set.

    OPERATIONAL — ROD's verification step (Google Places calls) populates
    is_operational. An UNVERIFIED export has is_operational == False (or
    null) for every row because verification has not run. Filtering such
    an export on is_operational would silently drop 100% of POIs, so this
    function detects that case and SKIPS the operational filter with a
    loud warning rather than zeroing the dataset. Once the export is
    verified, the same code path filters normally — no code change needed.

    Returns (filtered_gdf, report_dict).
    """
    report = {
        'n_input': len(gdf),
        'dropped_duplicate': 0,
        'dropped_non_operational': 0,
        'operational_filter_applied': False,
        'operational_filter_skipped_reason': None,
        'n_output': len(gdf),
    }
    out = gdf

    # ── Dedup ────────────────────────────────────────────────────────
    if drop_duplicates:
        before = len(out)
        if 'dedupe_is_duplicate' in out.columns:
            out = out[~_truthy(out['dedupe_is_duplicate'])]
        elif 'dedupe_keep' in out.columns:
            # No explicit duplicate flag; keep only rows ROD marked to keep.
            out = out[_truthy(out['dedupe_keep'])]
        report['dropped_duplicate'] = before - len(out)

    # ── Operational ──────────────────────────────────────────────────
    if require_operational:
        if 'is_operational' not in out.columns:
            report['operational_filter_skipped_reason'] = (
                "no is_operational column present in the export")
        else:
            op = _truthy(out['is_operational'])
            if not bool(op.any()):
                # Every row is non-operational/null -> unverified export.
                report['operational_filter_skipped_reason'] = (
                    "every POI has is_operational=False/null, which is the "
                    "signature of an UNVERIFIED ROD export; run ROD "
                    "verification before finalizing output")
            else:
                before = len(out)
                out = out[op]
                report['dropped_non_operational'] = before - len(out)
                report['operational_filter_applied'] = True

    report['n_output'] = len(out)
    return out.copy(), report


def process_rod_export(
    places_file: Path,
    source_dir: Path,
    derived_dir: Path,
    clip_to_canvas: bool = True,
    require_operational: bool = True,
) -> Path:
    """
    Read a ROD export, drop duplicate/non-operational POIs, classify each
    remaining place into the Rossetti rollup, optionally clip to the SAD's
    canvas extent, and write standardized outputs. Returns the path of the
    written GeoJSON.
    """
    derived_dir.mkdir(parents=True, exist_ok=True)

    # Load the manifest so we know which SAD this is and where its canvas is.
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"manifest.json not found at {manifest_path}. Run Module 1 first."
        )
    manifest = Manifest.model_validate_json(manifest_path.read_text())

    print(f"Reading {places_file.name}...")
    gdf = gpd.read_file(places_file)
    n_total = len(gdf)
    print(f"  {n_total} places loaded")

    if gdf.crs is None:
        raise ValueError(
            f"{places_file.name} has no CRS - re-export with projection metadata."
        )
    gdf = gdf.to_crs("EPSG:4326")

    # ─── Dedup + operational filter ───────────────────────────────────
    # Produces one clean POI set that every downstream module inherits.
    # The operational filter auto-skips (with a warning) on unverified
    # exports; see filter_places() for the full rules.
    gdf, filter_report = filter_places(
        gdf, drop_duplicates=True, require_operational=require_operational,
    )
    print(f"  filtering: {filter_report['n_input']} -> "
          f"{filter_report['n_output']} places "
          f"({filter_report['dropped_duplicate']} duplicate, "
          f"{filter_report['dropped_non_operational']} non-operational)")
    if filter_report['operational_filter_skipped_reason']:
        print(f"  WARN: operational filter skipped - "
              f"{filter_report['operational_filter_skipped_reason']}")
    if len(gdf) == 0:
        raise ValueError(
            "No places remained after dedup/operational filtering. "
            "Check the ROD export."
        )

    # Clip to canvas extent. ROD pulls a radius that's typically larger
    # than the user's canvas, so most places lie outside the area we
    # actually analyze. Clip here so the spatial join in Module 5 doesn't
    # carry irrelevant points.
    if clip_to_canvas:
        minlon, minlat, maxlon, maxlat = manifest.bbox_geo
        before = len(gdf)
        gdf = gdf.cx[minlon:maxlon, minlat:maxlat].copy()
        print(f"  clipped to canvas: {len(gdf)} of {before} places inside "
              f"({100*len(gdf)/before:.1f}%)")

    if len(gdf) == 0:
        raise ValueError(
            "No places remained after clipping. "
            "Check that the ROD export covers the canvas area."
        )

    # Sanity check expected schema
    required_cols = {'primary_category', 'taxonomy_hierarchy'}
    missing = required_cols - set(gdf.columns)
    if missing:
        raise ValueError(
            f"ROD export missing required columns: {missing}. "
            f"Available columns: {list(gdf.columns)}"
        )

    # Extract top-level category from the taxonomy hierarchy array
    gdf['top_level'] = gdf['taxonomy_hierarchy'].apply(_get_top_level)

    # Apply rollup
    gdf['rossetti_category'] = [
        rollup_category(pc, tl)
        for pc, tl in zip(gdf['primary_category'], gdf['top_level'])
    ]

    # Zone tagging: classify each POI by whether it falls inside the SAD boundary
    # (vs. inside the canvas but outside the SAD). This enables interior-vs-exterior
    # comparison in downstream visualizations.
    sad_boundary_path = source_dir / 'sad_boundary.geojson'
    if sad_boundary_path.exists():
        sad_boundary = gpd.read_file(sad_boundary_path).to_crs("EPSG:4326")
        # union_all() in newer geopandas (>=0.13), unary_union as fallback
        try:
            sad_poly = sad_boundary.union_all()
        except AttributeError:
            sad_poly = sad_boundary.unary_union
        # Spatial join would be overkill for a single polygon - just use .within()
        gdf['zone'] = ['interior' if pt.within(sad_poly) else 'exterior'
                       for pt in gdf.geometry]
        n_interior = (gdf['zone'] == 'interior').sum()
        print(f"  zone tagging: {n_interior} interior, "
              f"{len(gdf) - n_interior} exterior (in canvas, outside SAD)")
    else:
        print(f"  WARN: no sad_boundary.geojson found - skipping zone tagging")
        gdf['zone'] = 'unknown'

    # Print summary
    counts = gdf['rossetti_category'].value_counts()
    print(f"\n  Rossetti rollup:")
    for cat, n in counts.items():
        bar = '#' * int(40 * n / len(gdf))
        print(f"    {cat:30s} {n:5d}  ({100*n/len(gdf):5.1f}%)  {bar}")

    # GeoJSON doesn't serialize numpy arrays cleanly, so drop the hierarchy
    # column before writing. The top_level + rossetti_category we added
    # carry the useful information downstream.
    save_cols = [c for c in gdf.columns if c != 'taxonomy_hierarchy']
    gdf_save = gdf[save_cols].copy()

    # Write the standardized GeoJSON to the source folder (it's data we
    # collected and pre-processed, same convention as Module 4's
    # census_blockgroups.gpkg).
    source_dir.mkdir(parents=True, exist_ok=True)
    out_geojson = source_dir / 'rod_places.geojson'
    gdf_save.to_file(out_geojson, driver='GeoJSON')

    summary = {
        'sad_id': manifest.sad_id,
        'sad_name': manifest.sad_name,
        'source_file': places_file.name,
        'total_places_input': int(n_total),
        'total_places_after_filter': int(filter_report['n_output']),
        'total_places_in_canvas': int(len(gdf)),
        'clipped_to_canvas': clip_to_canvas,
        # Provenance: record exactly what filtering did to the input.
        'filtering': filter_report,
        'rossetti_counts': {str(k): int(v) for k, v in counts.items()},
        'rossetti_percentages': {
            str(k): round(100 * v / len(gdf), 2) for k, v in counts.items()
        },
        'unique_top_categories':
            sorted(gdf['top_level'].dropna().unique().tolist()),
        'unique_primary_categories':
            int(gdf['primary_category'].dropna().nunique()),
    }

    # Zone breakdown (interior vs exterior of SAD)
    if 'zone' in gdf.columns and (gdf['zone'] != 'unknown').any():
        zone_breakdown = {}
        for zone_name in ('interior', 'exterior'):
            sub = gdf[gdf['zone'] == zone_name]
            if len(sub) == 0:
                continue
            zc = sub['rossetti_category'].value_counts()
            zone_breakdown[zone_name] = {
                'count': int(len(sub)),
                'rossetti_counts': {str(k): int(v) for k, v in zc.items()},
                'rossetti_percentages': {
                    str(k): round(100 * v / len(sub), 2) for k, v in zc.items()
                },
            }
        summary['zone_breakdown'] = zone_breakdown
    summary_path = derived_dir / 'program_summary.json'
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"\n[OK] wrote {out_geojson}")
    print(f"[OK] wrote {summary_path}")
    return out_geojson


def main():
    p = argparse.ArgumentParser(
        description="ROD program extractor (Rossetti rollup adapter)"
    )
    p.add_argument('--places-file', type=Path, required=True,
                   help='Path to ROD GeoJSON export (any filename).')
    p.add_argument('--source', type=Path, required=True,
                   help='SAD source directory (output rod_places.geojson goes here).')
    p.add_argument('--derived', type=Path, required=True,
                   help='SAD derived directory (must contain manifest.json from Module 1).')
    p.add_argument('--no-clip', action='store_true',
                   help='Skip clipping to canvas extent. Default behaviour is to '
                        'keep only places within the SAD canvas (ROD typically '
                        'pulls a larger radius than the canvas covers).')
    p.add_argument('--keep-non-operational', action='store_true',
                   help='Disable the operational filter entirely. Duplicates are '
                        'still dropped. By default the operational filter is on '
                        'but auto-skips on unverified exports (where every POI '
                        'has is_operational=False), so you normally do NOT need '
                        'this flag while working with unverified data.')
    args = p.parse_args()

    if not args.places_file.exists():
        sys.exit(f"places file not found: {args.places_file}")

    process_rod_export(
        places_file=args.places_file,
        source_dir=args.source,
        derived_dir=args.derived,
        clip_to_canvas=not args.no_clip,
        require_operational=not args.keep_non_operational,
    )


if __name__ == '__main__':
    main()
