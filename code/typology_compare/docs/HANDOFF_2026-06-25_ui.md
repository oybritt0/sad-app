# SAD Tool: Deep Restart Note (UI + channels) - 2026-06-25 (evening)

Standalone continuation doc. Supersedes nothing; extends SAD_restart_2026-06-25_structures.md
(in SAD_structures_handoff_2026-06-25.zip). Read that for the NSI build detail. This
note is the current frontier: the three-channel server is live, the Compare and Map
UI just got four fixes applied and validated, and two wire-ups remain.

================================================================
## WHAT THE TOOL IS (one paragraph)

Draw a district boundary on a map, get which of 37 existing North American
stadium-anchored districts (SADs) it most resembles, and why. Retrieval only;
generation is a confirmed dead end at N=37. Districts are read through the firm's
four-typology framework (PDF: 2026_0505 SAD Typologies DRAFT 1): Entertainment
Destinations, Community-Centered Districts, Innovation/Employment Districts, Sports
Tourism Districts (the tool's internal shorthand for the last is "Sports Park").
The framework sorts on two axes: who it serves (local/everyday to visitor/
destination) and what value it creates (civic to commercial). Typology is defined
by economic function, not built form; the tool's own findings confirm functional
signals beat morphology.

## THE THREE CHANNELS (all live on the match server, port 8000)

The tool reads a district through three independent lenses, each ranks the 37 and
votes a typology fit:
1. Demographic - who lives there (Census ACS). 35 districts. POST /analyze.
2. Program - what uses are there (Overture POIs). 39 districts. POST /analyze_program.
   Returns typology_fit {percent_by_typology, ranked, top_typology, why, neighbors}.
3. Structure - what the buildings are by floor area (FEMA/USACE NSI). 37 districts.
   POST /analyze_structures. NEW this session. Same typology_fit contract.

Server boot prints all three:
  program corpus: 39 districts with POI histograms
  corpus loaded: 35 SADs (demographic)
  structure corpus: 37 districts with NSI shares

NSI verdict (already measured): complementary housing-truth channel, NOT a
replacement. Separates typologies slightly better than program (0.0543 vs 0.0397),
recovers residential POI missed in 16 of 35, but is sport-blind (HAZUS has no sport
class so the anchor lands in retail) and under-reads stories. Kept as its own
channel. The program-vs-structure disagreement is the useful signal.

================================================================
## PATHS AND CONVENTIONS (unchanged)

- Scripts: C:\Users\jmeyers\Desktop\sad-app\code\
- Data:    C:\Users\jmeyers\Desktop\Detroit_Test\data
- UI:      C:\Users\jmeyers\Desktop\Detroit_Test\data\_compare_ui\
           (compare_dash.js, compare.html, map_v2.js, map.html)
- QGIS bundled Python only:
    $bat = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
- Detroit is folder 32, test it first. Folders numbered 02-38.
- Dry-run by default; new-named files, never overwrite; py_compile / node --check
  before delivery; no em dashes; PowerShell for changes; read the real file before
  assuming. Deliver edits as anchor-checked Python patchers (backup, verify every
  anchor, idempotent), NOT raw PowerShell here-strings. Validate JS with node --check.

## TYPOLOGY COLORS
Entertainment #D85A30, Innovation #534AB7, Sports Park #1D9E75, Community #888780.

================================================================
## WHAT WAS DONE THIS SESSION

A) NSI structure channel built end to end and wired live (see structures note + zip):
   nsi_corpus_enrich.py, structure_vs_program_eval.py, structure_match.py,
   wire_structure_channel.py. 37 NSI files written. Server runs three channels.

B) Four UI fixes, applied via two anchor-checked patchers (node-validated, backups
   taken: compare_dash_BACKUP_20260625_143717.js, map_v2_BACKUP_20260625_143719.js):
   - patch_compare_dash.py (12 anchors) and patch_map_v2.py (4 anchors).
   1. Typology explainer. Hover any Typology fit bar/legend swatch for the type's
      definition. compare_dash cTypology got <title> tooltips + a subtitle hint;
      map_v2 typoFitSection got title= attrs + a one-line caption. Both reference a
      new TYPO_DEF map.
   2. Program mix donut made honest. Subtitle now "business counts, blind to
      housing, inflates retail". It is also program_real-READY: cProgram now prefers
      a per-district field r.program_real ({bucket: share}); when present the center
      shows "BY AREA" and the ring shows real floor-area shares. Dormant until the
      manifest carries program_real (see OPEN 1).
   3. Closeness panel fixed for a drawn district. cSim had no row in the morphology
      distance matrix (DIST) for a drawn focus, so it sat empty. Now falls back to
      program-vector closeness (vecOf) and relabels its subtitle "program (form not
      available for a drawn district)". CONFIRMED rendering: bars show, honest label.
   4. Drawn district city label. map_v2 now has resolveCity(geometry): centroid +
      Nominatim reverse geocode to "City, ST", set into S.drawn.analysis.name/region
      after /analyze, used in the panel title and carried into save. Fails safe to
      "Drawn district" if the geocoder is unreachable.

