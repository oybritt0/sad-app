#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_fix_area_globals.py

Fix-up for the ported /extract route: the lifted _area() helper reads two
module-level cache globals (_AREA, _AREA_ERR) that were not carried across from
district_server.py, causing NameError at request time. This lifts those exact
top-level assignments from district_server.py into sad_match_server.py, placed
just before _area's definition (or before make_app if _area is not found).

Idempotent (skips names already defined at module level in the target).
Compiles the patched target before writing. Dry-run by default; --write applies.

Usage (PowerShell, QGIS bundled python):
  $bat  = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
  $code = "C:\Users\jmeyers\Desktop\Detroit_Test\code"
  & $bat "$code\patch_fix_area_globals.py" --code-dir $code
  # review dry-run, then re-run with --write, then RESTART the match server
"""
import argparse
import datetime
import io
import os
import re
import sys

# the cache globals _area() depends on
WANTED = ["_AREA", "_AREA_ERR"]


def toplevel_assign(lines, name):
    """Return (start_idx, block_lines) for a top-level `name = ...` assignment,
    including any continuation via brackets/parens or trailing backslash.
    None if not found."""
    pat = re.compile(r'^' + re.escape(name) + r'\s*(?::[^=]+)?=')
    for i, ln in enumerate(lines):
        if pat.match(ln):
            block = [ln]
            # simple single-line assignment is the expected case (= None)
            # extend for open brackets or line continuations, just in case
            depth = ln.count("(") + ln.count("[") + ln.count("{") \
                - ln.count(")") - ln.count("]") - ln.count("}")
            cont = ln.rstrip("\n").endswith("\\")
            j = i + 1
            while (depth > 0 or cont) and j < len(lines):
                nl = lines[j]
                block.append(nl)
                depth += nl.count("(") + nl.count("[") + nl.count("{") \
                    - nl.count(")") - nl.count("]") - nl.count("}")
                cont = nl.rstrip("\n").endswith("\\")
                j += 1
            return i, block
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--source", default="district_server.py")
    ap.add_argument("--target", default="sad_match_server.py")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    src_path = os.path.join(args.code_dir, args.source)
    tgt_path = os.path.join(args.code_dir, args.target)
    for p in (src_path, tgt_path):
        if not os.path.exists(p):
            print("ERROR: not found: %s" % p); sys.exit(2)

    with io.open(src_path, "r", encoding="utf-8", newline="") as f:
        src = f.read()
    with io.open(tgt_path, "r", encoding="utf-8", newline="") as f:
        tgt = f.read()

    src_lines = src.splitlines(keepends=True)

    # which wanted globals are already defined at top level in the target?
    def defined_toplevel(text, name):
        return re.search(r'^' + re.escape(name) + r'\s*(?::[^=]+)?=', text, re.M) is not None

    missing = [n for n in WANTED if not defined_toplevel(tgt, n)]
    if not missing:
        print("Nothing to do: %s already defines %s. No change."
              % (args.target, ", ".join(WANTED)))
        sys.exit(0)

    blocks = []
    for name in missing:
        found = toplevel_assign(src_lines, name)
        if not found:
            print("ERROR: could not find a top-level '%s = ...' in %s." % (name, args.source))
            print("       Aborting; nothing changed.")
            sys.exit(3)
        blocks.append((name, "".join(found[1])))

    insert_text = "# --- ported cache globals for _area() ---\n" \
        + "".join(b for _, b in blocks)
    if not insert_text.endswith("\n"):
        insert_text += "\n"
    insert_text += "\n"

    # insert just before def _area, else before def make_app
    m = re.search(r'^def _area\s*\(', tgt, re.M)
    if not m:
        m = re.search(r'^def make_app\s*\(', tgt, re.M)
    if not m:
        print("ERROR: could not find 'def _area(' or 'def make_app(' in target.")
        sys.exit(4)
    pos = m.start()
    new_tgt = tgt[:pos] + insert_text + tgt[pos:]

    try:
        compile(new_tgt, tgt_path, "exec")
    except SyntaxError as e:
        print("ERROR: patched target failed to compile: %s" % e)
        sys.exit(5)

    print("Target        : %s" % tgt_path)
    print("Missing globals: %s" % ", ".join(missing))
    print("Compile       : OK")
    print("")
    print("--- injecting before %s ---" % ("def _area" if "def _area" in tgt else "def make_app"))
    for ln in insert_text.rstrip("\n").splitlines():
        print("  + " + ln)

    if not args.write:
        print("")
        print("DRY-RUN ONLY. No file written. Re-run with --write, then RESTART the")
        print("match server (Ctrl+C Window 1, re-run) and hard-reload.")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = "%s.%s.bak" % (tgt_path, ts)
    with io.open(backup, "w", encoding="utf-8", newline="") as f:
        f.write(tgt)
    with io.open(tgt_path, "w", encoding="utf-8", newline="") as f:
        f.write(new_tgt)
    print("")
    print("Backup written: %s" % backup)
    print("Wrote in place: %s" % tgt_path)
    print("Done. RESTART the match server (Ctrl+C Window 1, re-run it), then hard-reload.")


if __name__ == "__main__":
    main()
