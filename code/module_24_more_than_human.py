"""
module_24_more_than_human.py

The "more-than-human" feature block for a SAD. This is the ecological /
interspecies expansion of the Module 8 cross-district feature vector,
following the same field->summary logic Module 22 already uses for heat:
each ecological layer is a raster (or vector) field over the district, and
the feature it contributes to M8 is that field's zonal summary.

It does NOT touch the morphology field view (06c/06d) — those are intra-district
deterministic geometric reprojections. This module operates at the district-
summary scale, producing one feature dict per SAD.

WHAT IT MEASURES  (band prefix: mth_)
  Vegetation & canopy
    mth_ndvi_mean                 greenness inside the SAD (-1..1)         [Landsat/S2, PC]
    mth_ndvi_seasonal_amplitude   phenology: p90-p10 of monthly NDVI       [Sentinel-2, PC]
    mth_tree_canopy_pct           % canopy cover                           [NLCD TCC (US) / WorldCover tree]
  Land-cover composition (ESA WorldCover, global) — full histogram, not just built-up
    mth_lc_tree_pct / _grass_pct / _crop_pct / _water_pct /
    mth_lc_wetland_pct / _bare_pct / _builtup_pct
    mth_lc_shannon                land-cover diversity (Shannon)
  Hydrology
    mth_dist_to_water_m           SAD edge -> nearest mapped water         [USGS NHD]
    mth_floodplain_pct            % SAD in FEMA SFHA                        [FEMA NFHL, US]
    mth_wetland_pct               % SAD in mapped wetland                   [FWS NWI, US]
    mth_huc12                     containing watershed id (metadata)       [USGS WBD]
  Biodiversity (all rate-limited; cached + throttled like Overpass)
    mth_gbif_richness             distinct species (GBIF, in canvas bbox)
    mth_gbif_occurrences          occurrence records (GBIF)
    mth_inat_research_grade       research-grade observations (iNaturalist)
    mth_ebird_richness            distinct bird species (eBird; needs API key)
  Soil & runoff
    mth_impervious_pct            % impervious                             [NLCD (US) / WorldCover built-up]
    mth_runoff_potential          area-weighted hydrologic soil group D-ness 0..1 [USDA SSURGO, US]
    mth_stormwater_burden         impervious_pct/100 * runoff_potential (0..1 proxy)
  Air & light
    mth_no2_mean                  tropospheric NO2 column                  [Sentinel-5P, PC]
    mth_nightlight_mean           VIIRS radiance inside the SAD            [VIIRS, PC]
    mth_nightlight_vs_surroundings  SAD mean minus canvas mean (disturbance delta)
  Connectivity
    mth_dist_to_green_patch_m     SAD edge -> nearest large green patch    [from WorldCover]
    mth_green_patch_count         distinct green patches intersecting SAD
    mth_green_edge_density_m_per_ha  green-patch perimeter / SAD area (fragmentation)

DATA-QUALITY FLAGS  (mirrors your residential-audit pattern)
  Every layer that returns nothing, gets capped, is US-only-skipped, or hits a
  rate limit is recorded in summary['flags'] so a false zero never silently
  enters the feature matrix. Re-pull flagged SADs, exactly like the Overpass 429s.

DEPENDENCIES
  pip install pystac-client planetary-computer rioxarray rasterio requests shapely geopandas
  Optional: export EBIRD_API_KEY=...   (eBird richness skipped without it)

USAGE
  python module_24_more_than_human.py ^
      --derived ..\\data\\32_District-Detroit_Detroit-MI\\derived ^
      --source  ..\\data\\32_District-Detroit_Detroit-MI\\source
  # Validate on Detroit first; only then batch. Network-heavy: --skip-slow drops
  # the biodiversity APIs and Sentinel-2 phenology for a fast structural check.

OUTPUTS
  derived/<sad>/more_than_human/mth_summary.json     (the M8 feed)
  derived/<sad>/more_than_human/landcover_grid.geojson  (field-view feed, optional)

HONEST CAVEATS
  - This file is structurally complete but every numeric value depends on live
    network pulls (Planetary Computer, GBIF, iNaturalist, eBird, USDA/USGS/FEMA
    REST services). Nothing here is validated against your real data on this end;
    smoke-test on Detroit and read the flags before trusting the vector.
  - US-only layers (NLCD canopy/impervious, SSURGO, FEMA, NWI) degrade gracefully
    on the 4 Canadian SADs to global fallbacks (WorldCover, NHD has no CA cover —
    those become flags, not zeros), same US/Canada split Module 22 already handles.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import geopandas as gpd
from shapely.geometry import shape, mapping, box as shp_box

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest
import canvas_render as cr


# ─── Config ──────────────────────────────────────────────────────────────────

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"

# ESA WorldCover class -> our land-cover bucket (10 m, global, 2021 v200)
WORLDCOVER_CLASSES = {
    10: 'tree', 20: 'shrub', 30: 'grass', 40: 'crop', 50: 'builtup',
    60: 'bare', 70: 'snow', 80: 'water', 90: 'wetland', 95: 'mangrove',
    100: 'moss',
}
GREEN_CLASSES = {10, 20, 30, 40, 90, 95}          # vegetated / habitat-bearing
LARGE_PATCH_MIN_HA = 0.5                          # "large green patch" threshold

# ArcGIS REST services. Layer IDs introspected dynamically (find_layer_id below)
# with these as the verified fallback default (USGS NHD restructured Sept 2024,
# FEMA NFHL re-numbered Aug 2025 — introspection protects against future shifts).
NHD_MAPSERVER = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer"
NHD_FLOWLINE_LAYER_DEFAULT = 6                    # "Flowline - Large Scale"
NHD_FLOWLINE_NAME_HINT = 'flowline'               # substring match (case-insensitive)
NHD_QUERY_BUFFER_DEG = 0.02                       # ~2 km lat-equiv: inland SADs
                                                  # can sit beyond an NHD reach;
                                                  # "distance to water" is a
                                                  # regional question — the
                                                  # SAD-edge distance math still
                                                  # returns the correct value.

WBD_MAPSERVER = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer"
WBD_HUC12_LAYER_DEFAULT = 6
WBD_HUC12_NAME_HINT = 'huc12'

FEMA_NFHL_MAPSERVER = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
FEMA_SFHA_LAYER_DEFAULT = 28                      # "Flood Hazard Zones"
FEMA_SFHA_NAME_HINT = 'flood hazard zone'

# NWI: the legacy fwsprimary.wim.usgs.gov host returns HTTP 500 on queries
# (as of 2026). The current FWS-canonical endpoint is the public-services host,
# per https://www.fws.gov/program/national-wetlands-inventory/web-mapping-services.
NWI_MAPSERVER = "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer"
NWI_LAYER_DEFAULT = 0
NWI_NAME_HINT = 'wetland'

SSURGO_SDA = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"

GBIF_OCC = "https://api.gbif.org/v1/occurrence/search"
INAT_OBS = "https://api.inaturalist.org/v1/observations"
EBIRD_GEO = "https://api.ebird.org/v2/data/obs/geo/recent"

# Throttle policy — same discipline you applied to Overpass 429/504s.
HTTP_TIMEOUT = 60
THROTTLE_S = 1.0
MAX_RETRY = 4
BACKOFF_S = 5.0


# ─── HTTP helper with Overpass-style backoff ──────────────────────────────────

def _http_get(url, params=None, headers=None):
    import requests
    last = None
    for attempt in range(MAX_RETRY):
        try:
            r = requests.get(url, params=params, headers=headers,
                             timeout=HTTP_TIMEOUT)
            if r.status_code in (429, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            r.raise_for_status()
            time.sleep(THROTTLE_S)
            return r
        except Exception as e:                    # noqa: BLE001
            last = str(e)
            time.sleep(BACKOFF_S * (attempt + 1))
    raise RuntimeError(f"GET failed after {MAX_RETRY} tries: {last}")


def _http_post(url, data=None, json_body=None, headers=None):
    import requests
    last = None
    for attempt in range(MAX_RETRY):
        try:
            r = requests.post(url, data=data, json=json_body, headers=headers,
                              timeout=HTTP_TIMEOUT)
            if r.status_code in (429, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            r.raise_for_status()
            time.sleep(THROTTLE_S)
            return r
        except Exception as e:                    # noqa: BLE001
            last = str(e)
            time.sleep(BACKOFF_S * (attempt + 1))
    raise RuntimeError(f"POST failed after {MAX_RETRY} tries: {last}")


# ─── Pure helpers ──────────────────────────────────────────────────────────

def shannon(counts) -> float:
    """Shannon entropy of a count vector (natural log). 0 for empty/uniform-1."""
    arr = np.asarray([c for c in counts if c > 0], dtype=float)
    if arr.size <= 1:
        return 0.0
    p = arr / arr.sum()
    return float(-(p * np.log(p)).sum())


def zonal_mask(data, transform, polygon_metric):
    from rasterio.features import geometry_mask
    return geometry_mask([polygon_metric], out_shape=data.shape,
                         transform=transform, invert=True)


def class_fractions(data, transform, polygon_metric) -> dict[int, float]:
    """Fraction of valid pixels per integer class within polygon_metric."""
    m = zonal_mask(data, transform, polygon_metric)
    vals = data[m & np.isfinite(data)]
    if vals.size == 0:
        return {}
    uniq, cnt = np.unique(vals.astype(int), return_counts=True)
    total = cnt.sum()
    return {int(u): float(c) / total for u, c in zip(uniq, cnt)}


# ─── Planetary Computer ──────────────────────────────────────────────────────

def _pc_client():
    try:
        import pystac_client
        import planetary_computer as pc
    except ImportError:
        sys.exit("Module 24 needs: pip install pystac-client planetary-computer "
                 "rioxarray rasterio requests")
    return pystac_client.Client.open(PC_STAC, modifier=pc.sign_inplace)


def _stac_items_with_retry(cat, **search_kwargs):
    """pystac_client.search().items() with the same retry discipline as
    _http_get. PC's STAC API occasionally returns 'request exceeded the
    maximum allowed time' under load — never a fatal failure, just retry.
    Returns a list of items, or [] after MAX_RETRY attempts (so the caller
    can flag and continue rather than crash the whole run)."""
    last_exc = None
    for attempt in range(MAX_RETRY):
        try:
            search = cat.search(**search_kwargs)
            return list(search.items())
        except Exception as e:                     # noqa: BLE001
            last_exc = e
            msg = str(e).lower()
            # Transient: timeout / 5xx / rate limit. Retry with backoff.
            if any(s in msg for s in ('maximum allowed time', 'timeout',
                                       'timed out', '429', '502', '503',
                                       '504', 'too many requests')):
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            # Other error: bail immediately, caller flags it
            print(f"  STAC search failed (non-transient): {e}")
            return []
    print(f"  STAC search failed after {MAX_RETRY} retries: {last_exc}")
    return []


def _read_band(item, asset_key, metric_crs, bbox_m):
    import rioxarray  # noqa: F401  registers .rio
    da = __import__('rioxarray').open_rasterio(
        item.assets[asset_key].href, masked=True).squeeze()
    da = da.rio.reproject(metric_crs)
    return da.rio.clip_box(*bbox_m)


def fetch_worldcover_grid(bbox_geo, metric_crs, bbox_m):
    """Return (class_raster, transform) from ESA WorldCover 10 m, reprojected to
    metric_crs and clipped to the canvas bbox. None on failure."""
    cat = _pc_client()
    items = _stac_items_with_retry(cat, collections=['esa-worldcover'],
                                   bbox=list(bbox_geo))
    if not items:
        return None, None
    item = items[0]
    asset = 'map' if 'map' in item.assets else list(item.assets)[0]
    da = _read_band(item, asset, metric_crs, bbox_m)
    return da.values.astype('float32'), da.rio.transform()


def fetch_s2_ndvi_amplitude(bbox_geo, metric_crs, sad_poly_m, bbox_m, flags):
    """Seasonal NDVI amplitude (p90 - p10 of monthly mean NDVI over ~1 yr) from
    Sentinel-2 L2A. A phenology signal: how much the district greens up and down.
    Heavy; returns (amplitude, mean) or (None, None)."""
    import datetime as dt
    cat = _pc_client()
    end = dt.date.today()
    start = dt.date(end.year - 1, end.month, 1)
    items = _stac_items_with_retry(
        cat, collections=['sentinel-2-l2a'], bbox=list(bbox_geo),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={'eo:cloud_cover': {'lt': 20}})
    if not items:
        flags.append('s2_phenology_empty')
        return None, None
    by_month: dict[int, list] = {}
    for it in items:
        by_month.setdefault(it.datetime.month, []).append(it)
    monthly_means = []
    for month, its in sorted(by_month.items()):
        it = min(its, key=lambda x: x.properties.get('eo:cloud_cover', 100))
        try:
            red = _read_band(it, 'B04', metric_crs, bbox_m)
            nir = _read_band(it, 'B08', metric_crs, bbox_m)
            nir = nir.rio.reproject_match(red)
            r = red.values.astype('float32'); n = nir.values.astype('float32')
            with np.errstate(invalid='ignore', divide='ignore'):
                ndvi = (n - r) / (n + r)
            ndvi[~np.isfinite(ndvi)] = np.nan
            m = zonal_mask(ndvi, red.rio.transform(), sad_poly_m)
            vals = ndvi[m & np.isfinite(ndvi)]
            if vals.size:
                monthly_means.append(float(np.nanmean(vals)))
        except Exception:                          # noqa: BLE001
            continue
    if len(monthly_means) < 4:
        flags.append('s2_phenology_sparse')
        return None, (float(np.mean(monthly_means)) if monthly_means else None)
    arr = np.array(monthly_means)
    return float(np.percentile(arr, 90) - np.percentile(arr, 10)), float(arr.mean())


def fetch_pc_scalar_field(collection, asset_key, bbox_geo, metric_crs,
                          sad_poly_m, canvas_poly_m, bbox_m, flags, tag):
    """Generic mean-over-SAD + (SAD - surroundings) delta for a PC raster
    collection (used for Sentinel-5P NO2 and VIIRS nightlights)."""
    import datetime as dt
    cat = _pc_client()
    end = dt.date.today()
    start = dt.date(end.year - 1, 1, 1)
    items = _stac_items_with_retry(
        cat, collections=[collection], bbox=list(bbox_geo),
        datetime=f"{start.isoformat()}/{end.isoformat()}")
    if not items:
        flags.append(f'{tag}_empty')
        return None, None
    vals_sad, vals_out = [], []
    for it in items[:6]:                           # cap scenes for speed
        try:
            da = _read_band(it, asset_key, metric_crs, bbox_m)
            arr = da.values.astype('float32')
            tr = da.rio.transform()
            m_sad = zonal_mask(arr, tr, sad_poly_m)
            m_can = zonal_mask(arr, tr, canvas_poly_m)
            s = arr[m_sad & np.isfinite(arr)]
            o = arr[(m_can & ~m_sad) & np.isfinite(arr)]
            if s.size:
                vals_sad.append(float(np.nanmean(s)))
            if o.size:
                vals_out.append(float(np.nanmean(o)))
        except Exception:                          # noqa: BLE001
            continue
    if not vals_sad:
        flags.append(f'{tag}_no_valid_pixels')
        return None, None
    mean_sad = float(np.mean(vals_sad))
    delta = (mean_sad - float(np.mean(vals_out))) if vals_out else None
    return mean_sad, delta


# ─── Vector REST services (ArcGIS FeatureServer / MapServer) ──────────────────

# Cache: avoid re-introspecting the same MapServer for every SAD.
_LAYER_ID_CACHE: dict[tuple[str, str], int | None] = {}


def find_layer_id(mapserver_url: str, name_hint: str, default_id: int) -> int:
    """Introspect an ArcGIS MapServer and return the layer id whose name
    contains name_hint (case-insensitive). Falls back to default_id if
    introspection fails or no match is found. Cached per (server, hint)."""
    key = (mapserver_url, name_hint)
    if key in _LAYER_ID_CACHE:
        cached = _LAYER_ID_CACHE[key]
        return cached if cached is not None else default_id
    try:
        r = _http_get(f"{mapserver_url}/layers", params={'f': 'json'})
        layers = (r.json() or {}).get('layers', []) or []
        # Some services (e.g. NWI) return empty from /layers; fall back to the
        # root MapServer JSON, which always lists layers in summary form.
        if not layers:
            r = _http_get(mapserver_url, params={'f': 'json'})
            layers = (r.json() or {}).get('layers', []) or []
        hint = name_hint.lower()
        for layer in layers:
            name = str(layer.get('name', '')).lower()
            if hint in name:
                lid = int(layer.get('id'))
                _LAYER_ID_CACHE[key] = lid
                return lid
    except Exception:                              # noqa: BLE001
        pass
    _LAYER_ID_CACHE[key] = None                    # remember the fallback
    return default_id


def _arcgis_query_geojson(mapserver_url, name_hint, default_layer_id,
                          bbox_geo, flags, tag, where='1=1',
                          buffer_deg=0.0, force_arcgis_json=False):
    """Query an ArcGIS REST layer by geographic envelope, return a GeoDataFrame
    in EPSG:4326. Resolves the layer id by name introspection (with default
    fallback). Empty GDF (not error) when the layer has no features here.

    buffer_deg: optional outward bbox buffer (in degrees) for services where
      the SAD canvas can geometrically miss a feature (e.g. NHD flowlines for
      inland SADs). The returned features are still clipped/intersected
      against the SAD polygon downstream.
    force_arcgis_json: skip f=geojson and request f=json instead, parsing the
      Esri feature JSON ourselves. Use for servers that return HTTP 500 on
      geojson serialization (e.g. NWI's FWS primary endpoint).
    """
    layer_id = find_layer_id(mapserver_url, name_hint, default_layer_id)
    url = f"{mapserver_url}/{layer_id}/query"
    minx, miny, maxx, maxy = bbox_geo
    if buffer_deg:
        minx -= buffer_deg; miny -= buffer_deg
        maxx += buffer_deg; maxy += buffer_deg
    params = {
        'where': where, 'geometry': f"{minx},{miny},{maxx},{maxy}",
        'geometryType': 'esriGeometryEnvelope', 'inSR': 4326,
        'spatialRel': 'esriSpatialRelIntersects', 'outFields': '*',
        'returnGeometry': 'true', 'outSR': 4326,
        'f': 'json' if force_arcgis_json else 'geojson',
    }
    try:
        r = _http_get(url, params=params)
        gj = r.json()
        if 'error' in gj:                          # server-side query error
            flags.append(f'{tag}_server_error')
            return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
        if force_arcgis_json:
            # Convert Esri feature JSON -> GeoDataFrame (handles polygon rings
            # without the GeoJSON serializer some servers can't produce).
            return _esri_json_to_gdf(gj, flags, tag)
        feats = gj.get('features', [])
        if not feats:
            flags.append(f'{tag}_empty')
            return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
        return gpd.GeoDataFrame.from_features(feats, crs='EPSG:4326')
    except Exception:                              # noqa: BLE001
        flags.append(f'{tag}_failed')
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')


def _esri_json_to_gdf(gj, flags, tag):
    """Convert an Esri-flavored query response (f=json, polygon rings) to a
    GeoDataFrame in EPSG:4326. Tolerates rings-only polygon geometry, the form
    NWI's FWS primary endpoint returns."""
    from shapely.geometry import Polygon, MultiPolygon
    feats = gj.get('features', [])
    if not feats:
        flags.append(f'{tag}_empty')
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    rows, geoms = [], []
    for f in feats:
        g = f.get('geometry') or {}
        rings = g.get('rings')
        if not rings:
            continue                               # skip non-polygon for now
        try:
            polys = [Polygon(r) for r in rings if len(r) >= 4]
            if not polys:
                continue
            geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        except Exception:                          # noqa: BLE001
            continue
        rows.append(f.get('attributes') or {})
        geoms.append(geom)
    if not geoms:
        flags.append(f'{tag}_empty')
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    return gpd.GeoDataFrame(rows, geometry=geoms, crs='EPSG:4326')


def hydrology_features(bbox_geo, metric_crs, sad_poly_m, flags):
    out = {}
    # Distance to nearest mapped water (NHD flowlines). Inland SAD canvases can
    # geometrically miss every reach; we buffer the query envelope outward so
    # "distance to nearest water" is regionally honest. The SAD-edge distance
    # math is still computed against the SAD polygon, so the metric is correct.
    nhd = _arcgis_query_geojson(NHD_MAPSERVER, NHD_FLOWLINE_NAME_HINT,
                                NHD_FLOWLINE_LAYER_DEFAULT,
                                bbox_geo, flags, 'nhd',
                                buffer_deg=NHD_QUERY_BUFFER_DEG)
    if len(nhd):
        nhd_m = nhd.to_crs(metric_crs)
        out['mth_dist_to_water_m'] = round(
            float(nhd_m.distance(sad_poly_m).min()), 1)
    else:
        out['mth_dist_to_water_m'] = None
    # FEMA Special Flood Hazard Area fraction (US). The post-2014 SFHA_TF='T'
    # filter no longer matches in the live NFHL schema; the canonical SFHA
    # definition by zone designation (everything except X, D, NP) is robust
    # across schema versions. To distinguish "mapped, no SFHA here" from
    # "no NFHL coverage here", we probe the unfiltered count first.
    fema = _arcgis_query_geojson(FEMA_NFHL_MAPSERVER, FEMA_SFHA_NAME_HINT,
                                 FEMA_SFHA_LAYER_DEFAULT, bbox_geo, flags, 'fema',
                                 where="FLD_ZONE NOT IN ('X','D','NP')")
    if len(fema):
        out['mth_floodplain_pct'] = _intersect_area_pct(fema, metric_crs, sad_poly_m)
    else:
        # SFHA query returned empty — was the area mapped at all?
        if _fema_has_any_coverage(bbox_geo, flags):
            out['mth_floodplain_pct'] = 0.0       # mapped, no SFHA here
            # drop the spurious _empty flag added by the filtered query
            if 'fema_empty' in flags:
                flags.remove('fema_empty')
        else:
            out['mth_floodplain_pct'] = None      # no NFHL coverage
    # FWS National Wetlands Inventory fraction (US). The legacy fwsprimary host
    # returns HTTP 500; the public-services host responds correctly. As with
    # FEMA, an empty result inside CONUS means "mapped, no wetlands here" (0%),
    # while non-US bboxes are genuinely outside coverage (None).
    nwi = _arcgis_query_geojson(NWI_MAPSERVER, NWI_NAME_HINT, NWI_LAYER_DEFAULT,
                                bbox_geo, flags, 'nwi',
                                force_arcgis_json=True)
    if len(nwi):
        out['mth_wetland_pct'] = _intersect_area_pct(nwi, metric_crs, sad_poly_m)
    elif _bbox_in_conus(bbox_geo):
        out['mth_wetland_pct'] = 0.0              # mapped, no wetlands here
        if 'nwi_empty' in flags:
            flags.remove('nwi_empty')
    else:
        out['mth_wetland_pct'] = None             # outside NWI coverage
    # Containing HUC12 watershed (metadata, not a numeric feature)
    wbd = _arcgis_query_geojson(WBD_MAPSERVER, WBD_HUC12_NAME_HINT,
                                WBD_HUC12_LAYER_DEFAULT,
                                bbox_geo, flags, 'wbd')
    huc = None
    if len(wbd):
        col = next((c for c in wbd.columns if c.lower() in ('huc12', 'huc_12')), None)
        if col:
            huc = str(wbd.iloc[0][col])
    out['mth_huc12'] = huc
    return out


def _intersect_area_pct(gdf, metric_crs, sad_poly_m):
    if gdf is None or len(gdf) == 0:
        return None
    try:
        g = gdf.to_crs(metric_crs)
        inter = g.geometry.intersection(sad_poly_m)
        a = float(inter.area.sum())
        return round(100 * a / sad_poly_m.area, 1)
    except Exception:                              # noqa: BLE001
        return None


def _bbox_in_conus(bbox_geo) -> bool:
    """True if the bbox center sits inside the NWI mapping footprint (lower 48,
    Alaska, Hawaii, US territories). Used to tell 'no wetlands here' (real 0%)
    from 'outside coverage' (genuine missing data, e.g. Canadian SADs)."""
    minx, miny, maxx, maxy = bbox_geo
    lon, lat = (minx + maxx) / 2, (miny + maxy) / 2
    # Lower 48 + a generous box around Alaska + Hawaii + Puerto Rico
    return ((-125 <= lon <= -66) and (24 <= lat <= 50)) or \
           ((-170 <= lon <= -130) and (51 <= lat <= 72)) or \
           ((-161 <= lon <= -154) and (18 <= lat <= 23)) or \
           ((-68 <= lon <= -65) and (17 <= lat <= 19))


def _fema_has_any_coverage(bbox_geo, flags) -> bool:
    """Returns True if any NFHL flood-zone feature (any zone, including X)
    intersects the bbox. Used to tell "no SFHA here" (0%) from "no NFHL data
    here" (None)."""
    layer_id = find_layer_id(FEMA_NFHL_MAPSERVER, FEMA_SFHA_NAME_HINT,
                             FEMA_SFHA_LAYER_DEFAULT)
    url = f"{FEMA_NFHL_MAPSERVER}/{layer_id}/query"
    minx, miny, maxx, maxy = bbox_geo
    try:
        r = _http_get(url, params={
            'where': '1=1', 'geometry': f"{minx},{miny},{maxx},{maxy}",
            'geometryType': 'esriGeometryEnvelope', 'inSR': 4326,
            'spatialRel': 'esriSpatialRelIntersects',
            'returnCountOnly': 'true', 'f': 'json',
        })
        return int((r.json() or {}).get('count', 0)) > 0
    except Exception:                              # noqa: BLE001
        return False


def soil_runoff_feature(bbox_geo, flags):
    """Area-weighted 'D-ness' of hydrologic soil group from USDA SSURGO via the
    Soil Data Access REST endpoint. Returns 0..1 (A=0 ... D=1). US only."""
    minx, miny, maxx, maxy = bbox_geo
    wkt = (f"POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},"
           f"{minx} {maxy},{minx} {miny}))")
    q = ("SELECT co.hydgrp, SUM(co.comppct_r) AS pct "
         "FROM mapunit mu "
         "INNER JOIN component co ON co.mukey = mu.mukey "
         f"WHERE mu.mukey IN (SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')) "
         "AND co.hydgrp IS NOT NULL GROUP BY co.hydgrp")
    try:
        r = _http_post(SSURGO_SDA, json_body={'query': q, 'format': 'JSON+COLUMNNAME'})
        rows = r.json().get('Table', [])
        if len(rows) <= 1:
            flags.append('ssurgo_empty'); return None
        weight = {'A': 0.0, 'B': 0.33, 'C': 0.66, 'D': 1.0,
                  'A/D': 0.5, 'B/D': 0.66, 'C/D': 0.83}
        num = den = 0.0
        for grp, pct in rows[1:]:
            w = weight.get(str(grp).strip().upper())
            if w is None:
                continue
            p = float(pct or 0)
            num += w * p; den += p
        return round(num / den, 3) if den else None
    except Exception:                              # noqa: BLE001
        flags.append('ssurgo_failed'); return None


# ─── Biodiversity APIs (rate-limited; cached) ────────────────────────────────

def _cache_path(source_dir, tag):
    d = source_dir.parent.parent / '_mth_cache'
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{source_dir.parent.name}_{tag}.json"


def gbif_features(bbox_geo, source_dir, flags):
    cache = _cache_path(source_dir, 'gbif')
    if cache.exists():
        try:
            d = json.loads(cache.read_text())
            return {'mth_gbif_richness': d['richness'],
                    'mth_gbif_occurrences': d['occurrences']}
        except Exception:                          # noqa: BLE001
            pass
    minx, miny, maxx, maxy = bbox_geo
    species, total = set(), 0
    offset, limit = 0, 300
    try:
        while True:
            r = _http_get(GBIF_OCC, params={
                'decimalLongitude': f"{minx},{maxx}",
                'decimalLatitude': f"{miny},{maxy}",
                'hasCoordinate': 'true', 'limit': limit, 'offset': offset})
            j = r.json()
            total = j.get('count', total)
            for rec in j.get('results', []):
                k = rec.get('speciesKey') or rec.get('species')
                if k:
                    species.add(k)
            if j.get('endOfRecords') or offset > 3000:   # cap pages
                if not j.get('endOfRecords'):
                    flags.append('gbif_capped')
                break
            offset += limit
        out = {'richness': len(species), 'occurrences': int(total)}
        cache.write_text(json.dumps(out))
        return {'mth_gbif_richness': out['richness'],
                'mth_gbif_occurrences': out['occurrences']}
    except Exception:                              # noqa: BLE001
        flags.append('gbif_failed')
        return {'mth_gbif_richness': None, 'mth_gbif_occurrences': None}


def inat_feature(bbox_geo, source_dir, flags):
    cache = _cache_path(source_dir, 'inat')
    if cache.exists():
        try:
            return {'mth_inat_research_grade': json.loads(cache.read_text())['n']}
        except Exception:                          # noqa: BLE001
            pass
    minx, miny, maxx, maxy = bbox_geo
    try:
        r = _http_get(INAT_OBS, params={
            'swlng': minx, 'swlat': miny, 'nelng': maxx, 'nelat': maxy,
            'quality_grade': 'research', 'per_page': 0})
        n = int(r.json().get('total_results', 0))
        cache.write_text(json.dumps({'n': n}))
        return {'mth_inat_research_grade': n}
    except Exception:                              # noqa: BLE001
        flags.append('inat_failed')
        return {'mth_inat_research_grade': None}


def ebird_feature(bbox_geo, source_dir, flags):
    key = os.environ.get('EBIRD_API_KEY')
    if not key:
        flags.append('ebird_no_api_key')
        return {'mth_ebird_richness': None}
    cache = _cache_path(source_dir, 'ebird')
    if cache.exists():
        try:
            return {'mth_ebird_richness': json.loads(cache.read_text())['n']}
        except Exception:                          # noqa: BLE001
            pass
    minx, miny, maxx, maxy = bbox_geo
    lat, lng = (miny + maxy) / 2, (minx + maxx) / 2
    try:
        r = _http_get(EBIRD_GEO, params={'lat': round(lat, 4), 'lng': round(lng, 4),
                                         'dist': 10, 'back': 30},
                      headers={'X-eBirdApiToken': key})
        species = {rec.get('speciesCode') for rec in r.json() if rec.get('speciesCode')}
        cache.write_text(json.dumps({'n': len(species)}))
        return {'mth_ebird_richness': len(species)}
    except Exception:                              # noqa: BLE001
        flags.append('ebird_failed')
        return {'mth_ebird_richness': None}


# ─── Land-cover-derived features (composition + connectivity) ─────────────────

def landcover_features(wc_raster, wc_transform, metric_crs, sad_poly_m,
                       canvas_poly_m, derived_dir, flags):
    out = {}
    if wc_raster is None:
        flags.append('worldcover_empty')
        return {f'mth_lc_{b}_pct': None for b in
                ('tree', 'grass', 'crop', 'water', 'wetland', 'bare', 'builtup')} | \
               {'mth_lc_shannon': None, 'mth_impervious_pct': None,
                'mth_dist_to_green_patch_m': None, 'mth_green_patch_count': None,
                'mth_green_edge_density_m_per_ha': None}

    fr = class_fractions(wc_raster, wc_transform, sad_poly_m)
    bucket = {}
    for cls, frac in fr.items():
        name = WORLDCOVER_CLASSES.get(cls, 'other')
        bucket[name] = bucket.get(name, 0.0) + frac
    for b in ('tree', 'grass', 'crop', 'water', 'wetland', 'bare', 'builtup'):
        out[f'mth_lc_{b}_pct'] = round(100 * bucket.get(b, 0.0), 1)
    out['mth_lc_shannon'] = round(shannon(list(bucket.values())), 3)
    # WorldCover built-up doubles as a global impervious proxy (US NLCD overrides later)
    out['mth_impervious_pct'] = out['mth_lc_builtup_pct']

    # Connectivity: polygonize green pixels, measure patch structure
    try:
        out.update(_green_connectivity(wc_raster, wc_transform, metric_crs,
                                       sad_poly_m, derived_dir))
    except Exception:                              # noqa: BLE001
        flags.append('connectivity_failed')
        out['mth_dist_to_green_patch_m'] = None
        out['mth_green_patch_count'] = None
        out['mth_green_edge_density_m_per_ha'] = None
    return out


def _green_connectivity(wc_raster, transform, metric_crs, sad_poly_m, derived_dir):
    from rasterio.features import shapes as rio_shapes
    green = np.isin(wc_raster.astype(int), list(GREEN_CLASSES)).astype(np.uint8)
    polys, areas = [], []
    for geom, val in rio_shapes(green, mask=green.astype(bool), transform=transform):
        if val != 1:
            continue
        p = shape(geom)
        if p.area / 1e4 >= LARGE_PATCH_MIN_HA:       # hectares
            polys.append(p); areas.append(p.area)
    if not polys:
        return {'mth_dist_to_green_patch_m': None, 'mth_green_patch_count': 0,
                'mth_green_edge_density_m_per_ha': 0.0}
    gseries = gpd.GeoSeries(polys, crs=metric_crs)
    inside = gseries[gseries.intersects(sad_poly_m)]
    dist = float(gseries.distance(sad_poly_m).min())
    edge_m = float(inside.length.sum()) if len(inside) else 0.0
    sad_ha = sad_poly_m.area / 1e4
    # Optional field-view feed: large green patches as a GeoJSON field layer
    try:
        gpd.GeoDataFrame(geometry=gseries.to_crs('EPSG:4326')).to_file(
            derived_dir / 'more_than_human' / 'landcover_grid.geojson',
            driver='GeoJSON')
    except Exception:                              # noqa: BLE001
        pass
    return {'mth_dist_to_green_patch_m': round(dist, 1),
            'mth_green_patch_count': int(len(inside)),
            'mth_green_edge_density_m_per_ha': round(edge_m / sad_ha, 2) if sad_ha else 0.0}


# ─── Orchestration ────────────────────────────────────────────────────────────

def process_sad(derived_dir: Path, source_dir: Path, skip_slow: bool = False) -> Path:
    # Module 1 writes the manifest into derived/ (canonical pipeline convention,
    # matching module_22_environment.py and others).
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        raise SystemExit(f"No manifest at {manifest_path}. Run Module 1 first.")
    manifest = Manifest.model_validate_json(manifest_path.read_text())

    ctx = cr.load_canvas_context(source_dir)
    metric_crs = ctx['metric_crs']
    sad_poly_m = ctx['sad_geom_m']
    canvas_poly_m = ctx['canvas_geom_m']
    bbox_m = ctx['canvas_bbox']
    bbox_geo = manifest.bbox_geo

    print(f"More-than-human profile for {manifest.sad_id} "
          f"({'US' if _looks_us(bbox_geo) else 'non-US'} bbox)...")
    flags: list[str] = []
    feats: dict = {}

    # Resume from a prior run: if mth_summary.json already exists with real
    # values, reuse them. Only pull features whose value is None — i.e. things
    # the prior run (likely --skip-slow) deferred or that genuinely failed.
    # Per-source flags from the prior run are dropped; we'll re-record them
    # honestly this pass.
    out_path = derived_dir / 'more_than_human' / 'mth_summary.json'
    prior_feats: dict = {}
    if out_path.exists():
        try:
            prior_blob = json.loads(out_path.read_text())
            prior_feats = (prior_blob or {}).get('features', {}) or {}
            real_count = sum(1 for v in prior_feats.values()
                             if isinstance(v, (int, float)))
            print(f"  resuming from prior run ({real_count} cached values; "
                  f"None values will be re-pulled)")
        except Exception:                          # noqa: BLE001
            prior_feats = {}

    def cached_or(key: str):
        """Return the cached value if real (not None), else None (signaling a
        fresh pull is needed). String metadata like mth_huc12 also passes
        through if cached."""
        v = prior_feats.get(key)
        if v is None:
            return None
        if isinstance(v, (int, float, str)):
            return v
        return None

    # 1) Land cover (global; the backbone — composition + connectivity).
    # Cheap and always runs — re-computing on resume costs only one STAC call.
    wc_raster, wc_tr = fetch_worldcover_grid(bbox_geo, metric_crs, bbox_m)
    (derived_dir / 'more_than_human').mkdir(parents=True, exist_ok=True)
    feats.update(landcover_features(wc_raster, wc_tr, metric_crs, sad_poly_m,
                                    canvas_poly_m, derived_dir, flags))

    # 2) Vegetation & canopy. Sentinel-2 phenology is the heaviest call in the
    # module; if a prior run already populated mth_ndvi_mean we skip it.
    cached_amp = cached_or('mth_ndvi_seasonal_amplitude')
    cached_mean = cached_or('mth_ndvi_mean')
    if not skip_slow and (cached_amp is None or cached_mean is None):
        amp, ndvi_mean = fetch_s2_ndvi_amplitude(bbox_geo, metric_crs, sad_poly_m,
                                                 bbox_m, flags)
        feats['mth_ndvi_seasonal_amplitude'] = (round(amp, 3) if amp is not None else None)
        feats['mth_ndvi_mean'] = (round(ndvi_mean, 3) if ndvi_mean is not None else None)
    elif cached_amp is not None or cached_mean is not None:
        feats['mth_ndvi_seasonal_amplitude'] = cached_amp
        feats['mth_ndvi_mean'] = cached_mean
        print("  reused cached Sentinel-2 NDVI values")
    else:
        feats['mth_ndvi_seasonal_amplitude'] = None
        feats['mth_ndvi_mean'] = None
        flags.append('s2_skipped')
    # Tree canopy: use WorldCover tree-class fraction (global, 10 m). The legacy
    # NLCD Tree Canopy Cover product is not currently available on Planetary
    # Computer (NLCD restructured to Annual NLCD in 2024 — not yet ingested).
    feats['mth_tree_canopy_pct'] = feats.get('mth_lc_tree_pct')

    # 3) Soil & runoff (US). Impervious is WorldCover built-up (global, 10 m);
    # we previously overrode with NLCD impervious for US districts, but the
    # NLCD collection on PC is not currently queryable, so WorldCover stands.
    cached_runoff = cached_or('mth_runoff_potential')
    if cached_runoff is not None:
        runoff = cached_runoff
        print("  reused cached SSURGO runoff")
    else:
        runoff = soil_runoff_feature(bbox_geo, flags)
    feats['mth_runoff_potential'] = runoff
    if feats.get('mth_impervious_pct') is not None and runoff is not None:
        feats['mth_stormwater_burden'] = round(
            (feats['mth_impervious_pct'] / 100.0) * runoff, 3)
    else:
        feats['mth_stormwater_burden'] = None

    # 4) Hydrology (vector REST; US coverage). Cheap; re-pull each time so a
    # changed bbox or service-side update is picked up. Cached HUC12 string is
    # preserved if present.
    cached_huc = cached_or('mth_huc12')
    feats.update(hydrology_features(bbox_geo, metric_crs, sad_poly_m, flags))
    if feats.get('mth_huc12') is None and cached_huc:
        feats['mth_huc12'] = cached_huc

    # 5) Air & light (Planetary Computer rasters). Each PC call can take
    # several scenes; cache + resume.
    cached_no2 = cached_or('mth_no2_mean')
    cached_nl = cached_or('mth_nightlight_mean')
    cached_nl_d = cached_or('mth_nightlight_vs_surroundings')
    if not skip_slow and cached_no2 is None:
        no2, _ = fetch_pc_scalar_field('sentinel-5p-l2-netcdf', 'no2',
                                       bbox_geo, metric_crs, sad_poly_m,
                                       canvas_poly_m, bbox_m, flags, 'no2')
        feats['mth_no2_mean'] = (round(no2, 6) if no2 is not None else None)
    elif cached_no2 is not None:
        feats['mth_no2_mean'] = cached_no2
    else:
        feats['mth_no2_mean'] = None

    if not skip_slow and (cached_nl is None or cached_nl_d is None):
        nl, nl_delta = fetch_pc_scalar_field('viirs-nighttime-lights', 'avg_rad',
                                             bbox_geo, metric_crs, sad_poly_m,
                                             canvas_poly_m, bbox_m, flags, 'viirs')
        feats['mth_nightlight_mean'] = (round(nl, 3) if nl is not None else None)
        feats['mth_nightlight_vs_surroundings'] = (round(nl_delta, 3)
                                                   if nl_delta is not None else None)
    elif cached_nl is not None:
        feats['mth_nightlight_mean'] = cached_nl
        feats['mth_nightlight_vs_surroundings'] = cached_nl_d
    else:
        feats['mth_nightlight_mean'] = None
        feats['mth_nightlight_vs_surroundings'] = None

    if skip_slow and (cached_no2 is None and cached_nl is None):
        flags.append('air_light_skipped')

    # 6) Biodiversity (rate-limited APIs; cached). Each API caches on disk
    # under data/_mth_cache/ regardless of resume, so this is doubly safe.
    cached_gbif_r = cached_or('mth_gbif_richness')
    cached_gbif_o = cached_or('mth_gbif_occurrences')
    cached_inat = cached_or('mth_inat_research_grade')
    cached_ebird = cached_or('mth_ebird_richness')
    if not skip_slow:
        if cached_gbif_r is None or cached_gbif_o is None:
            feats.update(gbif_features(bbox_geo, source_dir, flags))
        else:
            feats['mth_gbif_richness'] = cached_gbif_r
            feats['mth_gbif_occurrences'] = cached_gbif_o
        if cached_inat is None:
            feats.update(inat_feature(bbox_geo, source_dir, flags))
        else:
            feats['mth_inat_research_grade'] = cached_inat
        if cached_ebird is None:
            feats.update(ebird_feature(bbox_geo, source_dir, flags))
        else:
            feats['mth_ebird_richness'] = cached_ebird
    else:
        feats.update({'mth_gbif_richness': cached_gbif_r,
                      'mth_gbif_occurrences': cached_gbif_o,
                      'mth_inat_research_grade': cached_inat,
                      'mth_ebird_richness': cached_ebird})
        if all(v is None for v in (cached_gbif_r, cached_inat, cached_ebird)):
            flags.append('biodiversity_skipped')

    summary = {
        'sad_id': manifest.sad_id, 'sad_name': manifest.sad_name,
        'band': 'more_than_human',
        'sources': ('ESA WorldCover, NLCD, Sentinel-2/5P, VIIRS (Planetary Computer); '
                    'USGS NHD/WBD, FEMA NFHL, FWS NWI, USDA SSURGO; '
                    'GBIF, iNaturalist, eBird'),
        'features': feats,
        'flags': sorted(set(flags)),
        'feature_count': len([v for v in feats.values() if isinstance(v, (int, float))]),
    }
    out = derived_dir / 'more_than_human' / 'mth_summary.json'
    out.write_text(json.dumps(summary, indent=2, default=str))

    print(f"\n[OK] {manifest.sad_id}")
    for k, v in feats.items():
        print(f"  {k:34s} {v}")
    if summary['flags']:
        print(f"\n  FLAGS (re-pull / verify): {', '.join(summary['flags'])}")
    print(f"\n  wrote {out}")
    return out


def _looks_us(bbox_geo) -> bool:
    minx, miny, maxx, maxy = bbox_geo
    return (-170 < (minx + maxx) / 2 < -50) and (18 < (miny + maxy) / 2 < 72)


def main():
    p = argparse.ArgumentParser(description="More-than-human ecological profile for a SAD")
    p.add_argument('--derived', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--skip-slow', action='store_true',
                   help='Drop Sentinel-2 phenology, air/light, and biodiversity APIs '
                        'for a fast structural check (validate wiring before full pull)')
    args = p.parse_args()
    process_sad(args.derived, args.source, args.skip_slow)


if __name__ == '__main__':
    main()
