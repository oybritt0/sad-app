"""
module_08_district_embedding.py

Builds a district-level latent-vibe map by extracting a unified feature vector
per SAD covering morphology, program, anchor structure, and demographics —
ALL spatially filtered to the SAD interior (not the wider canvas).

Each SAD becomes a single point in a ~27-dimensional feature space.
With N >= 3 SADs, we compute pairwise distances and PCA-project to 2D.

Outputs (in data/_comparisons/embedding_<timestamp>/):
  feature_matrix.csv                Raw feature values, one row per SAD
  feature_matrix_normalized.csv     Z-scored across SADs
  distance_matrix.csv               Pairwise cosine distances
  feature_signatures.{png,svg}      Parallel-coordinate plot per SAD
  distance_heatmap.{png,svg}        Symmetric heatmap with annotations
  pca_2d.{png,svg}                  PCA projection (rank-2 max for N=3)
  top_distinguishing_features.{png,svg}
                                    Per-pair: top features driving distance
  feature_groups_radar.{png,svg}    Mean per feature band: morphology /
                                    program / anchor / demographics
  vibe_embedding_summary.json       Everything machine-readable

USAGE
  python module_08_district_embedding.py \
      --data-dir <path> --sads <sad_id_1> <sad_id_2> ...

CRITICAL: requires that each SAD has been through M1-M5 plus M6c v2
(interior_exterior_signature.json). Census features included if
source/<sad>/census_blockgroups.gpkg exists for ALL SADs; otherwise
demographic features are skipped with a warning.
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import MultiPolygon

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from scipy.stats import skew, circmean
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_distances, euclidean_distances


ROSSETTI_CATEGORIES = [
    'sport', 'residential', 'hotel', 'retail_food_entertainment',
    'office', 'parking', 'open_space', 'other',
]

SAD_PALETTE = ['#1B2845', '#D97757', '#5C8B89', '#7C5E9C', '#D4A93A', '#6A4E7C']


# ─── Helpers ───────────────────────────────────────────────────────────

def to_polygon(geom):
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def shannon_entropy(values: list[float]) -> float:
    """Shannon entropy of a list of values (treated as un-normalized weights)."""
    arr = np.array([v for v in values if v is not None and v > 0],
                    dtype=float)
    if len(arr) == 0:
        return 0.0
    p = arr / arr.sum()
    return float(-np.sum(p * np.log(p + 1e-12)))


def rayleigh_concentration(orientations_deg: np.ndarray) -> float:
    """Rayleigh R statistic on orientations: 0 = uniform, 1 = perfectly aligned.
    Treats orientations as axial (modulo 180 deg)."""
    if len(orientations_deg) == 0:
        return 0.0
    radians = np.radians((orientations_deg % 180.0) * 2.0)
    R = np.sqrt(np.cos(radians).mean()**2 + np.sin(radians).mean()**2)
    return float(R)


def safe_div(num, den, default=0.0):
    try:
        v = num / den
        if np.isfinite(v):
            return float(v)
    except (TypeError, ZeroDivisionError, ValueError):
        pass
    return default


# ─── Feature extraction per SAD ────────────────────────────────────────

def extract_morphology_features(buildings_inside: gpd.GeoDataFrame,
                                  sad_area_m2: float) -> dict:
    """8 morphology features over SAD-interior buildings."""
    n = len(buildings_inside)
    if n == 0:
        return {k: 0.0 for k in [
            'morph_density_per_km2', 'morph_coverage', 'morph_median_area_m2',
            'morph_area_iqr', 'morph_area_log_skew', 'morph_mean_compactness',
            'morph_orientation_R', 'morph_cluster_diversity',
        ]}
    
    areas = buildings_inside['area_m2'].dropna().values
    if len(areas) == 0:
        areas = np.array([0.0])
    
    # Density per km²
    density = n / (sad_area_m2 / 1e6) if sad_area_m2 > 0 else 0.0
    
    # Coverage = sum of building areas / SAD area
    coverage = safe_div(areas.sum(), sad_area_m2)
    
    # Area statistics
    median_area = float(np.median(areas))
    iqr = float(np.percentile(areas, 75) - np.percentile(areas, 25))
    log_skew = float(skew(np.log1p(areas))) if len(areas) > 2 else 0.0
    
    # Compactness (4*pi*A / P^2), per building, averaged
    if 'perimeter_m' in buildings_inside.columns:
        peri = buildings_inside['perimeter_m'].fillna(0).values
    else:
        # Compute perimeter from geometry as fallback
        peri = buildings_inside.geometry.length.values
    valid = (peri > 0) & (areas > 0)
    if valid.any():
        comp = 4 * np.pi * areas[valid] / (peri[valid]**2)
        mean_compactness = float(np.mean(comp))
    else:
        mean_compactness = 0.0
    
    # Orientation concentration
    if 'orientation_deg' in buildings_inside.columns:
        orientations = buildings_inside['orientation_deg'].dropna().values
        R = rayleigh_concentration(orientations)
    else:
        R = 0.0
    
    # Cluster diversity (Shannon entropy of cluster_id distribution)
    if 'cluster_id' in buildings_inside.columns:
        counts = buildings_inside['cluster_id'].value_counts().values
        cluster_diversity = shannon_entropy(counts)
    else:
        cluster_diversity = 0.0
    
    return {
        'morph_density_per_km2': density,
        'morph_coverage': coverage,
        'morph_median_area_m2': median_area,
        'morph_area_iqr': iqr,
        'morph_area_log_skew': log_skew,
        'morph_mean_compactness': mean_compactness,
        'morph_orientation_R': R,
        'morph_cluster_diversity': cluster_diversity,
    }


def extract_program_features(interior_ext_data: dict) -> dict:
    """8 program % features + 1 program diversity feature from SAD interior."""
    if not interior_ext_data or 'categories' not in interior_ext_data:
        return {f'prog_pct_{c}': 0.0 for c in ROSSETTI_CATEGORIES} | \
               {'prog_diversity': 0.0}
    
    pcts = {row['category']: float(row.get('interior_pct', 0.0))
            for row in interior_ext_data['categories']}
    features = {f'prog_pct_{c}': pcts.get(c, 0.0)
                for c in ROSSETTI_CATEGORIES}
    features['prog_diversity'] = shannon_entropy(list(pcts.values()))
    return features


def extract_anchor_features(polar_data: dict,
                             buildings_inside: gpd.GeoDataFrame,
                             sad_boundary,
                             buildings_full: gpd.GeoDataFrame) -> dict:
    """3 anchor features. Only counts anchors whose centroid is inside SAD."""
    features = {
        'anchor_count_inside': 0.0,
        'anchor_max_area_ratio': 0.0,
        'anchor_size_concentration': 0.0,
    }
    
    if not polar_data or 'anchors' not in polar_data:
        return features
    
    anchors = polar_data.get('anchors', [])
    if not anchors:
        return features
    
    # For each anchor, check if its building is inside the SAD
    inside_anchors = []
    for a in anchors:
        bid = a.get('building_id')
        # Look up the building in the full canvas; check if its centroid
        # falls inside the SAD polygon
        if bid is None:
            continue
        match = buildings_full[buildings_full['building_id'] == bid]
        if len(match) == 0:
            continue
        centroid = match.geometry.iloc[0].centroid
        if centroid.within(sad_boundary):
            inside_anchors.append(a)
    
    n_inside = len(inside_anchors)
    features['anchor_count_inside'] = float(n_inside)
    
    if n_inside == 0:
        return features
    
    areas = sorted([a.get('area_m2', 0) for a in inside_anchors], reverse=True)
    total_building_area = buildings_inside['area_m2'].sum() \
                            if len(buildings_inside) > 0 else 1.0
    
    # max anchor / total building area
    features['anchor_max_area_ratio'] = safe_div(areas[0], total_building_area)
    
    # size concentration: top-1 / sum(top-3); 1.0 = single dominant anchor
    top3_sum = sum(areas[:3])
    features['anchor_size_concentration'] = safe_div(areas[0], top3_sum, 1.0)
    
    return features


def compute_sad_interior_demographics(census_bgs: gpd.GeoDataFrame,
                                       sad_boundary) -> dict:
    """Re-area-weight ACS block-group data to the SAD interior."""
    # Reproject if needed (assume both in same CRS already; defensive copy)
    bgs = census_bgs.copy()
    
    # Compute intersection area with SAD
    sad_gs = gpd.GeoSeries([sad_boundary], crs=bgs.crs)
    bgs['intersection'] = bgs.geometry.intersection(sad_boundary)
    bgs['intersection_area'] = bgs['intersection'].area
    bgs['bg_area'] = bgs.geometry.area
    bgs['weight'] = (bgs['intersection_area'] / bgs['bg_area']).clip(0, 1)
    
    # Filter to BGs that actually intersect
    bgs = bgs[bgs['weight'] > 0].copy()
    if len(bgs) == 0:
        return None
    
    # Population-weighted means for medians, weighted sums for counts
    def w_sum(col):
        if col not in bgs.columns:
            return None
        return float((bgs[col].fillna(0) * bgs['weight']).sum())
    
    def w_pop_weighted_mean(col):
        if col not in bgs.columns or 'total_pop' not in bgs.columns:
            return None
        vals = bgs[col]
        weights = bgs['total_pop'].fillna(0) * bgs['weight']
        valid = vals.notna() & (weights > 0)
        if not valid.any():
            return None
        return float((vals[valid] * weights[valid]).sum() / weights[valid].sum())
    
    total_pop = w_sum('total_pop')
    sad_area_km2 = sad_gs.area.iloc[0] / 1e6
    
    population_density = safe_div(total_pop, sad_area_km2) if total_pop else 0.0
    median_age = w_pop_weighted_mean('median_age') or 0.0
    median_hh_income = w_pop_weighted_mean('median_household_income') or 0.0
    median_home_value = w_pop_weighted_mean('median_home_value') or 0.0
    
    # Bachelor's+ rate
    edu_total = w_sum('edu_total_pop_25plus') or 0.0
    edu_bach_plus = sum(filter(None, [w_sum('edu_bachelors'),
                                        w_sum('edu_masters'),
                                        w_sum('edu_professional'),
                                        w_sum('edu_doctorate')]))
    pct_bachelors_plus = safe_div(edu_bach_plus, edu_total) * 100
    
    # Unemployment rate
    unemployed = w_sum('unemployed') or 0.0
    labor_force = w_sum('labor_force') or 0.0
    unemployment_rate = safe_div(unemployed, labor_force) * 100
    
    # Owner occupancy rate
    owner_occ = w_sum('owner_occupied') or 0.0
    occ_total = w_sum('occupied_total') or 0.0
    pct_owner_occ = safe_div(owner_occ, occ_total) * 100
    
    # Racial diversity (Shannon entropy across white, black, asian, hispanic, other)
    race_white = w_sum('race_white') or 0.0
    race_black = w_sum('race_black') or 0.0
    race_asian = w_sum('race_asian') or 0.0
    hispanic = w_sum('hispanic') or 0.0
    race_total = w_sum('race_total') or 0.0
    other = max(0.0, race_total - race_white - race_black - race_asian)
    racial_diversity = shannon_entropy([race_white, race_black, race_asian,
                                          hispanic, other])
    
    return {
        'demo_population_density': population_density,
        'demo_median_age': median_age,
        'demo_median_hh_income': median_hh_income,
        'demo_median_home_value': median_home_value,
        'demo_pct_bachelors_plus': pct_bachelors_plus,
        'demo_unemployment_rate': unemployment_rate,
        'demo_pct_owner_occupied': pct_owner_occ,
        'demo_racial_diversity': racial_diversity,
    }


# ─── Phase 2 extractors: NSI occupancy, demographics/economic (time-series), connectivity ───
import math as _math
import re as _re2

def _nsi_group(occtype):
    if not occtype: return 'other'
    _m = _re2.match(r'^([A-Z]+)(\d+)', str(occtype))
    if not _m: return 'other'
    fam, num = _m.group(1), int(_m.group(2))
    if fam == 'RES':
        return 'hotel/institutional' if num == 4 else 'residential'
    if fam == 'COM':
        if num == 1: return 'retail'
        if num == 4: return 'office'
        if num in (8, 9): return 'entertainment'
        if num in (2, 3): return 'industrial/service'
        return 'civic/medical'
    if fam in ('IND', 'AGR'): return 'industrial/service'
    if fam in ('REL', 'EDU', 'GOV'): return 'civic/medical'
    return 'other'

def _program_group(props):
    dom = str(props.get('dominant_program_inside') or '').strip().lower()
    prg = str(props.get('programs_inside') or '').strip().lower()
    if dom == 'parking' or prg == 'parking':
        return 'parking'
    return _nsi_group(props.get('occtype'))

_OCC_CATS = ['residential', 'hotel/institutional', 'retail', 'office', 'entertainment',
             'industrial/service', 'civic/medical', 'parking', 'other']
def _occ_key(c): return 'progocc_pct_' + c.replace('/', '_').replace(' ', '_')

def _shannon(vals):
    tot = sum(v for v in vals if v)
    if tot <= 0: return 0.0
    h = 0.0
    for v in vals:
        if v and v > 0:
            pp = v / tot; h -= pp * _math.log(pp)
    return h

def extract_building_occupancy_features(buildings_inside):
    """Area-weighted NSI-occupancy program mix over SAD-interior buildings.
    Program = NSI occtype for all buildings; POI tag used only to identify parking."""
    area_by = {c: 0.0 for c in _OCC_CATS}
    # buildings_inside is a GeoDataFrame; iterate rows
    for _, row in buildings_inside.iterrows():
        props = row.to_dict()
        area = props.get('area_m2')
        if area is None:
            try: area = float(row.geometry.area)
            except Exception: area = None
        if area is None or area <= 0: continue
        area_by[_program_group(props)] += float(area)
    total = sum(area_by.values())
    feats = {}
    for c in _OCC_CATS:
        feats[_occ_key(c)] = (100.0 * area_by[c] / total) if total > 0 else 0.0
    feats['progocc_diversity'] = _shannon(list(area_by.values()))
    return feats

def _slope(years, vals):
    pts = [(y, v) for y, v in zip(years, vals)
           if v is not None and not (isinstance(v, float) and _math.isnan(v))]
    if len(pts) < 2: return 0.0
    n = len(pts); sx = sum(a for a, _ in pts); sy = sum(b for _, b in pts)
    sxx = sum(a * a for a, _ in pts); sxy = sum(a * b for a, b in pts)
    den = n * sxx - sx * sx
    return ((n * sxy - sx * sy) / den) if den else 0.0

def extract_demographic_economic_features(derived, sad_area_km2):
    """Level (latest year) + trend (2013-2023 slope) from census_timeseries.json summaries."""
    fp = derived / 'census_timeseries.json'
    if not fp.exists(): return {}
    try:
        ts = json.loads(fp.read_text())
    except Exception:
        return {}
    summ = ts.get('summaries', {})
    years = sorted(int(y) for y in summ.keys())
    if not years: return {}
    area = sad_area_km2 if sad_area_km2 and sad_area_km2 > 0 else 1.0
    L = summ[str(years[-1])]
    def ser(key): return [summ[str(y)].get(key) for y in years]
    f = {}
    pop = L.get('estimated_population')
    f['demo_pop_density'] = (pop / area) if pop else 0.0
    f['demo_median_age'] = L.get('median_age_pop_weighted') or 0.0
    f['demo_pct_owner_occupied'] = L.get('pct_owner_occupied') or 0.0
    f['econ_median_income'] = L.get('median_household_income_pop_weighted') or 0.0
    f['econ_median_home_value'] = L.get('median_home_value_pop_weighted') or 0.0
    f['econ_median_gross_rent'] = (L.get('median_gross_rent_pop_weighted')
                                   or L.get('median_gross_rent') or 0.0)
    f['demo_pop_trend'] = _slope(years, ser('estimated_population'))
    f['demo_ownership_trend'] = _slope(years, ser('pct_owner_occupied'))
    f['econ_income_trend'] = _slope(years, ser('median_household_income_pop_weighted'))
    f['econ_home_value_trend'] = _slope(years, ser('median_home_value_pop_weighted'))
    return f

def extract_connectivity_features(derived, sad_area_km2):
    """Connectivity composite from per-district summary JSONs, area-normalized."""
    def _load(rel):
        fp = derived / rel
        try: return json.loads(fp.read_text())
        except Exception: return {}
    ts = _load('transit/transit_summary.json')
    tr = _load('transit/transit_routes_summary.json')
    los = _load('transit_los/transit_los_summary.json')
    wk = _load('walkshed/walkshed_summary.json')
    sc = _load('street_centrality_summary.json')
    area = sad_area_km2 if sad_area_km2 and sad_area_km2 > 0 else 1.0
    f = {}
    st = ts.get('total_stations')
    f['conn_stations_per_km2'] = (st / area) if st else 0.0
    f['conn_routes_serving'] = float(tr.get('routes_serving_sad') or los.get('routes_serving') or 0.0)
    f['conn_trips_per_day'] = float(los.get('trips_per_day') or 0.0)
    hw = los.get('median_route_headway_min')
    f['conn_headway_inv'] = (60.0 / hw) if hw and hw > 0 else 0.0
    f['conn_span_hours'] = float(los.get('span_hours') or 0.0)
    f['conn_stops_in_buffer'] = float(los.get('stops_in_buffer') or 0.0)
    si, sb = los.get('stops_inside'), los.get('stops_in_buffer')
    f['conn_stops_inside_ratio'] = (si / sb) if si and sb else 0.0
    wa = 0.0
    for w in (wk.get('walksheds') or []):
        for k in ('area_sqft', 'area_m2', 'area_acres'):
            if w.get(k):
                wa = max(wa, float(w[k])); break
    f['conn_walkshed_area'] = wa
    f['conn_street_mean_centrality'] = float(sc.get('mean_centrality_nonzero')
                                             or sc.get('mean_centrality') or 0.0)
    fc = sc.get('feature_count')
    f['conn_street_density'] = (fc / area) if fc else 0.0
    return f


def gather_sad_features(data_dir: Path, sad_id: str,
                          include_demographics: bool = True) -> dict:
    """Compute the full feature vector for one SAD, all SAD-interior."""
    sad_path = data_dir / sad_id
    source = sad_path / 'source'
    derived = sad_path / 'derived'
    
    # Load SAD boundary
    sad_boundary_path = source / 'sad_boundary.geojson'
    if not sad_boundary_path.exists():
        raise FileNotFoundError(f"Missing {sad_boundary_path}")
    sad_gdf = gpd.read_file(sad_boundary_path)
    try:
        sad_boundary = sad_gdf.union_all()
    except AttributeError:
        sad_boundary = sad_gdf.unary_union
    
    # Load buildings (full canvas) and project to metric CRS
    # Phase 2: read .geojson (the .gpkg is no longer produced; only .gpkg.bak remains)
    buildings_path = derived / 'buildings_enriched.geojson'
    if not buildings_path.exists():
        _gpkg = derived / 'buildings_enriched.gpkg'
        if _gpkg.exists():
            buildings_full = gpd.read_file(_gpkg, layer='buildings')
        else:
            raise FileNotFoundError(f"Missing {buildings_path}")
    else:
        buildings_full = gpd.read_file(buildings_path)
    metric_crs = buildings_full.estimate_utm_crs()
    buildings_full = buildings_full.to_crs(metric_crs)
    
    # Reproject SAD boundary to metric for area calcs
    sad_boundary_metric = (gpd.GeoSeries([sad_boundary], crs=sad_gdf.crs)
                            .to_crs(metric_crs).iloc[0])
    sad_area_m2 = float(sad_boundary_metric.area)
    
    # Filter buildings to SAD-interior by centroid
    in_sad_mask = buildings_full.geometry.centroid.within(sad_boundary_metric)
    buildings_inside = buildings_full[in_sad_mask].copy()
    
    # ─── Morphology features ─────────────────────────────────────────
    morph_feats = extract_morphology_features(buildings_inside, sad_area_m2)

    # ─── Phase 2: NSI occupancy, demographics/economic, connectivity ───
    occ_feats = extract_building_occupancy_features(buildings_inside)
    _area_km2 = sad_area_m2 / 1e6
    demo_econ_feats = extract_demographic_economic_features(derived, _area_km2)
    conn_feats = extract_connectivity_features(derived, _area_km2)
    
    # ─── Program features (from interior_exterior_signature) ─────────
    iex_path = derived / 'interior_exterior_signature.json'
    iex_data = {}
    if iex_path.exists():
        iex_data = json.loads(iex_path.read_text())
    program_feats = extract_program_features(iex_data)
    # Residential is sourced from the OSM building tag (Module 3b), not POIs.
    # Override the residential program share with the building-footprint share.
    # NOTE: intentional denominator difference — this dim is a building-area
    # share while other prog_pct_* are POI shares (honest signal beats a zero).
    _pf_path = derived / 'plan_footprint.json'
    if _pf_path.exists():
        try:
            _pf = json.loads(_pf_path.read_text())
            _res_b = (_pf.get('per_layer', {})
                         .get('residential_buildings', {})
                         .get('pct_of_building_footprint'))
            if _res_b is not None:
                program_feats['prog_pct_residential'] = float(_res_b)
        except Exception:
            pass
    
    # ─── Anchor features ─────────────────────────────────────────────
    polar_path = derived / 'anchor_polar_plots.json'
    polar_data = {}
    if polar_path.exists():
        polar_data = json.loads(polar_path.read_text())
    anchor_feats = extract_anchor_features(polar_data, buildings_inside,
                                             sad_boundary_metric,
                                             buildings_full)
    
    # ─── Demographic features ────────────────────────────────────────
    demo_feats = {}
    if include_demographics:
        census_gpkg = source / 'census_blockgroups.gpkg'
        if census_gpkg.exists():
            bgs = gpd.read_file(census_gpkg, layer='blockgroups')
            bgs = bgs.to_crs(metric_crs)
            demo_feats = compute_sad_interior_demographics(bgs,
                                                            sad_boundary_metric)
            if demo_feats is None:
                demo_feats = {}
    
    # ─── Identity metadata (kept separate from feature vector) ───────
    profile_path = derived / 'district_profile.json'
    name = sad_id
    typology = 'unknown'
    anchor_venue = 'unknown'
    if profile_path.exists():
        prof = json.loads(profile_path.read_text())
        name = prof.get('sad_name', sad_id)
        typology = prof.get('typology', 'unknown')
        anchor_venue = prof.get('anchor_venue', 'unknown')
    
    return {
        'sad_id': sad_id,
        'name': name,
        'typology': typology,
        'anchor_venue': anchor_venue,
        'sad_area_km2': sad_area_m2 / 1e6,
        'buildings_inside_count': len(buildings_inside),
        'features': {**morph_feats, **program_feats, **anchor_feats,
                      **demo_feats, **occ_feats, **demo_econ_feats, **conn_feats},
    }


# ─── Analysis ─────────────────────────────────────────────────────────

def build_feature_matrix(sads_data: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Stack each SAD's feature dict into a DataFrame. Index is sad_id (unique).
    Returns (feature_df, sad_id -> display_name mapping)."""
    rows = []
    name_lookup = {}
    for sd in sads_data:
        row = {'sad_id': sd['sad_id']}
        row.update(sd['features'])
        rows.append(row)
        name_lookup[sd['sad_id']] = sd['name']
    df = pd.DataFrame(rows).set_index('sad_id')
    return df, name_lookup


