"""
module_02_cv_extractor.py

Extracts the morphological signature of a SAD in two complementary ways:

  1. PER-BUILDING (vector-native): For every footprint polygon, compute
     area, perimeter, compactness, elongation, orientation, hull ratio,
     nearest-neighbor distance, and density-of-neighbors. Written as new
     attribute columns on a buildings_cv.gpkg layer.

  2. WHOLE-FIELD (raster-level): On the rasterized figure-ground, compute
     coverage, component statistics, fractal dimension (box-counting),
     Hough-line alignment, GLCM texture features. Written to cv_metrics.json.

These two views of the same SAD complement each other - one captures
*what each piece looks like*, the other captures *what the field as a
whole feels like*. Both feed the unified district profile in Module 5.

USAGE
    python module_02_cv_extractor.py \\
        --source  ../data/source/per_sad/the_battery_atl \\
        --derived ../data/derived/per_sad/the_battery_atl
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from scipy import ndimage
from skimage import feature, transform as sk_transform
from skimage.feature import graycomatrix, graycoprops
from skimage.measure import moments, moments_central, moments_normalized, moments_hu
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest, FieldMetrics, CVMetrics


# ─── Per-building (vector) metrics ────────────────────────────────────────────

def _to_polygon(geom):
    """
    Normalize any building geometry to a single Polygon.
    
    Real-world OSM buildings include MultiPolygons (a stadium-and-annexes
    sharing one OSM relation, or buildings imported from cadastres with
    multi-part footprints). MultiPolygons don't have a `.exterior` attribute,
    so any per-polygon iteration that assumes simple Polygons silently fails.
    
    For SAD analysis the largest part of a MultiPolygon is the main
    building footprint; smaller parts are usually annexes. Returning the
    largest sub-polygon is the right approximation for shape analysis.
    Returns None for empty or unusable geometries.
    """
    if geom is None or geom.is_empty:
        return None
    if hasattr(geom, 'exterior'):
        return geom  # already a simple Polygon
    if hasattr(geom, 'geoms'):  # MultiPolygon, GeometryCollection
        polys = [p for p in geom.geoms 
                 if hasattr(p, 'exterior') and not p.is_empty]
        if not polys:
            return None
        return max(polys, key=lambda p: p.area)
    return None


def _hu_moments(geom, raster_size: int = 64) -> list[float]:
    """
    Compute the 7 Hu invariant moments for a polygon by rasterizing it to a
    `raster_size`x`raster_size` image (centered, scaled to fit with margin).
    Returns log-transformed values so all 7 moments occupy a similar dynamic
    range - important for clustering, since raw Hu moments span ~20 orders
    of magnitude.
    
    Hu moments are invariant to translation, rotation, AND scale, so two
    buildings of different size but identical shape get the same Hu vector.
    This is what lets the phylogenetic tree group buildings by form rather
    than by absolute size.
    """
    try:
        poly = _to_polygon(geom)
        if poly is None:
            return [np.nan] * 7
        minx, miny, maxx, maxy = poly.bounds
        w, h = maxx - minx, maxy - miny
        if w <= 0 or h <= 0:
            return [np.nan] * 7
        # Scale to fit raster_size with 2-pixel margin
        scale = (raster_size - 4) / max(w, h)
        img = Image.new('L', (raster_size, raster_size), 0)
        draw = ImageDraw.Draw(img)
        # Note: index into the tuple instead of unpacking with `for x, y in ...`
        # because some OSM exports (e.g. Green Bay buildings) include a z
        # coordinate, making coords 3-tuples. Indexing works for both 2D and 3D.
        coords = [
            ((c[0] - minx) * scale + 2,
             raster_size - ((c[1] - miny) * scale + 2))  # flip y for image space
            for c in poly.exterior.coords
        ]
        draw.polygon(coords, fill=255)
        arr = np.asarray(img, dtype=float)
        if not arr.any():
            return [np.nan] * 7
        m = moments(arr, order=3)
        if m[0, 0] == 0:
            return [np.nan] * 7
        mc = moments_central(arr, center=(m[1, 0] / m[0, 0], m[0, 1] / m[0, 0]), order=3)
        mn = moments_normalized(mc, order=3)
        hu = moments_hu(mn)
        # Log-transform: -sign(h) * log10(|h| + eps) keeps sign, compresses range
        return [float(-np.sign(h) * np.log10(abs(h) + 1e-30)) for h in hu]
    except Exception:
        return [np.nan] * 7

def compute_vector_metrics(
    buildings: gpd.GeoDataFrame,
    utm_crs: str,
    neighbor_radius_m: float = 50.0,
) -> gpd.GeoDataFrame:
    """
    Compute per-building geometric features. Operates in the metric CRS so
    areas are in square meters and distances in meters, then writes results
    back as columns on the original GeoDataFrame (still in EPSG:4326).
    
    Feature families (closely tracking the RoweBot vocabulary of Witt 2016):
      - Scale-sensitive size:    area_m2, perimeter_m, equivalent_diameter_m,
                                  max_diameter_m, mrr_long_side_m, mrr_short_side_m
      - Compactness/convexity:   compactness, hull_ratio, solidity, roughness
      - Elongation/eccentricity: bbox_elongation, mrr_elongation, eccentricity
      - Orientation:             orientation_deg
      - Complexity:              vertex_count
      - Scale/rotation-invariant Hu moments: hu_1 ... hu_7 (log-transformed for stability)
      - Spatial context:         nearest_neighbor_m, neighbors_within_50m
    
    Hu moments are the same family of invariants Witt references - translation,
    rotation, and scale-invariant shape descriptors that let two buildings of
    different size but same morphology cluster together.
    """
    metric = buildings.to_crs(utm_crs).copy()
    
    # ─── Scale-sensitive size ─────────────────────────────────────────────────
    metric['area_m2'] = metric.geometry.area
    metric['perimeter_m'] = metric.geometry.length
    metric['equivalent_diameter_m'] = 2.0 * np.sqrt(metric['area_m2'] / np.pi)
    
    # ─── Compactness, convexity, solidity ─────────────────────────────────────
    # Compactness (Polsby-Popper): 4πA / P². 1.0 = perfect circle, lower = elongated.
    metric['compactness'] = (
        (4 * np.pi * metric['area_m2']) / (metric['perimeter_m'] ** 2 + 1e-9)
    )
    # Hull ratio: area / convex_hull.area. Low = L/U/courtyard shapes.
    hull = metric.geometry.convex_hull
    metric['hull_ratio'] = metric['area_m2'] / (hull.area + 1e-9)
    # Solidity: area / bbox_area. Distinct from hull_ratio: catches notched rectangles.
    bounds = metric.geometry.bounds
    bb_w = bounds['maxx'] - bounds['minx']
    bb_h = bounds['maxy'] - bounds['miny']
    metric['solidity'] = metric['area_m2'] / (bb_w * bb_h + 1e-9)
    # Roughness: actual perimeter / hull perimeter. > 1 = boundary has detail.
    metric['roughness'] = metric['perimeter_m'] / (hull.length + 1e-9)
    
    # ─── Elongation ───────────────────────────────────────────────────────────
    metric['bbox_elongation'] = (
        np.maximum(bb_w, bb_h) / (np.minimum(bb_w, bb_h) + 1e-9)
    )
    
    # Per-polygon features that need iteration: MRR, eccentricity, vertex_count,
    # max_diameter, Hu moments. _to_polygon() converts MultiPolygons (common in
    # OSM building data, e.g. stadium-with-annexes) to their largest sub-polygon
    # so .exterior access works.
    mrr_elong, mrr_orient = [], []
    mrr_long, mrr_short = [], []
    eccentricity, vertex_count, max_diameter = [], [], []
    hu_features = []  # list of 7-tuples
    n_multi = 0  # diagnostic: how many were MultiPolygons
    
    for geom_raw in metric.geometry:
        poly = _to_polygon(geom_raw)
        if poly is None:
            mrr_elong.append(np.nan); mrr_orient.append(np.nan)
            mrr_long.append(np.nan); mrr_short.append(np.nan)
            eccentricity.append(np.nan); vertex_count.append(0); max_diameter.append(np.nan)
            hu_features.append([np.nan] * 7)
            continue
        
        if geom_raw is not poly:  # was a MultiPolygon
            n_multi += 1
        
        # Minimum-rotated-rectangle
        try:
            mrr = poly.minimum_rotated_rectangle
            coords = list(mrr.exterior.coords)[:4]
            edge_lens = [
                np.hypot(coords[i][0] - coords[(i+1) % 4][0],
                         coords[i][1] - coords[(i+1) % 4][1])
                for i in range(4)
            ]
            sides = sorted(edge_lens, reverse=True)
            long_s = sides[0] if sides[0] > 0 else 1e-9
            short_s = sides[2] if sides[2] > 0 else 1e-9
            mrr_long.append(long_s); mrr_short.append(short_s)
            mrr_elong.append(long_s / short_s)
            
            i_long = np.argmax(edge_lens)
            dx = coords[(i_long + 1) % 4][0] - coords[i_long][0]
            dy = coords[(i_long + 1) % 4][1] - coords[i_long][1]
            mrr_orient.append(np.degrees(np.arctan2(dy, dx)) % 180)
        except Exception:
            mrr_long.append(np.nan); mrr_short.append(np.nan)
            mrr_elong.append(np.nan); mrr_orient.append(np.nan)
            sides = [np.nan, np.nan, np.nan, np.nan]
        
        # Eccentricity from fitted ellipse (using MRR axes as a quick proxy):
        # e = sqrt(1 - (b/a)^2)   where a = semi-major, b = semi-minor
        try:
            a, b = sides[0] / 2.0, sides[2] / 2.0
            ecc = np.sqrt(1.0 - (b / max(a, 1e-9)) ** 2) if a > 0 else 0
            eccentricity.append(float(ecc))
        except Exception:
            eccentricity.append(np.nan)
        
        # Vertex count after light simplification (drops collinear points)
        try:
            simp = _to_polygon(poly.simplify(0.5))
            vertex_count.append(len(simp.exterior.coords) - 1 if simp else 0)
        except Exception:
            vertex_count.append(0)
        
        # Maximum diameter: longest distance between any two boundary points.
        try:
            hull_coords = np.array(poly.convex_hull.exterior.coords[:-1])
            if len(hull_coords) >= 2:
                d = np.max(np.sqrt(
                    ((hull_coords[:, None] - hull_coords[None, :]) ** 2).sum(axis=2)
                ))
                max_diameter.append(float(d))
            else:
                max_diameter.append(np.nan)
        except Exception:
            max_diameter.append(np.nan)
        
        # Hu moments (7 scale/rotation/translation-invariant shape descriptors)
        hu_features.append(_hu_moments(poly))
    
    if n_multi > 0:
        print(f"  note: {n_multi} of {len(buildings)} buildings were MultiPolygons; "
              f"used largest part for shape analysis")
    
    metric['mrr_elongation'] = mrr_elong
    metric['orientation_deg'] = mrr_orient
    metric['mrr_long_side_m'] = mrr_long
    metric['mrr_short_side_m'] = mrr_short
    metric['eccentricity'] = eccentricity
    metric['vertex_count'] = vertex_count
    metric['max_diameter_m'] = max_diameter
    hu_arr = np.array(hu_features)
    for i in range(7):
        metric[f'hu_{i+1}'] = hu_arr[:, i]
    
    # ─── Spatial context ──────────────────────────────────────────────────────
    sindex = metric.sindex
    centroids = metric.geometry.centroid
    
    nn_dist = []
    nn_count = []
    for idx, geom in enumerate(metric.geometry):
        c = centroids.iloc[idx]
        possible = list(sindex.query(c.buffer(neighbor_radius_m * 2)))
        possible = [p for p in possible if p != idx]
        
        if not possible:
            nn_dist.append(np.nan)
            nn_count.append(0)
            continue
        
        dists = [geom.distance(metric.geometry.iloc[p]) for p in possible]
        nn_dist.append(min(dists))
        nn_count.append(sum(1 for d in dists if d <= neighbor_radius_m))
    
    metric['nearest_neighbor_m'] = nn_dist
    metric[f'neighbors_within_{int(neighbor_radius_m)}m'] = nn_count
    
    # Copy all CV columns back onto the original (EPSG:4326) frame
    cv_cols = [
        # Size
        'area_m2', 'perimeter_m', 'equivalent_diameter_m', 'max_diameter_m',
        'mrr_long_side_m', 'mrr_short_side_m',
        # Compactness / convexity / solidity
        'compactness', 'hull_ratio', 'solidity', 'roughness',
        # Elongation / eccentricity
        'bbox_elongation', 'mrr_elongation', 'eccentricity',
        # Orientation
        'orientation_deg',
        # Complexity
        'vertex_count',
        # Hu moments (rotation+scale+translation invariant)
        'hu_1', 'hu_2', 'hu_3', 'hu_4', 'hu_5', 'hu_6', 'hu_7',
        # Spatial context
        'nearest_neighbor_m', f'neighbors_within_{int(neighbor_radius_m)}m',
    ]
    out = buildings.copy()
    for col in cv_cols:
        out[col] = metric[col].values
    return out


# ─── Whole-field (raster) metrics ─────────────────────────────────────────────

def box_counting_fd(mask: np.ndarray, sizes: list[int] | None = None) -> float:
    """
    Box-counting fractal dimension of the binary mask.
    
    For figure-ground analysis, this captures spatial complexity at
    multiple scales - high FD means the built form has detail at many
    scales (e.g. dense urban core with varied massing); low FD means
    the form is dominated by a few large simple shapes (e.g. suburban
    sea-of-parking).
    """
    if sizes is None:
        sizes = [2, 4, 8, 16, 32, 64, 128, 256]
    h, w = mask.shape
    counts = []
    used_sizes = []
    for s in sizes:
        if s > min(h, w):
            break
        n = 0
        for i in range(0, h, s):
            for j in range(0, w, s):
                if mask[i:i+s, j:j+s].any():
                    n += 1
        if n > 0:
            counts.append(n)
            used_sizes.append(s)
    if len(used_sizes) < 2:
        return 0.0
    log_sizes = np.log(used_sizes)
    log_counts = np.log(counts)
    # FD is the negative slope of log(N) vs log(s)
    return float(-np.polyfit(log_sizes, log_counts, 1)[0])


def compute_field_metrics(mask: np.ndarray) -> FieldMetrics:
    """Whole-image metrics for a binary figure-ground mask."""
    coverage = float((mask > 0).mean())
    
    # Connected components (the figure)
    labeled, n_comp = ndimage.label(mask)
    if n_comp > 0:
        sizes = ndimage.sum(mask, labeled, range(1, n_comp + 1))
        mean_sz = float(sizes.mean())
        med_sz = float(np.median(sizes))
        max_sz = float(sizes.max())
    else:
        mean_sz = med_sz = max_sz = 0.0
    
    # Largest void (the ground)
    void = (1 - mask).astype(np.uint8)
    void_labeled, n_void = ndimage.label(void)
    if n_void > 0:
        void_sizes = ndimage.sum(void, void_labeled, range(1, n_void + 1))
        largest_void = float(void_sizes.max())
    else:
        largest_void = 0.0
    
    fd = box_counting_fd(mask)
    
    # Hough-line alignment: how axially-organized is the field?
    edges = feature.canny(mask.astype(float), sigma=1.0)
    h, theta, d = sk_transform.hough_line(edges)
    accum, angles, dists = sk_transform.hough_line_peaks(
        h, theta, d, threshold=0.3 * h.max() if h.max() > 0 else 1, num_peaks=50
    )
    line_count = int(len(angles))
    if line_count > 0:
        ang_deg = np.degrees(angles) % 180
        # Score how concentrated lines are around 0° (horizontal) and 90° (vertical)
        h_score = float(np.mean(np.exp(-((ang_deg - 0) ** 2) / 200) +
                                np.exp(-((ang_deg - 180) ** 2) / 200)) / 2)
        v_score = float(np.mean(np.exp(-((ang_deg - 90) ** 2) / 200)))
    else:
        h_score = v_score = 0.0
    
    # GLCM texture features on a downsampled grayscale
    small = (mask * 255).astype(np.uint8)
    if small.shape[0] > 256:
        small = small[::small.shape[0] // 256, ::small.shape[1] // 256]
    glcm = graycomatrix(small, distances=[1], angles=[0, np.pi/2],
                        levels=256, symmetric=True, normed=True)
    glcm_contrast = float(graycoprops(glcm, 'contrast').mean())
    glcm_homog = float(graycoprops(glcm, 'homogeneity').mean())
    glcm_energy = float(graycoprops(glcm, 'energy').mean())
    
    return FieldMetrics(
        coverage=coverage,
        component_count=n_comp,
        mean_component_size_px=mean_sz,
        median_component_size_px=med_sz,
        max_component_size_px=max_sz,
        largest_void_px=largest_void,
        fractal_dimension=fd,
        hough_line_count=line_count,
        horizontal_alignment_score=h_score,
        vertical_alignment_score=v_score,
        glcm_contrast=glcm_contrast,
        glcm_homogeneity=glcm_homog,
        glcm_energy=glcm_energy,
    )


# ─── Orchestration ────────────────────────────────────────────────────────────

def process_sad(source_dir: Path, derived_dir: Path) -> CVMetrics:
    # Load manifest (must have been produced by Module 1)
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"manifest.json not found at {manifest_path}. Run Module 1 first."
        )
    manifest = Manifest.model_validate_json(manifest_path.read_text())
    
    # Load buildings (from source GeoJSON) + mask (from Module 1 output)
    buildings_path = source_dir / 'buildings.geojson'
    if not buildings_path.exists():
        raise FileNotFoundError(f"Expected buildings.geojson in {source_dir}")
    buildings = gpd.read_file(buildings_path).to_crs("EPSG:4326")
    mask = np.load(derived_dir / 'figureground_mask.npy')
    
    # Vector metrics -> save as buildings_cv.gpkg (Module 5 will join programs onto this)
    print(f"  Computing per-building metrics on {len(buildings)} polygons...")
    buildings_cv = compute_vector_metrics(buildings, manifest.crs_metric)
    buildings_cv.to_file(derived_dir / 'buildings_cv.gpkg', driver='GPKG', layer='buildings')
    
    # Field metrics
    print(f"  Computing whole-field metrics on {mask.shape} mask...")
    field = compute_field_metrics(mask)
    
    # Build the CV summary
    cv_metrics = CVMetrics(
        sad_id=manifest.sad_id,
        field=field,
        building_count=len(buildings_cv),
        median_area_m2=float(buildings_cv['area_m2'].median()),
        median_compactness=float(buildings_cv['compactness'].median()),
        median_elongation=float(buildings_cv['mrr_elongation'].median()),
        median_neighbor_distance_m=float(buildings_cv['nearest_neighbor_m'].median()),
        p25_area_m2=float(buildings_cv['area_m2'].quantile(0.25)),
        p75_area_m2=float(buildings_cv['area_m2'].quantile(0.75)),
    )
    
    with open(derived_dir / 'cv_metrics.json', 'w') as f:
        f.write(cv_metrics.model_dump_json(indent=2))
    
    return cv_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True,
                        help='Source directory containing buildings.geojson')
    parser.add_argument('--derived', type=Path, required=True)
    args = parser.parse_args()
    
    cv = process_sad(args.source, args.derived)
    print(f"[OK] {cv.sad_id}")
    print(f"  coverage: {cv.field.coverage:.3f}")
    print(f"  components: {cv.field.component_count}")
    print(f"  fractal dim: {cv.field.fractal_dimension:.3f}")
    print(f"  median building area: {cv.median_area_m2:.0f} m²")


if __name__ == '__main__':
    main()
