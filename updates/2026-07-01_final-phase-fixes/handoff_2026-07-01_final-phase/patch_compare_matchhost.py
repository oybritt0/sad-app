#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_compare_matchhost.py

Prepends a small host shim to compare_dash.js so the Compare tab sends its
match-server calls (/analyze_program, /extract, /health, etc.) to the match
server on http://localhost:8000 instead of the static page server on :5500,
which returns 501 for POST.

The shim rewrites ONLY match-server endpoints; static asset fetches are left
alone. It is scoped in an IIFE and idempotent (marker __sadMatchHostShim). If
the file begins with a "use strict" directive, the shim is inserted right after
it so the directive stays first.

Default DRY-RUN. Pass --write to back up (timestamped .bak) then write in place.

Usage (PowerShell, QGIS bundled python):
  $bat = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
  $ui  = "C:\Users\jmeyers\Desktop\Detroit_Test\data\_compare_ui"
  & $bat "$ui\patch_compare_matchhost.py" --ui-dir $ui
  # review the dry-run, then re-run with --write appended
"""
import argparse
import datetime
import io
import os
import re
import sys

MARKER = "__sadMatchHostShim"

SHIM = r"""/* SAD match-server host shim. The Compare tab must send match-server calls to
   the match server on :8000, not the static page server on :5500 (which returns
   501 for POST). This rewrites ONLY match-server endpoints; static asset fetches
   are left untouched. Additive and reversible. */
(function(){
  if (window.__sadMatchHostShim) return;
  window.__sadMatchHostShim = true;
  var MATCH = 'http://localhost:8000';
  var EP = /^\/(analyze(_[a-z]+)?|extract|health)(\/|\?|$)/;
  var _fetch = window.fetch.bind(window);
  function reroute(u){
    if (typeof u !== 'string') return null;
    if (EP.test(u)) return MATCH + u;
    if (u.indexOf(location.origin) === 0){
      var p = u.slice(location.origin.length);
      if (EP.test(p)) return MATCH + p;
    }
    return null;
  }
  window.fetch = function(input, init){
    try {
      var url = (typeof input === 'string') ? input : (input && input.url) || '';
      var abs = reroute(url);
      if (abs){
        input = (typeof input === 'string') ? abs : new Request(abs, input);
      }
    } catch (e) { /* fall through to original fetch */ }
    return _fetch(input, init);
  };
})();
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui-dir", required=True,
                    help="Directory that contains compare_dash.js")
    ap.add_argument("--file", default="compare_dash.js",
                    help="Target filename inside --ui-dir")
    ap.add_argument("--write", action="store_true",
                    help="Back up then write in place (default is dry-run)")
    args = ap.parse_args()

    target = os.path.join(args.ui_dir, args.file)
    if not os.path.exists(target):
        print("ERROR: not found: %s" % target)
        sys.exit(2)

    with io.open(target, "r", encoding="utf-8", newline="") as f:
        s = f.read()

    if MARKER in s:
        print("Already patched: %s already has the match-host shim. No change." % args.file)
        sys.exit(0)

    # keep a leading "use strict" directive first, if present
    m = re.match(r"""\s*(['"])use strict\1\s*;?[ \t]*\r?\n""", s)
    if m:
        cut = m.end()
        new_s = s[:cut] + SHIM + s[cut:]
        where = "after the leading 'use strict' directive"
    else:
        new_s = SHIM + s
        where = "at the top of the file"

    print("Target : %s" % target)
    print("Insert : %s" % where)
    print("Shim   : rewrites /analyze*, /extract, /health to http://localhost:8000")
    print("")
    print("--- first lines after patch ---")
    for l in new_s.splitlines()[:6]:
        print("    " + l)

    if not args.write:
        print("")
        print("DRY-RUN ONLY. No file written. Re-run with --write to apply, then")
        print("hard-reload the Compare tab (Ctrl+F5). No server restart needed.")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = "%s.%s.bak" % (target, ts)
    with io.open(backup, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    with io.open(target, "w", encoding="utf-8", newline="") as f:
        f.write(new_s)
    print("")
    print("Backup written: %s" % backup)
    print("Wrote in place: %s" % target)
    print("Done. Hard-reload the Compare tab (Ctrl+F5).")


if __name__ == "__main__":
    main()
