"""
census_native.py

Native-geography census reporting for SAD place cards.

WHY THIS EXISTS
The pipeline pulls ACS 5-year data at the block-group level (module_04). A
SAD boundary almost never aligns with block-group boundaries, so any
attempt to produce a "SAD-interior" demographic figure requires
apportioning block-group data down to the SAD footprint. That
apportionment rests on an unobservable assumption about how population and
income are distributed *within* the block group, and at SAD scale that
assumption dominates the result. Separately, aggregating block-group
*medians* (income, age, home value) by averaging them is not a valid
operation — the median of a combined area is not the mean of its parts'
medians.

The planning-standard alternative, used when a study area does not match
census geography, is to NOT reweight: report the representative census
unit's published values as-is, and name the geography. You are not
claiming "the SAD's median income is X"; you are truthfully stating
"the SAD sits within Block Group Y, where ACS reports median income X."

WHAT THIS DOES
Picks the single block group with the largest overlap with the SAD
boundary as the representative geography, and returns that block group's
ACS values exactly as published. Rates (% bachelor's+, % owner-occupied,
unemployment) are computed only WITHIN that one block group, which is
legitimate — it is a single unit, count over count, no cross-unit
aggregation. Population density is that block group's published population
over its land area.

If the SAD overlaps several block groups, that fact is disclosed in the
returned dict so the place card can caveat it.

NOTE ON FINER GRAIN
Block groups are the smallest geography ACS publishes most variables for.
If sub-block-group population resolution is ever needed, the rigorous
route is the decennial census block count (a 100% count, not a sample
estimate) — a separate data pull, not implemented here.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import geopandas as gpd


# ACS sentinel values for "not applicable / could not compute". module_04
# should already have NaN'd these; we re-check defensively.
_ACS_NULL_SENTINELS = (-666666666, -555555555, -333333333,
                       -222222222, -888888888)

# Minimal state FIPS -> USPS abbreviation, for building a human label.
_STATE_FIPS = {
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


def _clean(v):
    """Return a float, or None for NaN / ACS sentinel / non-numeric."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if np.isnan(f):
        return None
    try:
        if int(f) in _ACS_NULL_SENTINELS:
            return None
    except (ValueError, OverflowError):
        pass
    return f


def _safe_ratio(num, den):
    """num/den as a percentage, or None if either is missing or den <= 0."""
    n, d = _clean(num), _clean(den)
    if n is None or d is None or d <= 0:
        return None
    return 100.0 * n / d


def _bachelors_plus_pct(row):
    """% with a bachelor's degree or higher, within this one block group.
    Legitimate as a single-unit rate; returns None if no education data."""
    parts = [_clean(row.get(c)) for c in
             ('edu_bachelors', 'edu_masters',
              'edu_professional', 'edu_doctorate')]
    present = [p for p in parts if p is not None]
    if not present:
        return None
    return _safe_ratio(sum(present), row.get('edu_total_pop_25plus'))


def _acs_vintage(derived_dir: Path):
    """Read the ACS vintage label from census_summary.json if present."""
    p = derived_dir / 'census_summary.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get('acs_vintage')
    except (json.JSONDecodeError, OSError):
        return None


def _bg_label(row, vintage):
    """Human-readable label for a block group, from TIGER fields if
    available, else reconstructed from the 12-digit GEOID."""
    geoid = str(row.get('GEOID', '') or '')
    statefp = str(row.get('STATEFP', '') or (geoid[0:2] if len(geoid) >= 2 else ''))
    tractce = str(row.get('TRACTCE', '') or (geoid[5:11] if len(geoid) >= 11 else ''))
    blkgrp = str(row.get('BLKGRPCE', '') or (geoid[11:12] if len(geoid) >= 12 else ''))

    state = _STATE_FIPS.get(statefp, statefp)
    # TRACTCE is 6 digits = 4 integer + 2 decimal in conventional display
    # (e.g. 000400 -> "4", 010102 -> "101.02").
    tract_disp = tractce
    if tractce.isdigit() and len(tractce) == 6:
        whole, frac = int(tractce[:4]), tractce[4:]
        tract_disp = f"{whole}" if frac == '00' else f"{whole}.{frac}"

    parts = []
    if blkgrp:
        parts.append(f"Block Group {blkgrp}")
    if tract_disp:
        parts.append(f"Tract {tract_disp}")
    if state:
        parts.append(state)
    geo = ", ".join(parts) if parts else (f"GEOID {geoid}" if geoid else "block group")

    if not vintage:
        return geo
    # census_summary.json's acs_vintage may already start with "ACS"
    # (e.g. "ACS 5-year (2019-2023)"). Only add the prefix when it isn't
    # already there, so the label never reads "ACS ACS ...".
    v = str(vintage).strip()
    if not v.upper().startswith('ACS'):
        v = f"ACS {v}"
    return f"{v} \u2014 {geo}"


