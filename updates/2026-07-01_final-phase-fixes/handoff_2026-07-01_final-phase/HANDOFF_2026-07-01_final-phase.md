# SAD tool: final-phase fixes and handoff
## 2026-07-01

This folder contains everything from the 2026-07-01 session: the fixes that got the
drawn-district viewer fully working (analyze, matching, Compare tab, layer toggles,
nature block), a one-click launcher, the remaining save fix, and a roadmap for
expanding the Nature lens with more public data sources. No em dashes per convention.

Target repo for deploy: `oybritt0/sad-app` (Britto deploys to Render). Suggested
push flow, unchanged: copy this folder into `updates\2026-07-01_final-phase-fixes\`,
then `git add -f` the folder, commit, `git pull --rebase origin main`, push to main.

---

## WHAT IS NOW WORKING (verified this session, in server logs + browser)

- Analyze on a drawn district: population, income, profile radar. Was stuck on
  "Pulling ACS ..." because of a CORS preflight failure. FIXED.
- Compare tab matching: was showing "match server not reachable" because it POSTed
  to the static page server on :5500. FIXED (host shim).
- Drawn-area layer toggles: Buildings, Parks, Streets, POIs, Transit, Walkshed,
  heatmap. Were 404 because the `/extract` route lived only in the old
  `district_server.py`. FIXED (route ported into `sad_match_server.py`).
- Drawn-panel Nature block: green cover, water, POI programs pull live on a fresh
  draw. FIXED (injectNature call added to `renderDrawnPanel`).

## WHAT IS NOT DONE (known, with the reason)

- `/save_area` ("Save as district"): still 404. Same class of fix as `/extract`
  (the route is in `district_server.py`, not the running server). Porter script is
  included: `PASTE_job2e_port_save.ps1` + `patch_port_save.py`. Apply steps below.
- Canopy height / trees in the Nature block: BLANK. Root cause is a NETWORK BLOCK,
  not a bug. The ETH Global Canopy Height tile host
  (`share.phys.ethz.ch`) is unreachable from the work network, exactly like
  `overpass-api.de` was. See NATURE_EXPANSION_ROADMAP.md for the fix paths (the
  clean one is to switch canopy to NLCD, which is US-wide and fully public).
- `census` layer 500 in the `/extract` log: harmless. The client asks `/extract`
  for a "census" layer the engine does not serve; census block groups come from a
  different path. Cosmetic; can be silenced by having the client not request it,
  or by returning an empty FeatureCollection for unknown layers.
- Nature display polish: "greener than 100% of corpus" wording and the blank
  Canopy/Trees/Clean-air corpus-rank bars. Cosmetic JS.

---

## HOW THE FIXES WERE MADE (so they can be reviewed or re-applied)

Every fix is a small, anchored, self-guarding patcher: it reads the actual file,
verifies its anchor, backs up (timestamped .bak), compiles/validates, and only then
writes. All default to dry-run; add `--write` to apply. This is the project's
standing convention.

1. CORS preflight shim  (`patch_cors_preflight.py`, wrapper `PASTE_job2_cors_fix.ps1`)
   Adds a global `before_request` OPTIONS handler to `make_app` so every route
   answers the browser preflight with 204 + CORS headers. Without it, module routes
   ran their POST body on the preflight, threw, and returned 500, which the browser
   blocked. Fixes toggles + nature + program/demographic matching at once.

2. Compare host shim  (`patch_compare_matchhost.py`, wrapper `PASTE_job2b_compare_host.ps1`)
   Prepends a scoped shim to `compare_dash.js` that reroutes match-server calls
   (`/analyze*`, `/extract`, `/health`) to `http://localhost:8000`. Static asset
   fetches are untouched.

3. Drawn-panel nature  (`patch_drawn_nature.py`, wrapper `PASTE_job1_drawn_nature.ps1`)
   Adds `injectNature({ sad_id: 'drawn', typology: null });` at the end of
   `renderDrawnPanel` in `map_v2.js`, so a fresh draw fires the live nature pull.

4. Port /extract  (`patch_port_extract.py`, wrapper `PASTE_job2c_port_extract.ps1`)
   Lifts the working `/extract` route (and helper `_area`) from `district_server.py`
   into `sad_match_server.py`. The pull engine `module_area_extract.py` was already
   present and correct; only the route was in the wrong server.

