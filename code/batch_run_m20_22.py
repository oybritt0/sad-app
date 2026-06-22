#!/usr/bin/env python
"""
batch_run_m20_22.py — run M20 (jobs/LODES), M21 (transit LOS), and
M22 (environment) across the districts that don't yet have this data.

WHY THIS EXISTS
    M20, M21, and M22 are NOT registered in batch_run_pipeline.py's module
    catalogue (it stops at M16 / M6d v4), so `batch_run_pipeline.py --modules
    M20` silently runs nothing. This standalone runner mirrors that runner's
    idempotency model: it discovers every SAD under --data-dir, checks each
    module's output marker, and runs ONLY the (district, module) pairs whose
    marker is missing — i.e. exactly "the newly added districts I haven't
    added this data for yet". Re-running it is safe; finished work is skipped.

THE THREE MODULES
    M20  module_20_jobs_lodes.py      LEHD LODES workplace jobs (daytime pop).
         marker  derived/jobs/jobs_summary.json
         US ONLY — LODES has no Canadian coverage; Canadian SADs are skipped.
         Needs internet (Census LODES + TIGER) and manifest.json (M1).
    M21  module_21_transit_los.py     GTFS/GBFS transit level-of-service.
         marker  derived/transit_los/transit_los_summary.json
         Run with --discover so it auto-finds feeds whose bbox overlaps the
         SAD (no per-agency URLs needed). Needs internet + manifest.json.
    M22  module_22_environment.py     Landsat heat island + NDVI + impervious.
         marker  derived/environment/environment_summary.json
         Works everywhere (impervious is US-only but degrades gracefully).
         Needs internet (Microsoft Planetary Computer) + manifest.json.

USAGE  (run from the code\ directory, point --data-dir at data\)
    # Fill the gap: every district missing any of the three
    python batch_run_m20_22.py --data-dir ..\\data

    # One district first to validate (recommended), e.g. Philadelphia
    python batch_run_m20_22.py --data-dir ..\\data ^
        --sads 41_South-Philadelphia-Sports-Complex_Philadelphia-PA

    # Only one or two of the modules
    python batch_run_m20_22.py --data-dir ..\\data --modules M22,M20

    # Add the LODES historical series to M20
    python batch_run_m20_22.py --data-dir ..\\data --timeseries

    # Re-run even where outputs already exist
    python batch_run_m20_22.py --data-dir ..\\data --force

    # See what WOULD run without running anything
    python batch_run_m20_22.py --data-dir ..\\data --dry-run

OUTPUTS
    <data_dir>/_batch_runs/<timestamp>/
        m20_22_log.txt        full stdout for every module run
        m20_22_summary.csv     one row per (sad_id, module) attempt
"""
from __future__ import annotations
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ─── Module catalogue ────────────────────────────────────────────────────────
# args use {source} / {derived} placeholders, substituted per district.
# marker is relative to derived/ and signals "already done".
# us_only modules are skipped for Canadian SADs.

MODULES = [
    {
        'name':    'M20',
        'script':  'module_20_jobs_lodes.py',
        'args':    ['--derived', '{derived}', '--source', '{source}'],
        'marker':  'jobs/jobs_summary.json',
        'us_only': True,
        'timeseries': True,   # gets --timeseries appended when --timeseries is set
    },
    {
        'name':    'M21',
        'script':  'module_21_transit_los.py',
        'args':    ['--derived', '{derived}', '--source', '{source}',
                    '--discover'],
        'marker':  'transit_los/transit_los_summary.json',
        'us_only': False,
        'timeseries': False,
    },
    {
        'name':    'M22',
        'script':  'module_22_environment.py',
        'args':    ['--derived', '{derived}', '--source', '{source}'],
        'marker':  'environment/environment_summary.json',
        'us_only': False,
        'timeseries': False,
    },
]


# ─── SAD / country detection (matches batch_run_pipeline.py) ──────────────────

CANADIAN_PROVINCES = {
    'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'
}


def is_canadian_sad(sad_id: str) -> bool:
    """SAD ids end with -<region code>; detect Canadian by province code."""
    last = sad_id.rsplit('-', 1)
    return len(last) == 2 and last[-1] in CANADIAN_PROVINCES


