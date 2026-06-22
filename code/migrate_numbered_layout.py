#!/usr/bin/env python3
"""
migrate_numbered_layout.py

One-time converter from the numbered QGIS-export layout to the v2 pipeline
layout.

The numbered layout (one folder per district, named NN_Name_City-ST):
    data/<NN_Name_City-ST>/01_GeoJSONs/01_Buildings/<anything>.geojson
                                      /02_SAD-Boundary/<anything>.geojson
                                      /03_Canvas-Extents/<anything>.geojson
                                      /04_Parking/<anything>.geojson   (optional)
                                      /05_Highways/<anything>.geojson  (optional)
                                      /06_Parks/<anything>.geojson      (optional)

The pipeline needs:
    data/<sad_id>/source/buildings.geojson
                        /sad_boundary.geojson
                        /image_extent.geojson
                        /parking.geojson    (optional, copied as-is)
                        /highways.geojson   (optional, copied as-is)
                        /parks.geojson      (optional, copied as-is)

The filenames inside each numbered folder are inconsistent across districts
(Buildings.geojson, Building.geojson, Builldings.geojson, 02_Green_Bay_
Buildings.geojson ...), but the FOLDER names are stable, so this script
finds each layer by its folder and takes the .geojson inside.

It does NOT guess when a folder is ambiguous or empty. It runs in two modes:

  --dry-run  (default)
      Scan every district folder, match it to the master SAD.csv (for the
      anchor venue), and classify each district:
        READY        - exactly one geojson each for buildings/boundary/extent
        NEEDS_CHOICE - more than one candidate in a required folder; you pick
        BLOCKED      - a required layer is missing (or present only as a
                       shapefile, which the pipeline cannot read)
      Writes migration_plan.csv and prints a summary. Changes nothing.

  --commit
      Read the reviewed migration_plan.csv and, for every row that is READY,
      run setup_sad_source.py to build data/<sad_id>/source/, then copy
      parking/highways/parks alongside. Skips BLOCKED rows and unresolved
      NEEDS_CHOICE rows.

      Typology is NOT set here. setup_sad_source.py records it as
      "unspecified"; set the real classification later by re-running the
      Setup tab (or setup_sad_source.py) per district once you have it.

WORKFLOW
    1. python migrate_numbered_layout.py --data-root "...\\Detroit_Test\\data" \\
                                         --master-csv "...\\SAD.csv"
    2. open migration_plan.csv and:
         - for each NEEDS_CHOICE row, edit the buildings/boundary/extent cell
           to the correct file, then change its `status` to READY
         - BLOCKED rows: fix the folder in QGIS (usually: export a real
           .geojson boundary), then re-run --dry-run; or leave them to skip
    3. python migrate_numbered_layout.py --data-root "...\\Detroit_Test\\data" \\
                                         --commit

Stray non-district folders in data/ (_comparisons, an empty derived/ or
source/, etc.) have no 01_GeoJSONs/ inside and are ignored automatically.
"""
from __future__ import annotations
import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path


GEOJSON_ROOT = '01_GeoJSONs'
LAYER_FOLDERS = {
    'buildings': '01_Buildings',
    'boundary':  '02_SAD-Boundary',
    'extent':    '03_Canvas-Extents',
    'parking':   '04_Parking',
    'highways':  '05_Highways',
    'parks':     '06_Parks',
}
REQUIRED_LAYERS = ('buildings', 'boundary', 'extent')
OPTIONAL_LAYERS = ('parking', 'highways', 'parks')
# setup_sad_source.py writes these canonical names; we match them when
# copying the optional layers it does not itself ingest.
CANONICAL_OPTIONAL = {
    'parking':  'parking.geojson',
    'highways': 'highways.geojson',
    'parks':    'parks.geojson',
}

# Typology is deliberately NOT a column here. setup_sad_source.py records it
# as "unspecified" at conversion time; the real classification is set later
# per district via the Setup tab. Keeping it out of the plan avoids implying
# it must be filled before --commit.
PLAN_COLUMNS = [
    'folder', 'sad_id', 'status', 'sad_name', 'anchor_venue',
    'buildings', 'boundary', 'extent', 'parking', 'highways', 'parks', 'notes',
]


# ─── Master CSV ───────────────────────────────────────────────────────

