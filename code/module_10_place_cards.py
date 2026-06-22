"""
module_10_place_cards.py

Generates one-page "place card" briefings per SAD — single-glance
characterizations designed for non-technical audiences (urban planners,
ROD leadership, project kickoff handouts).

Each card includes:
  - Header: SAD name, city, anchor venue, one-sentence character description
  - Plan view: SAD-interior buildings colored by program, anchors
    outlined boldly, SAD boundary highlighted (+ scale bar, north arrow)
  - At-a-glance: 6 big-number callouts (buildings, anchors, top program %,
    median area, population density if available, etc.)
  - Program donut: composition of SAD-interior POIs
  - Strengths / Opportunities: rule-based bullet lists from boundary-effect
    data + cross-SAD comparisons

The character descriptions and strengths/opps use codified planner
heuristics, NOT generative AI. The rules live near the top of this file
and are designed to be inspected and edited.

USAGE
  python module_10_place_cards.py --data-dir <path> \\
      --sads <id1> <id2> ... [--out <output_dir>]

OUTPUT (in <output-dir>/place_cards/)
  <sad_id>_place_card.{png,svg}   One card per SAD
  portfolio_overview.{png,svg}     All cards arranged in a grid
  characterizations.json           Machine-readable summary
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import MultiPolygon

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon as MplPolygon, FancyArrowPatch

# Reuse the feature-gathering logic from module 08
sys.path.insert(0, str(Path(__file__).parent))
from module_08_district_embedding import gather_sad_features


# ─── Unit conversion helpers (m -> ft / mi / acres) ──────────────────────────

M_PER_FT = 0.3048
M2_PER_SQFT = 0.092903
M2_PER_ACRE = 4046.86
M2_PER_SQMI = 2.589988e6

def m2_to_sqft(m2):  return m2 / M2_PER_SQFT
def m2_to_acres(m2): return m2 / M2_PER_ACRE
def m2_to_sqmi(m2):  return m2 / M2_PER_SQMI


# ─── Typology integration ──────────────────────────────────────────────────

def load_typology_for_sad(derived_dir):
    """Load derived/typology.json (written by setup_typologies.py).

    Returns a dict with primary_typology, secondary_typology, sad_name,
    anchor_venue, notes — or None if the file isn't there yet (run
    setup_typologies.py to generate them)."""
    typ_path = derived_dir / 'typology.json'
    if not typ_path.exists():
        return None
    try:
        return json.loads(typ_path.read_text())
    except Exception:
        return None


def compute_typology_peer_stats(data_dir, sad_ids, min_peers=3):
    """For each typology, compute median values for every numeric feature
    across SADs of that typology. Returns:
        {
            typology_name: {
                'n_peers':  int,
                'medians':  {feature_name: median_value, ...},
                'sad_ids':  [sad_id, ...],
            },
            ...
        }

    Typologies with fewer than `min_peers` SADs are SKIPPED so character
    descriptions don't quote unreliable comparisons.
    """
    import numpy as np
    from collections import defaultdict

    by_typology = defaultdict(list)
    for sad_id in sad_ids:
        derived = data_dir / sad_id / 'derived'
        typ_data = load_typology_for_sad(derived)
        if not typ_data:
            continue
        primary = typ_data.get('primary_typology')
        if not primary:
            continue
        try:
            sd = gather_sad_features(data_dir, sad_id,
                                       include_demographics=True)
            features = dict(sd['features'])
            features['sad_area_km2'] = sd['sad_area_km2']
            features['buildings_inside_count'] = sd['buildings_inside_count']
            by_typology[primary].append((sad_id, features))
        except Exception:
            continue

    out = {}
    for typ, items in by_typology.items():
        if len(items) < min_peers:
            continue
        feat_lists = defaultdict(list)
        for sad_id, feats in items:
            for k, v in feats.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    feat_lists[k].append(float(v))
        medians = {k: float(np.median(vs)) for k, vs in feat_lists.items()
                   if len(vs) >= min_peers}
        out[typ] = {
            'n_peers':  len(items),
            'medians':  medians,
            'sad_ids':  [s for s, _ in items],
        }
    return out


# ─── Visual palette (matches deck and pipeline) ────────────────────────

COLOR = {
    'navy':       '#1B2845',
    'navy_light': '#2E4A6E',
    'coral':      '#D97757',
    'coral_dark': '#B85A3F',
    'cream':      '#F5F1EB',
    'card_bg':    '#FFFFFF',
    'text':       '#2C2C2C',
    'text_muted': '#6B6B6B',
    'text_light': '#9A9A9A',
    'rule':       '#D6D0C7',
    'plan_outside': '#E8E2D5',
    'plan_inside':  '#FAF7F0',
}

PROGRAM_COLORS = {
    'sport':                     '#D62728',
    'residential':               '#2CA02C',
    'hotel':                     '#9467BD',
    'retail_food_entertainment': '#FF7F0E',
    'office':                    '#1F77B4',
    'parking':                   '#8C564B',
    'open_space':                '#BCBD22',
    'other':                     '#9C9C9C',
}

PROGRAM_LABELS = {
    'sport':                     'Sport',
    'residential':               'Residential',
    'hotel':                     'Hotel',
    'retail_food_entertainment': 'Retail / F&B',
    'office':                    'Office',
    'parking':                   'Parking',
    'open_space':                'Open space',
    'other':                     'Other',
}


# ─── Plan-overlay colors (for ground layers in plan view) ───────────────────
# These are the colors used for parking lots, parks/open space, and roads
# AS LAND LAYERS in the plan view — distinct from POI-program building
# colors above. Tones are muted so they read as ground rather than figure.
PARKING_LOT_COLOR = '#C8B8A6'   # warm tan
PARK_LAND_COLOR   = '#A8D5BA'   # muted sage
ROAD_COLOR        = '#8a8a8a'   # medium neutral grey
ROAD_WIDTH_PT     = 0.7


# ─── Demographic context helpers ────────────────────────────────────────────

# FIPS state code -> 2-letter abbreviation. Used in the ACS source citation.
STATE_FIPS_TO_ABBR = {
    '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA', '08': 'CO',
    '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL', '13': 'GA', '15': 'HI',
    '16': 'ID', '17': 'IL', '18': 'IN', '19': 'IA', '20': 'KS', '21': 'KY',
    '22': 'LA', '23': 'ME', '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN',
    '28': 'MS', '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
    '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND', '39': 'OH',
    '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI', '45': 'SC', '46': 'SD',
    '47': 'TN', '48': 'TX', '49': 'UT', '50': 'VT', '51': 'VA', '53': 'WA',
    '54': 'WV', '55': 'WI', '56': 'WY', '72': 'PR',
}

# ACS 5-year vintage. M4 pulls ACS at the year specified in its CLI,
# which defaults to the most recent available year. The displayed range
# is the 5-year window ending in that year.
ACS_END_YEAR = 2023
ACS_START_YEAR = ACS_END_YEAR - 4    # 5-year ACS = current + 4 prior


def _format_tract_code(tractce):
    """Convert TRACTCE (6-digit FIPS string like '320700' or '012901')
    to display form. Trailing ".00" is dropped, ".XX" suffix kept."""
    s = str(tractce).zfill(6)
    main = s[:4].lstrip('0') or '0'
    suffix = s[4:]
    if suffix == '00':
        return main
    return f"{main}.{suffix}"


def load_demographic_metadata(source_dir, sad_boundary_metric, metric_crs):
    """Load census block groups + identify the one with the largest
    SAD overlap. Returns dict with citation fields, or None if no
    census data is available (e.g. Canadian SADs)."""
    bg_path = source_dir / 'census_blockgroups.gpkg'
    if not bg_path.exists():
        return None
    try:
        bgs = gpd.read_file(bg_path)
    except Exception:
        return None
    if bgs.empty:
        return None
    try:
        bgs = bgs.to_crs(metric_crs)
    except Exception:
        pass

    # For each block group, compute intersection area with SAD
    try:
        bgs = bgs.copy()
        bgs['_overlap_area'] = bgs.geometry.apply(
            lambda g: sad_boundary_metric.intersection(g).area
            if g is not None and not g.is_empty else 0.0)
    except Exception:
        return None

    intersecting = bgs[bgs['_overlap_area'] > 1.0]
    if intersecting.empty:
        return None

    # Dominant block group: largest intersection area
    dominant = intersecting.sort_values('_overlap_area',
                                          ascending=False).iloc[0]
    statefp = str(dominant.get('STATEFP', '')).zfill(2)
    tractce = str(dominant.get('TRACTCE', ''))
    blkgrpce = str(dominant.get('BLKGRPCE', ''))
    state_abbr = STATE_FIPS_TO_ABBR.get(statefp, statefp)

    n_intersecting = int(len(intersecting))
    return {
        'statefp':         statefp,
        'state_abbr':      state_abbr,
        'tractce':         tractce,
        'tract_display':   _format_tract_code(tractce),
        'blkgrpce':        blkgrpce,
        'n_block_groups':  n_intersecting,
        'n_other_bgs':     max(0, n_intersecting - 1),
        'acs_start_year':  ACS_START_YEAR,
        'acs_end_year':    ACS_END_YEAR,
    }


# ─── Building program classifier ──────────────────────────────────────
# `dominant_program_inside` (POI count winner) routes stadia to retail/F&B
# because a stadium has many more concession POIs than sport POIs inside.
# The OSM `building` tag is often more authoritative — but not always:
# OSM mappers sometimes tag stadium footprints as `commercial` or `retail`
# (especially older stadiums or non-Premier-League US venues), which would
# silently route them to retail/F&B even though the name says otherwise.
#
# Priority order (each step overrides ALL subsequent steps):
#   1. Strong venue-name match for large buildings (most reliable signal)
#   2. OSM `building` tag for explicit non-default values
#   3. dominant_program_inside (POI count winner) — original behavior
#   4. 'other'
#
# Name-based checks REQUIRE area > MIN_VENUE_AREA_M2 to avoid catching
# small "Park Place Cafe" or "The Field House" type non-venues.

MIN_VENUE_AREA_M2 = 4000

# Unambiguous sport venue tokens (any appearance triggers, given area gate)
SPORT_VENUE_NAME_TOKENS = (
    'stadium', 'arena', 'ballpark', 'coliseum', 'speedway',
    'velodrome', 'racetrack', 'fieldhouse',
)

# Ambiguous tokens — require the name to have 2+ words AND end with this token
# (avoids "Park Avenue", "Park Place", "The Field", etc.)
SPORT_VENUE_NAME_SUFFIXES = ('park', 'field', 'dome', 'bowl', 'garden', 'forum')

OSM_BUILDING_TO_PROGRAM = {
    # Sport venues
    'stadium': 'sport', 'sports_hall': 'sport', 'sports_centre': 'sport',
    'sports_center': 'sport', 'pavilion': 'sport',
    # Hospitality
    'hotel': 'hotel', 'motel': 'hotel', 'hostel': 'hotel',
    # Residential
    'apartments': 'residential', 'house': 'residential',
    'residential': 'residential', 'terrace': 'residential',
    'bungalow': 'residential', 'dormitory': 'residential',
    'detached': 'residential', 'semidetached_house': 'residential',
    # Retail / commercial
    'retail': 'retail_food_entertainment',
    'supermarket': 'retail_food_entertainment',
    'commercial': 'retail_food_entertainment',
    'shop': 'retail_food_entertainment',
    'restaurant': 'retail_food_entertainment',
    'kiosk': 'retail_food_entertainment',
    # Office / civic / academic
    'office': 'office',
    'school': 'office', 'university': 'office', 'college': 'office',
    'civic': 'office', 'government': 'office', 'public': 'office',
    # Parking
    'parking': 'parking', 'garage': 'parking', 'garages': 'parking',
    'carport': 'parking',
    # Other
    'warehouse': 'other', 'industrial': 'other',
    'church': 'other', 'cathedral': 'other', 'mosque': 'other',
    'temple': 'other', 'chapel': 'other',
}


def normalize_name(name):
    """Lowercase, strip whitespace, drop parenthetical suffix.
    'Comerica Park (Detroit Tigers)' -> 'comerica park'"""
    if not isinstance(name, str):
        return ''
    n = name.lower().strip()
    if '(' in n:
        n = n.split('(')[0].strip()
    return n


def classify_building_program(row, debug=False) -> str:
    """Return the best-guess program tag for a building.
    Priority: venue name (most reliable) -> OSM tag -> POI dominance.
    """
    name = row.get('name')
    area = row.get('area_m2', 0) or 0
    osm_tag = row.get('building')
    
    # 1. Strong venue-name match (large buildings only). FIRST priority
    # because OSM mappers sometimes mistag stadium footprints as 'commercial'.
    if area > MIN_VENUE_AREA_M2:
        n = normalize_name(name)
        if n:
            # Unambiguous tokens anywhere in the name
            for token in SPORT_VENUE_NAME_TOKENS:
                if token in n:
                    if debug:
                        print(f"      classify [{name}]: matched token "
                              f"'{token}' -> sport")
                    return 'sport'
            # Ambiguous suffixes: only if name ends with token AND multi-word
            for suffix in SPORT_VENUE_NAME_SUFFIXES:
                if (n.endswith(' ' + suffix) or n == suffix) and \
                        len(n.split()) >= 2:
                    if debug:
                        print(f"      classify [{name}]: matched suffix "
                              f"'{suffix}' -> sport")
                    return 'sport'
    
    # 2. OSM building tag
    if isinstance(osm_tag, str):
        tag = osm_tag.lower().strip()
        if tag in OSM_BUILDING_TO_PROGRAM:
            mapped = OSM_BUILDING_TO_PROGRAM[tag]
            if debug:
                print(f"      classify [{name or '(unnamed)'}]: OSM tag "
                      f"'{tag}' -> {mapped}")
            return mapped
    
    # 3. POI-based dominance (original behavior)
    prog = row.get('dominant_program_inside')
    if isinstance(prog, str) and prog:
        if debug:
            print(f"      classify [{name or '(unnamed)'}]: POI dominance "
                  f"-> {prog}")
        return prog
    
    return 'other'


# ─── Codified planner heuristics ───────────────────────────────────────
# These rules translate measured features into legible planner language.
# Tunable — edit thresholds based on your firm's typology conventions.

DENSITY_RULES = [
    (0.25,  'high-density urban'),
    (0.15,  'medium-density urban'),
    (0.08,  'moderate-density'),
    (0.0,   'low-density / suburban'),
]

def density_tag(coverage):
    for thresh, label in DENSITY_RULES:
        if coverage >= thresh:
            return label
    return 'low-density'


def program_tags(features):
    """List of program-character tags, in order of strength."""
    tags = []
    office = features.get('prog_pct_office', 0)
    retail = features.get('prog_pct_retail_food_entertainment', 0)
    sport = features.get('prog_pct_sport', 0)
    hotel = features.get('prog_pct_hotel', 0)
    residential = features.get('prog_pct_residential', 0)
    
    if office >= 45:
        tags.append('office-dominated')
    elif office >= 30:
        tags.append('office-rich')
    
    if retail >= 35:
        tags.append('retail-led')
    elif retail >= 25:
        tags.append('retail-rich')
    
    if sport >= 7:
        tags.append('sport-anchored')
    elif sport >= 3:
        tags.append('with notable sport presence')
    
    if hotel >= 4:
        tags.append('hospitality-supported')
    
    if residential >= 10:
        tags.append('residentially integrated')
    
    if not tags:
        tags.append('mixed-program')
    
    return tags


def anchor_structure_tag(features):
    """One tag describing the anchor configuration."""
    n_anchors = features.get('anchor_count_inside', 0)
    max_ratio = features.get('anchor_max_area_ratio', 0)
    
    if max_ratio >= 0.20:
        return 'monolithic-anchor'
    if n_anchors >= 5:
        return 'multi-anchor'
    if n_anchors >= 2:
        return 'few-anchor'
    if n_anchors >= 1:
        return 'single-anchor'
    return 'anchor-light'


def describe_character(features, sad_meta, peer_stats=None):
    """Synthesize a 2-3 sentence character description.

    Combines typology framing, peer-comparison (when reliable peer
    statistics are available for the SAD's primary typology), and
    specific morphometric anchors. The goal is prose that reads as
    distinctive rather than formulaic.

    peer_stats: output of compute_typology_peer_stats(), or None.
    """
    typology = (sad_meta.get('primary_typology') or
                sad_meta.get('typology') or '').strip()
    secondary = (sad_meta.get('secondary_typology') or '').strip()
    anchor_venue = (sad_meta.get('anchor_venue') or '').strip()
    sad_name = (sad_meta.get('sad_name') or '').strip()

    coverage = features.get('morph_coverage', 0)
    n_anchors = int(features.get('anchor_count_inside', 0))
    max_ratio = features.get('anchor_max_area_ratio', 0)
    median_area_m2 = features.get('morph_median_area_m2', 0)
    median_area_sqft = m2_to_sqft(median_area_m2) if median_area_m2 else 0
    prog_div = features.get('prog_diversity', 0)
    retail_pct = features.get('prog_pct_retail_food_entertainment', 0)
    office_pct = features.get('prog_pct_office', 0)
    res_pct = features.get('prog_pct_residential', 0)
    hotel_pct = features.get('prog_pct_hotel', 0)
    sport_pct = features.get('prog_pct_sport', 0)
    open_pct = features.get('prog_pct_open_space', 0)

    # ── Sentence 1: Typology framing ─────────────────────────────────
    short_name = sad_name.split(',')[0].strip() if ',' in sad_name else sad_name
    if not short_name:
        short_name = "This district"

    if typology and secondary:
        type_phrase = (f"{typology}-typology SAD with a "
                       f"{secondary.lower()} secondary character")
    elif typology:
        type_phrase = f"{typology}-typology SAD"
    else:
        type_phrase = "SAD"

    if anchor_venue and anchor_venue.lower() not in ('unknown', '', 'none'):
        if len(anchor_venue) > 60:
            anchor_short = anchor_venue.split(',')[0].strip()
            anchor_clause = f"anchored by {anchor_short} and a small set of co-anchors"
        else:
            anchor_clause = f"anchored by {anchor_venue}"
    else:
        anchor_clause = (f"with {n_anchors} detected anchor"
                          + ("s" if n_anchors != 1 else ""))

    sentence_1 = f"{short_name} is an {type_phrase}, {anchor_clause}."

    # ── Sentence 2: What's distinctive (peer comparison if available) ──
    distinctive_phrases = []
    peer = (peer_stats or {}).get(typology, {})
    medians = peer.get('medians', {}) if peer else {}
    n_peers = peer.get('n_peers', 0) if peer else 0

    def compare(value, key, label, fmt='{:.0f}%', threshold_rel=0.30):
        """Return a 'higher than'/'lower than' clause if the deviation
        from typology peer median exceeds threshold_rel (relative)."""
        if not medians or key not in medians:
            return None
        med = medians[key]
        if med < 1e-6:
            return None
        rel = (value - med) / med
        if abs(rel) < threshold_rel:
            return None
        direction = 'above' if rel > 0 else 'below'
        return (f"{label} runs {fmt.format(value)} "
                f"({direction} the {typology}-typology median of {fmt.format(med)})")

    if n_peers >= 3:
        # Pick up to 2 strongest deviations to mention
        candidates = []
        for value, key, label in [
            (retail_pct, 'prog_pct_retail_food_entertainment', 'retail/F&B program share'),
            (office_pct, 'prog_pct_office', 'office program share'),
            (res_pct,   'prog_pct_residential',  'residential share'),
            (hotel_pct, 'prog_pct_hotel',        'hotel share'),
            (sport_pct, 'prog_pct_sport',        'sport-program share'),
            (open_pct,  'prog_pct_open_space',   'open-space share'),
        ]:
            phrase = compare(value, key, label, '{:.0f}%')
            if phrase:
                candidates.append((abs(value - medians.get(key, 0)), phrase))
        # Add coverage / density comparisons too
        if 'morph_coverage' in medians and coverage > 0:
            cov_pct = coverage * 100
            med_cov_pct = medians['morph_coverage'] * 100
            if med_cov_pct > 1 and abs(cov_pct - med_cov_pct) / med_cov_pct > 0.25:
                dir_ = 'above' if cov_pct > med_cov_pct else 'below'
                candidates.append((abs(cov_pct - med_cov_pct),
                    f"built coverage at {cov_pct:.0f}% "
                    f"({dir_} the {typology} median of {med_cov_pct:.0f}%)"))

        candidates.sort(key=lambda x: -x[0])
        distinctive_phrases = [p for _, p in candidates[:2]]

    if distinctive_phrases:
        sentence_2 = ("Among its " + str(n_peers) +
                       f" {typology}-typology peers, it stands apart with " +
                       ' and '.join(distinctive_phrases) + ".")
    else:
        # Fallback when no peer stats: describe the program mix concretely
        prog_pieces = []
        if retail_pct >= 5: prog_pieces.append(f"{retail_pct:.0f}% retail/F&B")
        if office_pct >= 5: prog_pieces.append(f"{office_pct:.0f}% office")
        if hotel_pct >= 3:  prog_pieces.append(f"{hotel_pct:.0f}% hotel")
        if res_pct >= 3:    prog_pieces.append(f"{res_pct:.0f}% residential")
        if sport_pct >= 5:  prog_pieces.append(f"{sport_pct:.0f}% sport")
        if prog_pieces:
            sentence_2 = ("Program mix: " +
                           ', '.join(prog_pieces[:3]) + ".")
        else:
            sentence_2 = ""

    # ── Sentence 3: Defining urban quality (concise morphology read) ────
    fabric_parts = []
    if median_area_sqft:
        if median_area_sqft < 3000:
            fabric_parts.append(f"fine-grain fabric ({median_area_sqft:,.0f} sq ft median building)")
        elif median_area_sqft < 10000:
            fabric_parts.append(f"mid-grain fabric ({median_area_sqft:,.0f} sq ft median building)")
        else:
            fabric_parts.append(f"large-format fabric ({median_area_sqft:,.0f} sq ft median building)")

    if max_ratio >= 0.20:
        fabric_parts.append("a single dominant anchor footprint")
    elif n_anchors >= 4:
        fabric_parts.append(f"a distributed system of {n_anchors} anchors")

    if fabric_parts:
        sentence_3 = ("Reads as " + " and ".join(fabric_parts) + ".")
    else:
        sentence_3 = ""

    paragraph = ' '.join(s for s in [sentence_1, sentence_2, sentence_3] if s)

    # Tags returned for downstream JSON
    tags = {
        'density': density_tag(coverage),
        'programs': program_tags(features),
        'anchor_structure': anchor_structure_tag(features),
        'peer_comparison': bool(distinctive_phrases),
        'n_typology_peers': n_peers,
    }
    return paragraph, tags


def derive_strengths_opportunities(features, sad_meta, boundary_diffs):
    """
    Generate planner-meaningful strengths and opportunities.
    
    Typology-aware: what counts as a strength for an entertainment district
    differs from what counts for a corporate or community district. Rules
    are deliberately conservative (each rule produces at most one bullet)
    and inspectable. Edit thresholds based on firm conventions.
    """
    typology = (sad_meta.get('typology') or '').lower()
    
    n_anchors = features.get('anchor_count_inside', 0)
    max_anchor_ratio = features.get('anchor_max_area_ratio', 0)
    prog_div = features.get('prog_diversity', 0)
    coverage = features.get('morph_coverage', 0)
    density = features.get('morph_density_per_km2', 0)
    median_area = features.get('morph_median_area_m2', 0)
    
    office_pct = features.get('prog_pct_office', 0)
    retail_pct = features.get('prog_pct_retail_food_entertainment', 0)
    sport_pct = features.get('prog_pct_sport', 0)
    hotel_pct = features.get('prog_pct_hotel', 0)
    res_pct = features.get('prog_pct_residential', 0)
    parking_pct = features.get('prog_pct_parking', 0)
    open_space_pct = features.get('prog_pct_open_space', 0)
    
    # Pick the dominant program by percentage
    prog_pcts = [
        ('retail_food_entertainment', retail_pct, 'retail/F&B'),
        ('office', office_pct, 'office'),
        ('sport', sport_pct, 'sport'),
        ('hotel', hotel_pct, 'hospitality'),
        ('residential', res_pct, 'residential'),
    ]
    top_cat, top_pct, top_label = max(prog_pcts, key=lambda x: x[1])
    
    # ── STRENGTHS ────────────────────────────────────────────────────
    strengths = []
    
    # Anchor structure as strength
    if n_anchors >= 5:
        strengths.append(
            f"Multi-anchor district ({int(n_anchors)} anchors), "
            f"reducing reliance on any single venue.")
    elif n_anchors >= 3:
        strengths.append(
            f"Distributed anchor structure ({int(n_anchors)} anchors).")
    
    # Hotel + sport pairing — event-day economy
    if hotel_pct >= 3.5 and sport_pct >= 5:
        strengths.append(
            f"Hotel + sport pairing ({hotel_pct:.0f}% / {sport_pct:.0f}%) "
            f"supports event-day visitor economy.")
    
    # Programmatic diversity (entropy > moderate)
    if prog_div >= 1.5 and top_pct < 50:
        strengths.append(
            f"Mixed-use programmatic profile (no single program above "
            f"{top_pct:.0f}%).")
    
    # Walkable fine-grain fabric
    if density >= 1500 and median_area <= 600:
        density_per_sqmi = density * 2.589988
        strengths.append(
            f"Walkable fine-grain fabric ({m2_to_sqft(median_area):,.0f} sq ft "
            f"median building, {density_per_sqmi:,.0f} buildings/sq mi).")
    elif coverage >= 0.22:
        strengths.append(
            f"Dense built fabric ({coverage*100:.0f}% coverage).")
    
    # Typology-aligned dominant program
    if ('entertainment' in typology and retail_pct >= 30 and
            sport_pct >= 3):
        strengths.append(
            f"Retail + sport base ({retail_pct:.0f}% / {sport_pct:.0f}%) "
            f"aligned with entertainment typology.")
    elif 'tourism' in typology and (hotel_pct >= 3 or sport_pct >= 5):
        strengths.append(
            f"Tourism-supporting anchor mix "
            f"(hotel {hotel_pct:.0f}% / sport {sport_pct:.0f}%).")
    
    # Demographic strengths (if census available)
    bach = features.get('demo_pct_bachelors_plus', 0)
    pop_density = features.get('demo_population_density', 0)
    income = features.get('demo_median_hh_income', 0)
    if bach >= 35:
        strengths.append(
            f"Educated catchment ({bach:.0f}% bachelor's+ in surrounding area).")
    if pop_density >= 3000:
        pop_per_sqmi = pop_density * 2.589988
        strengths.append(
            f"Dense urban catchment "
            f"({pop_per_sqmi:,.0f} people/sq mi around SAD).")
    
    # ── OPPORTUNITIES ───────────────────────────────────────────────
    opps = []
    
    # Typology-program mismatch — strong signal
    if 'entertainment' in typology and office_pct >= 40:
        opps.append(
            f"Program mix ({office_pct:.0f}% office) reads more "
            f"corporate than the 'entertainment' typology suggests.")
    elif 'community' in typology and office_pct >= 30:
        opps.append(
            f"Office concentration ({office_pct:.0f}%) tilts the SAD "
            f"toward daytime workforce rather than community fabric.")
    elif 'office' in typology and retail_pct >= 35:
        opps.append(
            f"Retail share ({retail_pct:.0f}%) suggests less "
            f"office-monoculture than typology label implies.")
    
    # 24-hour activation gap
    if res_pct < 1.5 and hotel_pct < 3:
        opps.append(
            f"Limited 24-hour activation "
            f"(residential {res_pct:.1f}%, hotel {hotel_pct:.1f}%).")
    elif res_pct < 1:
        opps.append(
            f"Minimal residential program ({res_pct:.1f}%) limits "
            f"non-event vitality.")
    
    # Single-anchor concentration risk
    if max_anchor_ratio >= 0.25:
        opps.append(
            f"Single-anchor dependency: top anchor occupies "
            f"{max_anchor_ratio*100:.0f}% of the SAD's built area.")
    elif max_anchor_ratio >= 0.15 and n_anchors <= 2:
        opps.append(
            f"Concentrated anchor footprint "
            f"(top anchor = {max_anchor_ratio*100:.0f}% of built area).")
    
    # Mono-programmatic dominance
    if top_pct >= 55:
        opps.append(
            f"Mono-programmatic profile ({top_label} = {top_pct:.0f}% "
            f"of POIs) limits programmatic resilience.")
    
    # Auto-supportive fabric
    if parking_pct >= 5 and res_pct < 3:
        opps.append(
            f"Auto-supportive fabric "
            f"({parking_pct:.0f}% parking POIs with limited residential).")
    
    # Open space deficit
    if open_space_pct < 1.5 and coverage > 0.10:
        opps.append(
            f"Open space deficit ({open_space_pct:.1f}% of POIs) "
            f"in a built-up fabric.")
    
    # Take top 3 of each (rules above are ordered by importance)
    return strengths[:3], opps[:3]


# ─── Geometry helpers ─────────────────────────────────────────────────

def to_polygon(geom):
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def load_per_sad_data(data_dir, sad_id):
    """Load all data needed to render this SAD's place card."""
    source = data_dir / sad_id / 'source'
    derived = data_dir / sad_id / 'derived'
    
    # SAD boundary
    sad_gdf = gpd.read_file(source / 'sad_boundary.geojson')
    try:
        sad_boundary = sad_gdf.union_all()
    except AttributeError:
        sad_boundary = sad_gdf.unary_union
    
    # Buildings (full canvas)
    buildings_full = gpd.read_file(derived / 'buildings_enriched.gpkg',
                                    layer='buildings')
    metric_crs = buildings_full.estimate_utm_crs()
    buildings_full = buildings_full.to_crs(metric_crs)
    
    sad_boundary_metric = (gpd.GeoSeries([sad_boundary], crs=sad_gdf.crs)
                            .to_crs(metric_crs).iloc[0])
    
    # Filter to SAD-interior by centroid
    in_sad_mask = buildings_full.geometry.centroid.within(sad_boundary_metric)
    buildings_inside = buildings_full[in_sad_mask].copy()
    
    # Anchor building IDs from polar plots
    anchor_ids = set()
    polar_path = derived / 'anchor_polar_plots.json'
    if polar_path.exists():
        polar_data = json.loads(polar_path.read_text())
        for a in polar_data.get('anchors', []):
            bid = a.get('building_id')
            if bid:
                anchor_ids.add(bid)
    
    # Boundary effect diffs (interior_pct - exterior_pct per category)
    boundary_diffs = {}
    iex_path = derived / 'interior_exterior_signature.json'
    if iex_path.exists():
        iex_data = json.loads(iex_path.read_text())
        for cat_data in iex_data.get('categories', []):
            cat = cat_data['category']
            boundary_diffs[cat] = float(cat_data.get('difference', 0))
    
    # District profile (for name + anchor_venue + typology + city)
    profile = {}
    profile_path = derived / 'district_profile.json'
    if profile_path.exists():
        profile = json.loads(profile_path.read_text())

    # Typology classification (written by setup_typologies.py).
    # Merge into the profile dict so callers can read it transparently
    # without knowing about the separate typology.json file.
    typology_data = load_typology_for_sad(derived)
    if typology_data:
        if typology_data.get('primary_typology'):
            profile['primary_typology']   = typology_data['primary_typology']
            # Also expose under the legacy 'typology' key for back-compat
            profile.setdefault('typology', typology_data['primary_typology'])
        if typology_data.get('secondary_typology'):
            profile['secondary_typology'] = typology_data['secondary_typology']
        if typology_data.get('anchor_venue'):
            profile.setdefault('anchor_venue', typology_data['anchor_venue'])
        if typology_data.get('sad_name'):
            profile.setdefault('sad_name', typology_data['sad_name'])
    
    # Plan-overlay layers (parking lots, parks/open space, roads).
    # Loaded lazily and clipped to the SAD interior so the plan view can
    # show them underneath the building footprints. Missing files are
    # non-fatal — render_plan_view skips gracefully.
    plan_parking = plan_parks = plan_highways = None
    for fname, target in [('parking.geojson',  'parking'),
                           ('parks.geojson',    'parks'),
                           ('highways.geojson', 'highways')]:
        p = source / fname
        if not p.exists():
            continue
        try:
            gdf = gpd.read_file(p)
            if gdf.crs is None:
                gdf = gdf.set_crs(sad_gdf.crs)
            gdf = gdf.to_crs(metric_crs)
            gdf = gpd.clip(gdf, sad_boundary_metric)
            if gdf.empty:
                continue
            if target == 'parking':
                plan_parking = gdf
            elif target == 'parks':
                plan_parks = gdf
            else:
                plan_highways = gdf
        except Exception:
            pass

    return {
        'sad_id': sad_id,
        'sad_boundary': sad_boundary_metric,
        'sad_crs': metric_crs,
        'sad_area_km2': sad_boundary_metric.area / 1e6,
        'buildings_inside': buildings_inside,
        'buildings_full': buildings_full,
        'canvas_bounds': _resolve_canvas_bounds(source, buildings_full,
                                                  metric_crs, sad_gdf.crs),
        'anchor_ids': anchor_ids,
        'boundary_diffs': boundary_diffs,
        'profile': profile,
        # Plan overlays
        'plan_parking':  plan_parking,
        'plan_parks':    plan_parks,
        'plan_highways': plan_highways,
        # Demographic citation metadata (None for SADs without census data)
        'demo_metadata': load_demographic_metadata(
            source, sad_boundary_metric, metric_crs),
    }


def _resolve_canvas_bounds(source_dir, buildings_full, metric_crs, source_crs):
    """Return (minx, miny, maxx, maxy) for the canvas extent.
    Prefers source/image_extent.geojson if present; falls back to the
    total bounds of all buildings in the canvas."""
    img_ext_path = source_dir / 'image_extent.geojson'
    if img_ext_path.exists():
        try:
            ext_gdf = gpd.read_file(img_ext_path)
            if ext_gdf.crs is None:
                ext_gdf = ext_gdf.set_crs(source_crs)
            return tuple(ext_gdf.to_crs(metric_crs).total_bounds)
        except Exception:
            pass
    return tuple(buildings_full.total_bounds)


# ─── Plan view rendering ──────────────────────────────────────────────

def render_plan_view(ax, sad_data):
    """Render the SAD-interior plan: buildings colored by program, anchors
    boldly outlined, SAD boundary highlighted, scale bar + north arrow."""
    sad_boundary = sad_data['sad_boundary']
    buildings_inside = sad_data['buildings_inside']
    anchor_ids = sad_data['anchor_ids']
    
    # Tight bounding box around SAD with small buffer for breathing room
    minx, miny, maxx, maxy = sad_boundary.bounds
    span = max(maxx - minx, maxy - miny)
    buffer = span * 0.04
    
    # SAD-interior region tinted lightly
    sad_p = to_polygon(sad_boundary)
    sad_x = [c[0] for c in sad_p.exterior.coords]
    sad_y = [c[1] for c in sad_p.exterior.coords]
    ax.fill(sad_x, sad_y, facecolor=COLOR['plan_inside'],
            edgecolor='none', zorder=1)

    # ── Plan overlays (ground layers BEFORE buildings) ───────────────
    # Roads as thin grey lines under everything else but above ground tint.
    highways = sad_data.get('plan_highways')
    if highways is not None and not highways.empty:
        for geom in highways.geometry:
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == 'LineString':
                lines = [geom]
            elif geom.geom_type == 'MultiLineString':
                lines = list(geom.geoms)
            else:
                continue
            for line in lines:
                xs = [c[0] for c in line.coords]
                ys = [c[1] for c in line.coords]
                ax.plot(xs, ys, color=ROAD_COLOR,
                        linewidth=ROAD_WIDTH_PT, zorder=1.5,
                        solid_capstyle='round', alpha=0.65)

    # Parking lot polygons (warm tan fill, low alpha so they read as ground)
    parking_gdf = sad_data.get('plan_parking')
    if parking_gdf is not None and not parking_gdf.empty:
        for geom in parking_gdf.geometry:
            poly = to_polygon(geom)
            if poly is None or poly.is_empty:
                continue
            x = [c[0] for c in poly.exterior.coords]
            y = [c[1] for c in poly.exterior.coords]
            ax.fill(x, y, facecolor=PARKING_LOT_COLOR,
                    edgecolor='none', zorder=2, alpha=0.75)

    # Parks / open space polygons (sage green fill)
    parks_gdf = sad_data.get('plan_parks')
    if parks_gdf is not None and not parks_gdf.empty:
        for geom in parks_gdf.geometry:
            poly = to_polygon(geom)
            if poly is None or poly.is_empty:
                continue
            x = [c[0] for c in poly.exterior.coords]
            y = [c[1] for c in poly.exterior.coords]
            ax.fill(x, y, facecolor=PARK_LAND_COLOR,
                    edgecolor='none', zorder=2.2, alpha=0.85)
    
    # Buildings, colored by program (with stadium-aware classifier)
    for idx, row in buildings_inside.iterrows():
        poly = to_polygon(row.geometry)
        if poly is None or poly.is_empty:
            continue
        prog = classify_building_program(row)
        color = PROGRAM_COLORS.get(prog, PROGRAM_COLORS['other'])

        is_anchor = row.get('building_id') in anchor_ids
        # Anchor override: in a Sports-Anchored District the anchor IS
        # the sports venue by definition, regardless of what the
        # POI-dominant-program classifier returned (which often routes
        # stadia to retail/F&B because of concession POIs).
        if is_anchor:
            color = PROGRAM_COLORS['sport']

        edge_color = COLOR['navy'] if is_anchor else '#555'
        edge_width = 1.4 if is_anchor else 0.25
        zorder = 4 if is_anchor else 3
        
        x = [c[0] for c in poly.exterior.coords]
        y = [c[1] for c in poly.exterior.coords]
        ax.fill(x, y, facecolor=color, edgecolor=edge_color,
                linewidth=edge_width, zorder=zorder, alpha=0.92)
    
    # SAD boundary as bold dark line
    ax.plot(sad_x, sad_y, color=COLOR['navy'], linewidth=2.2,
            linestyle='-', zorder=5, solid_capstyle='round')
    
    # Set view bounds — extra room at the BOTTOM for the scale bar zone
    bottom_extra = span * 0.07
    ax.set_xlim(minx - buffer, maxx + buffer)
    ax.set_ylim(miny - buffer - bottom_extra, maxy + buffer)
    ax.set_aspect('equal')
    
    # Hide axes
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    
    # Add scale bar (round to a nice number in FEET based on SAD size)
    span_ft = span * 3.28084
    if span_ft > 6500:
        scale_ft = 2000
    elif span_ft > 2600:
        scale_ft = 1000
    elif span_ft > 1300:
        scale_ft = 500
    else:
        scale_ft = 200
    scale_m = scale_ft / 3.28084   # convert back to plot units
    
    # Scale bar lives in the bottom buffer zone, BELOW the SAD data, so
    # it never overlaps plan content even on SADs with edge buildings
    # or parking lots extending to the boundary.
    bar_x0 = minx + buffer * 0.5
    bar_y = miny - buffer * 0.5 - bottom_extra * 0.4
    ax.plot([bar_x0, bar_x0 + scale_m], [bar_y, bar_y],
            color=COLOR['navy'], linewidth=2.5, zorder=10)
    ax.text(bar_x0 + scale_m / 2, bar_y - bottom_extra * 0.35,
            f'{scale_ft:,} ft', ha='center', va='top',
            fontsize=9, color=COLOR['navy'], fontweight='bold')
    
    # North arrow at top-right
    arrow_x = maxx - buffer * 0.7
    arrow_y_base = maxy - buffer * 1.0
    arrow_y_tip = maxy - buffer * 0.2
    ax.annotate('', xy=(arrow_x, arrow_y_tip),
                xytext=(arrow_x, arrow_y_base),
                arrowprops=dict(arrowstyle='-|>', color=COLOR['navy'],
                                  lw=1.8, mutation_scale=15),
                zorder=10)
    ax.text(arrow_x, arrow_y_tip + buffer * 0.1, 'N',
            ha='center', va='bottom',
            fontsize=10, color=COLOR['navy'], fontweight='bold')


# ─── Program donut chart ──────────────────────────────────────────────

def render_program_donut(ax, features):
    """Donut chart of SAD-interior POI composition + comprehensive plan key.

    The legend is engineered as a RELIABLE KEY for the plan view above:
      - All 8 building program colors are listed (even 0%), so every
        color on the plan view is identifiable in the legend.
      - A 'PLAN OVERLAYS' mini-section below shows parking lots, parks/
        open space, roads, anchor outline, and SAD boundary indicators
        for the layers drawn on the plan beneath the buildings.
    """
    cats = list(PROGRAM_COLORS.keys())
    # Donut data: only non-zero categories (otherwise pie() draws empty
    # zero-width wedges). Legend list always shows all 8.
    vals_for_donut = []
    colors_for_donut = []
    for cat in cats:
        v = features.get(f'prog_pct_{cat}', 0)
        if v > 0.3:
            vals_for_donut.append(v)
            colors_for_donut.append(PROGRAM_COLORS[cat])

    # Use a custom subplot layout: donut on left, legend on right,
    # both INSIDE the parent axes' bounds.
    ax.axis('off')
    parent_bbox = ax.get_position()
    fig = ax.figure

    # Donut takes left ~42% of parent panel
    donut_w = parent_bbox.width * 0.42
    donut_h = parent_bbox.height * 0.85
    donut_left = parent_bbox.x0
    donut_bottom = parent_bbox.y0 + (parent_bbox.height - donut_h) / 2
    ax_d = fig.add_axes([donut_left, donut_bottom, donut_w, donut_h])

    if vals_for_donut:
        ax_d.pie(vals_for_donut, colors=colors_for_donut, startangle=90,
                  wedgeprops=dict(width=0.42, edgecolor='white',
                                    linewidth=1.5))
    else:
        ax_d.text(0.5, 0.5, 'no program data',
                   ha='center', va='center', transform=ax_d.transAxes,
                   color=COLOR['text_muted'], fontsize=9)
    ax_d.set_aspect('equal')
    ax_d.axis('off')

    # Legend in right half of parent panel — comprehensive plan key
    legend_left = parent_bbox.x0 + parent_bbox.width * 0.47
    legend_w = parent_bbox.width * 0.50
    legend_h = parent_bbox.height * 0.95
    legend_bottom = parent_bbox.y0 + (parent_bbox.height - legend_h) / 2
    ax_l = fig.add_axes([legend_left, legend_bottom, legend_w, legend_h])
    ax_l.axis('off')

    # ── Section 1: Building programs (all 8) ────────────────────────
    # Sort by share descending; zero-pct entries go last in their natural order.
    program_items = []
    for cat in cats:
        v = features.get(f'prog_pct_{cat}', 0)
        program_items.append((PROGRAM_COLORS[cat], PROGRAM_LABELS[cat], v))
    program_items.sort(key=lambda x: -x[2])

    # ── Section 2: Plan overlays (5 indicators) ─────────────────────
    overlay_items = [
        ('swatch', PARKING_LOT_COLOR, 'Parking lot'),
        ('swatch', PARK_LAND_COLOR,   'Park / open space'),
        ('line',   ROAD_COLOR,        'Road'),
        ('outline', COLOR['navy'],    'Anchor building'),
        ('dashed',  COLOR['navy'],    'SAD boundary'),
    ]

    # Layout: programs take top ~62%, gap, overlays take bottom ~30%
    # 8 program rows + section header + gap + 5 overlay rows + section header
    n_prog = len(program_items)
    n_over = len(overlay_items)

    # Use uniform row height that fits everything
    total_rows = n_prog + n_over + 2   # +2 for the two section headers
    row_h = 0.92 / total_rows

    # Header for programs
    y = 0.98
    ax_l.text(0.0, y, 'BUILDING PROGRAM',
               transform=ax_l.transAxes,
               fontsize=7.5, color=COLOR['coral'],
               fontweight='bold', ha='left', va='top')
    y -= row_h

    # Program rows
    for c, l, v in program_items:
        y_mid = y - row_h / 2
        ax_l.add_patch(mpatches.Rectangle(
            (0.0, y_mid - 0.018), 0.08, 0.036,
            facecolor=c, edgecolor='white', linewidth=0.8,
            transform=ax_l.transAxes, clip_on=False,
        ))
        ax_l.text(0.11, y_mid, l,
                   transform=ax_l.transAxes,
                   fontsize=8, color=COLOR['text'],
                   ha='left', va='center')
        if v >= 0.5:
            label_pct = f"{v:.0f}%"
            weight = 'bold'
        else:
            label_pct = "0%"
            weight = 'normal'
        ax_l.text(1.0, y_mid, label_pct,
                   transform=ax_l.transAxes,
                   fontsize=8, color=COLOR['text_muted'] if v < 0.5
                                       else COLOR['text'],
                   ha='right', va='center', fontweight=weight)
        y -= row_h

    # Gap then overlay header
    y -= row_h * 0.3
    ax_l.text(0.0, y, 'PLAN OVERLAYS',
               transform=ax_l.transAxes,
               fontsize=7.5, color=COLOR['coral'],
               fontweight='bold', ha='left', va='top')
    y -= row_h

    # Overlay rows
    for kind, c, label in overlay_items:
        y_mid = y - row_h / 2
        if kind == 'swatch':
            ax_l.add_patch(mpatches.Rectangle(
                (0.0, y_mid - 0.018), 0.08, 0.036,
                facecolor=c, edgecolor='white', linewidth=0.8,
                transform=ax_l.transAxes, clip_on=False,
            ))
        elif kind == 'line':
            ax_l.add_line(plt.Line2D(
                [0.005, 0.075], [y_mid, y_mid],
                color=c, linewidth=1.4, transform=ax_l.transAxes,
                solid_capstyle='round'))
        elif kind == 'outline':
            ax_l.add_patch(mpatches.Rectangle(
                (0.0, y_mid - 0.018), 0.08, 0.036,
                facecolor=COLOR['text_muted'], edgecolor=c, linewidth=1.5,
                transform=ax_l.transAxes, clip_on=False,
            ))
        elif kind == 'dashed':
            ax_l.add_line(plt.Line2D(
                [0.005, 0.075], [y_mid, y_mid],
                color=c, linewidth=1.6, linestyle=(0, (3, 2)),
                transform=ax_l.transAxes))
        ax_l.text(0.11, y_mid, label,
                   transform=ax_l.transAxes,
                   fontsize=8, color=COLOR['text'],
                   ha='left', va='center')
        y -= row_h


# ─── Stat callout cells ───────────────────────────────────────────────

def make_stat_callouts(features, sad_data):
    """Pick the 6 SAD callouts. Population density swaps in for the 6th
    (median bldg area) when ACS census data is available — keeps the
    AT A GLANCE panel at a clean 2x3 grid. Full demographic context is
    rendered in its own panel via render_demographic_context()."""
    profile = sad_data['profile']
    buildings_n = len(sad_data['buildings_inside'])
    sad_area = sad_data['sad_area_km2']
    
    # Find the top 1-2 programs
    prog_pcts = {cat: features.get(f'prog_pct_{cat}', 0)
                  for cat in PROGRAM_COLORS}
    top_prog = max(prog_pcts.items(), key=lambda kv: kv[1])
    
    median_area = features.get('morph_median_area_m2', 0)
    n_anchors = int(features.get('anchor_count_inside', 0))
    coverage = features.get('morph_coverage', 0)
    
    callouts = [
        {'value': f"{buildings_n:,}", 'label': 'buildings inside'},
        {'value': f"{m2_to_acres(sad_area * 1e6):.1f} ac", 'label': 'SAD area'},
        {'value': f"{n_anchors}", 'label': 'anchors detected'},
        {'value': f"{coverage*100:.0f}%", 'label': 'built coverage'},
        {'value': f"{top_prog[1]:.0f}%",
         'label': f"{PROGRAM_LABELS.get(top_prog[0], top_prog[0]).lower()}"},
        {'value': f"{m2_to_sqft(median_area):,.0f} sf", 'label': 'median bldg area'},
    ]
    
    # Replace 6th callout with population density if available
    pop_density = features.get('demo_population_density')
    if pop_density and pop_density > 0:
        pop_per_sqmi = pop_density * 2.589988    # km^2 -> sq mi factor
        callouts[5] = {
            'value': f"{pop_per_sqmi:,.0f}",
            'label': 'people / sq mi (around SAD)',
        }
    
    return callouts


def render_stat_callouts(ax, callouts):
    """Render the 6 stat callouts in a 2x3 grid inside the given axes."""
    ax.axis('off')
    # First row starts at y_top=0.78 so the AT A GLANCE section header
    # at y=1.0 has clearance above the 22pt stat numbers.
    for i, c in enumerate(callouts):
        col = i % 3
        row = i // 3
        x = 0.04 + col * 0.32
        y_top = 0.78 - row * 0.46
        
        # Value (large)
        ax.text(x, y_top, c['value'],
                transform=ax.transAxes,
                fontsize=22, fontweight='bold',
                color=COLOR['navy'],
                ha='left', va='top',
                family='serif')
        # Label (small, muted)
        ax.text(x, y_top - 0.20, c['label'],
                transform=ax.transAxes,
                fontsize=9, color=COLOR['text_muted'],
                ha='left', va='top')


import textwrap


def render_demographic_context(ax, features, demo_meta):
    """Render the DEMOGRAPHIC CONTEXT panel (bottom-right column).

    Shows ACS 5-year metadata, a sorted list of label-value pairs for the
    main demographic metrics, and a footnote about the block group
    coverage of the SAD. Falls back to "(no census data)" for SADs that
    aren't covered by ACS (Canadian districts).
    """
    ax.axis('off')

    # Section heading
    ax.text(0.0, 1.0, 'DEMOGRAPHIC CONTEXT',
             transform=ax.transAxes,
             fontsize=10, color=COLOR['coral'],
             fontweight='bold', ha='left', va='top')

    # No-data fallback
    pop_density = features.get('demo_population_density') or 0
    if not demo_meta or pop_density <= 0:
        ax.text(0.0, 0.85, '(no census data)',
                 transform=ax.transAxes,
                 fontsize=10, color=COLOR['text_muted'],
                 style='italic', ha='left', va='top')
        return

    # Source citation
    citation = (f"ACS 5-year ({demo_meta['acs_start_year']}-"
                f"{demo_meta['acs_end_year']})  \u00b7  "
                f"Block Group {demo_meta['blkgrpce']}, "
                f"Tract {demo_meta['tract_display']}, "
                f"{demo_meta['state_abbr']}")
    ax.text(0.0, 0.88, citation,
             transform=ax.transAxes,
             fontsize=8, color=COLOR['text_muted'],
             ha='left', va='top')

    # Build the metric rows
    rows = []
    income = features.get('demo_median_hh_income')
    if income and income > 0:
        rows.append(('Median household income', f"${income:,.0f}"))

    age = features.get('demo_median_age')
    if age and age > 0:
        rows.append(('Median age', f"{age:.0f} yrs"))

    owner = features.get('demo_pct_owner_occupied')
    if owner is not None and owner >= 0:
        rows.append(('Owner-occupied', f"{owner:.0f}%"))

    bach = features.get('demo_pct_bachelors_plus')
    if bach is not None and bach >= 0:
        rows.append(("Bachelor's degree +", f"{bach:.0f}%"))

    pop_per_sqmi = pop_density * 2.589988  # km^2 -> sq mi factor
    rows.append(('Residents / sq mi', f"{pop_per_sqmi:,.0f}"))

    # Render rows: label on left, value bold on right
    n_rows = len(rows)
    row_h = 0.55 / max(n_rows, 1)
    y = 0.78
    for label, value in rows:
        ax.text(0.0, y, label,
                 transform=ax.transAxes,
                 fontsize=9.5, color=COLOR['text'],
                 ha='left', va='top')
        ax.text(1.0, y, value,
                 transform=ax.transAxes,
                 fontsize=9.5, color=COLOR['navy'],
                 fontweight='bold', ha='right', va='top',
                 family='serif')
        y -= row_h

    # Footnote (always included, gracefully says "only" when 1 BG)
    n_other = demo_meta.get('n_other_bgs', 0)
    if n_other > 0:
        footnote = (f"Figures describe the block group containing the "
                    f"largest share of the SAD; the SAD also extends "
                    f"into {n_other} other block group"
                    + ('s' if n_other != 1 else '') + '.')
    else:
        footnote = ("Figures describe the single block group covering "
                    "the SAD.")
    ax.text(0.0, y - 0.02, footnote,
             transform=ax.transAxes,
             fontsize=7.5, color=COLOR['text_muted'],
             style='italic', ha='left', va='top',
             wrap=True)


def render_bullet_list(ax, title, items, bullet_char, header_color,
                        bullet_size=12, text_size=10, wrap_width=42):
    """Render a titled list of bullets with proper wrapping."""
    ax.axis('off')
    
    # Section heading
    ax.text(0.0, 1.0, title.upper(),
             transform=ax.transAxes,
             fontsize=10, color=header_color,
             fontweight='bold', ha='left', va='top')
    
    if not items:
        ax.text(0.0, 0.85, '(none identified)',
                 transform=ax.transAxes,
                 fontsize=10, color=COLOR['text_light'],
                 ha='left', va='top', style='italic')
        return
    
    # Approximate line-height in axes coordinates; tuned for ~10pt text in
    # the bottom panels. Each wrapped line takes ~0.07 of the axes height,
    # each bullet gap adds another 0.05.
    line_h = 0.075
    gap_after_bullet = 0.045
    
    y = 0.86
    for item in items:
        wrapped = textwrap.fill(item, width=wrap_width)
        lines = wrapped.split('\n')
        # Bullet character and first line on the same y
        ax.text(0.0, y, bullet_char,
                 transform=ax.transAxes,
                 fontsize=bullet_size, color=header_color,
                 fontweight='bold', ha='left', va='top')
        ax.text(0.07, y, lines[0],
                 transform=ax.transAxes,
                 fontsize=text_size, color=COLOR['text'],
                 ha='left', va='top')
        # Subsequent wrapped lines, indented under the first
        for line in lines[1:]:
            y -= line_h
            ax.text(0.07, y, line,
                     transform=ax.transAxes,
                     fontsize=text_size, color=COLOR['text'],
                     ha='left', va='top')
        y -= (line_h + gap_after_bullet)


def render_context_inset(fig, position, sad_data):
    """Add a small figure-ground context inset at the given figure-relative
    position [left, bottom, width, height].
    
    BLACK buildings on WHITE ground (per design spec), SAD highlighted in
    coral. Acts as a key showing where the SAD sits in the wider canvas.
    """
    buildings_full = sad_data['buildings_full']
    canvas_bounds = sad_data['canvas_bounds']
    sad_boundary = sad_data['sad_boundary']
    
    ax = fig.add_axes(position)
    ax.set_facecolor('white')
    
    # Black building footprints — solid fill, no outlines (figure-ground)
    for _, row in buildings_full.iterrows():
        poly = to_polygon(row.geometry)
        if poly is None or poly.is_empty:
            continue
        x = [c[0] for c in poly.exterior.coords]
        y = [c[1] for c in poly.exterior.coords]
        ax.fill(x, y, facecolor='black', edgecolor='none', zorder=2)
    
    # SAD highlighted in coral: translucent fill + solid outline
    sad_p = to_polygon(sad_boundary)
    sad_x = [c[0] for c in sad_p.exterior.coords]
    sad_y = [c[1] for c in sad_p.exterior.coords]
    ax.fill(sad_x, sad_y, facecolor=COLOR['coral'], edgecolor='none',
             alpha=0.35, zorder=3)
    ax.plot(sad_x, sad_y, color=COLOR['coral'], linewidth=1.6, zorder=4)
    
    # Frame the canvas extent
    minx, miny, maxx, maxy = canvas_bounds
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    
    # Thin neutral border so the inset reads as a discrete object
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(COLOR['rule'])
        spine.set_linewidth(0.8)
    
    # Scale annotation (canvas width) — inside the inset, bottom-left
    canvas_w_km = (maxx - minx) / 1000.0
    ax.text(0.04, 0.04, f'{canvas_w_km:.1f} km',
             transform=ax.transAxes,
             fontsize=8, color=COLOR['text'],
             fontweight='bold', ha='left', va='bottom',
             bbox=dict(facecolor='white', edgecolor='none',
                        boxstyle='round,pad=0.2', alpha=0.85))
    
    return ax


# ─── Full place card composition ──────────────────────────────────────

def render_place_card(sad_data, features, character_tags,
                       sentence, strengths, opps, callouts,
                       out_png, out_svg):
    """One full place card, 13.3 x 8.5 inches.
    
    Layout uses explicit figure-relative positioning (not gridspec) so
    every panel has guaranteed gutters and predictable proportions.
    
    Three zones:
      Top band:    header (name / typology)
      Middle row:  PLAN VIEW (left, ~55% of width)
                   SIDEBAR right ─ context inset (square, top-left of sidebar)
                                 ─ character text (top-right of sidebar)
                                 ─ AT A GLANCE (below, full sidebar width)
      Bottom row:  PROGRAM (donut + legend) | STRENGTHS | OPPORTUNITIES
    """
    profile = sad_data['profile']
    sad_name = profile.get('sad_name', sad_data['sad_id'])
    city = profile.get('city') or ''
    typology = profile.get('typology', '')
    anchor_venue = profile.get('anchor_venue', '')
    
    fig = plt.figure(figsize=(13.3, 8.5), facecolor=COLOR['card_bg'])
    
    # ── PAGE GEOMETRY (all coords are figure-relative, 0-1) ──────────
    # Margins around the whole page
    M_L, M_R, M_T, M_B = 0.040, 0.040, 0.050, 0.045
    # Gutters between major panels
    G_MAJOR = 0.022   # between zones (header / middle / bottom)
    G_MID   = 0.022   # between panels in the same zone
    
    # Zone allocation
    HEADER_H = 0.085
    BOTTOM_H = 0.265
    
    header_y = 1.0 - M_T - HEADER_H
    middle_top = header_y - G_MAJOR
    bottom_top = M_B + BOTTOM_H
    middle_bottom = bottom_top + G_MAJOR
    middle_h = middle_top - middle_bottom
    
    usable_w = 1.0 - M_L - M_R
    
    # Plan view : sidebar split (55% : 45%)
    plan_w = usable_w * 0.55
    side_w = usable_w * 0.45 - G_MID
    plan_left = M_L
    side_left = plan_left + plan_w + G_MID
    
    # Within the sidebar middle zone:
    #   Top half: context inset (left, square) + character (right)
    #   Bottom half: AT A GLANCE
    # Gap between top split and stats — larger than the page gutter for
    # visual breathing room
    SIDEBAR_VGAP = G_MID * 1.8
    side_top_h = middle_h * 0.58
    side_bot_h = middle_h - side_top_h - SIDEBAR_VGAP
    
    # Context inset: square (in figure space, accounting for aspect ratio)
    fig_w_in, fig_h_in = 13.3, 8.5
    # Inset's pixel height = side_top_h * fig_h_in
    inset_h_in = side_top_h * fig_h_in
    inset_w_fig = inset_h_in / fig_w_in   # equal inches → square
    
    ctx_left = side_left
    ctx_bottom = middle_top - side_top_h
    ctx_w = inset_w_fig
    ctx_h = side_top_h
    
    # Character text panel: right of inset
    char_left = ctx_left + ctx_w + G_MID
    char_w = (side_left + side_w) - char_left
    char_bottom = ctx_bottom
    char_h = side_top_h
    
    # AT A GLANCE: below context+character, full sidebar width
    stats_left = side_left
    stats_w = side_w
    stats_h = side_bot_h
    stats_bottom = middle_bottom
    
    # Bottom row: three equal columns
    bot_y = M_B
    bot_h_actual = BOTTOM_H - G_MID  # leave gutter from middle
    col_w = (usable_w - 2 * G_MID) / 3
    
    donut_left = M_L
    str_left = donut_left + col_w + G_MID
    opp_left = str_left + col_w + G_MID
    
    # ── HEADER ────────────────────────────────────────────────────────
    ax_header = fig.add_axes([M_L, header_y, usable_w, HEADER_H])
    ax_header.axis('off')
    
    # Coral accent bar to left of name
    ax_header.add_patch(mpatches.Rectangle(
        (0.0, 0.30), 0.006, 0.65,
        transform=ax_header.transAxes,
        facecolor=COLOR['coral'], edgecolor='none', zorder=2,
    ))
    ax_header.text(0.015, 0.95, sad_name,
                    transform=ax_header.transAxes,
                    fontsize=28, fontweight='bold',
                    color=COLOR['navy'], ha='left', va='top',
                    family='serif')
    
    # Subtitle: anchor venue + city
    subtitle_parts = []
    if anchor_venue and anchor_venue.lower() not in ('unknown', '', 'none'):
        subtitle_parts.append(f"Anchored by {anchor_venue}")
    if city and city not in ('unknown', ''):
        subtitle_parts.append(city)
    if subtitle_parts:
        ax_header.text(0.015, 0.40, '  ·  '.join(subtitle_parts),
                        transform=ax_header.transAxes,
                        fontsize=10, color=COLOR['text_muted'],
                        ha='left', va='top', style='italic')
    
    # Right side of header
    ax_header.text(1.0, 0.92, 'PLACE CARD',
                    transform=ax_header.transAxes,
                    fontsize=10, color=COLOR['coral'],
                    fontweight='bold', ha='right', va='top')

    # Typology badge — primary in coral box, secondary in muted italic beneath
    primary_typ = (profile.get('primary_typology') or
                    profile.get('typology') or '').strip()
    secondary_typ = (profile.get('secondary_typology') or '').strip()
    if primary_typ and primary_typ.lower() not in ('unknown', ''):
        # Badge: filled rectangle behind the typology text on the right
        from matplotlib.patches import FancyBboxPatch
        badge_text = primary_typ.upper()
        badge_pad_x = 0.012
        # Approximate width based on text length
        txt_w = 0.012 * (len(badge_text) + 4)
        badge_left = 1.0 - txt_w
        badge_bottom = 0.40
        badge_h = 0.28
        ax_header.add_patch(FancyBboxPatch(
            (badge_left, badge_bottom),
            txt_w, badge_h,
            boxstyle="round,pad=0.005,rounding_size=0.005",
            transform=ax_header.transAxes,
            facecolor=COLOR['coral'], edgecolor='none', zorder=2))
        ax_header.text(badge_left + txt_w / 2,
                        badge_bottom + badge_h / 2,
                        badge_text,
                        transform=ax_header.transAxes,
                        fontsize=11, fontweight='bold',
                        color='white', ha='center', va='center',
                        zorder=3, family='sans-serif')
        if secondary_typ:
            ax_header.text(1.0, 0.05,
                            f"+ {secondary_typ} character",
                            transform=ax_header.transAxes,
                            fontsize=9, color=COLOR['text_muted'],
                            ha='right', va='top', style='italic')
    
    # ── PLAN VIEW (left of middle zone) ───────────────────────────────
    ax_plan = fig.add_axes([plan_left, middle_bottom, plan_w, middle_h])
    render_plan_view(ax_plan, sad_data)
    ax_plan.text(0.0, 1.025, 'PLAN VIEW',
                  transform=ax_plan.transAxes,
                  fontsize=9, color=COLOR['coral'],
                  fontweight='bold', ha='left', va='bottom')
    
    # ── CONTEXT INSET (top-left of sidebar) ──────────────────────────
    ax_ctx = render_context_inset(fig, [ctx_left, ctx_bottom, ctx_w, ctx_h],
                                    sad_data)
    ax_ctx.text(0.0, 1.025, 'CONTEXT',
                 transform=ax_ctx.transAxes,
                 fontsize=9, color=COLOR['coral'],
                 fontweight='bold', ha='left', va='bottom')
    
    # ── CHARACTER (top-right of sidebar, beside context inset) ──────
    ax_char = fig.add_axes([char_left, char_bottom, char_w, char_h])
    ax_char.axis('off')
    ax_char.text(0.0, 1.025, 'CHARACTER',
                  transform=ax_char.transAxes,
                  fontsize=9, color=COLOR['coral'],
                  fontweight='bold', ha='left', va='bottom')
    # Wrap to character panel width. Multi-sentence character text needs
    # to fit comfortably without bleeding into AT A GLANCE below.
    wrap_chars = max(40, int(char_w * 85))
    wrapped = textwrap.fill(sentence, width=wrap_chars)
    ax_char.text(0.0, 0.92, wrapped,
                  transform=ax_char.transAxes,
                  fontsize=9, color=COLOR['navy'],
                  ha='left', va='top', family='serif',
                  fontstyle='italic', linespacing=1.22)
    
    # ── AT A GLANCE (full sidebar width, below) ──────────────────────
    ax_stats = fig.add_axes([stats_left, stats_bottom, stats_w, stats_h])
    ax_stats.axis('off')
    ax_stats.text(0.0, 1.0, 'AT A GLANCE',
                   transform=ax_stats.transAxes,
                   fontsize=9, color=COLOR['coral'],
                   fontweight='bold', ha='left', va='top')
    render_stat_callouts(ax_stats, callouts)
    
    # ── HORIZONTAL SEPARATOR between middle and bottom zones ─────────
    sep_y = M_B + BOTTOM_H + G_MID * 0.35
    fig.add_artist(plt.Line2D(
        [M_L, M_L + usable_w], [sep_y, sep_y],
        color=COLOR['rule'], linewidth=0.6,
        transform=fig.transFigure,
    ))
    
    # ── BOTTOM ROW ────────────────────────────────────────────────────
    # Program (donut + legend)
    ax_donut = fig.add_axes([donut_left, bot_y, col_w, bot_h_actual])
    ax_donut.text(0.0, 1.04, 'PROGRAM',
                   transform=ax_donut.transAxes,
                   fontsize=9, color=COLOR['coral'],
                   fontweight='bold', ha='left', va='bottom')
    render_program_donut(ax_donut, features)
    
    # Strengths
    ax_str = fig.add_axes([str_left, bot_y, col_w, bot_h_actual])
    render_bullet_list(ax_str, 'Strengths', strengths, '+', '#2C8C5B',
                        wrap_width=44)
    
    # Demographic Context (replaces Opportunities in the bottom-right)
    ax_demo = fig.add_axes([opp_left, bot_y, col_w, bot_h_actual])
    render_demographic_context(ax_demo, features,
                                sad_data.get('demo_metadata'))
    
    # Save
    fig.savefig(out_png, dpi=170, bbox_inches='tight',
                 facecolor=COLOR['card_bg'])
    fig.savefig(out_svg, format='svg', bbox_inches='tight',
                 facecolor=COLOR['card_bg'])
    plt.close(fig)


# ─── Portfolio overview (all cards on one sheet) ──────────────────────

def render_portfolio_overview(all_sad_renders, out_png, out_svg):
    """A single sheet showing all place cards in a row/column."""
    n = len(all_sad_renders)
    if n == 0:
        return
    cols = min(n, 3)
    rows = (n + cols - 1) // cols
    
    fig = plt.figure(figsize=(13.3 * cols * 0.45, 8.5 * rows * 0.45 + 0.5),
                      facecolor=COLOR['card_bg'])
    fig.suptitle('SAD portfolio overview',
                  fontsize=18, fontweight='bold',
                  color=COLOR['navy'], x=0.04, ha='left', y=0.98)
    
    for i, render in enumerate(all_sad_renders):
        col = i % cols
        row = i // cols
        ax = fig.add_subplot(rows, cols, i + 1)
        ax.imshow(plt.imread(render['png_path']))
        ax.axis('off')
    
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_png, dpi=120, bbox_inches='tight',
                 facecolor=COLOR['card_bg'])
    fig.savefig(out_svg, format='svg', bbox_inches='tight',
                 facecolor=COLOR['card_bg'])
    plt.close(fig)


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate place card briefings per SAD")
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--sads', nargs='+', required=True)
    parser.add_argument('--out', type=Path, default=None)
    args = parser.parse_args()
    
    if args.out:
        out_dir = args.out
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        out_dir = args.data_dir / '_comparisons' / f'place_cards_{ts}'
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing to {out_dir}\n")
    
    all_renders = []
    all_characterizations = []
    
    # ── Compute typology peer statistics ONCE before the per-SAD loop ──
    # Used by describe_character to put each SAD in context of its peers.
    print(f"\n  computing typology peer statistics across {len(args.sads)} SADs...")
    peer_stats = compute_typology_peer_stats(args.data_dir, args.sads,
                                              min_peers=3)
    if peer_stats:
        for typ, info in peer_stats.items():
            print(f"    {typ}: {info['n_peers']} peers, "
                  f"{len(info['medians'])} comparable features")
    else:
        print("    (no peer stats available -- character text will fall back "
              "to non-comparative phrasing)")

    for sad_id in args.sads:
        print(f"  processing {sad_id}...")
        try:
            sad_data = load_per_sad_data(args.data_dir, sad_id)
        except FileNotFoundError as e:
            print(f"    ERROR: {e}")
            continue
        
        # Gather features via module 08 (recomputes interior-only)
        sd = gather_sad_features(args.data_dir, sad_id,
                                   include_demographics=True)
        features = sd['features']
        # Add the area + count metadata
        features['sad_area_km2'] = sd['sad_area_km2']
        features['buildings_inside_count'] = sd['buildings_inside_count']
        
        # Patch profile fields onto sad_data
        sad_data['profile'].setdefault('sad_name', sd['name'])
        sad_data['profile'].setdefault('anchor_venue', sd['anchor_venue'])
        sad_data['profile'].setdefault('typology', sd['typology'])
        
        # ── DIAGNOSTIC: classify anchor buildings out loud ───────────
        anchor_ids = sad_data['anchor_ids']
        if anchor_ids:
            print(f"    anchor classification (verify these):")
            anchor_rows = sad_data['buildings_inside'][
                sad_data['buildings_inside']['building_id'].isin(anchor_ids)
            ]
            for _, ar in anchor_rows.iterrows():
                cls = classify_building_program(ar)
                nm = ar.get('name') or '(unnamed)'
                osm = ar.get('building') or '?'
                area = ar.get('area_m2', 0)
                dom = ar.get('dominant_program_inside') or '?'
                print(f"      {ar['building_id']} | {nm:<30} | "
                      f"OSM={osm:<12} | area={area:>7,.0f}m² | "
                      f"POI-dom={dom:<26} | classified={cls}")
        
        sentence, char_tags = describe_character(features, sad_data['profile'],
                                                   peer_stats=peer_stats)
        strengths, opps = derive_strengths_opportunities(
            features, sad_data['profile'], sad_data['boundary_diffs'])
        callouts = make_stat_callouts(features, sad_data)
        
        all_characterizations.append({
            'sad_id': sad_id,
            'name': sad_data['profile'].get('sad_name', sad_id),
            'character_sentence': sentence,
            'character_tags': char_tags,
            'strengths': strengths,
            'opportunities': opps,
            'callouts': callouts,
            'demo_metadata': sad_data.get('demo_metadata'),
        })
        
        # Render
        png_path = out_dir / f'{sad_id}_place_card.png'
        svg_path = out_dir / f'{sad_id}_place_card.svg'
        render_place_card(sad_data, features, char_tags,
                           sentence, strengths, opps, callouts,
                           png_path, svg_path)
        all_renders.append({'sad_id': sad_id, 'png_path': png_path})
        print(f"    [OK] {png_path.name}")
        print(f"         '{sentence}'")
    
    # Portfolio overview
    if len(all_renders) >= 2:
        render_portfolio_overview(
            all_renders,
            out_dir / 'portfolio_overview.png',
            out_dir / 'portfolio_overview.svg',
        )
        print(f"\n  [OK] portfolio_overview")
    
    # Save characterizations JSON
    (out_dir / 'characterizations.json').write_text(
        json.dumps(all_characterizations, indent=2, default=str))
    print(f"  [OK] characterizations.json")
    
    print(f"\nAll outputs in: {out_dir}")


if __name__ == '__main__':
    main()
