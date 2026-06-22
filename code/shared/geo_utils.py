"""
geo_utils.py - reusable geographic transforms and helpers.

These are the small operations that show up in 4+ modules: picking a UTM
zone for metric math, building image extents, converting between pixel
and geographic coordinates, etc. Putting them here prevents copy-paste
drift.
"""
from __future__ import annotations
import math
from typing import Sequence

from shapely.geometry import Polygon, Point, box
from shapely.ops import transform as shp_transform
from pyproj import Transformer
from rasterio.transform import from_bounds, Affine
import geopandas as gpd


# ─── CRS selection ────────────────────────────────────────────────────────────

def utm_epsg_for(lat: float, lon: float) -> str:
    """
    Return the EPSG code for the appropriate UTM zone covering (lat, lon).
    Northern hemisphere: 326XX. Southern: 327XX.
    
    Use this to pick a metric CRS for area/distance calculations near a SAD.
    """
    zone = int((lon + 180) / 6) + 1
    if lat >= 0:
        return f"EPSG:{32600 + zone}"
    return f"EPSG:{32700 + zone}"


# ─── Image extent generation ──────────────────────────────────────────────────

def make_image_extent(
    center_lat: float,
    center_lon: float,
    extent_meters: float,
    crs_geo: str = "EPSG:4326",
) -> tuple[Polygon, str]:
    """
    Build a square polygon `extent_meters` on a side, centered on (lat, lon).
    
    Returns (polygon_in_geo_crs, utm_crs_used).
    
    The polygon is a true geographic square (constructed in metric CRS,
    reprojected to EPSG:4326), so the LoRA training data has consistent
    physical extent across all 37 SADs regardless of latitude.
    """
    utm = utm_epsg_for(center_lat, center_lon)
    to_metric = Transformer.from_crs(crs_geo, utm, always_xy=True).transform
    to_geo = Transformer.from_crs(utm, crs_geo, always_xy=True).transform
    
    cx, cy = to_metric(center_lon, center_lat)
    half = extent_meters / 2.0
    square_m = box(cx - half, cy - half, cx + half, cy + half)
    square_geo = shp_transform(to_geo, square_m)
    return square_geo, utm


# ─── Pixel ↔ geo affine math ──────────────────────────────────────────────────

def affine_from_geo_bbox(
    bbox: Sequence[float],
    width_px: int,
    height_px: int,
) -> Affine:
    """
    Build the rasterio Affine transform that maps pixel space to a geo bbox.
    
    bbox: (minlon, minlat, maxlon, maxlat)
    Returns: Affine(a, b, c, d, e, f) where
        x_geo = a*col + b*row + c
        y_geo = d*col + e*row + f
    
    Note: rasterio's Affine flips the y-axis so row=0 is at the top of
    the image (north), which matches PIL/numpy convention.
    """
    minlon, minlat, maxlon, maxlat = bbox
    return from_bounds(minlon, minlat, maxlon, maxlat, width_px, height_px)


def geo_to_pixel(transform: Affine, lon: float, lat: float) -> tuple[int, int]:
    """Convert (lon, lat) to (col, row) pixel coordinates."""
    inv = ~transform
    col, row = inv * (lon, lat)
    return int(round(col)), int(round(row))


def pixel_to_geo(transform: Affine, col: int, row: int) -> tuple[float, float]:
    """Convert (col, row) pixel coordinates to (lon, lat)."""
    lon, lat = transform * (col + 0.5, row + 0.5)  # +0.5 = pixel center
    return lon, lat


# ─── Common GIS operations ────────────────────────────────────────────────────

def clip_to_extent(gdf: gpd.GeoDataFrame, extent_polygon: Polygon) -> gpd.GeoDataFrame:
    """
    Clip a GeoDataFrame to a polygon, dropping features that fall entirely outside.
    Returns a new GeoDataFrame with original geometries (not clipped - clipping
    breaks shape statistics; we just filter membership).
    """
    return gdf[gdf.intersects(extent_polygon)].copy()


def buffer_in_meters(gdf: gpd.GeoDataFrame, meters: float, utm_crs: str) -> gpd.GeoDataFrame:
    """Buffer features by N meters, handling CRS reprojection."""
    original_crs = gdf.crs
    metric = gdf.to_crs(utm_crs)
    metric.geometry = metric.geometry.buffer(meters)
    return metric.to_crs(original_crs)


# ─── Distance helpers ─────────────────────────────────────────────────────────

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))
