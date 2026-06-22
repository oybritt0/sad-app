"""
pipeline_ui.py — Comprehensive Gradio interface for the SAD analysis pipeline.

Wraps every module from setup through visualization into a single point-and-click
workflow. Eliminates the need to type PowerShell commands with long paths.

USAGE
    cd code
    python pipeline_ui.py

Then open http://127.0.0.1:7860 in a browser.

ARCHITECTURE
    - Tab-based interface with one tab per pipeline stage
    - Each tab calls the underlying module as a subprocess (same code paths
      as the CLI - the UI is a thin wrapper, not a reimplementation)
    - State (project root, selected SAD, last-used options per module) is
      persisted to ~/.sad_pipeline_state.json so selections survive restarts
    - Subprocess output streams live to the log window

The v2 folder layout is required:
    <project_root>/data/<sad_id>/{source,derived,reference}/

If you're migrating from the v1 layout (data/source/per_sad/...), run
migrate_to_v2_layout.py first.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import gradio as gr


# ─── Paths and constants ─────────────────────────────────────────────────────

CODE_DIR = Path(__file__).parent.resolve()
DEFAULT_PROJECT_ROOT = CODE_DIR.parent
DEFAULT_DATA_DIR = DEFAULT_PROJECT_ROOT / 'data'

STATE_FILE = Path.home() / '.sad_pipeline_state.json'

TYPOLOGIES = ['entertainment', 'community', 'innovation', 'tourism']
BACKGROUND_OPTIONS = ['black', 'white', 'transparent']
ACS_YEARS = [2023, 2022, 2021]


# ─── State persistence ───────────────────────────────────────────────────────

DEFAULT_STATE = {
    'project_root': str(DEFAULT_PROJECT_ROOT),
    'data_dir': str(DEFAULT_DATA_DIR),
    'current_sad': '',
    'census_api_key': os.environ.get('CENSUS_API_KEY', ''),
    'rod_export_path': '',
    'module_options': {
        '02b': {'num_clusters': 8},
        '02c': {'background': 'black', 'columns': 50, 'tile_size': 50,
                'preserve_scale': False, 'no_labels': False, 'only_cluster': 0},
        '03': {'clip_to_canvas': True},
        '04': {'year': 2023},
        '06a': {},
        '06b': {},
        '06c': {'reference_file': ''},
        '06d': {'cluster': 0, 'radius_m': 100.0, 'simplify_m': 3.0,
                'top_n': 0, 'min_area': 0.0},
    },
}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return {**DEFAULT_STATE, **json.loads(STATE_FILE.read_text())}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError as e:
        print(f"could not save state: {e}")


# ─── Subprocess execution with live output ───────────────────────────────────

def stream_subprocess(cmd: list[str], cwd: Path, extra_env: dict | None = None
                      ) -> Iterator[str]:
    """Run a subprocess and yield (accumulated log, status) as output arrives."""
    # Strip stray surrounding quotes from any string arg - a common
    # Windows paste-mistake when copying paths from File Explorer.
    cleaned = []
    for c in cmd:
        if isinstance(c, str) and len(c) >= 2:
            if (c[0] == '"' and c[-1] == '"') or (c[0] == "'" and c[-1] == "'"):
                c = c[1:-1]
        cleaned.append(c)
    cmd = cleaned
    
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    
    accumulated = f"$ {' '.join(str(c) for c in cmd)}\n\n"
    yield accumulated
    
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as e:
        accumulated += f"ERROR: {e}\n"
        yield accumulated
        return
    
    for line in iter(proc.stdout.readline, ''):
        accumulated += line
        yield accumulated
    proc.stdout.close()
    proc.wait()
    
    accumulated += f"\n[exit code: {proc.returncode}]\n"
    yield accumulated


# ─── SAD discovery ───────────────────────────────────────────────────────────

def list_sads(data_dir: str) -> list[str]:
    """Return list of SAD IDs found in the v2 layout."""
    d = Path(data_dir)
    if not d.exists():
        return []
    result = []
    for item in d.iterdir():
        if item.is_dir() and (item / 'source').exists():
            result.append(item.name)
    return sorted(result)


# (stage_label, filename) pairs that a SAD must have in its derived/
# folder before the cross-SAD modules (M7 / M8 / M10) can consume it.
# Used to flag incomplete SADs in the selection lists so a half-processed
# SAD is not silently fed into a cross-SAD comparison.
CROSS_SAD_PREREQS = [
    ('M5',     'district_profile.json'),
    ('M5',     'buildings_enriched.gpkg'),
    ('M6c v2', 'interior_exterior_signature.json'),
    ('M6d v3', 'anchor_polar_plots.json'),
]


def sad_completeness(data_dir: str, sad_id: str) -> tuple[bool, list[str]]:
    """Check whether a SAD has the derived outputs the cross-SAD modules
    need. Returns (is_complete, ordered list of missing stage labels)."""
    derived = Path(data_dir) / sad_id / 'derived'
    missing = set()
    for stage, fname in CROSS_SAD_PREREQS:
        if not (derived / fname).exists():
            missing.add(stage)
    order = ['M5', 'M6c v2', 'M6d v3']  # report in pipeline order
    return (not missing), [s for s in order if s in missing]


def sad_choices(data_dir: str) -> list[tuple[str, str]]:
    """Build (label, value) choices for a SAD CheckboxGroup. The value is
    always the clean SAD id; the label is decorated to flag SADs that are
    not yet complete enough for the cross-SAD modules."""
    choices = []
    for sad_id in list_sads(data_dir):
        complete, missing = sad_completeness(data_dir, sad_id)
        if complete:
            label = sad_id
        else:
            label = (f"{sad_id}    \u26a0 incomplete \u2014 "
                     f"run: {', '.join(missing)}")
        choices.append((label, sad_id))
    return choices


def _coerce_sad_list(value) -> list[str]:
    """Normalize a SAD selection into a clean list of ids. Accepts a
    CheckboxGroup list or a legacy comma/space-separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.replace(',', ' ').split() if s.strip()]
    return [str(s).strip() for s in value if str(s).strip()]


