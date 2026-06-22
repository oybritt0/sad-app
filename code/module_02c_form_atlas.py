"""
module_02c_form_atlas.py

Generates a morphological atlas: a dense grid of every building in the SAD,
ordered by phylogenetic similarity (dendrogram leaf order), colored by
cluster.

OUTPUTS (both produced per run, paired so you can pick the right one for context)
  form_atlas.png         raster, 8-bit RGB, good for slides/web
  form_atlas.svg         vector, infinitely scalable, opens in Illustrator
                         Inkscape, Affinity Designer, or any browser.
                         Polygons grouped by cluster so you can select-all
                         of one form family at once in a vector editor.

SCALE MODES
  - SHAPE-ONLY  (default)         form_atlas.{png,svg}
    Each building scaled to fill its tile. Matches Witt's Machine View of
    the City framing: you read form vocabulary, ignoring scale.
    
  - RELATIVE-SCALE (--preserve-scale)  form_atlas_linear.{png,svg}
    Every building rendered at a single global meters-per-pixel scale.
    Stadiums dominate; rowhouses look like dots. You read scale and form
    together — the size distribution becomes legible at a glance.

This is the third visualization in the morphology pipeline:
  - building_phylogeny.png      the dendrogram tree itself
  - building_clusters_map.png   form families in their geographic locations
  - form_atlas.{png,svg} (THIS) every shape side-by-side, in similarity order

USAGE
    # Shape-only (Witt-style) — produces form_atlas.png + form_atlas.svg
    python module_02c_form_atlas.py --derived <dir>
    
    # Relative-scale — produces form_atlas_linear.png + form_atlas_linear.svg
    python module_02c_form_atlas.py --derived <dir> --preserve-scale
    
    # Bigger tiles, fewer columns
    python module_02c_form_atlas.py --derived <dir> --columns 30 --tile-size 80
    
    # White background instead of black
    python module_02c_form_atlas.py --derived <dir> --background white
    
    # Show only one cluster (e.g., the stadium family)
    python module_02c_form_atlas.py --derived <dir> --only-cluster 2
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont
from scipy.cluster.hierarchy import leaves_list

sys.path.insert(0, str(Path(__file__).parent))


def _to_polygon(geom):
    """
    Normalize any geometry to a single Polygon. Same logic as Module 2 —
    for MultiPolygons (common in OSM), use the largest sub-polygon.
    """
    if geom is None or geom.is_empty:
        return None
    if hasattr(geom, 'exterior'):
        return geom
    if hasattr(geom, 'geoms'):
        polys = [p for p in geom.geoms if hasattr(p, 'exterior') and not p.is_empty]
        if not polys:
            return None
        return max(polys, key=lambda p: p.area)
    return None


# tab10 colors as hex strings — must stay in sync with the matplotlib cmap
# used in building_clusters_map.png, so SVG/PNG/QGIS all match.
TAB10_HEX = [
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
    '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
]


def _cluster_hex(cluster_id: int) -> str:
    """Return the tab10 hex color for a 1-indexed cluster_id."""
    return TAB10_HEX[(cluster_id - 1) % 10]


def _polygon_to_tile_coords(
    poly,
    tile_size: int,
    margin: int = 3,
    fixed_scale_px_per_m: float | None = None,
) -> list[tuple[float, float]] | None:
    """
    Compute tile-local pixel coordinates for a polygon's exterior ring.
    Returns None for unusable geometries; otherwise a list of (x, y) tuples
    in tile-local space (origin at top-left of the tile, y increases downward).
    
    Two scaling modes:
      - PER-BUILDING (default, fixed_scale_px_per_m=None):
          Polygon scaled to fill the tile (with margin). Witt-style.
      - GLOBAL (fixed_scale_px_per_m provided):
          A single meters-per-pixel scale applied to every building, so
          their relative physical size is preserved.
    """
    if poly is None:
        return None
    minx, miny, maxx, maxy = poly.bounds
    w, h = maxx - minx, maxy - miny
    if w <= 0 or h <= 0:
        return None
    
    if fixed_scale_px_per_m is not None:
        scale = fixed_scale_px_per_m
    else:
        avail = tile_size - 2 * margin
        scale = avail / max(w, h)
    
    scaled_w, scaled_h = w * scale, h * scale
    pad_x = (tile_size - scaled_w) / 2
    pad_y = (tile_size - scaled_h) / 2
    
    # Index into tuple (handles both 2D and 3D coords; some OSM exports have z)
    return [
        ((c[0] - minx) * scale + pad_x,
         tile_size - ((c[1] - miny) * scale + pad_y))  # flip y for image coords
        for c in poly.exterior.coords
    ]


def _rasterize_polygon_silhouette(
    poly,
    tile_size: int,
    margin: int = 3,
    fixed_scale_px_per_m: float | None = None,
) -> np.ndarray:
    """
    Render a polygon as a binary silhouette inside a `tile_size`x`tile_size` tile.
    Centered within its tile. See `_polygon_to_tile_coords` for the two
    scaling modes available.
    """
    coords = _polygon_to_tile_coords(poly, tile_size, margin, fixed_scale_px_per_m)
    img = Image.new('L', (tile_size, tile_size), 0)
    if coords and len(coords) >= 3:
        draw = ImageDraw.Draw(img)
        draw.polygon(coords, fill=255)
    return np.asarray(img) > 0


def _build_svg(
    width: int,
    height: int,
    bg_color_rgb: tuple[int, int, int] | None,
    polygons_by_cluster: dict[int, list[list[tuple[float, float]]]],
    cluster_labels: list[tuple[str, float, float]] | None,
    font_size: int,
    bg_name: str,
) -> str:
    """
    Compose the SVG document.
    
    bg_color_rgb:        background fill color, or None for transparent
                         (no background rect emitted, SVG renders see-through).
    polygons_by_cluster: cluster_id -> list of polygon vertex-lists in canvas
                         pixel coordinates (origin top-left, y down).
    cluster_labels:      list of (text, x, y) where x,y is the top-left anchor
                         of the label in canvas coordinates.
    
    Polygons are wrapped in <g id="cluster-N"> groups so a designer can
    select-all-of-a-cluster in Illustrator/Inkscape.
    """
    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
    )
    if bg_color_rgb is not None:
        bg_hex = '#%02x%02x%02x' % bg_color_rgb
        lines.append(f'  <rect width="{width}" height="{height}" fill="{bg_hex}"/>')
    
    for cluster_id in sorted(polygons_by_cluster.keys()):
        color = _cluster_hex(cluster_id)
        polys = polygons_by_cluster[cluster_id]
        lines.append(f'  <g id="cluster-{cluster_id}" fill="{color}">')
        for coords in polys:
            pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in coords)
            lines.append(f'    <polygon points="{pts}"/>')
        lines.append('  </g>')
    
    if cluster_labels:
        # On transparent or white bg use dark text + light shadow; on black use the reverse.
        if bg_name == 'black':
            text_color, shadow_color = '#f0f0f0', '#000000'
        else:
            text_color, shadow_color = '#1e1e1e', '#ffffff'
        lines.append(
            f'  <g id="cluster-labels" font-family="Arial,Helvetica,sans-serif" '
            f'font-weight="bold" font-size="{font_size}">'
        )
        for label, x, y in cluster_labels:
            y_baseline = y + font_size
            lines.append(
                f'    <text x="{x+1:.1f}" y="{y_baseline+1:.1f}" '
                f'fill="{shadow_color}">{label}</text>'
            )
            lines.append(
                f'    <text x="{x:.1f}" y="{y_baseline:.1f}" '
                f'fill="{text_color}">{label}</text>'
            )
        lines.append('  </g>')
    
    lines.append('</svg>')
    return '\n'.join(lines)


def _resolve_dendrogram_order(
    buildings_clustered: gpd.GeoDataFrame,
    linkage_matrix: np.ndarray,
) -> np.ndarray:
    """
    Get the order in which buildings should appear in the grid.
    
    The linkage matrix Z was built from buildings_cv rows where cluster_id > 0.
    leaves_list(Z) returns indices 0..N-1 referring to those clustered rows
    in the order their original feature matrix was passed to clustering.
    We map those back to positions in buildings_clustered.
    """
    leaf_idx = leaves_list(linkage_matrix)  # indices into the clustered subset
    valid_mask = buildings_clustered['cluster_id'].values > 0
    # The clustered subset, in the order it was passed to linkage:
    valid_positions = np.where(valid_mask)[0]
    # Map: i-th leaf -> position in full buildings_clustered frame
    return valid_positions[leaf_idx]


def generate_form_atlas(
    derived_dir: Path,
    columns: int = 50,
    tile_size: int = 50,
    background: str = 'black',
    only_cluster: int | None = None,
    label_clusters: bool = True,
    preserve_scale: bool = False,
) -> Path:
    """
    Build the atlas image and save it to derived_dir/form_atlas.png
    (or form_atlas_linear.png when preserve_scale=True).
    Returns the output path.
    """
    # ─── Load inputs ──────────────────────────────────────────────────────────
    buildings = gpd.read_file(derived_dir / 'buildings_clustered.gpkg', layer='buildings')
    if 'cluster_id' not in buildings.columns:
        raise ValueError("buildings_clustered.gpkg missing cluster_id column — "
                         "run Module 2b first.")
    Z = np.load(derived_dir / 'building_linkage.npy')
    
    # Reproject to a metric CRS so the silhouettes are spatially consistent
    metric_crs = buildings.estimate_utm_crs()
    buildings = buildings.to_crs(metric_crs)
    
    # Determine ordering
    leaf_order_positions = _resolve_dendrogram_order(buildings, Z)
    
    # Optional filter to just one cluster
    if only_cluster is not None:
        keep = buildings.iloc[leaf_order_positions]['cluster_id'].values == only_cluster
        leaf_order_positions = leaf_order_positions[keep]
        if len(leaf_order_positions) == 0:
            raise ValueError(f"No buildings in cluster {only_cluster}")
        print(f"  filtering to cluster {only_cluster}: {len(leaf_order_positions)} buildings")
    
    n = len(leaf_order_positions)
    rows = int(np.ceil(n / columns))
    print(f"  laying out {n} buildings in {rows}x{columns} grid "
          f"({tile_size}px tiles -> {columns*tile_size}x{rows*tile_size} image)")
    
    # ─── Compute global scale if preserving relative size ─────────────────────
    fixed_scale = None
    if preserve_scale:
        # Find the largest building dimension across all rendered buildings.
        # Using the max ensures no building overflows its tile.
        max_dim_m = 0.0
        for pos in leaf_order_positions:
            poly = _to_polygon(buildings.geometry.iloc[pos])
            if poly is None:
                continue
            minx, miny, maxx, maxy = poly.bounds
            d = max(maxx - minx, maxy - miny)
            if d > max_dim_m:
                max_dim_m = d
        if max_dim_m <= 0:
            raise ValueError("Could not compute a usable global scale.")
        # Reserve a small margin so even the biggest building has padding
        margin_px = 3
        avail_px = tile_size - 2 * margin_px
        fixed_scale = avail_px / max_dim_m
        print(f"  preserve_scale ON: global scale = {fixed_scale*100:.3f} px/m "
              f"(largest building = {max_dim_m:.1f} m fills its tile)")
    
    # ─── Color palette matching building_clusters_map.png ─────────────────────
    max_cluster = int(buildings['cluster_id'].max())
    cmap = plt.colormaps.get_cmap('tab10').resampled(max(max_cluster, 10))
    palette = np.zeros((max_cluster + 1, 3), dtype=np.uint8)
    for k in range(1, max_cluster + 1):
        rgb = np.array(cmap((k - 1) / max(max_cluster, 10) % 10)[:3]) * 255
        palette[k] = rgb.astype(np.uint8)
    
    # ─── Canvas ───────────────────────────────────────────────────────────────
    # Three background modes:
    #   black/white     -> 3-channel RGB canvas filled with bg color
    #   transparent     -> 4-channel RGBA canvas, alpha=0 everywhere
    # Building pixels carry alpha=255 in the RGBA case so they remain
    # fully visible against any backdrop the user composites onto.
    is_transparent = (background == 'transparent')
    if is_transparent:
        canvas = np.zeros((rows * tile_size, columns * tile_size, 4), dtype=np.uint8)
        bg_color = None  # not used in transparent mode
    elif background == 'black':
        bg_color = np.array([12, 12, 12], dtype=np.uint8)
        canvas = np.tile(bg_color, (rows * tile_size, columns * tile_size, 1))
    else:  # white
        bg_color = np.array([245, 245, 245], dtype=np.uint8)
        canvas = np.tile(bg_color, (rows * tile_size, columns * tile_size, 1))
    
    # ─── Render each tile (collect data for BOTH png and svg outputs) ─────────
    # polygons_by_cluster maps cluster_id -> [canvas_coords_list1, ...]
    # We'll feed this directly into the SVG writer after the loop completes.
    polygons_by_cluster: dict[int, list[list[tuple[float, float]]]] = {}
    n_rendered = 0
    
    for i, pos in enumerate(leaf_order_positions):
        row = i // columns
        col = i % columns
        geom = buildings.geometry.iloc[pos]
        cluster_id = int(buildings['cluster_id'].iloc[pos])
        poly = _to_polygon(geom)
        
        # Compute polygon coordinates in tile-local space (used for both outputs)
        tile_coords = _polygon_to_tile_coords(
            poly, tile_size, fixed_scale_px_per_m=fixed_scale
        )
        if not tile_coords or len(tile_coords) < 3:
            continue
        
        # Convert tile-local -> canvas coords
        x0, y0 = col * tile_size, row * tile_size
        canvas_coords = [(tx + x0, ty + y0) for tx, ty in tile_coords]
        
        # PNG path: rasterize to mask and apply color
        tile_img = Image.new('L', (tile_size, tile_size), 0)
        ImageDraw.Draw(tile_img).polygon(tile_coords, fill=255)
        mask = np.asarray(tile_img) > 0
        if not mask.any():
            continue
        y1, x1 = y0 + tile_size, x0 + tile_size
        if is_transparent:
            # Set RGB to cluster color AND alpha=255 for building pixels
            canvas[y0:y1, x0:x1][mask, :3] = palette[cluster_id]
            canvas[y0:y1, x0:x1][mask, 3] = 255
        else:
            tile = canvas[y0:y1, x0:x1]
            tile[mask] = palette[cluster_id]
            canvas[y0:y1, x0:x1] = tile
        
        # SVG path: collect coords for this cluster
        polygons_by_cluster.setdefault(cluster_id, []).append(canvas_coords)
        n_rendered += 1
    
    print(f"  rendered {n_rendered} silhouettes")
    
    # ─── Compute cluster labels (used by BOTH outputs) ────────────────────────
    cluster_labels: list[tuple[str, float, float]] = []
    if label_clusters and only_cluster is None:
        cluster_seq = buildings.iloc[leaf_order_positions]['cluster_id'].values
        font_size = max(10, tile_size // 4)
        i = 0
        while i < len(cluster_seq):
            j = i
            while j < len(cluster_seq) and cluster_seq[j] == cluster_seq[i]:
                j += 1
            if j - i >= 5:
                row_start = i // columns
                col_start = i % columns
                x = col_start * tile_size + 4
                y = row_start * tile_size + 2
                cluster_labels.append((f"c{int(cluster_seq[i])}", x, y))
            i = j
    else:
        font_size = max(10, tile_size // 4)
    
    # ─── PNG output: draw labels onto PIL image ───────────────────────────────
    img = Image.fromarray(canvas)
    if cluster_labels:
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()
        if background == 'black':
            text_color = (240, 240, 240)
            shadow_color = (0, 0, 0)
        else:
            # white OR transparent: dark text + light shadow reads on most surfaces
            text_color = (30, 30, 30)
            shadow_color = (255, 255, 255)
        for label, x, y in cluster_labels:
            draw.text((x + 1, y + 1), label, fill=shadow_color, font=font)
            draw.text((x, y), label, fill=text_color, font=font)
    
    suffix = '_linear' if preserve_scale else ''
    png_path = derived_dir / f'form_atlas{suffix}.png'
    img.save(png_path)
    
    # ─── SVG output: write polygons grouped by cluster ────────────────────────
    width_px = columns * tile_size
    height_px = rows * tile_size
    if is_transparent:
        svg_bg_arg = None
    else:
        svg_bg_arg = tuple(int(c) for c in bg_color)
    svg_text = _build_svg(
        width=width_px,
        height=height_px,
        bg_color_rgb=svg_bg_arg,
        polygons_by_cluster=polygons_by_cluster,
        cluster_labels=cluster_labels if cluster_labels else None,
        font_size=font_size,
        bg_name=background,
    )
    svg_path = derived_dir / f'form_atlas{suffix}.svg'
    svg_path.write_text(svg_text, encoding='utf-8')
    
    print(f"  PNG: {png_path.name}")
    print(f"  SVG: {svg_path.name} ({len(svg_text) // 1024} KB)")
    return png_path


def main():
    p = argparse.ArgumentParser(description="Generate Witt-style morphological atlas.")
    p.add_argument('--derived', type=Path, required=True,
                   help='Derived directory containing buildings_clustered.gpkg + building_linkage.npy')
    p.add_argument('--columns', type=int, default=50,
                   help='Grid columns (default 50 - good for ~2000+ buildings)')
    p.add_argument('--tile-size', type=int, default=50,
                   help='Tile size in pixels (default 50)')
    p.add_argument('--background', choices=['black', 'white', 'transparent'],
                   default='black',
                   help='Background fill: black (Witt aesthetic, default), '
                        'white (print/light slides), or transparent (PNG keeps '
                        'alpha, SVG omits background rect — use when compositing '
                        'over other artwork).')
    p.add_argument('--only-cluster', type=int, default=None,
                   help='Show only buildings from one cluster id')
    p.add_argument('--no-labels', action='store_true',
                   help='Skip cluster ID overlay text')
    p.add_argument('--preserve-scale', action='store_true',
                   help='Render every building at the same meters-per-pixel scale, '
                        'so a stadium dominates and a rowhouse appears small. '
                        'Output is form_atlas_linear.png instead of form_atlas.png. '
                        'Without this flag (default), each building is scaled to '
                        'fill its tile (shape-only view, Witt-style).')
    args = p.parse_args()
    
    out_path = generate_form_atlas(
        args.derived,
        columns=args.columns,
        tile_size=args.tile_size,
        background=args.background,
        only_cluster=args.only_cluster,
        label_clusters=not args.no_labels,
        preserve_scale=args.preserve_scale,
    )
    print(f"[OK] wrote {out_path}")


if __name__ == '__main__':
    main()