def _norm_name(s: str) -> str:
    """Normalize a district name for folder<->CSV matching."""
    return s.strip().lower().replace(' ', '')


def read_master_csv(path: Path) -> dict:
    """Read SAD.csv into {normalized_name: {name, anchor_venue}}.

    Tolerates a utf-8-sig BOM, a stray non-CSV first line (the export
    sometimes writes a bare 'Table' line), and apostrophe-prefixed numeric
    cells (an Excel artifact) which we simply do not use.
    """
    if not path.exists():
        return {}
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    # Drop a stray leading line that is not the CSV header.
    if lines and ',' not in lines[0]:
        lines = lines[1:]
    out = {}
    for row in csv.DictReader(lines):
        name = (row.get('name') or '').strip()
        if name:
            out[_norm_name(name)] = {
                'name': name,
                'anchor_venue': (row.get('anchor_venue') or '').strip(),
            }
    return out


# ─── Folder / layer helpers ───────────────────────────────────────────

def strip_prefix(folder_name: str) -> str:
    """'07_LA-Live_Los-Angeles-CA' -> 'LA-Live_Los-Angeles-CA'.

    Strips only a leading <digits>_ ; the rest of the name is untouched."""
    head, _, tail = folder_name.partition('_')
    return tail if (tail and head.isdigit()) else folder_name


def display_name(name_after_prefix: str) -> str:
    """'Sportsmans-Park_Glendale-AZ' -> 'Sportsmans Park, Glendale-AZ'.

    A readable default for --sad-name; editable in the plan CSV."""
    segs = name_after_prefix.split('_')
    place = segs[0].replace('-', ' ').strip()
    if len(segs) > 1:
        return f"{place}, {'_'.join(segs[1:])}"
    return place