def get_sad_paths(data_dir: str, sad_id: str) -> dict[str, Path]:
    """Get standard paths for a SAD under the v2 layout."""
    root = Path(data_dir) / sad_id
    return {
        'root':      root,
        'source':    root / 'source',
        'derived':   root / 'derived',
        'reference': root / 'reference',
    }


def check_prerequisites(data_dir: str, sad_id: str, required_files: list[str]
                         ) -> tuple[bool, str]:
    """Verify the listed files exist for the SAD; return (ok, message)."""
    if not sad_id:
        return False, "No SAD selected."
    paths = get_sad_paths(data_dir, sad_id)
    missing = []
    for fname in required_files:
        # Files can be in source/ or derived/ depending on the module
        found = False
        for sub in ('source', 'derived'):
            if (paths[sub] / fname).exists():
                found = True
                break
        if not found:
            missing.append(fname)
    if missing:
        return False, f"missing files for {sad_id}: {', '.join(missing)}"
    return True, "OK"


# ─── Module runners (each returns a generator of log updates) ─────────────────

def run_setup(data_dir, sad_id, sad_name, typology, anchor_venue,
              buildings_file, boundary_file, extent_file, extent_meters):
    """Run setup_sad_source.py to consolidate raw inputs."""
    if not all([sad_id, sad_name, typology, anchor_venue]):
        yield "ERROR: please fill in sad_id, sad_name, typology, and anchor_venue."
        return
    if not all([buildings_file, boundary_file, extent_file]):
        yield "ERROR: please select all three required GeoJSONs."
        return
    
    output_dir = Path(data_dir) / sad_id / 'source'
    cmd = [
        sys.executable, 'setup_sad_source.py',
        '--buildings', buildings_file,
        '--boundary',  boundary_file,
        '--extent',    extent_file,
        '--output',    str(output_dir),
        '--sad-id',    sad_id,
        '--sad-name',  sad_name,
        '--typology',  typology,
        '--anchor-venue', anchor_venue,
    ]
    if extent_meters and float(extent_meters) > 0:
        cmd.extend(['--extent-meters', str(extent_meters)])
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_01(data_dir, sad_id):
    """Module 1: rasterize figure-ground"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_01_image_generator.py',
        '--source', str(paths['source']),
        '--out',    str(paths['derived']),
    ]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_02(data_dir, sad_id):
    """Module 2: per-building CV metrics"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_02_cv_extractor.py',
        '--source',  str(paths['source']),
        '--derived', str(paths['derived']),
    ]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_02b(data_dir, sad_id, num_clusters):
    """Module 2b: phylogenetic clustering"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_02b_building_phylogeny.py',
        '--derived', str(paths['derived']),
        '--num-clusters', str(int(num_clusters)),
    ]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_02c(data_dir, sad_id, background, columns, tile_size,
                    preserve_scale, no_labels, only_cluster):
    """Module 2c: form atlas"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_02c_form_atlas.py',
        '--derived', str(paths['derived']),
        '--background', background,
        '--columns', str(int(columns)),
        '--tile-size', str(int(tile_size)),
    ]
    if preserve_scale:
        cmd.append('--preserve-scale')
    if no_labels:
        cmd.append('--no-labels')
    if only_cluster and int(only_cluster) > 0:
        cmd.extend(['--only-cluster', str(int(only_cluster))])
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_03(data_dir, sad_id, places_file, clip_to_canvas):
    """Module 3: ROD program extractor"""
    if not places_file:
        yield "ERROR: select a ROD GeoJSON file."
        return
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_03_rod_program_extractor.py',
        '--places-file', places_file,
        '--source',  str(paths['source']),
        '--derived', str(paths['derived']),
    ]
    if not clip_to_canvas:
        cmd.append('--no-clip')
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_04(data_dir, sad_id, api_key, year):
    """Module 4: Census ACS pull"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_04_census_pull.py',
        '--source',  str(paths['source']),
        '--derived', str(paths['derived']),
        '--year', str(int(year)),
    ]
    extra_env = {'CENSUS_API_KEY': api_key} if api_key else None
    yield from stream_subprocess(cmd, CODE_DIR, extra_env=extra_env)


def run_module_4b(data_dir, sad_id, api_key):
    """Module 4b: ACS time-series pull (2013-2023) for the slider/chart."""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_4b_census_timeseries.py',
        '--source',  str(paths['source']),
        '--derived', str(paths['derived']),
    ]
    extra_env = {'CENSUS_API_KEY': api_key} if api_key else None
    yield from stream_subprocess(cmd, CODE_DIR, extra_env=extra_env)


def run_module_05(data_dir, sad_id):
    """Module 5: integration spatial join"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_05_spatial_join.py',
        '--source',  str(paths['source']),
        '--derived', str(paths['derived']),
    ]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06a(data_dir, sad_id):
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06a_cluster_program_heatmap.py',
           '--derived', str(paths['derived'])]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06b(data_dir, sad_id):
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06b_dendrogram_by_program.py',
           '--derived', str(paths['derived'])]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06c(data_dir, sad_id, reference_file):
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06c_land_area_reconciliation.py',
           '--derived', str(paths['derived'])]
    if reference_file:
        cmd.extend(['--reference-json', reference_file])
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06d(data_dir, sad_id, cluster, radius_m, simplify_m,
                    top_n, min_area):
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [
        sys.executable, 'module_06d_anchor_relational_field.py',
        '--source', str(paths['source']),
        '--derived', str(paths['derived']),
        '--radius-m', str(radius_m),
        '--simplify-m', str(simplify_m),
    ]
    if top_n and int(top_n) > 0:
        cmd.extend(['--top-n', str(int(top_n))])
    elif min_area and float(min_area) > 0:
        cmd.extend(['--min-area', str(float(min_area))])
    else:
        cmd.extend(['--cluster', str(int(cluster))])
    yield from stream_subprocess(cmd, CODE_DIR)