def list_sads(data_dir: Path) -> list[str]:
    """Discover all SADs by looking for <id>/source/ subdirs."""
    if not data_dir.exists():
        return []
    return sorted(c.name for c in data_dir.iterdir()
                  if c.is_dir() and (c / 'source').is_dir())


def get_paths(data_dir: Path, sad_id: str) -> dict[str, Path]:
    root = data_dir / sad_id
    return {'root': root, 'source': root / 'source', 'derived': root / 'derived'}


def render_args(template: list[str], ctx: dict) -> list[str]:
    return [tok.format(**ctx) for tok in template]


# ─── Subprocess execution ────────────────────────────────────────────────────

def run_subprocess(cmd: list[str], cwd: Path, log_fh) -> tuple[int, str]:
    """Run a subprocess, stream stdout to console + log, return
    (returncode, last_60_lines)."""
    log_fh.write(f"\n$ {' '.join(str(c) for c in cmd)}\n")
    log_fh.flush()
    print(f"    $ {Path(cmd[1]).name} ...", flush=True)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=os.environ.copy())
    except FileNotFoundError as e:
        msg = f"FAILED to launch: {e}"
        log_fh.write(msg + "\n")
        return -1, msg

    tail: list[str] = []
    if proc.stdout is not None:
        for line in proc.stdout:
            log_fh.write(line)
            log_fh.flush()
            tail.append(line)
            if len(tail) > 60:
                tail.pop(0)
    return proc.wait(), ''.join(tail).strip()


# ─── Planning: what needs to run ──────────────────────────────────────────────

def plan_work(data_dir: Path, sads: list[str], modules: list[dict],
              force: bool) -> tuple[list[tuple[str, dict]], list[dict]]:
    """Return (todo, skipped). todo is a list of (sad_id, module) pairs that
    need to run; skipped is summary rows for everything pre-skipped."""
    todo: list[tuple[str, dict]] = []
    skipped: list[dict] = []
    for sad_id in sads:
        paths = get_paths(data_dir, sad_id)
        manifest = paths['derived'] / 'manifest.json'
        manifest_ok = manifest.exists()
        for mod in modules:
            row = {'sad_id': sad_id, 'module': mod['name']}
            if mod['us_only'] and is_canadian_sad(sad_id):
                skipped.append({**row, 'status': 'skip-canada (LODES US-only)',
                                'duration_s': 0, 'error': ''})
                continue
            if not manifest_ok:
                skipped.append({**row, 'status': 'skip-no-manifest (run M1)',
                                'duration_s': 0, 'error': ''})
                continue
            marker = paths['derived'] / mod['marker']
            if marker.exists() and not force:
                skipped.append({**row, 'status': 'skip-already-done',
                                'duration_s': 0, 'error': ''})
                continue
            todo.append((sad_id, mod))
    return todo, skipped