def normalize_features(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Z-score each feature across SADs. Constant columns get 0."""
    feat_cols = [c for c in df_raw.columns if c != 'sad_id']
    df_feat = df_raw[feat_cols].copy()
    
    # Handle constant columns gracefully
    std = df_feat.std(axis=0)
    mean = df_feat.mean(axis=0)
    normalized = df_feat.copy()
    for c in feat_cols:
        if std[c] > 1e-10:
            normalized[c] = (df_feat[c] - mean[c]) / std[c]
        else:
            normalized[c] = 0.0
    # Phase 2: districts missing census/transit features have NaN cells; fill with 0.0
    # (the mean in z-space) so they sit neutrally on those axes rather than breaking
    # cosine distances / in-browser PCA.
    normalized = normalized.fillna(0.0)
    return normalized


def compute_distance_matrix(df_norm: pd.DataFrame) -> pd.DataFrame:
    """Pairwise cosine distance between SAD feature vectors."""
    X = df_norm.values
    D = cosine_distances(X)
    return pd.DataFrame(D, index=df_norm.index, columns=df_norm.index)


def pca_project(df_norm: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """Project N SADs to 2D via PCA. For N=3, this is rank-2 exact."""
    n = len(df_norm)
    if n < 2:
        raise ValueError("Need >= 2 SADs to project")
    n_comp = min(2, n - 1, df_norm.shape[1])
    pca = PCA(n_components=n_comp)
    coords = pca.fit_transform(df_norm.values)
    # Pad to 2D if needed
    if coords.shape[1] < 2:
        coords = np.hstack([coords, np.zeros((coords.shape[0], 1))])
    return pd.DataFrame(coords, index=df_norm.index,
                         columns=['PC1', 'PC2']), pca.explained_variance_ratio_


def top_distinguishing_features(df_norm: pd.DataFrame, name_lookup: dict,
                                  top_k: int = 8) -> dict:
    """For each pair of SADs, the K features driving the largest distance."""
    out = {}
    sad_ids = list(df_norm.index)
    for i, a in enumerate(sad_ids):
        for j, b in enumerate(sad_ids):
            if i >= j:
                continue
            name_a = name_lookup.get(a, a)
            name_b = name_lookup.get(b, b)
            diffs = (df_norm.loc[a] - df_norm.loc[b]).abs()
            top = diffs.sort_values(ascending=False).head(top_k)
            out[f"{name_a} vs {name_b}"] = [
                {'feature': feat, 'abs_z_diff': float(v),
                 f'{name_a}_z': float(df_norm.loc[a, feat]),
                 f'{name_b}_z': float(df_norm.loc[b, feat])}
                for feat, v in top.items()
            ]
    return out


# ─── Visualizations ────────────────────────────────────────────────────

def render_distance_heatmap(D: pd.DataFrame, name_lookup: dict,
                              out_png, out_svg):
    n = len(D)
    labels = [name_lookup.get(idx, idx) for idx in D.index]
    fig, ax = plt.subplots(figsize=(1.5 + n * 1.4, 1.5 + n * 1.0))
    im = ax.imshow(D.values, cmap='RdYlBu_r', vmin=0, vmax=D.values.max() * 1.1)
    
    for i in range(n):
        for j in range(n):
            v = D.values[i, j]
            color = 'white' if v > D.values.max() * 0.55 else '#222'
            ax.text(j, i, f'{v:.3f}', ha='center', va='center',
                     color=color, fontsize=11, fontweight='bold')
    
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(labels, rotation=30, ha='right', fontsize=10)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_title("Pairwise cosine distance between SAD vibe vectors\n"
                  "(0 = identical, higher = more distinct)",
                  fontsize=11, pad=10, loc='left')
    fig.colorbar(im, ax=ax, fraction=0.04, pad=0.04)
    fig.tight_layout()
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def render_pca_2d(coords: pd.DataFrame, name_lookup: dict, var_ratio,
                    out_png, out_svg):
    fig, ax = plt.subplots(figsize=(10, 7.5))
    
    for i, (sad_id, row) in enumerate(coords.iterrows()):
        color = SAD_PALETTE[i % len(SAD_PALETTE)]
        label = name_lookup.get(sad_id, sad_id)
        ax.scatter(row['PC1'], row['PC2'], s=550, c=color,
                    edgecolors='white', linewidth=2.5, zorder=3)
        ax.annotate(label,
                    xy=(row['PC1'], row['PC2']),
                    xytext=(8, 8), textcoords='offset points',
                    fontsize=14, fontweight='bold', color=color)
    
    pc1_v = var_ratio[0] if len(var_ratio) > 0 else 0
    pc2_v = var_ratio[1] if len(var_ratio) > 1 else 0
    ax.set_xlabel(f'PC1 ({pc1_v:.1%} of inter-SAD variance)', fontsize=11)
    ax.set_ylabel(f'PC2 ({pc2_v:.1%} of inter-SAD variance)', fontsize=11)
    ax.set_title('SAD vibe space — 2D PCA projection\n'
                  f'({len(coords)} SADs, PCA is mathematically exact for N={len(coords)})',
                  fontsize=12, loc='left', pad=10)
    ax.axhline(0, color='#ccc', linewidth=0.8, zorder=1)
    ax.axvline(0, color='#ccc', linewidth=0.8, zorder=1)
    ax.grid(alpha=0.25, linestyle='--', linewidth=0.5)
    ax.set_axisbelow(True)
    
    # Add buffer around points
    margins = 0.3
    x_range = coords['PC1'].max() - coords['PC1'].min()
    y_range = coords['PC2'].max() - coords['PC2'].min()
    if x_range > 0:
        ax.set_xlim(coords['PC1'].min() - x_range * margins,
                    coords['PC1'].max() + x_range * margins)
    if y_range > 0:
        ax.set_ylim(coords['PC2'].min() - y_range * margins,
                    coords['PC2'].max() + y_range * margins)
    
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def render_feature_signatures(df_norm: pd.DataFrame, name_lookup: dict,
                                out_png, out_svg):
    """Parallel-coordinates plot, one line per SAD."""
    fig, ax = plt.subplots(figsize=(max(13, len(df_norm.columns) * 0.4), 7))
    
    n_features = len(df_norm.columns)
    x = np.arange(n_features)
    
    for i, (sad_id, row) in enumerate(df_norm.iterrows()):
        color = SAD_PALETTE[i % len(SAD_PALETTE)]
        label = name_lookup.get(sad_id, sad_id)
        ax.plot(x, row.values, '-o', color=color, linewidth=2.0,
                 markersize=8, markeredgecolor='white', markeredgewidth=1,
                 label=label, alpha=0.9)
    
    # Group separators
    groups = {
        'morph_': '#fdecd2',
        'prog_': '#d6e5f7',
        'anchor_': '#f7d6d6',
        'demo_': '#d6f7e2',
    }
    spans = []
    last_prefix = None
    span_start = 0
    for k, col in enumerate(df_norm.columns):
        for prefix in groups:
            if col.startswith(prefix):
                this_prefix = prefix
                break
        else:
            this_prefix = None
        if this_prefix != last_prefix:
            if last_prefix is not None:
                spans.append((span_start, k - 1, last_prefix))
            span_start = k
            last_prefix = this_prefix
    if last_prefix is not None:
        spans.append((span_start, n_features - 1, last_prefix))
    
    for start, end, prefix in spans:
        if prefix and prefix in groups:
            ax.axvspan(start - 0.5, end + 0.5, color=groups[prefix],
                        alpha=0.4, zorder=0)
            # Group label
            label = prefix.rstrip('_').upper()
            ax.text((start + end) / 2, ax.get_ylim()[1] * 0.92, label,
                     ha='center', fontsize=9, fontweight='bold',
                     color='#444', alpha=0.7)
    
    ax.axhline(0, color='#888', linewidth=0.8, linestyle='--', zorder=1)
    ax.set_xticks(x)
    ax.set_xticklabels(
        [c.replace('morph_', '').replace('prog_pct_', '').replace('prog_', '')
           .replace('anchor_', '').replace('demo_', '')
         for c in df_norm.columns],
        rotation=55, ha='right', fontsize=8.5,
    )
    ax.set_ylabel('z-score (across SADs)', fontsize=10)
    ax.set_title('Vibe signature: each SAD as a line through the feature space\n'
                  '(z-scored; 0 = mean across SADs, +/-1 = one std deviation)',
                  fontsize=11, loc='left', pad=10)
    ax.legend(loc='upper right', fontsize=10, frameon=True, framealpha=0.95)
    ax.grid(axis='y', linestyle='--', alpha=0.3, linewidth=0.5)
    ax.set_axisbelow(True)
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    
    fig.tight_layout()
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def render_top_distinguishing(top_dist: dict, out_png, out_svg):
    """Per-pair: top features driving the distance."""
    pairs = list(top_dist.keys())
    n_pairs = len(pairs)
    fig, axes = plt.subplots(1, n_pairs, figsize=(6 * n_pairs, 6),
                              sharey=False)
    if n_pairs == 1:
        axes = [axes]
    
    for ax, pair in zip(axes, pairs):
        entries = top_dist[pair][:6]
        feats = [e['feature'] for e in entries]
        diffs = [e['abs_z_diff'] for e in entries]
        
        labels_clean = [
            f.replace('morph_', '').replace('prog_pct_', '')
             .replace('prog_', 'prog/').replace('anchor_', 'anchor/')
             .replace('demo_', 'demo/')
            for f in feats
        ]
        
        # Color by feature group
        bar_colors = []
        for f in feats:
            if f.startswith('morph_'):
                bar_colors.append('#D97757')
            elif f.startswith('prog_'):
                bar_colors.append('#1B2845')
            elif f.startswith('anchor_'):
                bar_colors.append('#7C5E9C')
            elif f.startswith('demo_'):
                bar_colors.append('#5C8B89')
            else:
                bar_colors.append('#999')
        
        y_pos = np.arange(len(feats))[::-1]
        ax.barh(y_pos, diffs, color=bar_colors, edgecolor='white',
                linewidth=0.6)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels_clean, fontsize=10)
        ax.set_xlabel('|z-score difference|', fontsize=10)
        ax.set_title(pair, fontsize=11, loc='left')
        for spine in ('top', 'right'):
            ax.spines[spine].set_visible(False)
        ax.grid(axis='x', linestyle='--', alpha=0.3, linewidth=0.5)
        ax.set_axisbelow(True)
    
    fig.suptitle('Top features driving each pairwise difference',
                  fontsize=13, y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


def render_feature_groups_radar(df_norm: pd.DataFrame, name_lookup: dict,
                                  out_png, out_svg):
    """Radar with one polygon per SAD, axes = feature group means."""
    groups = {
        'morphology': [c for c in df_norm.columns if c.startswith('morph_')],
        'program': [c for c in df_norm.columns if c.startswith('prog_')],
        'anchor': [c for c in df_norm.columns if c.startswith('anchor_')],
        'demographics': [c for c in df_norm.columns if c.startswith('demo_')],
    }
    # Group means of absolute z-scores (how "extreme" is this SAD per group)
    group_data = {}
    for sad_id, row in df_norm.iterrows():
        group_data[sad_id] = {}
        for g, cols in groups.items():
            if cols:
                group_data[sad_id][g] = float(row[cols].abs().mean())
            else:
                group_data[sad_id][g] = 0.0
    
    axes_labels = [g for g, cols in groups.items() if cols]
    if len(axes_labels) < 3:
        return
    
    angles = np.linspace(0, 2 * np.pi, len(axes_labels),
                          endpoint=False).tolist()
    angles_closed = angles + [angles[0]]
    
    fig, ax = plt.subplots(figsize=(8.5, 8.5), subplot_kw=dict(polar=True))
    
    for i, (sad_id, data) in enumerate(group_data.items()):
        vals = [data[g] for g in axes_labels]
        vals_closed = vals + [vals[0]]
        color = SAD_PALETTE[i % len(SAD_PALETTE)]
        label = name_lookup.get(sad_id, sad_id)
        ax.plot(angles_closed, vals_closed, color=color, linewidth=2,
                 label=label)
        ax.fill(angles_closed, vals_closed, color=color, alpha=0.15)
    
    ax.set_xticks(angles)
    ax.set_xticklabels(axes_labels, fontsize=11)
    ax.set_title('How extreme is each SAD per feature band?\n'
                  '(mean of absolute z-scores within each group)',
                  fontsize=11, pad=20)
    ax.legend(loc='upper right', bbox_to_anchor=(1.25, 1.1), fontsize=10)
    ax.grid(alpha=0.4)
    
    fig.tight_layout()
    fig.savefig(out_png, dpi=170, bbox_inches='tight')
    fig.savefig(out_svg, format='svg', bbox_inches='tight')
    plt.close(fig)


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="District-level latent-vibe embedding (SAD-interior only)")
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--sads', nargs='+', required=True)
    parser.add_argument('--out', type=Path, default=None)
    parser.add_argument('--skip-demographics', action='store_true',
                        help="Don't include census features even if available")
    args = parser.parse_args()
    
    print(f"Building vibe embedding for {len(args.sads)} SADs...\n")
    
    # First pass: check which SADs have census; only include demographics
    # if ALL SADs have it (otherwise we'd compare apples to apples-plus-context)
    include_demographics = not args.skip_demographics
    if include_demographics:
        missing = []
        for sad_id in args.sads:
            if not (args.data_dir / sad_id / 'source' /
                     'census_blockgroups.gpkg').exists():
                missing.append(sad_id)
        if missing:
            print(f"  WARN: census_blockgroups.gpkg missing for: "
                  f"{', '.join(missing)}")
            print(f"  --> dropping demographic features for all SADs "
                  f"(consistency requires same feature set across SADs)\n")
            include_demographics = False
        else:
            print("  Census data found for all SADs; "
                  "demographic features included.\n")
    
    # Gather features for each SAD
    sads_data = []
    for sad_id in args.sads:
        print(f"  processing {sad_id}...")
        try:
            sd = gather_sad_features(args.data_dir, sad_id,
                                       include_demographics=include_demographics)
            sads_data.append(sd)
            print(f"    {sd['buildings_inside_count']:,} SAD-interior buildings, "
                  f"{sd['sad_area_km2']:.2f} km², "
                  f"{len(sd['features'])} features")
        except Exception as e:
            print(f"    ERROR: {e} -- SKIPPING this district")
            continue
    
    if len(sads_data) < 2:
        raise SystemExit(f"Need >=2 SADs, got {len(sads_data)}")
    
    # Build matrix, normalize, analyze
    df_raw, name_lookup = build_feature_matrix(sads_data)
    df_norm = normalize_features(df_raw)
    D = compute_distance_matrix(df_norm)
    coords, var_ratio = pca_project(df_norm)
    top_dist = top_distinguishing_features(df_norm, name_lookup, top_k=8)
    
    # Output directory
    if args.out:
        out_dir = args.out
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        out_dir = args.data_dir / '_comparisons' / f'embedding_{ts}'
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting to {out_dir}")
    
    # Save tabular outputs
    df_raw.to_csv(out_dir / 'feature_matrix.csv')
    df_norm.to_csv(out_dir / 'feature_matrix_normalized.csv')
    D.to_csv(out_dir / 'distance_matrix.csv')
    coords.to_csv(out_dir / 'pca_coords.csv')
    print(f"  [OK] feature_matrix.csv ({df_raw.shape[0]} x {df_raw.shape[1]-1})")
    print(f"  [OK] feature_matrix_normalized.csv")
    print(f"  [OK] distance_matrix.csv")
    print(f"  [OK] pca_coords.csv")
    
    # Visualizations
    render_distance_heatmap(D, name_lookup,
                              out_dir / 'distance_heatmap.png',
                              out_dir / 'distance_heatmap.svg')
    print("  [OK] distance_heatmap")
    
    render_pca_2d(coords, name_lookup, var_ratio,
                   out_dir / 'pca_2d.png', out_dir / 'pca_2d.svg')
    print("  [OK] pca_2d")
    
    render_feature_signatures(df_norm, name_lookup,
                                out_dir / 'feature_signatures.png',
                                out_dir / 'feature_signatures.svg')
    print("  [OK] feature_signatures")
    
    render_top_distinguishing(top_dist,
                                out_dir / 'top_distinguishing_features.png',
                                out_dir / 'top_distinguishing_features.svg')
    print("  [OK] top_distinguishing_features")
    
    render_feature_groups_radar(df_norm, name_lookup,
                                  out_dir / 'feature_groups_radar.png',
                                  out_dir / 'feature_groups_radar.svg')
    print("  [OK] feature_groups_radar")
    
    # Save summary JSON
    summary = {
        'generated_at': datetime.now().isoformat(),
        'sads': [
            {'sad_id': sd['sad_id'], 'name': sd['name'],
             'typology': sd['typology'], 'anchor_venue': sd['anchor_venue'],
             'sad_area_km2': sd['sad_area_km2'],
             'buildings_inside_count': sd['buildings_inside_count']}
            for sd in sads_data
        ],
        'feature_names': list(df_raw.columns),
        'feature_count': df_raw.shape[1],
        'include_demographics': include_demographics,
        'pca_explained_variance': var_ratio.tolist(),
        'distance_matrix': D.to_dict(),
        'top_distinguishing_features': top_dist,
        'feature_matrix': df_raw.reset_index().to_dict(orient='records'),
        'feature_matrix_normalized': df_norm.reset_index().to_dict(orient='records'),
    }
    (out_dir / 'vibe_embedding_summary.json').write_text(
        json.dumps(summary, indent=2, default=str))
    print(f"  [OK] vibe_embedding_summary.json")
    
    print(f"\n  feature count: {df_raw.shape[1]}")
    print(f"  PCA variance: PC1 = {var_ratio[0]:.1%}, "
          f"PC2 = {var_ratio[1]:.1%}" if len(var_ratio) >= 2
          else f"  PCA variance: PC1 = {var_ratio[0]:.1%}")
    
    print(f"\n  Pairwise distances:")
    for i, a in enumerate(D.index):
        for j, b in enumerate(D.columns):
            if i < j:
                la = name_lookup.get(a, a)
                lb = name_lookup.get(b, b)
                print(f"    {la} <-> {lb}: {D.values[i, j]:.3f}")
    
    print(f"\n  All outputs in: {out_dir}")


if __name__ == '__main__':
    main()
