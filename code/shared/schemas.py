"""
schemas.py - pydantic data models for SAD pipeline outputs.

Every JSON file the pipeline produces conforms to one of these models.
This prevents schema drift across the 8 modules and makes the data flow
self-documenting.

Convention: load with `Manifest.parse_file(path)`, write with `manifest.json(indent=2)`.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ─── per-SAD geographic registration ──────────────────────────────────────────

class Manifest(BaseModel):
    """
    Written by Module 1. The georeferencing key for a single SAD.
    Any downstream module that needs to convert pixel↔geo reads this.
    """
    sad_id: str
    sad_name: str
    typology: str  # one of: entertainment, community, innovation, tourism
    anchor_venue: str
    
    # Geographic registration
    bbox_geo: tuple[float, float, float, float]  # (minlon, minlat, maxlon, maxlat) in EPSG:4326
    crs_source: str = "EPSG:4326"
    crs_metric: str  # local UTM zone, e.g. "EPSG:26917"
    
    # Image
    image_width_px: int = 1080
    image_height_px: int = 1080
    extent_meters: float  # nominal side length (e.g. 4286.0)
    
    # Affine transform: 6-tuple in rasterio order (a, b, c, d, e, f)
    # x_geo = a*px + b*py + c   ;   y_geo = d*px + e*py + f
    affine_geo_to_pixel: tuple[float, float, float, float, float, float]
    
    # Counts (sanity check)
    building_count: int
    

# ─── per-SAD CV outputs ───────────────────────────────────────────────────────

class FieldMetrics(BaseModel):
    """Whole-image (raster-level) metrics from Module 2."""
    coverage: float  # fraction of pixels that are solid
    component_count: int
    mean_component_size_px: float
    median_component_size_px: float
    max_component_size_px: float
    largest_void_px: float
    fractal_dimension: float
    hough_line_count: int
    horizontal_alignment_score: float
    vertical_alignment_score: float
    glcm_contrast: float
    glcm_homogeneity: float
    glcm_energy: float


class CVMetrics(BaseModel):
    """
    Written by Module 2. Combines whole-field raster metrics with a
    summary of per-building vector metrics (full per-building data lives
    in buildings_enriched.gpkg, not here).
    """
    sad_id: str
    field: FieldMetrics
    
    # Per-building summary stats (median, p25, p75)
    building_count: int
    median_area_m2: float
    median_compactness: float
    median_elongation: float
    median_neighbor_distance_m: float
    p25_area_m2: float
    p75_area_m2: float


# ─── unified district profile ─────────────────────────────────────────────────

class ProgramMix(BaseModel):
    """
    Aggregate of programs found in/around buildings via ROD spatial join.
    Categories follow Overture's taxonomy + Rossetti's seven-category rollup.
    """
    sport: float
    residential: float
    hotel: float
    retail_food_entertainment: float
    office: float
    parking: float
    open_space: float
    other: float
    total_places_inside: int
    total_places_adjacent: int  # within 10m of a building
    
    
class DemographicProfile(BaseModel):
    """ACS rollup for the block groups intersecting the SAD bbox."""
    total_population: int
    median_household_income: Optional[float]
    median_age: Optional[float]
    pct_white: Optional[float]
    pct_black: Optional[float]
    pct_hispanic: Optional[float]
    pct_bachelors_or_higher: Optional[float]
    pct_owner_occupied: Optional[float]
    pct_renter_occupied: Optional[float]
    block_groups_intersected: int


class DistrictProfile(BaseModel):
    """
    Written by Module 5. The unified vector representation of a SAD -
    morphology + program + demographics + typology label.
    
    This is the row that, stacked across all 37 SADs, becomes the
    feature matrix for Module 6's UMAP/clustering.
    """
    sad_id: str
    sad_name: str
    typology: str
    
    morphology: FieldMetrics
    program: ProgramMix
    demographics: DemographicProfile
    
    # Flattened numerical vector for ML use (computed property)
    def to_vector(self) -> list[float]:
        """Flatten to a single feature vector for clustering."""
        return (
            list(self.morphology.dict().values()) +
            [v for k, v in self.program.dict().items() if isinstance(v, (int, float))] +
            [v if v is not None else 0.0 for k, v in self.demographics.dict().items() 
             if isinstance(v, (int, float)) or v is None]
        )
