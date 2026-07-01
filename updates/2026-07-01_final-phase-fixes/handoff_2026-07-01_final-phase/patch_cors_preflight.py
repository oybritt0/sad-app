#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_cors_preflight.py

Adds a global CORS preflight shim to make_app() in sad_match_server.py so that
EVERY route (including modular /extract, /analyze_program, /analyze_nature)
answers the browser OPTIONS preflight with 204 + CORS headers before the route
handler runs. Without this, module routes execute their POST body on the
preflight, throw, and return 500, which the browser blocks as
"preflight ... does not have HTTP ok status".

Anchors on the single  app = Flask(__name__)  line inside make_app and inserts
the shim right after it. Requires exactly one match. Idempotent (marker
_cors_preflight). Compiles the patched source before writing.

Default DRY-RUN. Pass --write to back up (timestamped .bak) then write in place.

Usage (PowerShell, QGIS bundled python):
  $bat  = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
  $code = "C:\Users\jmeyers\Desktop\Detroit_Test\code"
  & $bat "$code\patch_cors_preflight.py" --code-dir $code
  # review the dry-run, then re-run with --write appended
"""
import argparse
import datetime
import io
import os
import re
import sys

MARKER = "_cors_preflight"
ANCHOR = re.compile(r"^(?P<indent>[ \t]*)app\s*=\s*Flask\(__name__\)[^\n]*$", re.M)

SHIM_TEMPLATE = (
    "{ind}# --- global CORS preflight shim (all routes; answers OPTIONS 204) ---\n"
    "{ind}from flask import make_response as _make_response\n"
    "{ind}@app.before_request\n"
    "{ind}def _cors_preflight():\n"
    "{ind}    if request.method == \"OPTIONS\":\n"
    "{ind}        _r = _make_response(\"\", 204)\n"
    "{ind}        _r.headers[\"Access-Control-Allow-Origin\"] = \"*\"\n"
    "{ind}        _r.headers[\"Access-Control-Allow-Headers\"] = \"Content-Type\"\n"
    "{ind}        _r.headers[\"Access-Control-Allow-Methods\"] = \"POST, GET, OPTIONS\"\n"
    "{ind}        return _r\n"
    "{ind}# --- end CORS preflight shim ---\n"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", required=True,
                    help="Directory that contains sad_match_server.py")
    ap.add_argument("--file", default="sad_match_server.py",
                    help="Target filename inside --code-dir")
    ap.add_argument("--write", action="store_true",
                    help="Back up then write in place (default is dry-run)")
    args = ap.parse_args()

    target = os.path.join(args.code_dir, args.file)
    if not os.path.exists(target):
        print("ERROR: not found: %s" % target)
        sys.exit(2)

    with io.open(target, "r", encoding="utf-8", newline="") as f:
        s = f.read()

    if MARKER in s:
        print("Already patched: %s already contains the CORS preflight shim. No change."
              % args.file)
        sys.exit(0)

    matches = list(ANCHOR.finditer(s))
    if len(matches) != 1:
        print("ERROR: expected exactly 1 'app = Flask(__name__)' line, found %d." % len(matches))
        for m in matches:
            ln = s.count("\n", 0, m.start()) + 1
            print("   line %d: %s" % (ln, m.group(0).strip()))
        print("       Aborting rather than guessing. Paste make_app if this is wrong.")
        sys.exit(3)

    m = matches[0]
    indent = m.group("indent")
    anchor_line = m.group(0)
    ln = s.count("\n", 0, m.start()) + 1

    # insert right after the anchor line (after its newline)
    line_end = m.end()
    if line_end < len(s) and s[line_end] == "\n":
        line_end += 1
    shim = SHIM_TEMPLATE.format(ind=indent)
    new_s = s[:line_end] + shim + s[line_end:]

    # compile guard before writing
    try:
        compile(new_s, target, "exec")
    except SyntaxError as e:
        print("ERROR: patched source failed to compile: %s" % e)
        sys.exit(4)

    print("Target      : %s" % target)
    print("Anchor      : line %d  ->  %s" % (ln, anchor_line.strip()))
    print("Compile     : OK")
    print("")
    print("--- inserting after the anchor ---")
    print("    " + anchor_line.strip())
    for l in shim.rstrip("\n").splitlines():
        print("  + " + l)
    print("")

    if not args.write:
        print("DRY-RUN ONLY. No file written. Re-run with --write to apply, then")
        print("restart the match server (Window 1) so make_app rebuilds.")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = "%s.%s.bak" % (target, ts)
    with io.open(backup, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    with io.open(target, "w", encoding="utf-8", newline="") as f:
        f.write(new_s)
    print("Backup written: %s" % backup)
    print("Wrote in place: %s" % target)
    print("Done. RESTART the match server (Ctrl+C in Window 1, re-run it), then reload the page.")


if __name__ == "__main__":
    main()
