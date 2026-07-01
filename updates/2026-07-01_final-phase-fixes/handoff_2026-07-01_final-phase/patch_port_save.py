#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_port_save.py

Ports the /save_area route from district_server.py into sad_match_server.py so
the "Save as district" button works (it currently 404s). Same mechanism as the
/extract port, but this also lifts any module-level GLOBALS the route/helpers
reference (not just functions), so there is no repeat of the _AREA NameError.

Reads both files. Idempotent (skips if /save_area already in target). Compiles
the patched target before writing. Dry-run by default; --write applies.

Usage (PowerShell, QGIS bundled python):
  $bat  = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
  $code = "C:\Users\jmeyers\Desktop\Detroit_Test\code"
  & $bat "$code\patch_port_save.py" --code-dir $code
  # review dry-run, then re-run with --write, then RESTART the match server
"""
import argparse
import datetime
import io
import os
import re
import sys

ROUTE = "/save_area"


def indent_of(line):
    return len(line) - len(line.lstrip())


def lift_def(lines, idx, allow_decorators):
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


def lift_assign(lines, name):
    pat = re.compile(r'^' + re.escape(name) + r'\s*(?::[^=]+)?=')
    for i, ln in enumerate(lines):
        if pat.match(ln):
            block = [ln]
            depth = ln.count("(") + ln.count("[") + ln.count("{") \
                - ln.count(")") - ln.count("]") - ln.count("}")
            cont = ln.rstrip("\n").endswith("\\")
            j = i + 1
            while (depth > 0 or cont) and j < len(lines):
                nl = lines[j]; block.append(nl)
                depth += nl.count("(") + nl.count("[") + nl.count("{") \
                    - nl.count(")") - nl.count("]") - nl.count("}")
                cont = nl.rstrip("\n").endswith("\\"); j += 1
            return "".join(block)
    return None


def find_route(lines, path):
    pat = re.compile(r'@app\.route\(\s*[\'"]' + re.escape(path) + r'[\'"]')
    for i, ln in enumerate(lines):
        if pat.search(ln):
            return i
    return None


def toplevel_names(lines):
    """defs and simple assignments at indent 0 -> {name: kind}."""
    out = {}
    for ln in lines:
        m = re.match(r'def (\w+)\s*\(', ln)
        if m:
            out[m.group(1)] = "def"; continue
        m = re.match(r'(\w+)\s*(?::[^=]+)?=(?!=)', ln)
        if m and indent_of(ln) == 0:
            out.setdefault(m.group(1), "var")
    return out


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

    if re.search(r'@app\.route\(\s*[\'"]' + re.escape(ROUTE) + r'[\'"]', tgt):
        print("Already ported: %s already registers %s. No change." % (args.target, ROUTE))
        sys.exit(0)

    src_lines = src.splitlines(keepends=True)
    ridx = find_route(src_lines, ROUTE)
    if ridx is None:
        print("ERROR: no %s route found in %s." % (ROUTE, args.source)); sys.exit(3)

    route_block, _ = lift_def(src_lines, ridx, True)
    if not route_block:
        print("ERROR: could not lift the %s route block." % ROUTE); sys.exit(3)
    route_text = "".join(route_block)

    src_names = toplevel_names(src_lines)
    tgt_names = set(toplevel_names(tgt.splitlines(keepends=True)).keys())

    # transitive lift of referenced top-level defs AND vars not already in target
    lifted_defs, lifted_vars = [], []
    seen = set()
    scan = [route_text]
    while scan:
        t = scan.pop()
        for name, kind in src_names.items():
            if name in seen or name in tgt_names:
                continue
            if re.search(r'\b' + re.escape(name) + r'\b', t):
                if kind == "def":
                    for k, sidx in enumerate(src_lines):
                        pass
                    # find its def line index
                    di = None
                    for k, ln in enumerate(src_lines):
                        if re.match(r'def ' + re.escape(name) + r'\s*\(', ln):
                            di = k; break
                    if di is None:
                        continue
                    blk, _ = lift_def(src_lines, di, False)
                    if blk:
                        seen.add(name); lifted_defs.append((name, "".join(blk)))
                        scan.append("".join(blk))
                else:
                    blk = lift_assign(src_lines, name)
                    if blk:
                        seen.add(name); lifted_vars.append((name, blk))

    # build inserts
    mk = re.search(r'^def make_app\(', tgt, re.M)
    if not mk:
        print("ERROR: no 'def make_app(' in %s." % args.target); sys.exit(4)

    pre = ""
    if lifted_vars:
        pre += "# --- ported globals for %s ---\n" % ROUTE
        for _, b in lifted_vars:
            pre += b if b.endswith("\n") else b + "\n"
        pre += "\n"
    for _, b in lifted_defs:
        pre += b.rstrip("\n") + "\n\n\n"

    new_tgt = tgt[:mk.start()] + pre + tgt[mk.start():]

    # route before make_app's return app
    mk2 = re.search(r'^def make_app\(', new_tgt, re.M)
    ret = re.search(r'\n([ \t]*)return app\b', new_tgt[mk2.start():])
    if not ret:
        print("ERROR: could not find 'return app' in make_app."); sys.exit(4)
    abs_ret = mk2.start() + ret.start() + 1
    block = (route_text if route_text.endswith("\n") else route_text + "\n") + "\n"
    new_tgt = new_tgt[:abs_ret] + block + new_tgt[abs_ret:]

    try:
        compile(new_tgt, tgt_path, "exec")
    except SyntaxError as e:
        print("ERROR: patched target failed to compile: %s" % e)
        print("       No file written."); sys.exit(5)

    watch = ["release", "RELEASE"]
    used = [w for w in watch if re.search(r'\b' + w + r'\b', route_text)]

    print("Source route : %s  ->  %s" % (args.source, args.target))
    print("Route        : %s" % ROUTE)
    print("Defs lifted  : %s" % (", ".join(n for n, _ in lifted_defs) if lifted_defs else "(none)"))
    print("Vars lifted  : %s" % (", ".join(n for n, _ in lifted_vars) if lifted_vars else "(none)"))
    print("Compile      : OK")
    if used:
        print("NOTE: route references %s (not a make_app var). Tell me and I will thread it." % ", ".join(used))
    print("")
    print("--- %s route being injected (verbatim) ---" % ROUTE)
    for ln in route_block:
        sys.stdout.write("    " + ln)
    if not route_block[-1].endswith("\n"):
        print("")

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
