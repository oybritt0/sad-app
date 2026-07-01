#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_drawn_nature.py

Adds a single line to the END of renderDrawnPanel(inner) in map_v2.js so that a
freshly drawn district fires the live nature pull. The wrapped injectNature sees
sad_id === 'drawn', POSTs to /analyze_nature, and fills the Nature block.

Inserted line (right before the closing brace of renderDrawnPanel):
    injectNature({ sad_id: 'drawn', typology: null });

This reads the ACTUAL file, locates renderDrawnPanel by definition (must be exactly
one), brace-matches the function body with a scanner that skips strings, template
literals (including ${...} interpolation), and comments, then inserts before the
function's closing brace. It refuses to run if:
  - renderDrawnPanel is not defined exactly once,
  - the function body cannot be brace-matched,
  - the matched body does not contain 'save-area' (wrong span guard),
  - the call is already present (idempotent no-op).

Default is DRY-RUN: it prints the tail of the function and the proposed edit.
Pass --write to back up (timestamped .bak) and write in place.

Usage (PowerShell, QGIS bundled python):
  $bat = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
  $ui  = "C:\Users\jmeyers\Desktop\Detroit_Test\data\_compare_ui"
  & $bat "$ui\patch_drawn_nature.py" --ui-dir $ui
  # review the dry-run, then re-run with --write appended
"""
import argparse
import datetime
import io
import os
import re
import sys

INSERT_LINE = "injectNature({ sad_id: 'drawn', typology: null });"

# regex forms of a renderDrawnPanel DEFINITION. Each ends at the body '{'.
DEF_PAT = re.compile(
    r"function\s+renderDrawnPanel\s*\([^)]*\)\s*\{"
    r"|renderDrawnPanel\s*=\s*(?:async\s*)?function\s*\([^)]*\)\s*\{"
    r"|renderDrawnPanel\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{"
    r"|renderDrawnPanel\s*=\s*(?:async\s*)?[A-Za-z_$][\w$]*\s*=>\s*\{"
    r"|renderDrawnPanel\s*\([^)]*\)\s*\{"
)


def match_body_end(s, body_open):
    """Given the index of the '{' that opens the function body, return the index
    of the matching '}'. Skips single/double quoted strings, backtick template
    literals with ${...} interpolation, and // and /* */ comments. Returns None
    if it cannot balance."""
    n = len(s)
    i = body_open
    depth = 0
    mode = "code"
    tmpl_depths = []  # code-brace depth recorded at each '${' entry
    while i < n:
        c = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if mode == "code":
            if c == "/" and nxt == "/":
                mode = "line"
                i += 2
                continue
            if c == "/" and nxt == "*":
                mode = "block"
                i += 2
                continue
            if c == "'":
                mode = "sq"
                i += 1
                continue
            if c == '"':
                mode = "dq"
                i += 1
                continue
            if c == "`":
                mode = "tmpl"
                i += 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
                if tmpl_depths and depth == tmpl_depths[-1]:
                    tmpl_depths.pop()
                    mode = "tmpl"
            i += 1
            continue
        if mode == "line":
            if c == "\n":
                mode = "code"
            i += 1
            continue
        if mode == "block":
            if c == "*" and nxt == "/":
                mode = "code"
                i += 2
                continue
            i += 1
            continue
        if mode == "sq":
            if c == "\\":
                i += 2
                continue
            if c == "'":
                mode = "code"
            i += 1
            continue
        if mode == "dq":
            if c == "\\":
                i += 2
                continue
            if c == '"':
                mode = "code"
            i += 1
            continue
        if mode == "tmpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                mode = "code"
                i += 1
                continue
            if c == "$" and nxt == "{":
                tmpl_depths.append(depth)
                depth += 1
                mode = "code"
                i += 2
                continue
            i += 1
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui-dir", required=True,
                    help="Directory that contains map_v2.js (…\\data\\_compare_ui)")
    ap.add_argument("--file", default="map_v2.js",
                    help="Target JS filename inside --ui-dir (default map_v2.js)")
    ap.add_argument("--write", action="store_true",
                    help="Back up then write in place (default is dry-run)")
    args = ap.parse_args()

    target = os.path.join(args.ui_dir, args.file)
    if not os.path.exists(target):
        print("ERROR: not found: %s" % target)
        sys.exit(2)

    with io.open(target, "r", encoding="utf-8", newline="") as f:
        s = f.read()

    defs = list(DEF_PAT.finditer(s))
    if len(defs) == 0:
        print("ERROR: no renderDrawnPanel definition found in %s." % args.file)
        print("       Paste the renderDrawnPanel function and I will anchor to it directly.")
        sys.exit(3)
    if len(defs) > 1:
        print("ERROR: renderDrawnPanel matched %d definition sites; expected exactly 1:"
              % len(defs))
        for m in defs:
            ln = s.count("\n", 0, m.start()) + 1
            print("   line %d: %s" % (ln, s[m.start():m.start() + 60].replace("\n", " ")))
        print("       Aborting rather than guessing. Paste the function if this is wrong.")
        sys.exit(3)

    m = defs[0]
    body_open = m.end() - 1  # index of the '{' that opens the body
    def_line = s.count("\n", 0, m.start()) + 1

    end = match_body_end(s, body_open)
    if end is None:
        print("ERROR: could not brace-match renderDrawnPanel body from line %d." % def_line)
        print("       Paste the function and I will anchor to it directly.")
        sys.exit(4)

    body = s[body_open:end + 1]

    # wrong-span guard: renderDrawnPanel is the panel that wires #save-area
    if "save-area" not in body:
        print("ERROR: matched a renderDrawnPanel span that does NOT contain 'save-area'.")
        print("       The brace match is probably wrong. Aborting; paste the function.")
        sys.exit(5)

    # idempotency
    body_ns = re.sub(r"\s+", "", body).lower()
    if "injectnature({sad_id:'drawn'" in body_ns:
        print("Already patched: renderDrawnPanel already calls injectNature for 'drawn'. "
              "No change.")
        sys.exit(0)

    # indentation of the closing brace line
    line_start = s.rfind("\n", 0, end) + 1
    close_indent = re.match(r"[ \t]*", s[line_start:end]).group(0)
    ins_indent = close_indent + "  "
    insertion = ins_indent + INSERT_LINE + "\n"

    new_s = s[:line_start] + insertion + s[line_start:]

    # ---- report ----
    tail_start = s.rfind("\n", 0, s.rfind("\n", 0, line_start))  # a couple lines up
    tail_start = s.rfind("\n", 0, line_start)
    # print the last ~12 lines of the function for visual confirmation
    body_lines = s[body_open:end + 1].splitlines()
    show = body_lines[-12:] if len(body_lines) > 12 else body_lines
    end_line = s.count("\n", 0, end) + 1

    print("Target        : %s" % target)
    print("Definition    : line %d" % def_line)
    print("Body close    : line %d" % end_line)
    print("save-area seen : yes")
    print("Insert line   : %s" % INSERT_LINE)
    print("")
    print("--- current tail of renderDrawnPanel ---")
    for ln in show:
        print("    " + ln)
    print("--- after patch (last lines shown) ---")
    preview = (s[body_open:line_start] + insertion + s[line_start:end + 1]).splitlines()
    for ln in preview[-6:]:
        print("    " + ln)

    if not args.write:
        print("")
        print("DRY-RUN ONLY. No file written. Re-run with --write to apply.")
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
    print("Done. Reload map.html, draw a district, and the Nature block should pull live.")


if __name__ == "__main__":
    main()
