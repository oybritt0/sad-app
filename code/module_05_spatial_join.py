"""
module_05_spatial_join.py

THE INTEGRATION MOMENT. Reads every signal source for one SAD and produces
the unified district profile.

INPUTS (consumed)
  derived/<sad>/manifest.json          (Module 1) georeferencing
  derived/<sad>/cv_metrics.json        (Module 2) field-level morphology
  derived/<sad>/buildings_clustered.gpkg (Module 2b) per-building features + cluster_id
  source/<sad>/rod_places.geojson      (Module 3) Rossetti-classified POIs
  derived/<sad>/program_summary.json   (Module 3) district-level program rollup
  source/<sad>/census_blockgroups.gpkg (Module 4) demographics with geometries
  derived/<sad>/census_summary.json    (Module 4) area-weighted demographic summary

OUTPUTS
  derived/<sad>/buildings_enriched.gpkg
      Every building polygon with: 21 CV shape features, cluster_id, programs
      found inside (list), programs found within 10m (list), counts of each,
      and the block group it sits in (with key ACS columns inherited).
  
  derived/<sad>/places_classified.geojson
      Every ROD point with the building_id of the polygon it falls in
      (or NULL for places in open space - those are the public-realm activations).
  
  derived/<sad>/cluster_program_crosstab.json
      For each phylogenetic form cluster, the program mix (Rossetti percentages)
      of POIs that fall inside its buildings. This is the cross-signal finding:
      empirical evidence that morphology and program are coupled. "Cluster 2
      contains predominantly sport programs" says the algorithm-derived
      stadium-class form cluster IS the venue cluster.
  
  derived/<sad>/district_profile.json
      The unified 33-dimension district vector: morphology (13) + program (8+2)
      + demographics (10). One row per SAD, ready to stack across all 37 in
      Module 6 for cross-SAD UMAP.

USAGE
    python module_05_spatial_join.py \\
        --source  ..\\data\\source\\per_sad\\district_detroit \\
        --derived ..\\data\\derived\\per_sad\\district_detroit
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import (
    Manifest, CVMetrics, ProgramMix, DemographicProfile, DistrictProfile,
)
from shared.geo_utils import buffer_in_meters


# Rossetti program buckets — order matters for stable JSON output
ROSSETTI_BUCKETS = [
    'sport', 'residential', 'hotel', 'retail_food_entertainment',
    'office', 'parking', 'open_space', 'other',
]


# ─── Step 1: places-into-buildings spatial join ──────────────────────────────

def join_places_to_buildings(
    buildings_cv: gpd.GeoDataFrame,
    places: gpd.GeoDataFrame,
    utm_crs: str,
    adjacency_meters: float = 10.0,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Two passes:
      (a) Which building does each place fall INSIDE?
      (b) Which buildings does each place fall WITHIN <adjacency_meters>?
    
    Returns (buildings_enriched, places_with_building_id).
    """
    places = places.to_crs(buildings_cv.crs)
    
    # Stable building IDs for joining
    if 'building_id' not in buildings_cv.columns:
        buildings_cv = buildings_cv.copy()
        buildings_cv['building_id'] = [f'b{i:05d}' for i in range(len(buildings_cv))]
    
    # ─── (a) Strict inside join ──────────────────────────────────────────
    inside = gpd.sjoin(
        places, buildings_cv[['building_id', 'geometry']],
        how='left', predicate='within',
    )
    inside_per_building = (
        inside.dropna(subset=['building_id'])
              .groupby('building_id')
              .agg(
                  programs_inside=('rossetti_category', list),
                  place_count_inside=('id', 'count'),
              )
              .reset_index()
    )
    
    # ─── (b) Adjacency join (within N meters of building edge) ──────────
    buildings_buffered = buildings_cv[['building_id', 'geometry']].copy()
    buildings_buffered = buffer_in_meters(buildings_buffered, adjacency_meters, utm_crs)
    
    adjacent = gpd.sjoin(
        places, buildings_buffered,
        how='left', predicate='within',
    )
    adjacent_per_building = (
        adjacent.dropna(subset=['building_id'])
                .groupby('building_id')
                .agg(
                    programs_adjacent=('rossetti_category', list),
                    place_count_adjacent=('id', 'count'),
                )
                .reset_index()
    )
    
    # Merge both onto buildings
    out = buildings_cv.merge(inside_per_building, on='building_id', how='left')
    out = out.merge(adjacent_per_building, on='building_id', how='left')
    
    # Empty lists for buildings with no programs (not NaN)
    out['programs_inside'] = out['programs_inside'].apply(
        lambda x: x if isinstance(x, list) else []
    )
    out['programs_adjacent'] = out['programs_adjacent'].apply(
        lambda x: x if isinstance(x, list) else []
    )
    out['place_count_inside'] = out['place_count_inside'].fillna(0).astype(int)
    out['place_count_adjacent'] = out['place_count_adjacent'].fillna(0).astype(int)
    
    # Add a "dominant program" column for easy per-building reading
    def dominant(progs):
        if not progs:
            return None
        # Most common category
        return max(set(progs), key=progs.count)
    out['dominant_program_inside'] = out['programs_inside'].apply(dominant)
    
    # Strip the spatial-join junk columns from places, keep building_id
    place_cols = [c for c in places.columns if c != 'index_right']
    classified_places = inside[place_cols + ['building_id']].copy()
    
    return out, classified_places


