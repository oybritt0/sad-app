r"""
migrate_to_v2_layout.py

Migrates SAD data from the v1 layout (data/source/per_sad/<sad>/, data/derived/per_sad/<sad>/)
to the v2 layout (data/<sad>/{source,derived,reference}/).

USAGE
  # Dry-run (default - shows what would happen without doing it)
  python migrate_to_v2_layout.py --data-root ..\data --sad-id district_detroit
  
  # Actually do it
  python migrate_to_v2_layout.py --data-root ..\data --sad-id district_detroit --commit
  
  # Migrate all SADs found under the old layout
  python migrate_to_v2_layout.py --data-root ..\data --all --commit

DESIGN NOTES
  - Operation is MOVE, not copy. Fast (rename within same filesystem) and reversible.
  - If new layout already exists, the script refuses to overwrite without --force.
  - Reference files (rossetti_*.json at the data-root level) get moved into the SAD's
    reference folder if their name matches the SAD ID.
  - The empty old folders (data/source/per_sad/, data/derived/per_sad/) are left in
    place after migration; safe to delete manually once you've verified the move.

NEW LAYOUT
  data/
    <sad_id>/
      source/        - input GeoJSONs (buildings, boundary, extent, places, census)
      derived/       - all module outputs (JSON, CSV, GPKG, PNG, SVG, NPY)
      reference/     - external benchmarks (rossetti report values, etc.)
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path


def find_sads_in_old_layout(data_root: Path) -> list[str]:
    """Return list of SAD IDs found under the old per_sad/ structure."""
    sads: set[str] = set()
    for parent in ('source', 'derived'):
        per_sad = data_root / parent / 'per_sad'
        if per_sad.exists():
            for item in per_sad.iterdir():
                if item.is_dir():
                    sads.add(item.name)
    return sorted(sads)


def plan_migration(data_root: Path, sad_id: str) -> dict:
    """Compute the migration plan for one SAD without executing anything."""
    old_source = data_root / 'source' / 'per_sad' / sad_id
    old_derived = data_root / 'derived' / 'per_sad' / sad_id
    new_root = data_root / sad_id
    new_source = new_root / 'source'
    new_derived = new_root / 'derived'
    new_reference = new_root / 'reference'
    
    # Look for any reference JSON files at the data root that match this SAD
    ref_candidates = list(data_root.parent.glob(f'rossetti_{sad_id}.json'))
    ref_candidates += list(data_root.parent.glob(f'reference_{sad_id}.json'))
    
    plan = {
        'sad_id': sad_id,
        'new_root': new_root,
        'actions': [],
        'warnings': [],
    }
    
    if new_root.exists() and any(new_root.iterdir()):
        plan['warnings'].append(
            f"new layout already exists at {new_root} (non-empty)"
        )
    
    if old_source.exists():
        plan['actions'].append(('move', old_source, new_source))
    else:
        plan['warnings'].append(f"no old source dir at {old_source}")
    
    if old_derived.exists():
        plan['actions'].append(('move', old_derived, new_derived))
    else:
        plan['warnings'].append(f"no old derived dir at {old_derived}")
    
    plan['actions'].append(('mkdir', None, new_reference))
    
    for ref in ref_candidates:
        plan['actions'].append(('move', ref, new_reference / ref.name))
    
    return plan


def execute_plan(plan: dict, force: bool = False) -> None:
    """Run the migration plan."""
    if plan['warnings'] and not force:
        for w in plan['warnings']:
            print(f"  WARN: {w}")
        if any('already exists' in w for w in plan['warnings']):
            sys.exit("refusing to overwrite. pass --force to override, "
                     "or delete the new-layout folder first.")
    
    plan['new_root'].mkdir(parents=True, exist_ok=True)
    
    for op, src, dst in plan['actions']:
        if op == 'move':
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                if force:
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                else:
                    print(f"  SKIP (destination exists): {dst}")
                    continue
            shutil.move(str(src), str(dst))
            print(f"  moved   {src.name:35s} -> {dst}")
        elif op == 'mkdir':
            dst.mkdir(parents=True, exist_ok=True)
            print(f"  created {dst}")


def show_plan(plan: dict) -> None:
    """Print the plan without executing it."""
    print(f"\nSAD: {plan['sad_id']}")
    print(f"  new layout root: {plan['new_root']}")
    if plan['warnings']:
        print(f"  warnings:")
        for w in plan['warnings']:
            print(f"    - {w}")
    if plan['actions']:
        print(f"  actions:")
        for op, src, dst in plan['actions']:
            if op == 'move':
                print(f"    move    {src.name:35s} -> {dst}")
            elif op == 'mkdir':
                print(f"    mkdir   {dst}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    parser.add_argument('--data-root', type=Path, required=True,
                        help='Path to data directory (e.g. ..\\data)')
    sad_grp = parser.add_mutually_exclusive_group(required=True)
    sad_grp.add_argument('--sad-id', type=str, default=None,
                         help='Single SAD ID to migrate')
    sad_grp.add_argument('--all', action='store_true',
                         help='Migrate all SADs found under the old layout')
    parser.add_argument('--commit', action='store_true',
                        help='Actually perform the migration. Without this, dry-run only.')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite if new layout already exists')
    args = parser.parse_args()
    
    data_root = args.data_root.resolve()
    if not data_root.exists():
        sys.exit(f"data root not found: {data_root}")
    
    sad_ids = find_sads_in_old_layout(data_root) if args.all else [args.sad_id]
    if not sad_ids:
        sys.exit(f"no SADs found under {data_root}/source/per_sad/ or "
                 f"{data_root}/derived/per_sad/")
    
    plans = [plan_migration(data_root, sid) for sid in sad_ids]
    
    print(f"data root: {data_root}")
    print(f"SADs to migrate: {len(plans)}")
    
    for plan in plans:
        show_plan(plan)
    
    if not args.commit:
        print(f"\n[DRY RUN] no files moved. Pass --commit to execute.")
        return
    
    print(f"\nExecuting migration...")
    for plan in plans:
        print(f"\n{plan['sad_id']}:")
        execute_plan(plan, force=args.force)
    
    print(f"\n[OK] migrated {len(plans)} SAD(s) to v2 layout")
    print(f"     verify the new structure at {data_root}/<sad_id>/")
    print(f"     once verified, you can manually delete the empty "
          f"{data_root}/source/per_sad/ and {data_root}/derived/per_sad/ folders")


if __name__ == '__main__':
    main()
