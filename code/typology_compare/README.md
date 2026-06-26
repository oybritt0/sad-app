# Typology Compare

The typology layer of the SAD precedent-retrieval tool: the work that makes the
Compare and Map tabs read a stadium-anchored district through the firm's
four-typology framework, plus the NSI structure channel that backs it. This folder
is the self-contained record of that work so anyone in the repo can find it, see
what it does, and reproduce it.

SAD is retrieval only: draw a boundary, get which of 37 existing North American
stadium-anchored districts it most resembles and why. Generation is a confirmed
dead end at N=37. Typology is defined by economic function, not built form.

## The four-typology framework

Districts are sorted on two axes, who they serve (local/everyday to visitor/
destination) and what value they create (civic to commercial):

| Typology                       | Internal name | Color     |
|--------------------------------|---------------|-----------|
| Entertainment Destinations     | Entertainment | `#D85A30` |
| Community-Centered Districts    | Community     | `#888780` |
| Innovation / Employment        | Innovation    | `#534AB7` |
| Sports Tourism Districts        | Sports Park   | `#1D9E75` |

(`Sports Park` is the tool's internal shorthand for `Sports Tourism Districts`.
Reconcile to one label in UI text; see docs.)

## The three channels

The tool reads a district through three independent lenses, each ranks the 37 and
votes a typology fit. All three run live on the match server, port 8000:

1. **Demographic** - who lives there (Census ACS). 35 districts. `POST /analyze`
2. **Program** - what uses are there (Overture POIs). 39 districts. `POST /analyze_program`
3. **Structure** - what the buildings are by floor area (FEMA/USACE NSI). 37
   districts. `POST /analyze_structures`

Each returns the same `typology_fit` contract: `percent_by_typology`, `ranked`,
`top_typology`, `why`, `neighbors`.

The structure (NSI) channel is the newest piece. Verdict from the corpus
evaluation: keep it as its **own** channel, do not fuse it into program. It
separates the four typologies slightly better than program alone (0.0543 vs
0.0397) and recovers residential occupancy that POIs miss in 16 of 35 districts,
but it is sport-blind (HAZUS has no sport class, so the anchor lands in retail).
The program-vs-structure disagreement is the analytically useful signal, so the
two are shown as two panels, never one fused number.

## What is in this folder

```
typology_compare/
  README.md                       this file
  ui_patches/                     reproduce the Compare/Map typology UI changes
    patch_compare_dash.py           typology hover explainer, honest + real-ready
                                    program donut, drawn-district closeness fallback
    patch_map_v2.py                 typology hover explainer, drawn-district city label
  structure_channel/              the NSI structure channel, end to end
    nsi_corpus_enrich.py            one-time corpus-wide NSI pull (writes per-district
                                    derived/structures/nsi_structures_<stamp>.json)
    structure_match.py              the channel: ranks + typology_fit, register() adds
                                    POST /analyze_structures (mirror of program_match)
    structure_vs_program_eval.py    head-to-head: is the NSI read better than program?
    wire_structure_channel.py       bolts /analyze_structures into sad_match_server.py
  docs/                           dated handoff notes (full state + open items)
    HANDOFF_2026-06-25_ui.md        UI internals + the channel architecture
    HANDOFF_2026-06-25_structures.md  the NSI build detail and the verdict
    HANDOFF_2026-06-26.md           current frontier + the things most likely to bite
```

## The four Compare/Map UI fixes (what the patchers do)

1. **Typology explainer.** Hover any typology fit bar or legend swatch to get the
   type's definition. Adds a `TYPO_DEF` map and `<title>` tooltips in both
   `compare_dash.js` (`cTypology`) and `map_v2.js` (`typoFitSection`).
2. **Honest, real-ready program donut.** The Program mix donut subtitle now states
   it is business counts, blind to housing, and inflates retail. `cProgram` also
   prefers a per-district `program_real` field; when present the center reads
   "BY AREA" and the ring shows real NSI floor-area shares. Dormant until the
   manifest carries `program_real` (see Open item below).
3. **Drawn-district closeness fallback.** A drawn district has no row in the
   morphology distance matrix, so the closeness panel sat empty. It now falls back
   to program-vector closeness and relabels its basis honestly.
4. **Drawn-district city label.** `map_v2.js` reverse-geocodes the drawn centroid
   to "City, ST" and uses it in the panel title and the save. Fails safe to "Drawn
   district" if the geocoder is unreachable.

## How to apply and run

The UI patchers are anchor-checked and idempotent: they verify every anchor
string exists, write only if all match, back up the original first, and skip if
the change is already present. They are dry-run by default; pass `--apply` to
write. If the repo's UI files differ from the versions these were cut against, the
dry-run reports the missing anchors and writes nothing, so they are safe to try.

Run with the QGIS bundled interpreter
(`$bat = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"`):

```
# UI: dry-run, then apply, against the working copy's UI files
& $bat ui_patches\patch_compare_dash.py "<path>\compare_dash.js"
& $bat ui_patches\patch_compare_dash.py "<path>\compare_dash.js" --apply
& $bat ui_patches\patch_map_v2.py       "<path>\map_v2.js"
& $bat ui_patches\patch_map_v2.py       "<path>\map_v2.js" --apply

# Structure channel: one-time corpus pull, then wire it into the server
& $bat structure_channel\nsi_corpus_enrich.py --data --apply
& $bat structure_channel\wire_structure_channel.py "<path>\sad_match_server.py" --apply

# Optional: re-run the head-to-head evaluation any time
& $bat structure_channel\structure_vs_program_eval.py --data-dir "<path>\data" --full

# Self-tests (offline)
& $bat structure_channel\structure_match.py --selftest
& $bat structure_channel\nsi_corpus_enrich.py --selftest
```

## What is here vs what lives on the machine

This folder is the source and the docs. The runnable front-end (`compare_dash.js`,
`map_v2.js`, `compare.html`, `map.html`), the corpus data, the manifest, and
`sad_match_server.py` live in the working tree on the build machine. The patchers
reproduce the UI changes against those files; the structure scripts produce the
NSI data and wire the endpoint. To stand the whole thing up, apply the patchers to
the working-copy UI files and run the structure channel as above.

## Open item (the current frontier)

The program donut is wired to flip to real NSI floor-area the moment the dashboard
manifest carries a per-district `program_real` (the 8-bucket `shares_sqft` from the
`nsi_structures_*.json` files). That requires the geo-manifest builder that writes
`compare_manifest_geo.json` (a separate script from `build_compare_manifest.py`,
which writes the plain `compare_manifest.json`). The bucket keys already match the
donut exactly, so once that builder is found, injecting `program_real` is a direct
copy with no remap. Note: until the anchor-to-sport reclass runs, the NSI read is
sport-blind, so the "by area" donut shows inflated retail on anchor districts. See
`docs/HANDOFF_2026-06-26.md`.