# ─── New visualization modules (Phase 3 - vector-first) ────────────────

def run_module_06a_v1(data_dir, sad_id):
    """06a v1: cluster x program morphology matrix (median building)"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06a_v1_cluster_program_morphology.py',
           '--derived', str(paths['derived'])]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06a_v2(data_dir, sad_id):
    """06a v2: cluster x program matrix (cluster population)"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06a_v2_cluster_population_matrix.py',
           '--derived', str(paths['derived'])]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_10_place_cards(data_dir, sad_ids):
    """Per-SAD place card briefings (M10)."""
    sad_ids = _coerce_sad_list(sad_ids)
    if len(sad_ids) < 1:
        yield "Select at least 1 SAD from the list above."
        return
    cmd = [sys.executable, 'module_10_place_cards.py',
           '--data-dir', str(data_dir),
           '--sads'] + sad_ids
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_09_scatters(embedding_dir):
    """Thematic scatter plots from an existing M8 embedding output."""
    if not embedding_dir or not embedding_dir.strip():
        yield ("Provide the path to an M8 embedding output directory "
               "(e.g. data/_comparisons/embedding_20260513_1500).")
        return
    p = Path(embedding_dir.strip())
    if not p.exists():
        yield f"Path not found: {p}"
        return
    if not (p / 'vibe_embedding_summary.json').exists():
        yield (f"Missing vibe_embedding_summary.json in {p}. "
               "Run M8 first to generate the embedding.")
        return
    cmd = [sys.executable, 'module_09_thematic_scatters.py',
           '--embedding-dir', str(p)]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_08_embedding(data_dir, sad_ids, skip_demo):
    """District-level latent vibe embedding (M8)."""
    sad_ids = _coerce_sad_list(sad_ids)
    if len(sad_ids) < 2:
        yield "Select at least 2 SADs from the list above to embed."
        return
    cmd = [sys.executable, 'module_08_district_embedding.py',
           '--data-dir', str(data_dir),
           '--sads'] + sad_ids
    if skip_demo:
        cmd.append('--skip-demographics')
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_07_compare(data_dir, sad_ids):
    """Cross-SAD comparison: takes a list of SAD IDs from the selector."""
    sad_ids = _coerce_sad_list(sad_ids)
    if len(sad_ids) < 2:
        yield "Select at least 2 SADs from the list above to compare."
        return
    cmd = [sys.executable, 'module_07_cross_sad_compare.py',
           '--data-dir', str(data_dir),
           '--sads'] + sad_ids
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06b_dendrogram(data_dir, sad_id):
    """06b dendrogram: phylogeny tree colored by program (existing module)"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06b_dendrogram_by_program.py',
           '--derived', str(paths['derived'])]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06b_v2(data_dir, sad_id):
    """06b v2: axis transect (walking through the SAD)"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06b_v2_axis_transect.py',
           '--source', str(paths['source']),
           '--derived', str(paths['derived'])]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06c_v2(data_dir, sad_id):
    """06c v2: interior vs exterior signature comparison"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06c_v2_interior_exterior_signature.py',
           '--derived', str(paths['derived'])]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06d_v2(data_dir, sad_id, cluster, ring_widths):
    """06d v2: anchor field halos (plan view)"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06d_v2_field_halos.py',
           '--source', str(paths['source']),
           '--derived', str(paths['derived']),
           '--cluster', str(int(cluster)),
           '--ring-widths', ring_widths]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06d_v3(data_dir, sad_id, cluster, rings):
    """06d v3: anchor polar plots"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06d_v3_anchor_polar.py',
           '--source', str(paths['source']),
           '--derived', str(paths['derived']),
           '--cluster', str(int(cluster)),
           '--rings', rings]
    yield from stream_subprocess(cmd, CODE_DIR)


def run_module_06d_v4(data_dir, sad_id, cluster, corridor_width, min_pois, exclude_halo):
    """06d v4: anchor connectivity graph"""
    paths = get_sad_paths(data_dir, sad_id)
    cmd = [sys.executable, 'module_06d_v4_anchor_connectivity.py',
           '--source', str(paths['source']),
           '--derived', str(paths['derived']),
           '--cluster', str(int(cluster)),
           '--corridor-width', str(float(corridor_width)),
           '--min-pois', str(int(min_pois)),
           '--exclude-halo', str(float(exclude_halo))]
    yield from stream_subprocess(cmd, CODE_DIR)


# ─── UI construction ─────────────────────────────────────────────────────────

def build_ui():
    state = load_state()
    
    initial_sads = list_sads(state['data_dir'])
    initial_sad = state['current_sad'] if state['current_sad'] in initial_sads \
                  else (initial_sads[0] if initial_sads else '')
    initial_sad_choices = sad_choices(state['data_dir'])
    
    with gr.Blocks(title="SAD Pipeline") as app:
        gr.Markdown("# SAD Analysis Pipeline\nPoint-and-click interface for the "
                    "Sports-Anchored District morphology + program + demographics pipeline.")
        
        # ─── Global controls (apply across all tabs) ────────────────────────
        with gr.Row():
            data_dir_box = gr.Textbox(
                label="Data directory (project root / data)",
                value=state['data_dir'], scale=4,
            )
            current_sad_dd = gr.Dropdown(
                label="Current SAD",
                choices=initial_sads, value=initial_sad,
                scale=3, allow_custom_value=False,
            )
            refresh_btn = gr.Button("Refresh SAD list", scale=1)
        
        def refresh_sads(dd):
            sads = list_sads(dd)
            new_state = load_state()
            new_state['data_dir'] = dd
            save_state(new_state)
            return gr.update(choices=sads, value=(sads[0] if sads else ''))
        
        refresh_btn.click(refresh_sads, inputs=[data_dir_box], outputs=[current_sad_dd])
        
        def remember_sad(sad):
            new_state = load_state()
            new_state['current_sad'] = sad or ''
            save_state(new_state)
            return ''
        current_sad_dd.change(remember_sad, inputs=[current_sad_dd], outputs=[])
        
        # ─── SAD multi-selector (used by the cross-SAD tabs M7/M8/M10) ──────
        def make_sad_selector(label):
            """Create a SAD CheckboxGroup with Select all / Clear / Refresh
            buttons, fully wired. The checkbox values are always clean SAD
            ids; incomplete SADs are flagged in their labels. Returns the
            CheckboxGroup component so the tab can pass it to its runner."""
            group = gr.CheckboxGroup(
                label=label,
                choices=initial_sad_choices,
                value=[],
            )
            with gr.Row():
                b_all = gr.Button("Select all", size='sm')
                b_clear = gr.Button("Clear", size='sm')
                b_refresh = gr.Button("\u21bb Refresh list", size='sm')
            # Select all: re-scan so choices and selection stay consistent
            b_all.click(
                lambda dd: gr.update(choices=sad_choices(dd),
                                     value=list_sads(dd)),
                inputs=[data_dir_box], outputs=[group])
            b_clear.click(lambda: gr.update(value=[]), outputs=[group])
            # Refresh: re-scan choices, keep any still-valid selections
            b_refresh.click(
                lambda dd, cur: gr.update(
                    choices=sad_choices(dd),
                    value=[s for s in _coerce_sad_list(cur)
                           if s in list_sads(dd)]),
                inputs=[data_dir_box, group], outputs=[group])
            return group
        
        # ─── Tabs ──────────────────────────────────────────────────────────
        with gr.Tabs():
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Setup new SAD                                         │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("Setup new SAD"):
                gr.Markdown(
                    "Consolidate raw GeoJSONs into a SAD source folder. "
                    "Run this once per new SAD before any analysis.")
                with gr.Row():
                    with gr.Column():
                        new_sad_id    = gr.Textbox(label="SAD ID (slug)",
                                                    placeholder="e.g. district_detroit")
                        new_sad_name  = gr.Textbox(label="SAD display name",
                                                    placeholder="District Detroit")
                        new_typology  = gr.Dropdown(label="Typology",
                                                     choices=TYPOLOGIES, value='innovation')
                        new_anchor    = gr.Textbox(label="Anchor venue",
                                                    placeholder="Little Caesars Arena")
                        new_extent_m  = gr.Number(
                            label="Extent override in meters (0 to use the extent geojson)",
                            value=0)
                    with gr.Column():
                        new_buildings = gr.File(label="buildings.geojson",
                                                 file_types=['.geojson'])
                        new_boundary  = gr.File(label="sad_boundary.geojson",
                                                 file_types=['.geojson'])
                        new_extent    = gr.File(label="image_extent.geojson",
                                                 file_types=['.geojson'])
                
                setup_btn = gr.Button("Setup new SAD", variant='primary')
                setup_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=20)
                
                def setup_wrapper(dd, sid, snm, typ, anc, bld, bnd, ext, em):
                    yield from run_setup(
                        dd, sid, snm, typ, anc,
                        bld.name if bld else None,
                        bnd.name if bnd else None,
                        ext.name if ext else None,
                        em,
                    )
                
                setup_btn.click(
                    fn=setup_wrapper,
                    inputs=[data_dir_box, new_sad_id, new_sad_name, new_typology,
                            new_anchor, new_buildings, new_boundary, new_extent,
                            new_extent_m],
                    outputs=[setup_log],
                )
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 1 – Figure-ground                              │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M1 - Rasterize"):
                gr.Markdown("**Module 1** rasterizes building footprints into a "
                            "1080×1080 figure-ground image and writes the manifest.")
                m1_btn = gr.Button("Run Module 1", variant='primary')
                m1_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                m1_btn.click(run_module_01,
                              inputs=[data_dir_box, current_sad_dd],
                              outputs=[m1_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 2 – CV metrics                                 │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M2 - CV metrics"):
                gr.Markdown("**Module 2** computes 21 shape features per building "
                            "and 13 whole-field morphology metrics.")
                m2_btn = gr.Button("Run Module 2", variant='primary')
                m2_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                m2_btn.click(run_module_02,
                              inputs=[data_dir_box, current_sad_dd],
                              outputs=[m2_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 2b – Phylogenetic clustering                   │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M2b - Clusters"):
                gr.Markdown("**Module 2b** runs hierarchical clustering on the "
                            "feature matrix. Produces the dendrogram + colored "
                            "geographic map.")
                m2b_clusters = gr.Slider(label="Number of clusters",
                                          minimum=2, maximum=20, step=1,
                                          value=state['module_options']['02b']['num_clusters'])
                m2b_btn = gr.Button("Run Module 2b", variant='primary')
                m2b_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                
                def m2b_run(dd, sid, nc):
                    s = load_state()
                    s['module_options']['02b']['num_clusters'] = int(nc)
                    save_state(s)
                    yield from run_module_02b(dd, sid, nc)
                
                m2b_btn.click(m2b_run,
                               inputs=[data_dir_box, current_sad_dd, m2b_clusters],
                               outputs=[m2b_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 2c – Form atlas                                │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M2c - Form atlas"):
                gr.Markdown("**Module 2c** renders every building as a silhouette in "
                            "a phylogenetically-ordered grid. Outputs PNG + SVG.")
                with gr.Row():
                    m2c_bg = gr.Dropdown(label="Background",
                                          choices=BACKGROUND_OPTIONS,
                                          value=state['module_options']['02c']['background'])
                    m2c_cols = gr.Slider(label="Columns", minimum=10, maximum=100, step=5,
                                          value=state['module_options']['02c']['columns'])
                    m2c_tile = gr.Slider(label="Tile size (px)",
                                          minimum=20, maximum=200, step=10,
                                          value=state['module_options']['02c']['tile_size'])
                with gr.Row():
                    m2c_preserve = gr.Checkbox(label="Preserve scale (relative sizes)",
                                                value=state['module_options']['02c']['preserve_scale'])
                    m2c_nolabels = gr.Checkbox(label="No cluster labels",
                                                value=state['module_options']['02c']['no_labels'])
                    m2c_only = gr.Number(label="Only cluster N (0 = all)",
                                          value=state['module_options']['02c']['only_cluster'])
                m2c_btn = gr.Button("Run Module 2c", variant='primary')
                m2c_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                
                def m2c_run(dd, sid, bg, cols, ts, pres, nolab, oc):
                    s = load_state()
                    s['module_options']['02c'] = {
                        'background': bg, 'columns': int(cols), 'tile_size': int(ts),
                        'preserve_scale': pres, 'no_labels': nolab,
                        'only_cluster': int(oc),
                    }
                    save_state(s)
                    yield from run_module_02c(dd, sid, bg, cols, ts, pres, nolab, oc)
                
                m2c_btn.click(m2c_run,
                               inputs=[data_dir_box, current_sad_dd, m2c_bg, m2c_cols,
                                       m2c_tile, m2c_preserve, m2c_nolabels, m2c_only],
                               outputs=[m2c_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 3 – ROD Program                                │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M3 - Program"):
                gr.Markdown("**Module 3** classifies POIs from the ROD tool into "
                            "Rossetti categories and tags each by SAD-interior/exterior.")
                m3_places = gr.Textbox(label="ROD GeoJSON path",
                                        value=state['rod_export_path'],
                                        placeholder=r"C:\...\rod_export.geojson")
                m3_clip = gr.Checkbox(label="Clip to canvas extent (recommended)",
                                       value=state['module_options']['03']['clip_to_canvas'])
                m3_btn = gr.Button("Run Module 3", variant='primary')
                m3_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                
                def m3_run(dd, sid, places, clip):
                    s = load_state()
                    s['rod_export_path'] = places
                    s['module_options']['03']['clip_to_canvas'] = clip
                    save_state(s)
                    yield from run_module_03(dd, sid, places, clip)
                
                m3_btn.click(m3_run,
                              inputs=[data_dir_box, current_sad_dd, m3_places, m3_clip],
                              outputs=[m3_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 4 – Census                                     │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M4 - Census"):
                gr.Markdown("**Module 4** pulls ACS demographics for block groups "
                            "intersecting the canvas. Free Census API key required: "
                            "https://api.census.gov/data/key_signup.html")
                m4_key = gr.Textbox(label="Census API key",
                                     value=state['census_api_key'],
                                     type='password',
                                     placeholder="40-char hex key")
                m4_year = gr.Dropdown(label="ACS 5-year end year",
                                       choices=ACS_YEARS,
                                       value=state['module_options']['04']['year'])
                m4_btn = gr.Button("Run Module 4", variant='primary')
                m4_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                
                def m4_run(dd, sid, key, year):
                    s = load_state()
                    s['census_api_key'] = key
                    s['module_options']['04']['year'] = int(year)
                    save_state(s)
                    yield from run_module_04(dd, sid, key, year)
                
                m4_btn.click(m4_run,
                              inputs=[data_dir_box, current_sad_dd, m4_key, m4_year],
                              outputs=[m4_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 5 – Integration                                │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M4b - Census time-series"):
                gr.Markdown("**Module 4b** pulls the full 2013-2023 ACS history "
                            "for the year slider and time-series chart. Uses the "
                            "same Census API key as M4 above.")
                m4b_key = gr.Textbox(label="Census API key",
                                     value=state['census_api_key'],
                                     type='password',
                                     placeholder="40-char hex key")
                m4b_btn = gr.Button("Run Module 4b", variant='primary')
                m4b_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                
                def m4b_run(dd, sid, key):
                    s = load_state()
                    s['census_api_key'] = key
                    save_state(s)
                    yield from run_module_4b(dd, sid, key)
                
                m4b_btn.click(m4b_run,
                              inputs=[data_dir_box, current_sad_dd, m4b_key],
                              outputs=[m4b_log])
            
            with gr.Tab("M5 - Integrate"):
                gr.Markdown("**Module 5** integrates all signals: spatial joins "
                            "places onto buildings, inherits block-group demographics, "
                            "and produces the unified district_profile.json.")
                m5_btn = gr.Button("Run Module 5", variant='primary')
                m5_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=20)
                m5_btn.click(run_module_05,
                              inputs=[data_dir_box, current_sad_dd],
                              outputs=[m5_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Module 6v - Vector visualizations                     │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M6v - Vector viz"):
                gr.Markdown(
                    "New SVG-first visualizations designed for editing in "
                    "Illustrator (recolor, restyle, retypography). Each is a "
                    "different way of reading the SAD: cluster×program matrices, "
                    "walking transects, anchor field maps, polar plots, "
                    "connectivity diagrams."
                )
                
                with gr.Accordion("06a v1 — cluster × program (median building)",
                                   open=False):
                    gr.Markdown("Each cluster's median building silhouette, "
                                "scaled by POI % across the row.")
                    m6a_v1_btn = gr.Button("Run 06a v1", variant='primary')
                    m6a_v1_log = gr.Textbox(label="Log", interactive=False,
                                             max_lines=20, autoscroll=True,
                                             lines=8)
                    m6a_v1_btn.click(run_module_06a_v1,
                                      inputs=[data_dir_box, current_sad_dd],
                                      outputs=[m6a_v1_log])
                
                with gr.Accordion("06a v2 — cluster × program (population)",
                                   open=False):
                    gr.Markdown("Same matrix but each cell shows multiple "
                                "buildings from the cluster, count scaled by POI %.")
                    m6a_v2_btn = gr.Button("Run 06a v2", variant='primary')
                    m6a_v2_log = gr.Textbox(label="Log", interactive=False,
                                             max_lines=20, autoscroll=True,
                                             lines=8)
                    m6a_v2_btn.click(run_module_06a_v2,
                                      inputs=[data_dir_box, current_sad_dd],
                                      outputs=[m6a_v2_log])
                
                with gr.Accordion("06b — Dendrogram colored by program (phylogeny)",
                                   open=False):
                    gr.Markdown("Phylogenetic tree of buildings (from "
                                "Module 2b clustering) with each leaf recolored "
                                "by its dominant program category. Shows whether "
                                "morphology predicts program — clustered colors "
                                "= yes, scattered = no. SVG-editable in Illustrator.")
                    m6b_dend_btn = gr.Button("Run 06b dendrogram", variant='primary')
                    m6b_dend_log = gr.Textbox(label="Log", interactive=False,
                                               max_lines=20, autoscroll=True,
                                               lines=8)
                    m6b_dend_btn.click(run_module_06b_dendrogram,
                                        inputs=[data_dir_box, current_sad_dd],
                                        outputs=[m6b_dend_log])
                
                with gr.Accordion("06b v2 — axis transect (walking sequence)",
                                   open=False):
                    gr.Markdown("Walking transect along the primary and "
                                "secondary axes of the SAD. Auto-detects "
                                "axes from anchor orientations. Buildings "
                                "above/below centerline = left/right sides; "
                                "POIs as colored dots; voids visible as gaps.")
                    m6b_v2_btn = gr.Button("Run 06b v2", variant='primary')
                    m6b_v2_log = gr.Textbox(label="Log", interactive=False,
                                             max_lines=20, autoscroll=True,
                                             lines=10)
                    m6b_v2_btn.click(run_module_06b_v2,
                                      inputs=[data_dir_box, current_sad_dd],
                                      outputs=[m6b_v2_log])
                
                with gr.Accordion("06c v2 — interior vs exterior signature",
                                   open=False):
                    gr.Markdown("The SAD's boundary effect: program mix "
                                "inside the SAD vs in the canvas surroundings. "
                                "Side-by-side bars + difference panel.")
                    m6c_v2_btn = gr.Button("Run 06c v2", variant='primary')
                    m6c_v2_log = gr.Textbox(label="Log", interactive=False,
                                             max_lines=20, autoscroll=True,
                                             lines=10)
                    m6c_v2_btn.click(run_module_06c_v2,
                                      inputs=[data_dir_box, current_sad_dd],
                                      outputs=[m6c_v2_log])
                
                with gr.Accordion("06d — Per-anchor relational field profiles",
                                   open=False):
                    gr.Markdown("Generates one PNG + SVG + JSON per anchor "
                                "building: plan view of the anchor with POI "
                                "halo, plus 'carpet view' showing POI position "
                                "along each face of the building's perimeter.")
                    with gr.Row():
                        m6d_orig_cluster = gr.Number(
                            label="Cluster ID (0 = auto-detect anchors)",
                            value=0)
                        m6d_orig_radius = gr.Slider(
                            label="Search radius (m)",
                            minimum=25, maximum=500, step=25,
                            value=state['module_options']['06d']['radius_m'])
                        m6d_orig_simplify = gr.Slider(
                            label="Face simplification (m)",
                            minimum=1, maximum=20, step=1,
                            value=state['module_options']['06d']['simplify_m'])
                    with gr.Row():
                        m6d_orig_topn = gr.Number(
                            label="Top N largest (overrides cluster)",
                            value=state['module_options']['06d']['top_n'])
                        m6d_orig_minarea = gr.Number(
                            label="Min area filter m^2 (overrides cluster)",
                            value=state['module_options']['06d']['min_area'])
                    m6d_orig_btn = gr.Button("Run 06d profiles",
                                              variant='primary')
                    m6d_orig_log = gr.Textbox(label="Log", interactive=False,
                                               max_lines=20, autoscroll=True,
                                               lines=12)
                    
                    def m6d_orig_run(dd, sid, cl, rad, sim, tn, ma):
                        s = load_state()
                        s['module_options']['06d'] = {
                            'cluster': int(cl), 'radius_m': float(rad),
                            'simplify_m': float(sim),
                            'top_n': int(tn), 'min_area': float(ma),
                        }
                        save_state(s)
                        yield from run_module_06d(dd, sid, cl, rad, sim, tn, ma)
                    
                    m6d_orig_btn.click(
                        m6d_orig_run,
                        inputs=[data_dir_box, current_sad_dd,
                                m6d_orig_cluster, m6d_orig_radius,
                                m6d_orig_simplify,
                                m6d_orig_topn, m6d_orig_minarea],
                        outputs=[m6d_orig_log])
                
                with gr.Accordion("06d v2 — anchor field halos (plan view)",
                                   open=False):
                    gr.Markdown("Plan view of the SAD with each anchor "
                                "surrounded by concentric POI density halos "
                                "colored by dominant program. Overlapping "
                                "halos reveal shared relational fields.")
                    with gr.Row():
                        m6d_v2_cluster = gr.Number(label="Cluster ID (0 = auto-detect anchors)", value=0)
                        m6d_v2_rings = gr.Textbox(label="Ring widths (m, comma-sep)",
                                                    value="50,100,150,200")
                    m6d_v2_btn = gr.Button("Run 06d v2", variant='primary')
                    m6d_v2_log = gr.Textbox(label="Log", interactive=False,
                                             max_lines=20, autoscroll=True,
                                             lines=8)
                    m6d_v2_btn.click(run_module_06d_v2,
                                      inputs=[data_dir_box, current_sad_dd,
                                              m6d_v2_cluster, m6d_v2_rings],
                                      outputs=[m6d_v2_log])
                
                with gr.Accordion("06d v3 — anchor polar plots", open=False):
                    gr.Markdown("Small-multiples grid of polar plots showing "
                                "each anchor's surroundings by compass "
                                "direction and distance.")
                    with gr.Row():
                        m6d_v3_cluster = gr.Number(label="Cluster ID (0 = auto-detect anchors)", value=0)
                        m6d_v3_rings = gr.Textbox(label="Ring outer radii (m)",
                                                    value="25,50,75,100")
                    m6d_v3_btn = gr.Button("Run 06d v3", variant='primary')
                    m6d_v3_log = gr.Textbox(label="Log", interactive=False,
                                             max_lines=20, autoscroll=True,
                                             lines=8)
                    m6d_v3_btn.click(run_module_06d_v3,
                                      inputs=[data_dir_box, current_sad_dd,
                                              m6d_v3_cluster, m6d_v3_rings],
                                      outputs=[m6d_v3_log])
                
                with gr.Accordion("06d v4 — anchor connectivity graph",
                                   open=False):
                    gr.Markdown("Network graph: anchors as nodes at true "
                                "geographic positions, edges weighted by "
                                "POI density (POIs per 100m of corridor). "
                                "Density-normalized so short corridors aren't "
                                "penalized vs long ones.")
                    with gr.Row():
                        m6d_v4_cluster = gr.Number(label="Cluster ID (0 = auto-detect anchors)", value=0)
                        m6d_v4_corridor = gr.Slider(label="Corridor half-width (m)",
                                                     minimum=10, maximum=80,
                                                     step=5, value=30)
                    with gr.Row():
                        m6d_v4_min = gr.Slider(label="Min POIs for edge",
                                                minimum=1, maximum=20, step=1,
                                                value=3)
                        m6d_v4_halo = gr.Slider(label="Anchor halo exclusion (m)",
                                                 minimum=0, maximum=80, step=5,
                                                 value=25,
                                                 info="POIs within this distance "
                                                 "of an anchor are excluded from "
                                                 "the corridor")
                    m6d_v4_btn = gr.Button("Run 06d v4", variant='primary')
                    m6d_v4_log = gr.Textbox(label="Log", interactive=False,
                                             max_lines=20, autoscroll=True,
                                             lines=8)
                    m6d_v4_btn.click(run_module_06d_v4,
                                      inputs=[data_dir_box, current_sad_dd,
                                              m6d_v4_cluster, m6d_v4_corridor,
                                              m6d_v4_min, m6d_v4_halo],
                                      outputs=[m6d_v4_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: M7 - Cross-SAD comparison                             │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M7 - Compare SADs"):
                gr.Markdown(
                    "Side-by-side comparison of 2+ SADs that have already "
                    "been through M1-M5 + M6v. Produces four cross-SAD "
                    "visualizations (morphology radar, program signatures, "
                    "boundary effects, anchor inventory) plus a numerical "
                    "synthesis JSON. Outputs go to "
                    "`data/_comparisons/comparison_<timestamp>/`."
                )
                
                m7_sads = make_sad_selector(
                    "SADs to compare  \u2014  \u26a0 flagged SADs need "
                    "M5 / M6c v2 / M6d v3 finished first")
                m7_btn = gr.Button("Run cross-SAD comparison",
                                    variant='primary', size='lg')
                m7_log = gr.Textbox(label="Log", interactive=False,
                                     max_lines=25, autoscroll=True, lines=15)
                m7_btn.click(run_module_07_compare,
                              inputs=[data_dir_box, m7_sads],
                              outputs=[m7_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: M8 - Vibe embedding (district-level latent map)       │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M8 - Vibe embedding"):
                gr.Markdown(
                    "**District-level latent vibe map.** Each SAD becomes a "
                    "single point in a ~27-dimensional feature space "
                    "(morphology + program + anchors + demographics), with "
                    "ALL features computed from data **inside the SAD "
                    "boundary only**. Outputs include pairwise distances, "
                    "PCA projection (rank-2 max for N=3), feature signatures, "
                    "and per-pair distinguishing features.\n\n"
                    "Outputs go to `data/_comparisons/embedding_<timestamp>/`.\n\n"
                    "Requires each SAD to have M1-M5 plus M6c v2 "
                    "(interior/exterior signature). Demographics included "
                    "only if `source/<sad>/census_blockgroups.gpkg` exists "
                    "for **all** SADs (else dropped for consistency).")
                
                m8_sads = make_sad_selector(
                    "SADs to embed  \u2014  \u26a0 flagged SADs need "
                    "M5 / M6c v2 / M6d v3 finished first")
                m8_skip_demo = gr.Checkbox(
                    label="Skip demographic features "
                          "(even if census data available)",
                    value=False,
                )
                m8_btn = gr.Button("Run vibe embedding",
                                    variant='primary', size='lg')
                m8_log = gr.Textbox(label="Log", interactive=False,
                                     max_lines=25, autoscroll=True, lines=15)
                m8_btn.click(run_module_08_embedding,
                              inputs=[data_dir_box, m8_sads, m8_skip_demo],
                              outputs=[m8_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: M9 - Thematic scatter plots                           │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M9 - Thematic scatters"):
                gr.Markdown(
                    "**Thematic 2D scatter plots** placing each SAD on "
                    "interpretable axis pairs (morphology, program, anchor "
                    "structure, scale, demographics). Each chart answers "
                    "one specific positioning question.\n\n"
                    "Reads features from an existing M8 embedding output. "
                    "Run M8 first, then point this at the resulting "
                    "`embedding_<timestamp>/` folder.\n\n"
                    "Outputs land in `<embedding-dir>/scatters/`, "
                    "including a composite overview grid (12+ charts) and "
                    "individual deck-grade SVGs in `scatters/individual/`."
                )
                m9_emb = gr.Textbox(
                    label="Path to M8 embedding output directory",
                    placeholder=r"..\data\_comparisons\embedding_<YYYYMMDD_HHMM>",
                    lines=1,
                )
                m9_btn = gr.Button("Run thematic scatters",
                                    variant='primary', size='lg')
                m9_log = gr.Textbox(label="Log", interactive=False,
                                     max_lines=25, autoscroll=True, lines=12)
                m9_btn.click(run_module_09_scatters,
                              inputs=[m9_emb],
                              outputs=[m9_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: M10 - Place cards (one-page SAD briefings)            │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("M10 - Place cards"):
                gr.Markdown(
                    "**One-page place card briefings** for non-technical "
                    "audiences (planners, ROD leadership, project kickoffs). "
                    "Each card combines: name + city + anchor venue header, "
                    "an architectural plan view with SAD-interior buildings "
                    "colored by program, a one-sentence character "
                    "description, six at-a-glance stat callouts, an "
                    "establishment-mix donut, a rule-based Strengths panel, "
                    "and a native-geography demographic context panel.\n\n"
                    "Character descriptions and Strengths bullets use "
                    "codified planner heuristics, not generative AI \u2014 "
                    "the rules are near the top of "
                    "`module_10_place_cards.py` and can be edited.\n\n"
                    "Outputs go to "
                    "`data/_comparisons/place_cards_<timestamp>/`."
                )
                m10_sads = make_sad_selector(
                    "SADs for place cards  \u2014  \u26a0 flagged SADs need "
                    "M5 / M6c v2 / M6d v3 finished first")
                m10_btn = gr.Button("Generate place cards",
                                     variant='primary', size='lg')
                m10_log = gr.Textbox(label="Log", interactive=False,
                                      max_lines=25, autoscroll=True, lines=12)
                m10_btn.click(run_module_10_place_cards,
                               inputs=[data_dir_box, m10_sads],
                               outputs=[m10_log])
            
            # ┌────────────────────────────────────────────────────────────┐
            # │ Tab: Migrate v1 -> v2                                      │
            # └────────────────────────────────────────────────────────────┘
            with gr.Tab("Migrate v1->v2"):
                gr.Markdown("Migrate from the old `data/source/per_sad/<sad>/` "
                            "layout to the new `data/<sad>/{source,derived,reference}/` "
                            "layout. Dry-run first; then commit.")
                
                def run_migrate(dd, sid, commit):
                    cmd = [
                        sys.executable, 'migrate_to_v2_layout.py',
                        '--data-root', dd,
                    ]
                    if sid:
                        cmd.extend(['--sad-id', sid])
                    else:
                        cmd.append('--all')
                    if commit:
                        cmd.append('--commit')
                    yield from stream_subprocess(cmd, CODE_DIR)
                
                with gr.Row():
                    mig_sid = gr.Textbox(label="SAD ID (blank = all)")
                    mig_dryrun = gr.Button("Dry run", variant='secondary')
                    mig_commit = gr.Button("Commit migration", variant='primary')
                mig_log = gr.Textbox(label="Log", interactive=False, max_lines=20, autoscroll=True, lines=15)
                
                def mig_dryrun_wrapper(dd, sid):
                    yield from run_migrate(dd, sid, False)
                
                def mig_commit_wrapper(dd, sid):
                    yield from run_migrate(dd, sid, True)
                
                mig_dryrun.click(
                    fn=mig_dryrun_wrapper,
                    inputs=[data_dir_box, mig_sid], outputs=[mig_log])
                mig_commit.click(
                    fn=mig_commit_wrapper,
                    inputs=[data_dir_box, mig_sid], outputs=[mig_log])
        
        gr.Markdown("---\n*State persisted to `~/.sad_pipeline_state.json`. "
                    "Selections survive restarts.*")
    
    return app


if __name__ == '__main__':
    app = build_ui()
    app.queue()  # enable streaming
    app.launch(server_name='127.0.0.1', server_port=7860, share=False,
               inbrowser=True)
