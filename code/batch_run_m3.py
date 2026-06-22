#!/usr/bin/env python3
"""
batch_run_m3.py

Run module_03_rod_program_extractor.py on every district that has both:
    data/<sad_id>/source/                       (set up via setup_sad_source.py)
    data/<sad_id>/03_ROD-Search-Tool/ROD_Search/<something>.geojson
                                                (the ROD POI export for that SAD)

Two modes:

  --dry-run (default)
      Scan every district folder and report which would run vs be skipped.
      Writes no files. Use this first to see the plan.

  --run
      Actually invoke module_03_rod_program_extractor.py for every district
      with status READY. M3 writes:
          data/<sad>/source/rod_places.geojson
          data/<sad>/derived/per_sad/<sad>/program_summary.json
      M3's own stdout is streamed through, with a section header per district.

Skip reasons:
  NO_SOURCE   - data/<sad>/source/ does not exist (set up the district first).
  NO_ROD      - no .geojson found under
                data/<sad>/03_ROD-Search-Tool/ROD_Search/.
  MULTIPLE    - more than one .geojson in the ROD folder; resolve manually.

USAGE
    # See what would run
    python batch_run_m3.py --data-root "C:\\...\\Detroit_Test\\data"

    # Run for real (all READY districts)
    python batch_run_m3.py --data-root "C:\\...\\Detroit_Test\\data" --run

    # Run for one district only (useful as a first sanity check)
    python batch_run_m3.py --data-root "C:\\...\\Detroit_Test\\data" --run \\
        --districts 02_Sportsmans-Park_Glendale-AZ

Notes
    - The script is idempotent. Re-running it just overwrites
      rod_places.geojson and program_summary.json for each district. Failed
      districts can be re-run safely.
    - --keep-non-operational and --no-clip are passed through to M3 if given.
      Default behaviour (neither flag) is correct for the current unverified
      ROD data: M3 auto-skips its operational filter (with a loud warning)
      when is_operational is all-false, and clips POIs to the SAD canvas.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path


ROD_SUBFOLDER = Path('03_ROD-Search-Tool') / 'ROD_Search'
SOURCE_SUBFOLDER = 'source'
DISTRICT_MARKER = '01_GeoJSONs'  # present in every numbered-layout district


def find_geojsons(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() == '.geojson')


def scan_district(district_dir: Path) -> dict:
    """Classify one district for M3 readiness."""
    name = district_dir.name
    source_dir = district_dir / SOURCE_SUBFOLDER
    rod_dir = district_dir / ROD_SUBFOLDER

    if not source_dir.is_dir():
        return {'folder': name, 'status': 'NO_SOURCE', 'rod_file': None,
                'reason': f"no {SOURCE_SUBFOLDER}/ folder yet — "
                          f"run setup_sad_source.py first "
                          f"(or fix the blocked migration)"}

    rod_files = find_geojsons(rod_dir)
    if not rod_files:
        return {'folder': name, 'status': 'NO_ROD', 'rod_file': None,
                'reason': f"no .geojson in {ROD_SUBFOLDER.as_posix()}/"}
    if len(rod_files) > 1:
        names = ', '.join(f.name for f in rod_files)
        return {'folder': name, 'status': 'MULTIPLE', 'rod_file': rod_files[0],
                'reason': f"{len(rod_files)} .geojson candidates ({names}) — "
                          f"keep only the one you want"}

    return {'folder': name, 'status': 'READY', 'rod_file': rod_files[0],
            'reason': ''}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('--data-root', type=Path, required=True,
                    help='The data/ folder containing the district folders.')
    ap.add_argument('--run', action='store_true',
                    help='Actually invoke M3. Default is a dry run.')
    ap.add_argument('--districts', nargs='*', default=None, metavar='FOLDER',
                    help='Specific district folder names to process. '
                         'Default: every district under --data-root.')
    ap.add_argument('--keep-non-operational', action='store_true',
                    help='Pass --keep-non-operational to M3 (do not require '
                         'is_operational=True).')
    ap.add_argument('--no-clip', action='store_true',
                    help='Pass --no-clip to M3 (keep POIs outside the SAD '
                         'canvas).')
    ap.add_argument('--m3-script', type=Path, default=None,
                    help='Path to module_03_rod_program_extractor.py. '
                         'Default: alongside this script.')
    args = ap.parse_args()

    data_root = args.data_root.resolve()
    if not data_root.is_dir():
        sys.exit(f"Data root not found: {data_root}")

    code_dir = Path(__file__).parent.resolve()
    m3_script = (args.m3_script
                 or code_dir / 'module_03_rod_program_extractor.py').resolve()
    derived_root = data_root / 'derived' / 'per_sad'

    # A district folder is one containing 01_GeoJSONs/ — same heuristic as
    # the migrate script, which makes _comparisons/derived/source ignored.
    districts = sorted(d for d in data_root.iterdir()
                       if d.is_dir() and (d / DISTRICT_MARKER).is_dir())
    if args.districts:
        wanted = set(args.districts)
        districts = [d for d in districts if d.name in wanted]
        missing = wanted - {d.name for d in districts}
        if missing:
            print(f"WARNING: requested but not found under {data_root}: "
                  f"{', '.join(sorted(missing))}")
    if not districts:
        sys.exit("No district folders found.")

    rows = [scan_district(d) for d in districts]

    by_status: dict[str, list] = {}
    for r in rows:
        by_status.setdefault(r['status'], []).append(r)

    print(f"\nScanned {len(rows)} district folder(s) under {data_root}\n")
    for status in ('READY', 'NO_SOURCE', 'NO_ROD', 'MULTIPLE'):
        print(f"  {status:11s} {len(by_status.get(status, []))}")

    for status in ('NO_SOURCE', 'NO_ROD', 'MULTIPLE'):
        for r in by_status.get(status, []):
            print(f"\n  [{status}] {r['folder']}")
            print(f"            {r['reason']}")

    ready = by_status.get('READY', [])
    if not args.run:
        print(f"\nDry run only. Re-run with --run to invoke M3 on the "
              f"{len(ready)} READY district(s).")
        return

    if not m3_script.exists():
        sys.exit(f"\nM3 script not found at {m3_script}. "
                 f"Pass --m3-script if it lives elsewhere.")
    if not ready:
        print("\nNothing READY to run.")
        return

    print(f"\nRunning M3 on {len(ready)} READY district(s)...\n")
    done = failed = 0
    for r in ready:
        district_dir = data_root / r['folder']
        print(f"\n=== {r['folder']} ===")
        print(f"  places: {r['rod_file'].name}")
        derived_dir = derived_root / r['folder']
        derived_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable, str(m3_script),
            '--places-file', str(r['rod_file']),
            '--source',      str(district_dir / SOURCE_SUBFOLDER),
            '--derived',     str(derived_dir),
        ]
        if args.keep_non_operational:
            cmd.append('--keep-non-operational')
        if args.no_clip:
            cmd.append('--no-clip')

        result = subprocess.run(cmd, cwd=str(code_dir))
        if result.returncode == 0:
            done += 1
        else:
            failed += 1
            print(f"  FAILED (exit {result.returncode})")

    print(f"\nDone. {done} processed, {failed} failed.")
    if failed:
        print("Re-run with the same flags to retry failed districts — "
              "successful ones are overwritten cleanly.")


if __name__ == '__main__':
    main()