def find_geojsons(folder: Path) -> list[Path]:
    """Every *.geojson directly inside a layer folder, sorted by name."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() == '.geojson')


def only_shapefile(folder: Path) -> bool:
    """True if the folder has a .shp but no .geojson — a useful diagnostic
    for BLOCKED districts (the pipeline reads GeoJSON, not shapefiles)."""
    if not folder.is_dir():
        return False
    exts = {p.suffix.lower() for p in folder.iterdir() if p.is_file()}
    return '.shp' in exts and '.geojson' not in exts


# ─── Scan one district ────────────────────────────────────────────────

def scan_district(district_dir: Path, master: dict) -> dict:
    """Classify one district folder and return a migration-plan row."""
    folder = district_dir.name
    geo_root = district_dir / GEOJSON_ROOT

    name_after_prefix = strip_prefix(folder)
    meta = master.get(_norm_name(name_after_prefix), {})

    row = {c: '' for c in PLAN_COLUMNS}
    row['folder'] = folder
    row['sad_id'] = folder                       # the folder name IS the id
    row['sad_name'] = display_name(name_after_prefix)
    row['anchor_venue'] = meta.get('anchor_venue', '')

    if not geo_root.is_dir():
        row['status'] = 'BLOCKED'
        row['notes'] = f"no {GEOJSON_ROOT}/ folder — not a numbered-layout district"
        return row

    notes = []
    if not row['anchor_venue']:
        notes.append("no anchor_venue match in master CSV — fill it in")

    blocked, needs_choice = [], []

    # Required layers — exactly one geojson each, or it is not READY.
    for layer in REQUIRED_LAYERS:
        layer_dir = geo_root / LAYER_FOLDERS[layer]
        cands = find_geojsons(layer_dir)
        if len(cands) == 1:
            row[layer] = cands[0].relative_to(district_dir).as_posix()
        elif len(cands) == 0:
            blocked.append(layer)
            if only_shapefile(layer_dir):
                notes.append(f"{layer}: only a shapefile in "
                             f"{LAYER_FOLDERS[layer]}/, no .geojson — "
                             f"re-export as GeoJSON from QGIS")
            else:
                notes.append(f"{layer}: no .geojson in "
                             f"{LAYER_FOLDERS[layer]}/")
        else:
            needs_choice.append(layer)
            # Put the first candidate in the cell as a starting point; the
            # user edits it to the correct file.
            row[layer] = cands[0].relative_to(district_dir).as_posix()
            names = ', '.join(c.name for c in cands)
            notes.append(f"{layer}: {len(cands)} candidates ({names}) — "
                         f"edit this cell to the correct file")

    # Optional layers — take the single geojson if there is exactly one.
    for layer in OPTIONAL_LAYERS:
        cands = find_geojsons(geo_root / LAYER_FOLDERS[layer])
        if len(cands) == 1:
            row[layer] = cands[0].relative_to(district_dir).as_posix()
        elif len(cands) > 1:
            row[layer] = cands[0].relative_to(district_dir).as_posix()
            notes.append(f"{layer}: {len(cands)} candidates — using "
                         f"{cands[0].name}, edit if wrong")
        # zero candidates -> leave blank; these layers are optional

    if blocked:
        row['status'] = 'BLOCKED'
    elif needs_choice:
        row['status'] = 'NEEDS_CHOICE'
    else:
        row['status'] = 'READY'
    row['notes'] = '; '.join(notes)
    return row


# ─── Dry run ──────────────────────────────────────────────────────────

def cmd_dry_run(data_root: Path, master_csv: Path, plan_path: Path,
                force: bool) -> None:
    if plan_path.exists() and not force:
        sys.exit(
            f"{plan_path} already exists.\n"
            f"Re-running the dry run would overwrite it and discard any "
            f"edits you have made (typologies, resolved choices).\n"
            f"Pass --force to overwrite, or move the existing plan aside "
            f"first."
        )

    master = read_master_csv(master_csv)
    if master:
        print(f"Master CSV: {master_csv}  ({len(master)} districts)")
    else:
        print(f"WARNING: master CSV not found or empty ({master_csv}).")
        print("         anchor_venue will be blank — fill it in the plan.")

    districts = sorted(
        d for d in data_root.iterdir()
        if d.is_dir() and (d / GEOJSON_ROOT).is_dir()
    )
    if not districts:
        sys.exit(f"No numbered-layout districts (folders containing "
                 f"{GEOJSON_ROOT}/) found under {data_root}")

    rows = [scan_district(d, master) for d in districts]

    with plan_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=PLAN_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    by_status: dict[str, list] = {}
    for r in rows:
        by_status.setdefault(r['status'], []).append(r)

    print(f"\nScanned {len(rows)} districts under {data_root}\n")
    for status in ('READY', 'NEEDS_CHOICE', 'BLOCKED'):
        print(f"  {status:13s} {len(by_status.get(status, []))}")

    for status in ('NEEDS_CHOICE', 'BLOCKED'):
        group = by_status.get(status, [])
        if group:
            print()
            for r in group:
                print(f"  [{status}] {r['folder']}")
                print(f"            {r['notes']}")

    print(f"\nPlan written to: {plan_path}")
    print("Next:")
    print("  1. Open the plan CSV.")
    print("  2. For NEEDS_CHOICE rows, edit the buildings/boundary/extent")
    print("     cell to the correct file, then set `status` to READY.")
    print("  3. BLOCKED rows: fix the folder in QGIS, or leave them to skip.")
    print("  4. Re-run this script with --commit.")
    print("  (Typology is set later, per district, via the Setup tab — it")
    print("   is recorded as 'unspecified' until then.)")


# ─── Commit ───────────────────────────────────────────────────────────

def cmd_commit(data_root: Path, plan_path: Path, code_dir: Path,
               setup_script: Path) -> None:
    if not plan_path.exists():
        sys.exit(f"Plan file not found: {plan_path}. Run the dry run first.")
    if not setup_script.exists():
        sys.exit(f"setup_sad_source.py not found at {setup_script}. "
                 f"Pass --setup-script with its location.")

    rows = list(csv.DictReader(plan_path.open(encoding='utf-8-sig')))
    done = skipped = failed = 0

    for r in rows:
        sad_id = (r.get('sad_id') or '').strip()
        folder = (r.get('folder') or sad_id).strip()
        status = (r.get('status') or '').strip().upper()

        if status == 'BLOCKED':
            print(f"SKIP  {folder}: BLOCKED — {r.get('notes', '')}")
            skipped += 1
            continue
        if status == 'NEEDS_CHOICE':
            print(f"SKIP  {folder}: still NEEDS_CHOICE — resolve it in the "
                  f"plan CSV and set status to READY")
            skipped += 1
            continue
        if status != 'READY':
            print(f"SKIP  {folder}: unrecognized status {status!r}")
            skipped += 1
            continue

        district_dir = data_root / folder
        paths = {layer: district_dir / (r.get(layer) or '')
                 for layer in REQUIRED_LAYERS}
        missing = [layer for layer, p in paths.items()
                   if not r.get(layer) or not p.exists()]
        if missing:
            print(f"SKIP  {folder}: required file(s) not found: "
                  f"{', '.join(missing)}")
            skipped += 1
            continue

        out_dir = data_root / sad_id / 'source'
        out_dir.mkdir(parents=True, exist_ok=True)
        # --typology is intentionally omitted: setup_sad_source.py records
        # it as "unspecified", and the real classification is set later,
        # per district, via the Setup tab.
        cmd = [
            sys.executable, str(setup_script),
            '--buildings', str(paths['buildings']),
            '--boundary',  str(paths['boundary']),
            '--extent',    str(paths['extent']),
            '--output',    str(out_dir),
            '--sad-id',    sad_id,
            '--sad-name',  (r.get('sad_name') or sad_id).strip(),
            '--anchor-venue', (r.get('anchor_venue') or 'unknown').strip(),
        ]
        print(f"RUN   {folder}: setup_sad_source.py")
        result = subprocess.run(cmd, cwd=str(code_dir))
        if result.returncode != 0:
            print(f"      FAILED (exit {result.returncode}) — "
                  f"parking/highways/parks not copied")
            failed += 1
            continue

        # setup_sad_source.py does not ingest parking/highways/parks; copy
        # them straight into source/ under the canonical names the downstream
        # modules look for.
        for layer, canonical in CANONICAL_OPTIONAL.items():
            rel = (r.get(layer) or '').strip()
            if not rel:
                continue
            src = district_dir / rel
            if src.exists():
                shutil.copyfile(src, out_dir / canonical)
                print(f"      copied {layer} -> source/{canonical}")
            else:
                print(f"      WARN: {layer} listed but not found: {src}")
        done += 1

    print(f"\nDone. {done} set up, {skipped} skipped, {failed} failed.")
    if skipped or failed:
        print("Re-run --commit after fixing the plan CSV / blocked folders "
              "to pick up the rest.")


# ─── Entry point ──────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('--data-root', type=Path, required=True,
                    help='The data/ folder containing the NN_Name_City-ST '
                         'district folders.')
    ap.add_argument('--master-csv', type=Path, default=None,
                    help='SAD.csv master list (supplies anchor_venue). '
                         'Default: looks for SAD.csv in the cwd, the data '
                         'root, its parent, or next to this script.')
    ap.add_argument('--plan', type=Path, default=None,
                    help='Path to migration_plan.csv. '
                         'Default: migration_plan.csv in the data root.')
    ap.add_argument('--commit', action='store_true',
                    help='Execute the reviewed plan. Default is a dry run '
                         'that only writes the plan and changes nothing.')
    ap.add_argument('--force', action='store_true',
                    help='Allow the dry run to overwrite an existing plan '
                         'CSV (discards your edits).')
    ap.add_argument('--setup-script', type=Path, default=None,
                    help='Path to setup_sad_source.py. '
                         'Default: alongside this script.')
    args = ap.parse_args()

    data_root = args.data_root.resolve()
    if not data_root.is_dir():
        sys.exit(f"Data root not found: {data_root}")

    code_dir = Path(__file__).parent.resolve()
    setup_script = (args.setup_script or code_dir / 'setup_sad_source.py').resolve()
    plan_path = (args.plan or data_root / 'migration_plan.csv').resolve()

    if args.master_csv:
        master_csv = args.master_csv.resolve()
    else:
        master_csv = data_root / 'SAD.csv'  # fallback if nothing is found
        for cand in (Path.cwd() / 'SAD.csv', data_root / 'SAD.csv',
                     data_root.parent / 'SAD.csv', code_dir / 'SAD.csv'):
            if cand.exists():
                master_csv = cand
                break

    if args.commit:
        cmd_commit(data_root, plan_path, code_dir, setup_script)
    else:
        cmd_dry_run(data_root, master_csv, plan_path, args.force)


if __name__ == '__main__':
    main()
