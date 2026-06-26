# SAD Tool: Restart Note (structure channel) - 2026-06-25

Continues SAD_restart_2026-06-25.md. This session added the FEMA/USACE NSI
structure-occupancy channel end to end and answered whether NSI building data
beats what the corpus already had.

## WHAT WAS DONE THIS SESSION

1. Built corpus-wide NSI building-attribute enrichment for all 37 districts.
2. Compared the NSI read against the existing program read, in the project's own
   validated terms (typology separation + leave-one-out placement).
3. Built the structure channel (mirror of program_match) and wired it into the
   live server. Three channels now run at once: demographic, program, structure.

## FILES DELIVERED (all in sad-app\code\, py_compiled, self-tested)

- `nsi_corpus_enrich.py` - one-time corpus pull. POSTs each district's
  source/sad_boundary.geojson bbox to https://nsi.sec.usace.army.mil/nsiapi/structures,
  clips returned structures to the polygon by point-in-polygon, maps HAZUS occtype
  to the 8 Rossetti buckets, writes derived/structures/nsi_structures_<stamp>.json
  per district (per-structure occtype, st_damcat, sqft, num_story, est_height_m
  proxy, found_ht foundation, bldgtype, bucket, lon/lat + occupancy-share summary).
  Modes: --derived (one), --data (corpus, Detroit first), --apply, --selftest.
- `structure_vs_program_eval.py` - the is-it-better comparison. Reads NSI
  shares_sqft vs program shares_after per district. Sections: residential headline,
  building-data coverage (counts + stories vs Overture), typology separation,
  leave-one-out placement (k=7), verdict. --data-dir, --full, --out, --selftest.
- `structure_match.py` - the channel. StructureCorpus + rank + typology_fit
  (same shape as program: percent_by_typology, ranked, top_typology, why,
  neighbors), register(app, data_dir) -> POST /analyze_structures. Live drawn
  pull reuses nsi_corpus_enrich. --data-dir, --selftest.
- `wire_structure_channel.py` - safe edit helper that inserted the structure
  register block into sad_match_server.py (backed up the original, compiled the
  result). Idempotent. Already applied.

## STATE (confirmed working on the machine)

- Corpus pull ran: 37 NSI files written, 4 Canadian districts stubbed
  (available:false, NSI is US only), 1 skip (00_Admin, no boundary).
- Server boots all three channels:
    program corpus: 39 districts with POI histograms
    corpus loaded: 35 SADs (demographic)
    structure corpus: 37 districts with NSI shares
  on http://localhost:8000, POST /analyze_structures live.
- Original server backed up: sad_match_server_20260625_1129.bak.py

## VERDICT: is NSI better than what we have

Mixed, and the eval called it a split. Net: keep NSI as its OWN channel, do not
fuse it into program.

- BETTER: NSI separates the four typologies more than program-alone
  (0.0543 vs 0.0397). It catches residential the floor-area read missed in 16 of
  35 districts, some large (Pacific Yards 0 to 86%, McGregor 0 to 93%,
  Downtown Commons 3 to 51%, Victory Park 5 to 48%).
- NOT BETTER: NSI is sport-blind (HAZUS has no sport class), so the anchor lands
  in retail (Detroit retail 81%, Arlington 95%) and sport reads 0 everywhere.
  Story counts read far too low (NSI median 1 to 2 vs Overture 7 to 12); Overture
  stays the height source. Detroit clipped to its boundary reads 6.9% residential,
  below program 16.9% and well below the 23.3% canvas figure that started this;
  the canvas number was inflated by housing outside the district.
- Leave-one-out placement tied (both 30.3% hard, 97% hybrid); hybrid is saturated
  at this k and a 54.5% majority, so placement does not separate the channels;
  separation does.

## KEY FINDINGS / DECISIONS (carry forward)

- Boundary, not canvas: structure shares are clipped to source/sad_boundary.geojson,
  making them comparable to the program read.
- RES4 (HAZUS temporary lodging) maps to hotel, not residential (seed had it wrong).
- est_height_m is a num_story x 3.5 proxy only; NSI has no building height
  (found_ht is foundation/flood). Overture is the measured-height source.
- The disagreement between program (sees anchor as sport) and structure (sees the
  housing, blind to sport) is the analytically useful signal, per the
  inbetween-is-the-content principle. Two panels, not one fused number.

## ON THE HORIZON (next session)

1. Compare-tab UI panel strip for the structure result (second "Most similar by
   structure" section in map_v2.js / the compare UI), mirroring the program panel.
   Typology colors: Entertainment coral #D85A30, Innovation purple #534AB7,
   Sports Park teal #1D9E75, Community gray #888780.
2. Anchor-to-sport reclass (the one lever that lifts Sports Park separation):
   use anchor_overrides.json [lon,lat]/largest specs to reclassify the NSI
   structure nearest each anchor into sport, de-inflating retail and giving the
   structure channel the sport signal HAZUS denies it. Build as a v2 pass over
   the nsi_structures files; re-run structure_vs_program_eval to measure the lift.
3. (Still queued from prior note) demographic trajectory channel
   (module_4b_census_timeseries.py is built; run corpus-wide, add sparklines).

## CONVENTIONS (unchanged)

QGIS bundled Python ($bat); dry-run by default; new-named files, never overwrite;
test Detroit (folder 32) first; GeoJSON over GPKG; py_compile before delivery;
no em dashes; PowerShell for changes; deeper file inspection before assuming.