def get_native_geography_demographics(data_dir, sad_id):
    """
    Return the representative block group's ACS values for a SAD, as
    published — no reweighting, no aggregation of medians.

    Returns None if no census_blockgroups.gpkg exists for the SAD.

    The returned dict is flat and place-card-ready:
        geoid, label, vintage
        n_overlapping_bgs            block groups the SAD touches at all
        representative_overlap       fraction of the chosen BG inside the SAD
        multi_bg_note                caveat string, or None
        total_pop, median_age, median_household_income,
        median_home_value, median_gross_rent       (as published)
        population_density_per_km2                 (BG pop / BG land area)
        density_area_basis                         'ALAND' or 'geometry'
        pct_bachelors_plus, pct_owner_occupied,
        unemployment_rate                          (computed within the BG)
    """
    data_dir = Path(data_dir)
    gpkg = data_dir / sad_id / 'source' / 'census_blockgroups.gpkg'
    if not gpkg.exists():
        return None

    bgs = gpd.read_file(gpkg, layer='blockgroups')
    if len(bgs) == 0:
        return None
    bgs = bgs.copy()

    # ── Pick the representative block group ──────────────────────────
    # Primary rule: the BG with the largest share of its area inside the
    # SAD boundary (module_04 already wrote sad_overlap_ratio). Fallbacks
    # keep the function working if that column is missing or all-null.
    overlap_col = None
    for cand in ('sad_overlap_ratio', 'intersection_area_ratio'):
        if cand in bgs.columns and bgs[cand].notna().any():
            overlap_col = cand
            break

    if overlap_col is not None:
        bgs['_overlap'] = bgs[overlap_col].fillna(0.0).astype(float)
    elif bgs.crs is not None:
        # Last resort: overlap unknown; rank by area (largest BG wins).
        metric = bgs.to_crs(bgs.estimate_utm_crs())
        areas = metric.geometry.area
        bgs['_overlap'] = (areas / areas.max()).values
    else:
        bgs['_overlap'] = 0.0

    rep = bgs.sort_values('_overlap', ascending=False).iloc[0]

    # How many block groups does the SAD genuinely touch? (>1% of the BG)
    n_touch = int((bgs['_overlap'] > 0.01).sum())

    # ── Land area for density (TIGER ALAND if present, else geometry) ─
    aland = _clean(rep.get('ALAND'))
    if aland and aland > 0:
        land_area_km2 = aland / 1e6
        density_basis = 'ALAND'
    elif bgs.crs is not None:
        # Geometry area in a metric CRS. This INCLUDES any water, so it is
        # a fallback only — flagged via density_area_basis so the caller
        # can caveat it if needed.
        one = gpd.GeoSeries([rep.geometry], crs=bgs.crs)
        land_area_km2 = float(one.to_crs(one.estimate_utm_crs())
                              .area.iloc[0]) / 1e6
        density_basis = 'geometry'
    else:
        land_area_km2 = None
        density_basis = None

    total_pop = _clean(rep.get('total_pop'))
    pop_density = (total_pop / land_area_km2
                   if (total_pop is not None and land_area_km2
                       and land_area_km2 > 0) else None)

    vintage = _acs_vintage(data_dir / sad_id / 'derived')

    multi_bg_note = None
    if n_touch > 1:
        others = n_touch - 1
        multi_bg_note = (
            f"Figures describe the block group containing the largest share "
            f"of the SAD; the SAD also extends into {others} other block "
            f"group{'s' if others != 1 else ''}.")

    return {
        'geoid': str(rep.get('GEOID', '')),
        'label': _bg_label(rep, vintage),
        'vintage': vintage,
        'n_overlapping_bgs': n_touch,
        'representative_overlap': round(float(rep['_overlap']), 3),
        'multi_bg_note': multi_bg_note,

        # Published values, verbatim from ACS — no weighting, no averaging.
        'total_pop': total_pop,
        'median_age': _clean(rep.get('median_age')),
        'median_household_income': _clean(rep.get('median_household_income')),
        'median_home_value': _clean(rep.get('median_home_value')),
        'median_gross_rent': _clean(rep.get('median_gross_rent')),
        'population_density_per_km2': pop_density,
        'density_area_basis': density_basis,

        # Rates computed WITHIN this one block group only — legitimate,
        # because it is a single unit (count / count, no aggregation).
        'pct_bachelors_plus': _bachelors_plus_pct(rep),
        'pct_owner_occupied': _safe_ratio(rep.get('owner_occupied'),
                                          rep.get('occupied_total')),
        'unemployment_rate': _safe_ratio(rep.get('unemployed'),
                                         rep.get('labor_force')),
    }