# ─── Main ──────────────────────────────────────────────────────────────────--

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True,
                    help='Root data directory containing <sad_id>/source/')
    ap.add_argument('--code-dir', type=Path, default=None,
                    help="Code directory (defaults to this script's own dir)")
    ap.add_argument('--modules', type=str, default='',
                    help='Comma-separated subset of M20,M21,M22 (default: all)')
    ap.add_argument('--sads', type=str, default='',
                    help='Comma-separated SAD ids (default: all discovered)')
    ap.add_argument('--timeseries', action='store_true',
                    help='Append --timeseries to M20 (LODES history)')
    ap.add_argument('--force', action='store_true',
                    help='Re-run even where output markers already exist')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print the plan and exit without running anything')
    args = ap.parse_args()

    data_dir: Path = args.data_dir.resolve()
    code_dir: Path = (args.code_dir or Path(__file__).parent).resolve()
    if not data_dir.exists():
        sys.exit(f"data-dir not found: {data_dir}")
    if not code_dir.exists():
        sys.exit(f"code-dir not found: {code_dir}")

    # Which modules
    if args.modules:
        wanted = {s.strip().upper() for s in args.modules.split(',') if s.strip()}
        modules = [m for m in MODULES if m['name'] in wanted]
        bad = wanted - {m['name'] for m in MODULES}
        if bad:
            print(f"WARNING: unknown module(s): {', '.join(sorted(bad))}")
    else:
        modules = list(MODULES)
    if not modules:
        sys.exit("No modules selected.")

    # Which districts
    all_sads = list_sads(data_dir)
    if args.sads:
        wanted = [s.strip() for s in args.sads.split(',') if s.strip()]
        sads = [s for s in wanted if s in all_sads]
        bad = [s for s in wanted if s not in all_sads]
        if bad:
            print(f"WARNING: not found under data-dir: {', '.join(bad)}")
    else:
        sads = all_sads
    if not sads:
        sys.exit("No SADs to process.")

    # Append --timeseries to M20 if requested
    if args.timeseries:
        for m in modules:
            if m['name'] == 'M20':
                m = m  # explicit; handled at command build below
    todo, pre_skipped = plan_work(data_dir, sads, modules, args.force)

    print(f"data-dir:  {data_dir}")
    print(f"code-dir:  {code_dir}")
    print(f"districts: {len(sads)}  |  modules: "
          f"{', '.join(m['name'] for m in modules)}")
    print(f"to run:    {len(todo)} (district, module) pair(s)")
    print(f"skipping:  {len(pre_skipped)} (already done / no manifest / Canada)")
    print()

    # Show the to-do list grouped by district so the gap is legible
    by_sad: dict[str, list[str]] = {}
    for sad_id, mod in todo:
        by_sad.setdefault(sad_id, []).append(mod['name'])
    if by_sad:
        print("districts with missing data:")
        for sad_id in sorted(by_sad):
            print(f"  {sad_id:<55} -> {', '.join(by_sad[sad_id])}")
        print()
    else:
        print("Nothing to do — every selected (district, module) is present.\n")

    if args.dry_run:
        print("(dry-run: nothing executed)")
        return 0
    if not todo:
        return 0

    # Set up logging
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = data_dir / '_batch_runs' / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / 'm20_22_log.txt'
    summary_path = run_dir / 'm20_22_summary.csv'

    summary_rows: list[dict] = list(pre_skipped)
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(f"M20-22 run started {datetime.now().isoformat()}\n")
        log_fh.write(f"data-dir: {data_dir}\n")
        log_fh.write(f"timeseries: {args.timeseries}  force: {args.force}\n\n")

        for i, (sad_id, mod) in enumerate(todo, 1):
            paths = get_paths(data_dir, sad_id)
            ctx = {'source': str(paths['source']),
                   'derived': str(paths['derived'])}
            cmd = [sys.executable, mod['script']] + render_args(mod['args'], ctx)
            if args.timeseries and mod.get('timeseries'):
                cmd.append('--timeseries')

            print(f"[{i}/{len(todo)}] {sad_id}  {mod['name']}", flush=True)
            ts = time.time()
            rc, tail = run_subprocess(cmd, code_dir, log_fh)
            dt = time.time() - ts
            marker = paths['derived'] / mod['marker']

            if rc == 0 and marker.exists():
                status, err = 'ok', ''
                print(f"    ok ({dt:.0f}s)", flush=True)
            elif rc == 0:
                status = 'ran-no-output'
                err = (tail.splitlines()[-1] if tail else 'marker not produced')
                print(f"    ran but no marker ({dt:.0f}s) — {err[:80]}", flush=True)
            else:
                status = 'fail'
                err = (tail.splitlines()[-1] if tail else f'returncode {rc}')
                print(f"    FAIL ({dt:.0f}s) — {err[:80]}", flush=True)
            summary_rows.append({'sad_id': sad_id, 'module': mod['name'],
                                 'status': status, 'duration_s': round(dt, 1),
                                 'error': err[:300]})

    # Write + print summary
    with open(summary_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=['sad_id', 'module', 'status',
                                           'duration_s', 'error'])
        w.writeheader()
        w.writerows(summary_rows)

    from collections import Counter
    counts = Counter(r['status'] for r in summary_rows)
    print("\n══ summary ════════════════════════════════════════════")
    print(f"  total attempts: {len(summary_rows)}")
    for status, n in counts.most_common():
        print(f"  {status:<28}{n}")
    print(f"\nTotal time: {(time.time()-t0)/60:.1f} min")
    print(f"Summary CSV: {summary_path}")
    print(f"Full log:    {log_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
