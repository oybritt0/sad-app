"""
morphology_ui.py - simple Gradio interface for the SAD morphology pipeline.

Wraps setup_sad_source.py -> module_01_image_generator -> module_02_cv_extractor
-> module_02b_building_phylogeny into a single point-and-click workflow.

USAGE
    cd code
    python morphology_ui.py

Then open http://127.0.0.1:7860 in a browser. (Or whatever port Gradio prints.)

WHAT IT DOES
    1. Three file pickers for buildings.geojson, sad_boundary.geojson,
       image_extent.geojson (any filenames accepted).
    2. Four metadata fields (sad_id, sad_name, typology, anchor_venue).
    3. Optional --extent-meters override and num-clusters slider.
    4. Click "Run morphology pipeline" to execute setup + M1 + M2 + M2b
       end-to-end. Progress is reported live in the log.
    5. Outputs displayed in the right column: figure-ground PNG, phylogenetic
       dendrogram, cluster-tinted figure-ground, JSON summaries.

This is a thin wrapper around the same scripts you'd run from the command
line - no logic lives here that isn't also in the modules themselves.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path

import gradio as gr


CODE_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = CODE_DIR.parent
DATA_DIR = PROJECT_ROOT / 'data'
SOURCE_ROOT = DATA_DIR / 'source' / 'per_sad'
DERIVED_ROOT = DATA_DIR / 'derived' / 'per_sad'

TYPOLOGIES = ['entertainment', 'community', 'innovation', 'tourism']


def _run(cmd: list[str], log: list[str]) -> tuple[bool, str]:
    """Execute a subprocess, capture output, append to log."""
    log.append(f"\n$ {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run(
            [str(c) for c in cmd],
            cwd=CODE_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        out = result.stdout + result.stderr
        log.append(out)
        return result.returncode == 0, out
    except subprocess.TimeoutExpired:
        log.append("TIMEOUT after 300s")
        return False, "TIMEOUT"
    except Exception as e:
        log.append(f"ERROR: {e}")
        return False, str(e)


def run_morphology(
    buildings_file,
    boundary_file,
    extent_file,
    sad_id: str,
    sad_name: str,
    typology: str,
    anchor_venue: str,
    num_clusters: int,
    progress=gr.Progress(),
):
    """
    Run the four-stage morphology pipeline:
      setup_sad_source -> module_01 -> module_02 -> module_02b
    """
    log: list[str] = []
    
    # ─── Validate ─────────────────────────────────────────────────────────────
    if not all([buildings_file, boundary_file, extent_file]):
        return (None, None, None, None, None,
                "ERROR: please provide all three GeoJSONs.")
    if not sad_id:
        return (None, None, None, None, None,
                "ERROR: sad_id is required (e.g. 'district_detroit').")
    
    # Gradio File components return objects with .name = path
    buildings_path = Path(buildings_file.name if hasattr(buildings_file, 'name') else buildings_file)
    boundary_path = Path(boundary_file.name if hasattr(boundary_file, 'name') else boundary_file)
    extent_path = Path(extent_file.name if hasattr(extent_file, 'name') else extent_file)
    
    source_dir = SOURCE_ROOT / sad_id
    derived_dir = DERIVED_ROOT / sad_id
    
    log.append(f"=== SAD Morphology Pipeline ===")
    log.append(f"sad_id:        {sad_id}")
    log.append(f"buildings:     {buildings_path.name}")
    log.append(f"boundary:      {boundary_path.name}")
    log.append(f"extent:        {extent_path.name}")
    log.append(f"output source: {source_dir}")
    log.append(f"output derived:{derived_dir}")
    
    # ─── Stage 1: setup_sad_source ────────────────────────────────────────────
    progress(0.10, desc="Stage 1/4: consolidating source files...")
    ok, _ = _run([
        sys.executable, 'setup_sad_source.py',
        '--buildings', buildings_path,
        '--boundary', boundary_path,
        '--extent', extent_path,
        '--output', source_dir,
        '--sad-id', sad_id,
        '--sad-name', sad_name or sad_id,
        '--typology', typology,
        '--anchor-venue', anchor_venue or 'unknown',
    ], log)
    if not ok:
        return (None, None, None, None, None,
                "\n".join(log) + "\n\nFAILED at Stage 1 (setup).")
    
    # ─── Stage 2: module_01 image generator ───────────────────────────────────
    progress(0.30, desc="Stage 2/4: rasterizing figure-ground...")
    ok, _ = _run([
        sys.executable, 'module_01_image_generator.py',
        '--source', source_dir,
        '--out', derived_dir,
    ], log)
    if not ok:
        return (None, None, None, None, None,
                "\n".join(log) + "\n\nFAILED at Stage 2 (Module 1).")
    
    # ─── Stage 3: module_02 CV extractor ──────────────────────────────────────
    progress(0.55, desc="Stage 3/4: computing per-building + field metrics...")
    ok, _ = _run([
        sys.executable, 'module_02_cv_extractor.py',
        '--source', source_dir,
        '--derived', derived_dir,
    ], log)
    if not ok:
        return (None, None, None, None, None,
                "\n".join(log) + "\n\nFAILED at Stage 3 (Module 2).")
    
    # ─── Stage 4: module_02b building phylogeny ───────────────────────────────
    progress(0.80, desc=f"Stage 4/4: building phylogenetic tree (k={num_clusters})...")
    ok, _ = _run([
        sys.executable, 'module_02b_building_phylogeny.py',
        '--derived', derived_dir,
        '--num-clusters', str(num_clusters),
    ], log)
    if not ok:
        return (None, None, None, None, None,
                "\n".join(log) + "\n\nFAILED at Stage 4 (Module 2b).")
    
    progress(1.0, desc="Done")
    
    # ─── Collect outputs ──────────────────────────────────────────────────────
    figureground = derived_dir / 'figureground.png'
    dendrogram = derived_dir / 'building_phylogeny.png'
    cluster_map = derived_dir / 'building_clusters_map.png'
    
    cv_metrics = json.loads((derived_dir / 'cv_metrics.json').read_text())
    phylo_summary = json.loads((derived_dir / 'building_phylogeny.json').read_text())
    
    # Combine into one display-friendly object
    summary = {
        'sad': {
            'id': sad_id, 'name': sad_name, 'typology': typology,
            'anchor': anchor_venue,
        },
        'morphology (whole-field)': cv_metrics['field'],
        'morphology (per-building summary)': {
            'building_count': cv_metrics['building_count'],
            'median_area_m2': cv_metrics['median_area_m2'],
            'median_compactness': cv_metrics['median_compactness'],
            'median_elongation': cv_metrics['median_elongation'],
            'median_neighbor_distance_m': cv_metrics['median_neighbor_distance_m'],
        },
        'phylogeny': {
            'num_form_clusters': phylo_summary['num_clusters'],
            'cluster_sizes': phylo_summary['cluster_sizes'],
            'tree_shape': phylo_summary['tree_shape'],
        },
    }
    
    return (
        str(figureground) if figureground.exists() else None,
        str(dendrogram) if dendrogram.exists() else None,
        str(cluster_map) if cluster_map.exists() else None,
        summary,
        f"All artifacts written to:\n  {derived_dir}",
        "\n".join(log),
    )


# ─── UI layout ────────────────────────────────────────────────────────────────

with gr.Blocks(title="SAD Morphology Pipeline", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        "# SAD Morphology Pipeline\n\n"
        "Run figure-ground rasterization, per-building shape metrics, and "
        "RoweBot-style phylogenetic clustering on one SAD. Outputs land in "
        "`data/derived/per_sad/<sad_id>/`."
    )
    
    with gr.Row():
        # ─── Left column: inputs ─────────────────────────────────────────────
        with gr.Column(scale=1):
            gr.Markdown("### Source GeoJSONs")
            buildings_in = gr.File(
                label="Buildings GeoJSON",
                file_types=['.geojson', '.json'],
            )
            boundary_in = gr.File(
                label="SAD Boundary GeoJSON",
                file_types=['.geojson', '.json'],
            )
            extent_in = gr.File(
                label="Canvas Extent GeoJSON",
                file_types=['.geojson', '.json'],
            )
            
            gr.Markdown("### SAD metadata")
            sad_id_in = gr.Textbox(
                label="SAD ID (slug, used as folder name)",
                placeholder="e.g. district_detroit",
                value="district_detroit",
            )
            sad_name_in = gr.Textbox(
                label="SAD Name (human-readable)",
                placeholder="e.g. District Detroit",
                value="District Detroit",
            )
            typology_in = gr.Dropdown(
                label="Rossetti Typology",
                choices=TYPOLOGIES,
                value="innovation",
            )
            anchor_in = gr.Textbox(
                label="Anchor Venue",
                placeholder="e.g. Little Caesars Arena",
                value="Little Caesars Arena",
            )
            
            gr.Markdown("### Phylogeny parameters")
            clusters_in = gr.Slider(
                label="Number of form clusters (k)",
                minimum=2, maximum=15, step=1, value=8,
                info="Witt's RoweBot doesn't fix k; we cut the dendrogram "
                     "here for visualization. 6-10 typical for SAD scale.",
            )
            
            run_btn = gr.Button("Run morphology pipeline", variant="primary", size="lg")
        
        # ─── Right column: outputs ───────────────────────────────────────────
        with gr.Column(scale=2):
            gr.Markdown("### Outputs")
            with gr.Tabs():
                with gr.TabItem("Figure-ground"):
                    fg_out = gr.Image(label="figureground.png", height=600)
                with gr.TabItem("Phylogenetic dendrogram"):
                    dendro_out = gr.Image(label="building_phylogeny.png", height=600)
                with gr.TabItem("Form-cluster map"):
                    map_out = gr.Image(label="building_clusters_map.png", height=600)
                with gr.TabItem("Metrics summary"):
                    summary_out = gr.JSON(label="Morphology + phylogeny summary")
                with gr.TabItem("Log"):
                    log_out = gr.Textbox(label="Pipeline log", lines=20, max_lines=40)
            output_path_msg = gr.Markdown()
    
    run_btn.click(
        fn=run_morphology,
        inputs=[buildings_in, boundary_in, extent_in,
                sad_id_in, sad_name_in, typology_in, anchor_in,
                clusters_in],
        outputs=[fg_out, dendro_out, map_out, summary_out, output_path_msg, log_out],
    )


if __name__ == '__main__':
    demo.launch(
        server_name='127.0.0.1',
        server_port=7860,
        show_error=True,
    )