5. Fix _AREA globals  (`patch_fix_area_globals.py`, wrapper `PASTE_job2d_fix_area_globals.ps1`)
   The ported `_area()` reads module-level cache globals `_AREA` / `_AREA_ERR` that
   were not carried across in step 4. This lifts them. (After this session's later
   port scripts, global-lifting is built into the porter, so step 5 is only needed
   if applying step 4's older version.)

6. Port /save_area  (`patch_port_save.py`, wrapper `PASTE_job2e_port_save.ps1`)
   Same as step 4 but for `/save_area`, and this version also lifts any module-level
   GLOBALS the route references (so there is no repeat of the _AREA miss). NOT YET
   APPLIED. Apply steps below.

IMPORTANT dependency: the port scripts (4 and 6) read `district_server.py` to lift
the route source. That file must be present in the code dir when they run. It is in
Zeffer's `Detroit_Test\code\`. If the deploy repo does not include it, either add it
temporarily to run the port, or paste the verbatim `/extract` route (captured in
this doc's appendix) directly into `sad_match_server.py`.

---

## APPLY ORDER ON A FRESH CHECKOUT (for the coworker)

Prereqs: QGIS bundled python only:
  `C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat`  (call it $bat)
  code dir  = `...\code`   (has sad_match_server.py, district_server.py, module_area_extract.py)
  data dir  = `...\data`   (has _compare_ui\ with map_v2.js, compare_dash.js)

Each PASTE_*.ps1 is meant to be RUN AS A WHOLE (paste its full contents into
PowerShell, or `Get-Content file.ps1 -Raw | Invoke-Expression`). It writes its
patcher .py, then dry-runs. After reviewing the dry-run, re-run the patcher with
`--write`. Do NOT run the `--write` line before the paste-block has written the .py.

Recommended sequence:

  # 1. CORS (server)      -> then RESTART the match server
  # 2. Compare host (JS)  -> hard-reload only
  # 3. Drawn nature (JS)  -> hard-reload only
  # 4. Port /extract      -> RESTART the match server
  # 5. Port /save_area    -> RESTART the match server

Server writes need a match-server restart (Ctrl+C, re-run) because make_app rebuilds
at boot. JS writes need only a hard browser reload (Ctrl+F5).

To apply /save_area right now (steps already 1-4 done in Zeffer's copy):

  $bat  = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
  $code = "C:\Users\jmeyers\Desktop\Detroit_Test\code"
  Get-Content "<path>\PASTE_job2e_port_save.ps1" -Raw | Invoke-Expression   # writes patch_port_save.py + dry-run
  & $bat "$code\patch_port_save.py" --code-dir $code --write                # apply
  # then Ctrl+C the match server window and re-run it, then click Save as district

---

## LAUNCHER

`START_SAD.bat` (double-clickable, no execution-policy dance): opens the match server
(8000) and page server (5500) each in their own window, waits for
`http://localhost:8000/health`, then opens
`http://localhost:5500/_compare_ui/map.html`. Paths inside match Zeffer's machine;
edit the three `set` lines for a different checkout.

---

## APPENDIX: verbatim /extract route (drop-in, if the porter cannot read district_server.py)

Insert inside `make_app`, before `return app`. It relies on `_area()` and the
globals `_AREA` / `_AREA_ERR`, and on `request`, `jsonify`, `traceback`, and the
closure vars `year`, `api_key` (all present in make_app).

    @app.route("/extract", methods=["POST", "OPTIONS"])
    def extract():
        if request.method == "OPTIONS":
            return ("", 204)
        a, err = _area()
        if not a:
            return jsonify({"ok": False, "error": f"Extract engine unavailable: {err}"}), 500
        try:
            b = request.get_json(force=True)
            geom = b.get("geometry")
            layer = b.get("layer")
            ext = b.get("extent", "city")
            if not geom or not layer:
                return jsonify({"ok": False, "error": "geometry and layer required"}), 400
            if layer == "walkshed":
                fc = a.walkshed_polygon(geom)
                return jsonify({"ok": True, "geojson": fc,
                                "extent": {"kind": "sad", "name": "Drawn boundary"}})
            extent_poly, extent_info = a.resolve_extent(geom, ext, year, api_key)
            fc = a.extract_layer(extent_poly, layer)
            return jsonify({"ok": True, "geojson": fc, "extent": extent_info})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

And the two cache globals at module level (above make_app):

    _AREA = None
    _AREA_ERR = None
