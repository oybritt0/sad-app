#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_port_extract.py

Ports the working /extract route from district_server.py into the running
sad_match_server.py, so drawn-area layer toggles (Buildings/Parks/etc.) work.
The layer-pull engine (module_area_extract.py) already exists; only the route
lives in the wrong server. This lifts the exact route (decorators + function)
plus any module-level helper it calls, and injects them into make_app.

Reads both files on disk. Idempotent (skips if /extract already present in the
target). Compiles the patched target before writing. Dry-run by default; pass
--write to back up (timestamped .bak) then write in place.

Usage (PowerShell, QGIS bundled python):
  $bat  = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
  $code = "C:\Users\jmeyers\Desktop\Detroit_Test\code"
  & $bat "$code\patch_port_extract.py" --code-dir $code
  # review dry-run, then re-run with --write, then RESTART the match server
"""
import argparse
import datetime
import io
import os
import re
import sys


def indent_of(line):
    return len(line) - len(line.lstrip())


def lift_block(lines, idx, allow_decorators):
    """Lift a def (with optional preceding decorators) starting at idx by
    indentation. Returns (list_of_lines, next_index)."""
    base = indent_of(lines[idx])
    out = []
    i = idx
    if allow_decorators:
        while i < len(lines) and lines[i].lstrip().startswith("@"):
            out.append(lines[i]); i += 1
    if i < len(lines) and lines[i].lstrip().startswith("def "):
        out.append(lines[i]); i += 1
    else:
        return out, i
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == "":
            out.append(ln); i += 1; continue
        if indent_of(ln) > base:
            out.append(ln); i += 1; continue
        break
    while out and out[-1].strip() == "":
        out.pop()
    return out, i


def find_route(lines, path):
    pat = re.compile(r'@app\.route\(\s*[\'"]' + re.escape(path) + r'[\'"]')
    for i, ln in enumerate(lines):
        if pat.search(ln):
            return i
    return None


def module_level_defs(lines):
    """name -> start line index, for top-level (indent 0) def lines."""
    out = {}
    for i, ln in enumerate(lines):
        m = re.match(r'def (\w+)\s*\(', ln)
        if m:
            out[m.group(1)] = i
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--source", default="district_server.py",
                    help="Server that HAS /extract (default district_server.py)")
    ap.add_argument("--target", default="sad_match_server.py",
                    help="Running server to add /extract to")
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

    if re.search(r'@app\.route\(\s*[\'"]/extract[\'"]', tgt):
        print("Already ported: %s already registers /extract. No change." % args.target)
        sys.exit(0)

    src_lines = src.splitlines(keepends=True)
    tgt_lines = tgt.splitlines(keepends=True)

    ridx = find_route(src_lines, "/extract")
    if ridx is None:
        print("ERROR: no /extract route found in %s." % args.source); sys.exit(3)

    route_block, _ = lift_block(src_lines, ridx, allow_decorators=True)
    if not route_block:
        print("ERROR: could not lift the /extract route block."); sys.exit(3)
    route_text = "".join(route_block)

    # transitive helper lift: module-level defs in source that the route calls
    src_defs = module_level_defs(src_lines)
    tgt_defs = set(module_level_defs(tgt_lines).keys())
    needed = []
    seen = set()
    scan_texts = [route_text]
    while scan_texts:
        t = scan_texts.pop()
        for name, sidx in src_defs.items():
            if name in seen or name in tgt_defs:
                continue
            if re.search(r'\b' + re.escape(name) + r'\s*\(', t):
                blk, _ = lift_block(src_lines, sidx, allow_decorators=False)
                if blk:
                    seen.add(name)
                    needed.append((name, "".join(blk)))
                    scan_texts.append("".join(blk))

    # does source alias the engine as module_area_extract as a ?
    engine_alias = None
    m = re.search(r'import\s+module_area_extract\s+as\s+(\w+)', src)
    if m:
        engine_alias = m.group(1)

    # ---- build the new target ----
    # 1) helpers at module level, right before 'def make_app'
    mk = re.search(r'^def make_app\(', tgt, re.M)
    if not mk:
        print("ERROR: could not find 'def make_app(' in %s." % args.target); sys.exit(4)
    helper_ins = mk.start()
    helpers_text = ""
    for name, blk in needed:
        helpers_text += blk.rstrip("\n") + "\n\n\n"

    new_tgt = tgt[:helper_ins] + helpers_text + tgt[helper_ins:]

    # recompute after inserting helpers
    tgt_lines2 = new_tgt.splitlines(keepends=True)

    # 2) guarded engine import inside make_app, right after 'app = Flask(__name__)'
    anchor = re.search(r'^([ \t]*)app\s*=\s*Flask\(__name__\)[^\n]*\n', new_tgt, re.M)
    if not anchor:
        print("ERROR: could not find 'app = Flask(__name__)' in %s." % args.target); sys.exit(4)
    ind = anchor.group(1)
    alias = engine_alias or "a"
    import_block = (
        "{i}# --- ported layer-extract engine (module_area_extract) ---\n"
        "{i}try:\n"
        "{i}    import module_area_extract as {al}\n"
        "{i}except Exception as _e:\n"
        "{i}    {al} = None\n"
        "{i}    print('  WARN: module_area_extract import failed:', _e)\n"
    ).format(i=ind, al=alias)
    pos = anchor.end()
    new_tgt = new_tgt[:pos] + import_block + new_tgt[pos:]

    # 3) the /extract route, right before the make_app 'return app'
    #    (the first 'return app' after def make_app)
    mk2 = re.search(r'^def make_app\(', new_tgt, re.M)
    ret = re.search(r'\n([ \t]*)return app\b', new_tgt[mk2.start():])
    if not ret:
        print("ERROR: could not find 'return app' in make_app."); sys.exit(4)
    ret_indent = ret.group(1)
    abs_ret_start = mk2.start() + ret.start() + 1  # +1 to land after the leading \n
    route_indented = route_text
    if not route_indented.endswith("\n"):
        route_indented += "\n"
    block = route_indented + "\n"
    new_tgt = new_tgt[:abs_ret_start] + block + new_tgt[abs_ret_start:]

    # compile guard
    try:
        compile(new_tgt, tgt_path, "exec")
    except SyntaxError as e:
        print("ERROR: patched target failed to compile: %s" % e)
        print("       No file written. The route lift needs review; nothing changed.")
        sys.exit(5)

    # free-name advisory (names the route uses that may be unbound in make_app)
    watch = ["release", "RELEASE", "corpus", "data_dir", "api_key", "year"]
    used = [w for w in watch if re.search(r'\b' + w + r'\b', route_text)]

    print("Source route : %s  ->  %s" % (args.source, args.target))
    print("Engine alias : %s (module_area_extract)" % alias)
    print("Helpers lifted: %s" % (", ".join(n for n, _ in needed) if needed else "(none)"))
    print("Compile      : OK")
    if used:
        print("NOTE: the route references %s. make_app provides data_dir/api_key/year;" % ", ".join(used))
        print("      if it uses 'release' or another closure var not present, tell me and")
        print("      I will thread it through. (Compile passed; this is a runtime heads-up.)")
    print("")
    print("--- /extract route being injected (verbatim from %s) ---" % args.source)
    for ln in route_block:
        sys.stdout.write("    " + ln)
    if not route_block[-1].endswith("\n"):
        print("")

    if not args.write:
        print("")
        print("DRY-RUN ONLY. No file written. Re-run with --write to apply, then")
        print("RESTART the match server (Ctrl+C Window 1, re-run) and hard-reload.")
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
