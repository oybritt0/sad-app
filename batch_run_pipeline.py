"""
batch_run_pipeline.py â€” Run the full SAD pipeline across all districts.

Walks every SAD with a source/ folder and runs each module in dependency
order. Cross-SAD modules (M7, M8, M9, M10) run once at the end after all
per-SAD outputs exist. Idempotent: skips a (district, module) pair whose
expected output file is already present, unless --force is passed.

USAGE
    # Run everything (default behaviour â€” all modules, all districts)
    python batch_run_pipeline.py --data-dir <path-to-data>

    # Re-run even where outputs already exist
    python batch_run_pipeline.py --data-dir <path-to-data> --force

    # Restrict to specific modules (comma-separated names like M1,M2,M3b,M11)
    python batch_run_pipeline.py --data-dir <path-to-data> --modules M11,M3b,M6e

    # Restrict to specific districts (comma-separated SAD ids)
    python batch_run_pipeline.py --data-dir <path-to-data> --sads 02_Sportsmans-Park_Glendale-AZ

    # Run only the per-SAD modules, or only the cross-SAD phase
    python batch_run_pipeline.py --data-dir <path-to-data> --stage per-sad
    python batch_run_pipeline.py --data-dir <path-to-data> --stage cross-sad

    # Census API key (needed for M4)
    set CENSUS_API_KEY=...   (PowerShell: $env:CENSUS_API_KEY = "...")

OUTPUTS
    <data_dir>/_batch_runs/<timestamp>/
        batch_log.txt          full stdout for every module run
        batch_summary.csv      one row per (sad_id, module) attempt:
                               status, duration_s, error_message
        batch_run.log          high-level runner log (status of each step)

    Cross-SAD outputs go to:
        <data_dir>/_comparisons/<module>/

DEPENDENCIES (informational â€” the runner runs them in this order)
    M1 â†’ M2 â†’ M2b â†’ M2c
    M1 â†’ M3 â†’ M5
    M1 â†’ M3b
    M1 â†’ M4
    M2, M3 â†’ M5
    M5 â†’ M6a v1, M6a v2, M6b, M6c v2, M6d v3, M6d v4
    source/ alone â†’ M6e, M11
    All per-SAD complete â†’ M7, M8 â†’ M9 â†’ M10
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# â”€â”€â”€ Module catalogue â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Each module entry is a dict with:
#   name          short label printed in logs
#   script        filename in code/
#   args          list of CLI arg tokens; tokens with {placeholders} are
#                 substituted at runtime from the context dict
#   marker        relative path under derived/ that signals success; if
#                 present (and no --force), the module is skipped
#   needs         list of preceding-marker paths that must exist before
#                 this module can run (under derived/). Empty list means
#                 no prerequisites beyond source/.
#
# Per-SAD modules are run for every district. Cross-SAD modules are
# run once with all 37 SAD ids; they use different placeholders.

PER_SAD_MODULES = [
    # ---- Phase 1: independent on source/ alone -----------------------------
    {
        'name':   'M1',
        'script': 'module_01_image_generator.py',
        'args':   ['--source', '{source}', '--out', '{derived}'],
        'marker': 'manifest.json',
        'needs':  [],
    },
    {
        'name':   'M3b',
        'script': 'module_3b_context_layers.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'plan_footprint.json',
        'needs':  [],
    },
    {
        'name':   'M6e',
        'script': 'module_06e_street_centrality.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'street_centrality.geojson',
        'needs':  [],
    },
    {
        'name':   'M11',
        'script': 'module_11_nolli_map.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'nolli_map.svg',
        'needs':  [],
    },
    {
        # M12: per-category amenity density heatmaps from ROD POIs.
        # Reads the same input file as M3 (the raw places geojson),
        # applies the Rossetti rollup independently, and renders one
        # KDE heatmap per category. Auto-skips if no ROD file exists.
        'name':   'M12',
        'script': 'module_12_amenity_density.py',
        'args':   ['--source', '{source}', '--derived', '{derived}',
                   '--places-file', '{rod_places}'],
        'marker': 'amenity_density/amenity_density_summary.json',
        'needs':  [],
        'requires_rod': True,
    },
    {
        # M13: transit stations from OpenStreetMap via Overpass API.
        # ROD cross-validation is optional â€” runs without ROD too.
        # Hits a public API so will fail without internet.
        'name':   'M13',
        'script': 'module_13_transit_stations.py',
        'args':   ['--source', '{source}', '--derived', '{derived}',
                   '--places-file', '{rod_places}'],
        'marker': 'transit/transit_summary.json',
        'needs':  [],
    },

    # ---- Transit routes (GTFS shapes) â€” sibling to M13 stations ------------
    {
        'name':   'M14',
        'script': 'module_14_transit_routes.py',
        'args':   ['--derived', '{derived}', '--source', '{source}', '--discover'],
        'marker': 'transit/transit_routes.geojson',
        'needs':  ['manifest.json'],
    },    {
        # M15: 5/10/15-minute pedestrian walksheds from SAD centroid.
        # Uses networkx Dijkstra on the walkable street network.
        # Pure local computation â€” no external dependencies.
        'name':   'M15',
        'script': 'module_15_walkshed.py',
        'args':   ['--source', '{source}', '--derived', '{derived}',
                   '--minutes', '5', '10', '15'],
        'marker': 'walkshed/walkshed_summary.json',
        'needs':  [],
    },
    {
        # M16: feature-anchored program proximity. For each SAD, compares
        # program mix INSIDE a buffer around transit stations / parks vs
        # OUTSIDE. Cleveland-style dot plot makes the shift legible
        # (e.g. retail/F&B +14pp near transit).
        # Requires M13 transit output AND source/rod_places.geojson.
        'name':   'M16',
        'script': 'module_16_feature_program_proximity.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'feature_program_proximity/'
                  'feature_program_proximity_summary.json',
        'needs':  ['transit/transit_stations.geojson'],
        'requires_rod': True,
    },

    # ---- Phase 2: after M1 -------------------------------------------------
    {
        'name':   'M2',
        'script': 'module_02_cv_extractor.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'cv_metrics.json',
        'needs':  ['manifest.json'],
    },
    {
        'name':   'M3',
        'script': 'module_03_rod_program_extractor.py',
        'args':   ['--source', '{source}', '--derived', '{derived}',
                   '--places-file', '{rod_places}'],
        # M3 always writes program_summary.json, even in the unverified
        # auto-skip case. rod_places.geojson is written ONLY when the
        # data passes verification. So program_summary.json is the
        # reliable success marker.
        'marker': 'program_summary.json',
        'needs':  ['manifest.json'],
        'requires_rod': True,
    },
    {
        'name':   'M4',
        'script': 'module_04_census_pull.py',
        'args':   ['--source', '{source}', '--derived', '{derived}',
                   '--year', '2023'],
        # M4 writes census_summary.json to derived/ and census_blockgroups.gpkg
        # to source/. Use the summary as the success marker.
        'marker': 'census_summary.json',
        'needs':  ['manifest.json'],
        'requires_env': 'CENSUS_API_KEY',
        # M4 uses the US Census Bureau API, which has no coverage outside
        # the US. Canadian SADs (Toronto, Winnipeg, Edmonton, Vancouver,
        # etc.) are auto-detected by trailing province code and skipped.
        'us_only': True,
    },
    {
        'name':   'M4b',
        'script': 'module_4b_census_timeseries.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        # Writes census_timeseries.json to derived/ plus per-vintage GeoJSONs
        # to source/. Time-series version of M4 covering 2013-2023.
        'marker': 'census_timeseries.json',
        'needs':  ['manifest.json'],
        'requires_env': 'CENSUS_API_KEY',
        'us_only': True,
    },

    # ---- Phase 3: after M2 -------------------------------------------------
    {
        'name':   'M2b',
        'script': 'module_02b_building_phylogeny.py',
        'args':   ['--derived', '{derived}', '--num-clusters', '8'],
        'marker': 'buildings_clustered.gpkg',
        'needs':  ['cv_metrics.json'],
    },
    {
        'name':   'M2c',
        'script': 'module_02c_form_atlas.py',
        'args':   ['--derived', '{derived}', '--background', 'black',
                   '--columns', '50', '--tile-size', '50'],
        'marker': 'form_atlas.png',
        'needs':  ['buildings_clustered.gpkg'],
    },
    {
        'name':   'M2c scale',
        'script': 'module_02c_form_atlas.py',
        'args':   ['--derived', '{derived}', '--background', 'black',
                   '--columns', '50', '--tile-size', '50',
                   '--preserve-scale'],
        # --preserve-scale produces a parallel pair of outputs that include
        # building size: stadiums dominate, rowhouses look like dots.
        'marker': 'form_atlas_linear.png',
        'needs':  ['buildings_clustered.gpkg'],
    },

    # ---- Phase 4: spatial join â€” needs M2 + M3 -----------------------------
    {
        'name':   'M5',
        'script': 'module_05_spatial_join.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'buildings_enriched.gpkg',
        # M5 reads manifest.json, cv_metrics.json, and program_summary.json
        # (NOT rod_places.geojson directly). So as long as M3 produced the
        # summary, M5 can run â€” even when M3 auto-skipped writing the
        # per-feature places file.
        'needs':  ['cv_metrics.json', 'program_summary.json'],
    },

    # ---- Phase 4b: gpkg -> geojson for the viewer (auto post-M5) -----------
    # M5 writes buildings_enriched.gpkg; the web viewer can only read GeoJSON.
    # This converts the just-written gpkg to derived/buildings_enriched.geojson
    # so a building reprocess (or a freshly drawn district) never leaves the
    # viewer rendering stale geometry. Single-district mode via --derived.
    {
        'name':   'M5geo',
        'script': 'convert_enriched_buildings.py',
        'args':   ['--derived', '{derived}'],
        'marker': 'buildings_enriched.geojson',
        'needs':  ['buildings_enriched.gpkg'],
    },

    # ---- Phase 4c: enrich buildings with NSI HAZUS + FEMA USA Structures ----
    # M3d augments buildings_enriched.geojson IN PLACE with structural attrs
    # (occupancy class, story count, year built, square footage, measured
    # height, etc.) joined from public US datasets. Powers the viewer's
    # Occupancy / Height / Year-built color modes and stacked filters.
    #
    # Sources: USACE National Structure Inventory (HAZUS-aligned point data)
    #          FEMA USA Structures (polygon footprints with measured height)
    # Both are US-only -> Canadian SADs auto-skip via us_only.
    # Hits two public endpoints, so will fail without internet.
    {
        'name':   'M3d',
        'script': 'module_3d_building_attrs.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        # buildings_attrs.geojson is the intermediate raw-join output;
        # the enriched-in-place file (buildings_enriched.geojson) is the
        # one the viewer reads. Either could serve as the marker; we use
        # the intermediate because it is M3d-specific (the enriched file
        # is already M5geo's marker, so reusing it would mask M3d skips).
        'marker': 'buildings_attrs.geojson',
        'needs':  ['buildings_enriched.geojson'],
        'us_only': True,
    },

    # ---- Phase 5: after M5 -------------------------------------------------
    {
        'name':   'M6a v1',
        'script': 'module_06a_v1_cluster_program_morphology.py',
        'args':   ['--derived', '{derived}'],
        'marker': 'cluster_program_crosstab.json',
        'needs':  ['buildings_enriched.gpkg'],
    },
    {
        'name':   'M6a v2',
        'script': 'module_06a_v2_cluster_population_matrix.py',
        'args':   ['--derived', '{derived}'],
        # M6a v2 actually writes an SVG, not a JSON.
        'marker': 'cluster_program_morphology_v2.svg',
        'needs':  ['buildings_enriched.gpkg'],
    },
    {
        'name':   'M6b',
        'script': 'module_06b_dendrogram_by_program.py',
        'args':   ['--derived', '{derived}'],
        'marker': 'dendrogram_by_program.png',
        'needs':  ['buildings_enriched.gpkg'],
    },
    {
        'name':   'M6b v2',
        'script': 'module_06b_v2_axis_transect.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        # M6b v2 writes a JSON summary, not a PNG.
        'marker': 'axis_transect_summary.json',
        'needs':  ['buildings_enriched.gpkg', 'source/rod_places.geojson'],
    },
    {
        'name':   'M6c v2',
        'script': 'module_06c_v2_interior_exterior_signature.py',
        'args':   ['--derived', '{derived}'],
        'marker': 'interior_exterior_signature.json',
        'needs':  ['program_summary.json'],
    },
    {
        'name':   'M6d v3',
        'script': 'module_06d_v3_anchor_polar.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'anchor_polar_plots.json',
        'needs':  ['buildings_enriched.gpkg', 'source/rod_places.geojson'],
    },
    {
        'name':   'M6d v4',
        'script': 'module_06d_v4_anchor_connectivity.py',
        'args':   ['--source', '{source}', '--derived', '{derived}'],
        'marker': 'anchor_connectivity.json',
        'needs':  ['buildings_enriched.gpkg', 'source/rod_places.geojson'],
    },
    # ---- Free data streams: jobs / transit LOS / environment ---------------
    # These pull live (LODES, GTFS, Planetary Computer) and write the GeoJSON
    # layers the viewer + map render. US-only sources auto-skip on Canadian
    # SADs. They read the manifest (M1) + source geometry, so they run late.
    {
        'name':   'M20',
        'script': 'module_20_jobs_lodes.py',
        'args':   ['--derived', '{derived}', '--source', '{source}', '--timeseries'],
        'marker': 'jobs/jobs_blocks.geojson',
        'needs':  ['manifest.json'],
        'skip_for_drawn': True,  # LODES TIGER download peaks memory; skip on hosted draws
    },
    {
        'name':   'M21',
        'script': 'module_21_transit_los.py',
        'args':   ['--derived', '{derived}', '--source', '{source}', '--discover'],
        'marker': 'transit_los/transit_los_stops.geojson',
        'needs':  ['manifest.json'],
    },
    {
        'name':   'M22',
        'script': 'module_22_environment.py',
        'args':   ['--derived', '{derived}', '--source', '{source}'],
        'marker': 'environment/heat_grid.geojson',
        'needs':  ['manifest.json'],
        'skip_for_drawn': True,  # Planetary Computer raster ops peak memory; skip on hosted draws
    },
]

# Cross-SAD modules â€” run once with the full SAD list.
CROSS_SAD_MODULES = [
    {
        'name':   'M7',
        'script': 'module_07_cross_sad_compare.py',
        'args':   ['--data-dir', '{data_dir}', '--out', '{out}'],
        'append_sads': True,
        # Marker unknown â€” M7's actual output filename hasn't been verified.
        # Set to '' so the runner always executes M7 (cheap, runs once).
        'marker': '',
        'out_subdir': 'cross_sad_compare',
    },
    {
        'name':   'M8',
        'script': 'module_08_district_embedding.py',
        'args':   ['--data-dir', '{data_dir}', '--out', '{out}'],
        'append_sads': True,
        'marker': 'vibe_embedding_summary.json',
        'out_subdir': 'district_embedding',
    },
    {
        'name':   'M9',
        'script': 'module_09_thematic_scatters.py',
        'args':   ['--embedding-dir', '{m8_out}', '--out', '{out}'],
        'append_sads': False,
        # M9 writes a gallery â€” composite_overview is the headline file.
        'marker': 'composite_overview.png',
        'out_subdir': 'thematic_scatters',
    },
    {
        'name':   'M10',
        'script': 'module_10_place_cards.py',
        'args':   ['--data-dir', '{data_dir}', '--out', '{out}'],
        'append_sads': True,
        # Marker unknown â€” M10's actual output filename hasn't been verified.
        # Set to '' so it always executes (cheap, runs once).
        'marker': '',
        'out_subdir': 'place_cards',
    },
]


# â”€â”€â”€ Filesystem helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def list_sads(data_dir: Path) -> list[str]:
    """Discover all SADs by looking for <id>/source/ subdirs."""
    if not data_dir.exists():
        return []
    out = []
    for child in data_dir.iterdir():
        if child.is_dir() and (child / 'source').is_dir():
            out.append(child.name)
    return sorted(out)


def get_paths(data_dir: Path, sad_id: str) -> dict[str, Path]:
    root = data_dir / sad_id
    return {
        'root':    root,
        'source':  root / 'source',
        'derived': root / 'derived',
    }


def find_rod_places(data_dir: Path, sad_id: str) -> Path | None:
    """Look in <sad>/03_ROD-Search-Tool/ROD_Search/ for any .geojson (corpus
    convention), then fall back to source/rod_places.geojson (written by
    save_district for map-drawn districts)."""
    rod_dir = data_dir / sad_id / '03_ROD-Search-Tool' / 'ROD_Search'
    if rod_dir.exists():
        # Prefer the canonical puller output. Picking the alphabetically-first
        # geojson grabbed stale "2km_..._unverified.geojson" exports (old extent)
        # over the freshly-pulled overture_places.geojson, feeding M3 wrong POIs.
        canonical = rod_dir / 'overture_places.geojson'
        if canonical.exists() and canonical.stat().st_size > 0:
            return canonical
        candidates = sorted(rod_dir.glob('*.geojson'))
        if candidates:
            return candidates[0]
    drawn = data_dir / sad_id / 'source' / 'rod_places.geojson'
    if drawn.exists() and drawn.stat().st_size > 0:
        return drawn
    return None


# Canadian province codes â€” used to detect SADs that should be skipped by
# US-only modules like M4 (US Census Bureau). The trailing 2-letter region
# code in our SAD id pattern (e.g. "_Toronto-ON") distinguishes country.
CANADIAN_PROVINCES = {
    'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'
}


def is_drawn_district(sad_id: str) -> bool:
    """Drawn-via-UI districts have either 'Drawn-district' (unnamed timestamp
    fallback) OR 'Drawn-boundary' (user-named, suffix from resolve_extent
    info dict) in the slug. Match either pattern.
    Heavier modules (M20 LODES, M22 environment) peak memory beyond the
    hosted instance ceiling, so we skip them for drawn districts. Corpus
    districts (which were processed locally) keep their full outputs."""
    return ('Drawn-district' in sad_id) or ('Drawn-boundary' in sad_id)


def is_canadian_sad(sad_id: str) -> bool:
    """SAD ids end with -<region code>; detect Canadian by province code."""
    last = sad_id.rsplit('-', 1)
    return len(last) == 2 and last[-1] in CANADIAN_PROVINCES


def render_args(template: list[str], ctx: dict) -> list[str]:
    return [tok.format(**ctx) for tok in template]


# â”€â”€â”€ Subprocess execution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_subprocess(cmd: list[str], cwd: Path, log_fh,
                   extra_env: dict | None = None) -> tuple[int, str]:
    """Run a subprocess, stream stdout to console and log file, return
    (returncode, last_100_lines_of_output)."""
    env = os.environ.copy()
    # Force UTF-8 so module output with non-ASCII glyphs (e.g. the trend arrow
    # in M20) doesn't crash under Windows' legacy cp1252 console code page.
    env.setdefault('PYTHONUTF8', '1')
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    if extra_env:
        env.update(extra_env)
    log_fh.write(f"\n$ {' '.join(str(c) for c in cmd)}\n")
    log_fh.flush()
    print(f"  $ {Path(cmd[1]).name} ...", flush=True)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1, env=env)
    except FileNotFoundError as e:
        msg = f"FAILED to launch: {e}"
        log_fh.write(msg + "\n")
        return -1, msg

    tail: list[str] = []
    if proc.stdout is not None:
        for line in proc.stdout:
            log_fh.write(line)
            log_fh.flush()
            tail.append(line)
            if len(tail) > 100:
                tail.pop(0)
    rc = proc.wait()
    return rc, ''.join(tail).strip()


# â”€â”€â”€ Per-SAD phase â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_per_sad_phase(data_dir: Path, code_dir: Path, sads: list[str],
                      modules: list[dict], force: bool,
                      log_fh, summary_rows: list[dict]) -> None:
    total = len(sads) * len(modules)
    done = 0
    for sad_id in sads:
        paths = get_paths(data_dir, sad_id)
        rod_places = find_rod_places(data_dir, sad_id)
        ctx = {
            'source':     str(paths['source']),
            'derived':    str(paths['derived']),
            'rod_places': str(rod_places) if rod_places else '',
        }
        # Make sure derived/ exists
        paths['derived'].mkdir(parents=True, exist_ok=True)

        print(f"\nâ”€â”€ {sad_id} â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€", flush=True)
        log_fh.write(f"\nâ•â• {sad_id} â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n")

        for mod in modules:
            done += 1
            tag = f"[{done}/{total}] {mod['name']:<7}"
            # Resolve marker path. An empty marker means "always run" â€” used
            # for modules whose actual output filename hasn't been confirmed.
            marker_rel = mod['marker']
            marker = paths['derived'] / marker_rel if marker_rel else None

            # Skip if marker already exists and not forced
            if marker is not None and marker.exists() and not force:
                print(f"  {tag} skip â€” {marker_rel} already exists", flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'skip-already-done', 'duration_s': 0,
                    'error': ''})
                continue

            # Skip M3 if no places file present
            if mod.get('requires_rod') and not rod_places:
                print(f"  {tag} skip â€” no ROD places file for {sad_id}", flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'skip-no-rod', 'duration_s': 0,
                    'error': 'No .geojson in 03_ROD-Search-Tool/ROD_Search/'})
                continue

            # Skip US-only modules for Canadian SADs (the trailing region
            # code in the SAD id distinguishes country).
            if mod.get('us_only') and is_canadian_sad(sad_id):
                print(f"  {tag} skip â€” {sad_id} is Canadian "
                      f"(M4 uses US Census Bureau API only)", flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'skip-not-us', 'duration_s': 0,
                    'error': 'US Census Bureau API has no Canadian coverage'})
                continue
            # Skip memory-heavy modules for drawn districts on hosted instances.
            # Corpus districts already have these outputs from local runs;
            # drawn districts can be re-integrated locally if needed.
            if mod.get('skip_for_drawn') and is_drawn_district(sad_id):
                print(f"  {tag} skip â€” {sad_id} is a drawn district "
                      f"(skip on hosted to fit memory budget)", flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'skip-drawn', 'duration_s': 0,
                    'error': 'Skipped for drawn district (run locally for full coverage)'})
                continue

            # Skip M4 if no CENSUS_API_KEY in env
            env_key = mod.get('requires_env')
            if env_key and not os.environ.get(env_key):
                print(f"  {tag} skip â€” {env_key} not set in environment",
                      flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'skip-no-env', 'duration_s': 0,
                    'error': f'{env_key} required'})
                continue

            # Skip if prerequisites are not satisfied. Prereqs starting with
            # "source/" are looked up under source/, everything else under
            # derived/.
            def _need_path(n: str) -> Path:
                if n.startswith('source/'):
                    return paths['source'] / n[len('source/'):]
                return paths['derived'] / n

            missing = [n for n in mod['needs'] if not _need_path(n).exists()]
            if missing:
                print(f"  {tag} skip â€” missing prerequisites: "
                      f"{', '.join(missing)}", flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'skip-prereq', 'duration_s': 0,
                    'error': f"missing: {', '.join(missing)}"})
                continue

            # Run
            print(f"  {tag} running...", flush=True)
            t0 = time.time()
            cmd = [sys.executable, mod['script']] + render_args(mod['args'], ctx)
            rc, tail = run_subprocess(cmd, code_dir, log_fh)
            dt = time.time() - t0

            if rc == 0 and (marker is None or marker.exists()):
                print(f"  {tag} OK ({dt:.0f}s)", flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'ok', 'duration_s': round(dt, 1),
                    'error': ''})
            elif rc == 0:
                # Returncode 0 but expected marker absent â€” module ran but
                # didn't produce the configured output file. Common when a
                # module has multiple skip-paths (e.g. M6e on a SAD with no
                # vehicular roads). Distinct from a hard failure.
                err = tail.splitlines()[-1] if tail else 'no marker produced'
                print(f"  {tag} ran but no output ({dt:.0f}s) â€” {err[:80]}",
                      flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'ran-no-output', 'duration_s': round(dt, 1),
                    'error': err[:300]})
            else:
                err = tail.splitlines()[-1] if tail else f'returncode {rc}'
                print(f"  {tag} FAIL ({dt:.0f}s) â€” {err[:80]}", flush=True)
                summary_rows.append({
                    'sad_id': sad_id, 'module': mod['name'],
                    'status': 'fail', 'duration_s': round(dt, 1),
                    'error': err[:300]})


# â”€â”€â”€ Cross-SAD phase â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_cross_sad_phase(data_dir: Path, code_dir: Path, sads: list[str],
                        modules: list[dict], force: bool,
                        log_fh, summary_rows: list[dict]) -> None:
    comparisons_root = data_dir / '_comparisons'
    comparisons_root.mkdir(exist_ok=True)
    # Track M8's output dir so M9 can read it
    m8_out = comparisons_root / 'district_embedding'

    # Cross-SAD modules need M5's buildings_enriched.gpkg to exist for every
    # SAD they're given. Filter the SAD list to only districts where that
    # file is present â€” otherwise the modules crash on the first missing
    # one and we lose the result for all 36 other working districts.
    eligible = [s for s in sads
                if (data_dir / s / 'derived' / 'buildings_enriched.gpkg').exists()]
    excluded = [s for s in sads if s not in eligible]

    print(f"\nâ”€â”€ cross-SAD phase ({len(eligible)} of {len(sads)} districts "
          f"eligible) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€", flush=True)
    if excluded:
        print(f"  excluding {len(excluded)} district(s) missing "
              f"buildings_enriched.gpkg:", flush=True)
        for s in excluded:
            print(f"    - {s}", flush=True)
    log_fh.write(f"\nâ•â• cross-SAD phase â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n")
    log_fh.write(f"eligible: {len(eligible)} / {len(sads)}\n")
    log_fh.write(f"excluded: {excluded}\n")

    if not eligible:
        print("  no eligible districts â€” skipping cross-SAD phase", flush=True)
        for mod in modules:
            summary_rows.append({
                'sad_id': '_cross_', 'module': mod['name'],
                'status': 'skip-prereq', 'duration_s': 0,
                'error': 'no districts have buildings_enriched.gpkg'})
        return

    sads = eligible   # use the filtered list from here on

    for mod in modules:
        out_dir = comparisons_root / mod['out_subdir']
        out_dir.mkdir(exist_ok=True)
        marker_rel = mod['marker']
        marker = out_dir / marker_rel if marker_rel else None
        tag = f"{mod['name']:<5}"

        if marker is not None and marker.exists() and not force:
            print(f"  {tag} skip â€” {marker_rel} already exists", flush=True)
            summary_rows.append({
                'sad_id': '_cross_', 'module': mod['name'],
                'status': 'skip-already-done', 'duration_s': 0,
                'error': ''})
            continue

        ctx = {
            'data_dir': str(data_dir),
            'out':      str(out_dir),
            'm8_out':   str(m8_out),
        }
        cmd = [sys.executable, mod['script']] + render_args(mod['args'], ctx)
        if mod.get('append_sads'):
            cmd += ['--sads'] + sads

        print(f"  {tag} running ({mod['script']})...", flush=True)
        t0 = time.time()
        rc, tail = run_subprocess(cmd, code_dir, log_fh)
        dt = time.time() - t0

        if rc == 0:
            if marker is None or marker.exists():
                status, err_msg = 'ok', ''
            else:
                status, err_msg = 'ran-no-output', 'marker not produced'
            print(f"  {tag} {status} ({dt:.0f}s)", flush=True)
            summary_rows.append({
                'sad_id': '_cross_', 'module': mod['name'],
                'status': status, 'duration_s': round(dt, 1),
                'error': err_msg})
        else:
            err = tail.splitlines()[-1] if tail else f'returncode {rc}'
            print(f"  {tag} FAIL ({dt:.0f}s) â€” {err[:80]}", flush=True)
            summary_rows.append({
                'sad_id': '_cross_', 'module': mod['name'],
                'status': 'fail', 'duration_s': round(dt, 1),
                'error': err[:300]})


# â”€â”€â”€ Summary reporting â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def write_summary(summary_rows: list[dict], path: Path) -> None:
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=[
            'sad_id', 'module', 'status', 'duration_s', 'error'])
        w.writeheader()
        for row in summary_rows:
            w.writerow(row)


def print_summary(summary_rows: list[dict]) -> None:
    from collections import Counter
    counts = Counter(r['status'] for r in summary_rows)
    print("\nâ•â• summary â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•")
    print(f"  total attempts:        {len(summary_rows)}")
    for status, n in counts.most_common():
        print(f"  {status:<22}{n}")
    # Per-module breakdown
    print()
    print("  per-module status:")
    by_mod: dict = {}
    for r in summary_rows:
        by_mod.setdefault(r['module'], Counter())[r['status']] += 1
    for mod_name in sorted(by_mod.keys()):
        breakdown = ', '.join(f"{s}={n}"
                              for s, n in by_mod[mod_name].most_common())
        print(f"    {mod_name:<10} {breakdown}")


# â”€â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True,
                    help='Root data directory containing <sad_id>/source/ subdirs')
    ap.add_argument('--code-dir', type=Path, default=None,
                    help='Code directory (defaults to script\'s own directory)')
    ap.add_argument('--stage', choices=['all', 'per-sad', 'cross-sad'],
                    default='all',
                    help='Which phase to run (default: all)')
    ap.add_argument('--modules', type=str, default='',
                    help='Comma-separated module names to run '
                         '(e.g. "M1,M2,M11"). Default: all.')
    ap.add_argument('--sads', type=str, default='',
                    help='Comma-separated SAD ids to run. Default: all '
                         'districts found under data-dir.')
    ap.add_argument('--force', action='store_true',
                    help='Re-run modules even if output markers exist')
    args = ap.parse_args()

    data_dir: Path = args.data_dir.resolve()
    code_dir: Path = (args.code_dir or Path(__file__).parent).resolve()
    if not data_dir.exists():
        sys.exit(f"data-dir not found: {data_dir}")
    if not code_dir.exists():
        sys.exit(f"code-dir not found: {code_dir}")

    # Determine districts
    all_sads = list_sads(data_dir)
    if args.sads:
        wanted = [s.strip() for s in args.sads.split(',') if s.strip()]
        sads = [s for s in wanted if s in all_sads]
        bad = [s for s in wanted if s not in all_sads]
        if bad:
            print(f"WARNING: not found in data-dir: {', '.join(bad)}")
    else:
        sads = all_sads
    if not sads:
        sys.exit("No SADs to process.")

    # Determine which modules
    if args.modules:
        wanted = {s.strip() for s in args.modules.split(',') if s.strip()}
        per_sad = [m for m in PER_SAD_MODULES if m['name'] in wanted]
        cross   = [m for m in CROSS_SAD_MODULES if m['name'] in wanted]
    else:
        per_sad = list(PER_SAD_MODULES)
        cross   = list(CROSS_SAD_MODULES)
    if args.stage == 'per-sad':
        cross = []
    elif args.stage == 'cross-sad':
        per_sad = []

    # Set up output dirs and log
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = data_dir / '_batch_runs' / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / 'batch_log.txt'
    summary_path = run_dir / 'batch_summary.csv'

    print(f"data-dir:   {data_dir}")
    print(f"code-dir:   {code_dir}")
    print(f"districts:  {len(sads)}")
    print(f"per-SAD:    {len(per_sad)} modules"
          f" ({', '.join(m['name'] for m in per_sad)})")
    print(f"cross-SAD:  {len(cross)} modules"
          f" ({', '.join(m['name'] for m in cross)})")
    print(f"force:      {args.force}")
    print(f"log:        {log_path}")
    print()

    summary_rows: list[dict] = []
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(f"batch run started {datetime.now().isoformat()}\n")
        log_fh.write(f"data-dir: {data_dir}\n")
        log_fh.write(f"districts: {len(sads)}\n")
        log_fh.write(f"per-SAD modules: {[m['name'] for m in per_sad]}\n")
        log_fh.write(f"cross-SAD modules: {[m['name'] for m in cross]}\n")
        log_fh.write(f"force: {args.force}\n\n")

        if per_sad:
            run_per_sad_phase(data_dir, code_dir, sads, per_sad,
                              args.force, log_fh, summary_rows)
        if cross:
            run_cross_sad_phase(data_dir, code_dir, sads, cross,
                                args.force, log_fh, summary_rows)

    total_dt = time.time() - t0
    write_summary(summary_rows, summary_path)
    print_summary(summary_rows)
    print(f"\nTotal time: {total_dt/60:.1f} min")
    print(f"Summary CSV: {summary_path}")
    print(f"Full log:    {log_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())