CONFIRMED working by eye: typology fit chart renders both places; program donut
honest subtitle; closeness fallback bars + honest subtitle; server three channels.
NOT YET confirmed by eye: typology hover tooltip; city label on a fresh draw.

================================================================
## KEY UI INTERNALS (so the next chat does not re-read blind)

compare_dash.js (~294 lines, CRLF):
- Loads compare_manifest_geo.json (NOT compare_manifest.json).
- extract(m): builds row.name and row.city from the FOLDER NAME parts
  (parts[1], parts[2]). This is why a saved drawn district reads
  "Drawn district / Drawn boundary" in the dashboard. The Map-side city label does
  not change this; fixing the dashboard label means the saved folder must carry the
  city (save sends name = resolved city, so once a city-named district is saved and
  the manifest rebuilt, it shows correctly).
- DIST = manifest.embedding.distance_matrix (SNF morphology+demographics). Drawn
  districts are not in it.
- cProgram donut: reads r.amenity (POI category_counts); now prefers r.program_real.
- cSim closeness: now has the program fallback + basis label.
- cTypology: reads FITS via ensureFit(id) which POSTs /analyze_program per district;
  TYPO_ORDER, TYPO_FIT_COLORS, TYPO_DEF globals.
- CHARTS registry order: typology, census, table, benchmark, rose, similarity,
  program, scatter, ranking.

map_v2.js (~1089 lines, CRLF):
- typoFitSection(prog): renders /analyze_program typology_fit; now has hover defs.
- simListHTML(lens,a,demoMatches): program/demographics toggle in the Map draw panel.
- resolveCity(geometry): NEW; Nominatim reverse geocode.
- Two draw paths POST /analyze (CREATED handler ~524, restore ~845); both now resolve
  city. saveArea() ~744 sends name = S.drawn.analysis.name.

================================================================
## OPEN ITEMS (priority order, exact actions)

1. REAL NSI DATA IN THE DONUT (highest value, blocked on one file).
   The dashboard reads compare_manifest_geo.json. build_compare_manifest.py writes
   compare_manifest.json, a DIFFERENT file; a separate geo-manifest builder produces
   the _geo version and I do not have it. ACTION: locate that geo-builder script
   (search code\ for whatever writes compare_manifest_geo.json; likely adds geometry/
   coords to the plain manifest). Then add per-district program_real = the NSI
   shares_sqft from derived/structures/nsi_structures_*.json (newest), 8 buckets
   matching AMEN_C keys: retail_food_entertainment, office, sport, parking, hotel,
   residential, open_space, other. Rerun the builder. The donut auto-flips to
   "BY AREA" with real program. No JS change needed; cProgram is already ready.

2. CONFIRM the two unconfirmed fixes by eye (typology hover, city on fresh draw).
   If city stays blank, the work network is blocking Nominatim (it blocks
   overpass-api.de). Fallback: server-side US Census geocoder in /analyze (network
   reaches Census). Needs the CURRENT sad_match_server.py to patch safely.

3. STRUCTURE CHANNEL'S OWN PANEL. Add a "Most similar by structure" strip using
   POST /analyze_structures, distinct from the program panel, in both the Map draw
   panel (map_v2 simListHTML could gain a third lens 'structure', or a new section)
   and the Compare dashboard (a cStructure chart, or extend cTypology to toggle
   program vs structure channel). Same typology_fit contract, so it mirrors the
   program wiring already in place.

4. ANCHOR-TO-SPORT RECLASS (the one lever that lifts Sports Park separation).
   Build a v2 pass over the nsi_structures files: using anchor_overrides.json
   ([lon,lat]/largest specs for Cowboys HQ, Ford Center, The Star Frisco, Detroit,
   etc.), reclassify the NSI structure nearest each anchor into the sport bucket,
   de-inflating retail and giving the structure channel the sport signal HAZUS
   denies it. Re-run structure_vs_program_eval to measure the lift.

5. NAMING reconcile: tool says "Sports Park", PDF says "Sports Tourism Districts".
   Same category. Decide one label for UI text.

================================================================
## FILE INVENTORY (this session's deliverables, in /mnt outputs and on machine)

- patch_compare_dash.py, patch_map_v2.py  (applied; idempotent; keep as record)
- nsi_corpus_enrich.py, structure_vs_program_eval.py, structure_match.py,
  wire_structure_channel.py  (structure channel; in SAD_structures_handoff zip)
- Backups on machine: compare_dash_BACKUP_20260625_143717.js,
  map_v2_BACKUP_20260625_143719.js, sad_match_server_20260625_1129.bak.py