# ─── Step 2: block-group context onto buildings ──────────────────────────────

def join_blockgroups_to_buildings(
    buildings: gpd.GeoDataFrame,
    block_groups: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Inherit each building's block group's demographic columns via a
    centroid-in-polygon join. Buildings whose centroid doesn't fall in any
    block group (e.g. on water) get NaN demographics.
    """
    block_groups = block_groups.to_crs(buildings.crs)
    
    # Centroid in geographic CRS triggers a (correct) Shapely warning.
    # Reproject to a metric CRS first, then convert centroids back to
    # the buildings' CRS for the spatial join.
    metric_crs = buildings.estimate_utm_crs()
    centroids = buildings.copy()
    cent_metric = centroids.geometry.to_crs(metric_crs).centroid
    centroids['_centroid'] = cent_metric.to_crs(buildings.crs)
    centroids = centroids.set_geometry('_centroid')
    
    keep_bg_cols = ['GEOID'] + [
        c for c in block_groups.columns if c in (
            'total_pop', 'median_age', 'median_household_income',
            'median_home_value', 'median_gross_rent',
            'race_white', 'race_black', 'race_asian', 'hispanic',
            'edu_bachelors', 'edu_masters',
            'owner_occupied', 'renter_occupied',
        )
    ]
    
    joined = gpd.sjoin(
        centroids, block_groups[keep_bg_cols + ['geometry']],
        how='left', predicate='within',
    )
    
    # Restore the building geometry as primary
    joined = joined.set_geometry('geometry').drop(
        columns=['_centroid', 'index_right'], errors='ignore'
    )
    return joined


# ─── Step 3: cluster x program crosstab (the headline cross-signal finding) ──

def build_cluster_program_crosstab(enriched: gpd.GeoDataFrame) -> dict:
    """
    For each form cluster, count Rossetti programs from buildings tagged
    with that cluster_id. Returns a dict structured for direct JSON dump:
    
      {
        "cluster_1": {
          "n_buildings": 815,
          "n_programs_inside": 312,
          "program_pct": {"sport": 0.02, "office": 0.51, ...},
          "dominant_program": "office"
        },
        "cluster_2": {...},
        ...
      }
    
    This is the empirical link between morphology and program: if cluster 2
    is the stadium-class form family, its program mix should be sport-heavy.
    If it isn't, the morphological typology is uncorrelated with program -
    a finding in itself.
    """
    if 'cluster_id' not in enriched.columns:
        return {'note': 'cluster_id column missing - rerun Module 2b first'}
    
    crosstab = {}
    for cid in sorted(c for c in enriched['cluster_id'].unique() if c > 0):
        members = enriched[enriched['cluster_id'] == cid]
        
        # Flatten all program lists for this cluster
        all_programs: list[str] = []
        for progs in members['programs_inside']:
            all_programs.extend(progs)
        
        n_inside = len(all_programs)
        program_pct = {}
        for bucket in ROSSETTI_BUCKETS:
            program_pct[bucket] = (
                round(all_programs.count(bucket) / n_inside, 4)
                if n_inside > 0 else 0.0
            )
        
        dom = max(program_pct, key=program_pct.get) if n_inside > 0 else None
        
        crosstab[f'cluster_{int(cid)}'] = {
            'n_buildings': int(len(members)),
            'n_programs_inside': int(n_inside),
            'program_pct': program_pct,
            'dominant_program': dom,
            'building_count_with_programs': int((members['place_count_inside'] > 0).sum()),
            'median_area_m2': (
                float(members['area_m2'].median())
                if 'area_m2' in members.columns else None
            ),
        }
    return crosstab


# ─── Step 4: district-level unified profile ──────────────────────────────────

def build_district_profile(
    manifest: Manifest,
    cv_metrics: CVMetrics,
    program_summary: dict,
    census_summary: dict | None,
    residential_building_pct: float | None = None,
) -> DistrictProfile:
    """
    Combine the four input summaries into the unified DistrictProfile.
    Uses program_summary's percentages (already canvas-wide via Module 3)
    and census_summary's area-weighted aggregates (from Module 4) rather
    than recomputing from raw POIs and BGs.
    """
    pct = program_summary.get('rossetti_percentages', {})
    program = ProgramMix(
        sport=                     pct.get('sport', 0.0) / 100.0,
        # Residential from the OSM building tag (Module 3b), not POIs. Falls back
        # to the (now ~0) POI share only when plan_footprint.json is unavailable.
        residential=(residential_building_pct / 100.0
                     if residential_building_pct is not None
                     else pct.get('residential', 0.0) / 100.0),
        hotel=                     pct.get('hotel', 0.0) / 100.0,
        retail_food_entertainment= pct.get('retail_food_entertainment', 0.0) / 100.0,
        office=                    pct.get('office', 0.0) / 100.0,
        parking=                   pct.get('parking', 0.0) / 100.0,
        open_space=                pct.get('open_space', 0.0) / 100.0,
        other=                     pct.get('other', 0.0) / 100.0,
        # These get overwritten downstream with the spatial-join counts;
        # for now use the total places in canvas as the canonical inside count
        total_places_inside=program_summary.get('total_places_in_canvas', 0),
        total_places_adjacent=0,  # will be filled by orchestration
    )
    
    if census_summary is not None:
        pop = census_summary.get('estimated_population') or 0
        demographics = DemographicProfile(
            total_population=int(pop),
            median_household_income=census_summary.get('median_household_income_pop_weighted'),
            median_age=                census_summary.get('median_age_pop_weighted'),
            pct_white=                 census_summary.get('pct_white'),
            pct_black=                 census_summary.get('pct_black'),
            pct_hispanic=              census_summary.get('pct_hispanic'),
            pct_bachelors_or_higher=   census_summary.get('pct_bachelors_or_higher'),
            pct_owner_occupied=        census_summary.get('pct_owner_occupied'),
            pct_renter_occupied=       census_summary.get('pct_renter_occupied'),
            block_groups_intersected=  census_summary.get('block_groups_total', 0),
        )
    else:
        demographics = DemographicProfile(
            total_population=0,
            median_household_income=None,
            median_age=None,
            pct_white=None,
            pct_black=None,
            pct_hispanic=None,
            pct_bachelors_or_higher=None,
            pct_owner_occupied=None,
            pct_renter_occupied=None,
            block_groups_intersected=0,
        )
    
    return DistrictProfile(
        sad_id=manifest.sad_id,
        sad_name=manifest.sad_name,
        typology=manifest.typology,
        morphology=cv_metrics.field,
        program=program,
        demographics=demographics,
    )


# ─── Orchestration ───────────────────────────────────────────────────────────

def process_sad(source_dir: Path, derived_dir: Path) -> DistrictProfile:
    # ─── Load every signal source ────────────────────────────────────────
    manifest    = Manifest.model_validate_json((derived_dir / 'manifest.json').read_text())
    
    print(f"Integrating signals for {manifest.sad_id}...")
    
    cv_metrics  = CVMetrics.model_validate_json((derived_dir / 'cv_metrics.json').read_text())
    program_sum = json.loads((derived_dir / 'program_summary.json').read_text())
    
    # Prefer the clustered buildings (has cluster_id); fall back to cv if 2b not run
    clustered_path = derived_dir / 'buildings_clustered.gpkg'
    cv_path = derived_dir / 'buildings_cv.gpkg'
    if clustered_path.exists():
        buildings = gpd.read_file(clustered_path, layer='buildings')
        print(f"  loaded {len(buildings)} buildings with cluster_id")
    else:
        buildings = gpd.read_file(cv_path, layer='buildings')
        print(f"  WARNING: buildings_clustered.gpkg not found - cluster_id "
              f"will be missing. Run Module 2b first.")
    
    places = gpd.read_file(source_dir / 'rod_places.geojson')
    print(f"  loaded {len(places)} places")
    
    bg_path = source_dir / 'census_blockgroups.gpkg'
    if bg_path.exists():
        block_groups = gpd.read_file(bg_path, layer='blockgroups')
        print(f"  loaded {len(block_groups)} block groups")
    else:
        print(f"  WARNING: no census_blockgroups.gpkg - demographics empty")
        block_groups = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    
    census_sum_path = derived_dir / 'census_summary.json'
    census_sum = (
        json.loads(census_sum_path.read_text())
        if census_sum_path.exists() else None
    )
    
    # ─── Step 1: places into buildings ───────────────────────────────────
    print(f"  joining places to buildings (strict inside + 10m adjacency)...")
    enriched, classified_places = join_places_to_buildings(
        buildings, places, manifest.crs_metric
    )
    
    # ─── Step 2: block groups onto buildings ────────────────────────────
    if len(block_groups) > 0:
        print(f"  joining block group demographics onto buildings...")
        enriched = join_blockgroups_to_buildings(enriched, block_groups)
    
    # ─── Step 3: cluster x program crosstab (the headline finding) ───────
    print(f"  building cluster x program crosstab...")
    crosstab = build_cluster_program_crosstab(enriched)
    
    # ─── Step 3b: zone tagging — classify each building as interior/exterior
    # to the SAD boundary. Enables interior-vs-exterior filtering in
    # downstream analyses.
    sad_boundary_path = source_dir / 'sad_boundary.geojson'
    if sad_boundary_path.exists():
        sad_boundary = gpd.read_file(sad_boundary_path).to_crs(enriched.crs)
        try:
            sad_poly = sad_boundary.union_all()
        except AttributeError:
            sad_poly = sad_boundary.unary_union
        # Compute centroids in metric CRS to silence the geographic-CRS
        # warning; reproject back to enriched.crs for the within() test
        metric_crs = enriched.estimate_utm_crs()
        centroids = enriched.geometry.to_crs(metric_crs).centroid.to_crs(enriched.crs)
        enriched['zone'] = ['interior' if c.within(sad_poly) else 'exterior'
                            for c in centroids]
        n_interior = (enriched['zone'] == 'interior').sum()
        print(f"  zone tagging: {n_interior} interior buildings, "
              f"{len(enriched) - n_interior} exterior")
    else:
        print(f"  WARN: no sad_boundary.geojson - skipping zone tagging")
        enriched['zone'] = 'unknown'
    
    # ─── Step 4: unified district profile ───────────────────────────────
    # Building-tag residential share (Module 3b) — the honest residential source.
    res_bldg_pct = None
    _pf_path = derived_dir / 'plan_footprint.json'
    if _pf_path.exists():
        try:
            _pf = json.loads(_pf_path.read_text())
            res_bldg_pct = (_pf.get('per_layer', {})
                               .get('residential_buildings', {})
                               .get('pct_of_building_footprint'))
        except Exception:
            res_bldg_pct = None

    profile = build_district_profile(manifest, cv_metrics, program_sum,
                                     census_sum,
                                     residential_building_pct=res_bldg_pct)
    # Fill in the adjacent count from the spatial join
    total_adjacent = sum(
        enriched['place_count_adjacent'][enriched['place_count_adjacent'] > 0]
    )
    profile.program.total_places_adjacent = int(total_adjacent)
    
    # ─── Write outputs ───────────────────────────────────────────────────
    # GPKG doesn't store native Python lists; stringify the program list cols
    enriched_out = enriched.copy()
    for col in ('programs_inside', 'programs_adjacent'):
        if col in enriched_out.columns:
            enriched_out[col] = enriched_out[col].apply(
                lambda x: ';'.join(x) if isinstance(x, list) else ''
            )
    enriched_out.to_file(derived_dir / 'buildings_enriched.gpkg',
                         driver='GPKG', layer='buildings')
    
    classified_places.to_file(derived_dir / 'places_classified.geojson',
                              driver='GeoJSON')
    
    (derived_dir / 'cluster_program_crosstab.json').write_text(
        json.dumps(crosstab, indent=2)
    )
    
    (derived_dir / 'district_profile.json').write_text(
        profile.model_dump_json(indent=2)
    )
    
    # ─── Console readout ─────────────────────────────────────────────────
    print(f"\n[OK] {profile.sad_id}")
    print(f"  morphology:")
    print(f"    coverage:       {profile.morphology.coverage:.1%}")
    print(f"    components:     {profile.morphology.component_count}")
    print(f"    fractal dim:    {profile.morphology.fractal_dimension:.3f}")
    print(f"  program mix (canvas-wide):")
    for b in ROSSETTI_BUCKETS:
        pct = getattr(profile.program, b)
        if pct > 0.01:
            print(f"    {b:30s} {pct:5.1%}")
    print(f"    total places inside buildings:   {profile.program.total_places_inside:,}")
    print(f"    total places adjacent (within 10m): {profile.program.total_places_adjacent:,}")
    if profile.demographics.total_population:
        print(f"  demographics (area-weighted):")
        print(f"    population:                 {profile.demographics.total_population:,}")
        if profile.demographics.median_age:
            print(f"    median age:                 {profile.demographics.median_age:.1f}")
        if profile.demographics.median_household_income:
            print(f"    median household income:    ${profile.demographics.median_household_income:,.0f}")
        if profile.demographics.pct_renter_occupied:
            print(f"    renter-occupied:            {profile.demographics.pct_renter_occupied}%")
    print(f"\n  cluster x program crosstab:")
    for cluster_key in sorted(crosstab.keys()):
        if not cluster_key.startswith('cluster_'):
            continue
        c = crosstab[cluster_key]
        dom = c['dominant_program'] or '(no programs inside)'
        print(f"    {cluster_key:12s} {c['n_buildings']:4d} buildings, "
              f"{c['n_programs_inside']:4d} programs inside, "
              f"dominant: {dom}")
    
    print(f"\n  wrote buildings_enriched.gpkg")
    print(f"  wrote places_classified.geojson")
    print(f"  wrote cluster_program_crosstab.json")
    print(f"  wrote district_profile.json")
    
    return profile


def main():
    parser = argparse.ArgumentParser(description="Integration of morphology, program, and demographics")
    parser.add_argument('--source', type=Path, required=True,
                        help='Source directory (must contain rod_places.geojson '
                             'and census_blockgroups.gpkg)')
    parser.add_argument('--derived', type=Path, required=True,
                        help='Derived directory (must contain manifest.json, '
                             'cv_metrics.json, buildings_clustered.gpkg, '
                             'program_summary.json, census_summary.json)')
    args = parser.parse_args()
    
    process_sad(args.source, args.derived)


if __name__ == '__main__':
    main()
