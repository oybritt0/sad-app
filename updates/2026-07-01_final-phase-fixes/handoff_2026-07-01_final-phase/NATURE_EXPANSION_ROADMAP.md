# SAD Nature lens: expansion roadmap (public data sources)
## 2026-07-01

The Nature lens is orthogonal to typology (eta2 = 0.102) and follows a repeatable
pattern: pull live for the drawn boundary, anchor by geometry, retrieve against the
corpus, keep it a standalone lens, let the user weight it. Every source below fits
that pattern. This doc is a menu, not a commitment. No em dashes per convention.

First priority is to fix the current blank canopy, because that host is network
blocked. NLCD does that and is fully public.

Legend: [KEY] needs a free API key. [OPEN] no key. [US] US only. [GLOBAL] worldwide.
[GEE] easiest via Google Earth Engine or Microsoft Planetary Computer STAC.

---

## TIER 1 (fixes a current gap or adds a strong new lens; low friction)

1. NLCD impervious + tree canopy  [OPEN] [US]
   Source: MRLC / USGS National Land Cover Database. Public WCS/WMS; also on the
   Planetary Computer and GEE catalogs. Gives percent impervious surface and percent
   tree canopy for the boundary. This is the direct, unblocked replacement for the
   ETH Global Canopy Height tiles that fail on the work network. Do this first.
   Slots into: canopy_area_share and a new impervious_share field.

2. NDVI (vegetation greenness)  [OPEN] [GLOBAL] [GEE]
   Source: Sentinel-2 (10m) or Landsat 8/9 (30m) surface reflectance via Planetary
   Computer STAC or GEE. Compute mean/median NDVI in the boundary. A continuous
   "how green and how healthy" measure that is stronger than land-cover share and
   avoids the ETH host entirely.
   Slots into: a new ndvi_mean field alongside green_area_share.

3. FEMA flood zone  [OPEN] [US]
   Source: FEMA National Flood Hazard Layer (NFHL), public ArcGIS REST + WMS.
   Intersect the boundary with flood zones; report share of area in
   1%-annual-chance (100-year) and 0.2% (500-year) zones. New orthogonal lens with
   high place-character value.
   Slots into: a new flood block (share_in_100yr, share_in_500yr).

---

## TIER 2 (new orthogonal lenses; a bit more setup)

4. Urban heat / land surface temperature  [OPEN] [US/GLOBAL] [GEE]
   Source: Landsat 8/9 thermal band (30m, resampled) or MODIS LST (1km). Summer
   daytime mean LST in the boundary, and the delta vs the metro mean as an urban
   heat island proxy. Pairs naturally with canopy and impervious.

5. DOT National Transportation Noise Map  [OPEN] [US]
   Source: US DOT BTS national road + aviation noise raster (public). Mean dB in the
   boundary and share above a threshold. Easy raster zonal stat, clean new lens.

6. Park access  [OPEN] [US]
   Source: Trust for Public Land ParkServe. Percent of the district (or its
   population) within a 10-minute walk of a park. Social-environmental angle that
   complements the physical green measures.

---

## TIER 3 (novel or heavier; note for later)

7. EPA AirNow  [KEY] [US]
   Cleaner, US-official air quality than OpenAQ (which also needs its key
   regenerated, see below). Could replace or cross-check the current air signal.

8. USDA SSURGO / gSSURGO soils  [OPEN] [US]
   Drainage class and hydrologic soil group in the boundary. Feeds a
   "green-infrastructure feasibility" or stormwater angle. Heavier to parse.

9. GBIF / iNaturalist biodiversity  [OPEN] [GLOBAL]
   Species occurrence counts / richness in the boundary via public APIs. Novel
   "urban biodiversity" signal. Noisy, observation-biased; treat as exploratory.

10. VIIRS nighttime lights  [OPEN] [GLOBAL] [GEE]
    Mean night radiance in the boundary as an activity/urbanization proxy. Adjacent
    to nature but useful context.

---

## IMPLEMENTATION NOTES (how to add one, following the existing pattern)

- Add a puller in the shape of the existing nature pullers
  (`pull_nature_overture.py`, `pull_air_openaq.py`, `pull_canopy_height_cog.py`):
  take `--data-dir --sad --boundary [--release] --apply`, write a
  `nature_<source>_<ts>.json` under `derived\per_sad\<sad>\`.
- Register it in the live path the same way `nature_match.py` calls the three
  existing pullers, so a drawn boundary triggers it.
- Add the corpus loop in the shape of `pull_nature_corpus.py`, then enrich the
  compare manifest like `enrich_compare_manifest_nature.py`.
- Keep it a SEPARATE channel. Do not fuse into the main embedding. Run the honest
  separation/redundancy test (as was done for green/canopy/air) before trusting it,
  especially heat vs canopy and impervious vs green, which will correlate.
- Prefer STAC (Planetary Computer) or a public WCS/REST over any host that the work
  network blocks. The ETH canopy and Overpass blocks are the cautionary tale here;
  NLCD, NFHL, and Planetary Computer are known reachable patterns to prefer.

## OPEN MAINTENANCE ITEMS TIED TO NATURE

- Regenerate the OpenAQ key (it was pasted in plaintext in an earlier chat) and save
  it to `openaq_key.txt`; the server reads it at request time, no restart needed.
- Canopy: either switch to NLCD (recommended, Tier 1 item 1) or, as a stopgap,
  download the one ETH 3-degree tile manually from the tile index and pass
  `--local <path>` to the canopy puller.
