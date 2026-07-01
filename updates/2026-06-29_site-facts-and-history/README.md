# SAD update, 2026-06-29 (Zeffer)

Two pieces of work, packaged here for review before merging into the live UI.
Nothing here is wired into `code/compare_ui/` automatically; see "How to apply"
at the bottom. These are the finished files plus notes.

---

## 1. Site facts complete for all 37 districts

`compare_manifest_geo.json` now carries a full report/fact block for every one
of the 37 corpus districts. Previously 10 districts (the ones that were never in
the 2025 SAD report) had blank fact panels. They now show:

- anchor sport, league, year opened
- location type (Urban Core / Suburban)
- district size in acres
- owner type (public / private / public-private / team-owned) and owner name
- status

The 10 newly-filled districts: 02 Sportsmans-Park, 17 The-Boxyard, 19 Arlington,
20 Astor-Park, 21 Hub-on-Causeway, 26 LECOM-Harborcenter, 27 Treasure-Island,
34 Aquilini-Centre, 37 Galaxy-Park, 38 Bicentennial-Unity-Plaza.

Honest gaps left as dashes on purpose: `cost_per_acre` and `total_gsf` stay
blank for those 10. Those values were report-derived for the original 27 and
have no public source for the other 10, so they show "-" rather than invented
numbers.

Caveat worth knowing: `size_acres` for the 10 is boundary-measured (area of the
drawn SAD boundary), while the original 27 are report-stated headline sizes.
These can differ (e.g. Galaxy Park measures 361 ac as drawn vs ~125 ac headline
for the AEG complex). Same column, two definitions. Flagged so nobody reads it
as one consistent metric.

Files changed: `compare_manifest_geo.json`.

---

## 2. National "History" imagery slider on the map

New basemap mode on the map tab: a **History** button next to Map / Satellite.
Selecting it reveals a time slider (2014 to present) that swaps the basemap to
historical satellite imagery from Esri's free World Imagery Wayback archive.

- Works for ANY district, drawn or corpus, because Wayback is global. Not
  Detroit-specific.
- No API key, no cost. Imagery tiles come from
  `wayback.maptiles.arcgis.com`; the release list is fetched from the public
  Wayback config, with a hardcoded fallback if that fetch is blocked.
- Drag the slider or use the prev/next arrows; the date shows above the slider.

How it works in the code (all in `map_v2.js`):
- `S.baseWayback` is an `L.tileLayer` with a custom `{rel}` token (the Wayback
  release number) that Leaflet substitutes from layer options.
- `setBasemap('history')` shows that layer and the date control.
- `wireWayback()` builds the slider, thinning the full archive to ~2 dates/year.
- `setWaybackRelease(rel)` swaps the release and redraws.

`map.html` gained the History button and an empty `#wayback-ctrl` container.

Files changed: `map_v2.js`, `map.html`.

---

## How to apply

These three files are drop-in replacements for the same names in
`code/compare_ui/`. To merge:

```
copy map_v2.js                  -> code/compare_ui/map_v2.js
copy map.html                   -> code/compare_ui/map.html
copy compare_manifest_geo.json  -> code/compare_ui/compare_manifest_geo.json
```

On Render, `compare_manifest_geo.json` is the one the dashboard reads, same as
before. The map changes are pure front-end (Leaflet); no server route changes,
no new dependencies.

Quick visual check after merging: open the map, click **History**, confirm a
date slider appears and the imagery changes when you drag it. Turn off the
Buildings / POI / heatmap / walkshed layers to actually see the imagery.

---

## Not included here (separate / parked)

- Land-ownership puller (Detroit city parcel data) was built and tested but is
  NOT in this folder; it generated no committed data yet and the hover layer
  isn't wired. See the separate "Land Ownership Data, Sourcing & Cost" doc for
  the sourcing analysis and recommendation (Regrid Data With Purpose academic
  route as the single-national option).
- `cost_per_acre` / `total_gsf` for the 10 districts (no public source).
