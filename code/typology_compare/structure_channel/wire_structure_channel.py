"""
wire_structure_channel.py  -  bolt /analyze_structures into sad_match_server.py

The structure channel registers exactly like program_match. This inserts the
register block into make_app, right after the program_match registration (or, if
that is not found, just before `return app`). Python does the text edit so there
is no PowerShell quoting to mangle.

SAFE BY DEFAULT:
  - dry-run unless --apply; prints where it will insert.
  - idempotent: if 'structure_match' is already in the file, it does nothing.
  - on --apply: backs up the original to sad_match_server_<stamp>.bak.py
    (new-named, never overwritten), writes the edit, then py_compiles the result
    and restores the backup if compilation fails.

USAGE (QGIS bundled interpreter):
  $bat = "C:\\Program Files\\QGIS 3.40.11\\bin\\python-qgis-ltr.bat"
  & $bat wire_structure_channel.py
  & $bat wire_structure_channel.py --apply
"""
from __future__ import annotations
import argparse
import datetime
import py_compile
import shutil
from pathlib import Path

DEFAULT_SERVER = Path(r'C:\Users\jmeyers\Desktop\sad-app\code\sad_match_server.py')

BLOCK = [
    '    try:',
    '        import structure_match',
    '        structure_match.register(app, data_dir)',
    '    except Exception as e:',
    '        print(f"  [warn] structure match not wired: {e}")',
    '',
]


def find_insert_index(lines):
    """After the program_match register statement if present, else before the
    first `return app`. Returns (index, anchor_description)."""
    prog_idx = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if 'structure_match' in s:
            return None, 'already wired'
        if 'program_match' in s and '.register(' in s:
            prog_idx = i
    if prog_idx is not None:
        # insert after the program try/except block: walk to the next line whose
        # indent returns to the make_app body level (4 spaces) and is blank or code
        j = prog_idx + 1
        while j < len(lines) and (lines[j].startswith('        ')
                                  or lines[j].strip() == ''
                                  or lines[j].lstrip().startswith('except')
                                  or lines[j].lstrip().startswith('print(')):
            j += 1
            if j < len(lines) and lines[j].strip().startswith('@app'):
                break
        return j, f'after program_match register (line {prog_idx + 1})'
    for i, ln in enumerate(lines):
        if ln.strip() == 'return app':
            return i, f'before `return app` (line {i + 1})'
    return None, 'no anchor found (no program register, no `return app`)'


def main():
    ap = argparse.ArgumentParser(description='Wire structure_match into sad_match_server.py')
    ap.add_argument('--server', type=Path, default=DEFAULT_SERVER)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    dry = not a.apply

    if not a.server.exists():
        raise SystemExit(f'[FATAL] server not found: {a.server}')
    text = a.server.read_text(encoding='utf-8')
    lines = text.splitlines()

    idx, anchor = find_insert_index(lines)
    print(f'server : {a.server}')
    print(f'mode   : {"DRY-RUN (no writes)" if dry else "APPLY"}')
    print(f'anchor : {anchor}')

    if anchor == 'already wired':
        print('\n[skip] structure_match is already wired into this server. Nothing to do.')
        return
    if idx is None:
        raise SystemExit('[FATAL] could not find an insertion point. Wire it by hand: '
                         'add the try/except block inside make_app before `return app`.')

    new_lines = lines[:idx] + BLOCK + lines[idx:]
    preview_start = max(0, idx - 3)
    preview_end = min(len(new_lines), idx + len(BLOCK) + 3)
    print('\n--- insertion preview ---')
    for n in range(preview_start, preview_end):
        mark = '+' if (idx <= n < idx + len(BLOCK)) else ' '
        print(f'  {mark} {new_lines[n]}')

    if dry:
        print('\n[DRY-RUN] re-run with --apply to write. Original untouched.')
        return

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    backup = a.server.with_name(f'{a.server.stem}_{stamp}.bak.py')
    if backup.exists():
        raise SystemExit(f'[FATAL] refusing to overwrite backup {backup}')
    shutil.copy2(a.server, backup)
    print(f'\n[backup] {backup}')

    a.server.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
    try:
        py_compile.compile(str(a.server), doraise=True)
        print(f'[WROTE]  {a.server}  (compiles clean)')
        print('Restart the server; boot should print "structure corpus: 37 districts with NSI shares".')
    except py_compile.PyCompileError as e:
        shutil.copy2(backup, a.server)
        print(f'[FATAL] edit did not compile, restored original from backup.\n{e}')


if __name__ == '__main__':
    main()
