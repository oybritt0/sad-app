r"""
sanitize_export_svg.py

Fix exported viewer SVGs that Illustrator opens in OUTLINE preview (GPU preview
fails) because some path has coordinates thousands of times outside the artboard.

Root cause seen in the wild: the sad_boundary "dim-outside" overlay draws a huge
outer rectangle at un-projected / sentinel coordinates (e.g. 12,180,310 with a
viewBox of 1990x1774). One such vertex blows up the art bounding box and forces
Illustrator out of GPU preview.

WHAT IT DOES
    Reads the SVG's viewBox to get the real artboard (W,H). Then, per <path>:
      - parses the d= coordinate pairs,
      - if ANY coordinate is wildly outside the artboard (beyond
        --pad x the artboard on any side), the path is either:
          * CLAMPED  (default): every coordinate clamped into
                       [-pad*W .. (1+pad)*W] x [-pad*H .. (1+pad)*H], or
          * DROPPED  (--drop-offenders): the offending <path> removed entirely
                       (use when the offender is a full-canvas dim overlay you
                       don't need in the print export).
    Other elements (circle/rect/image) are checked too; circles far outside are
    dropped (stray un-projected POIs).

    Writes <name>.svg in place after backing up to <name>.orig.svg (once), or to
    --out if given. Idempotent: a file already within bounds is reported clean.

USAGE
    python sanitize_export_svg.py path\to\export.svg
    python sanitize_export_svg.py export.svg --drop-offenders
    python sanitize_export_svg.py "Downloads\*.svg" --dry-run
"""
from __future__ import annotations
import argparse
import glob
import re
import sys
from pathlib import Path

NUM = re.compile(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?')


def _artboard(svg_text: str):
    m = re.search(r'viewBox="\s*([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s*"',
                  svg_text)
    if m:
        _, _, w, h = (float(x) for x in m.groups())
        return w, h
    # fall back to width/height attrs
    w = re.search(r'\bwidth="([\d.]+)"', svg_text)
    h = re.search(r'\bheight="([\d.]+)"', svg_text)
    if w and h:
        return float(w.group(1)), float(h.group(1))
    return None


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def sanitize(svg_text: str, pad: float, drop_offenders: bool):
    art = _artboard(svg_text)
    if art is None:
        return svg_text, {'error': 'no viewBox/width-height; cannot size artboard'}
    W, H = art
    xlo, xhi = -pad * W, (1 + pad) * W
    ylo, yhi = -pad * H, (1 + pad) * H
    # generous "wildly out" threshold for detection (10x the pad band)
    xout, yout = -10 * pad * W - W, 10 * pad * W + 2 * W
    yout_lo, yout_hi = -10 * pad * H - H, 10 * pad * H + 2 * H

    stats = {'paths_clamped': 0, 'paths_dropped': 0, 'circles_dropped': 0,
             'max_before': 0.0}

    # ---- paths ----
    def fix_path(mobj):
        whole = mobj.group(0)
        d = mobj.group(1)
        coords = [float(x) for x in NUM.findall(d)]
        if not coords:
            return whole
        mx = max(abs(c) for c in coords)
        stats['max_before'] = max(stats['max_before'], mx)
        # only act if something is wildly outside
        if mx <= max(xhi, yhi) * 3:
            return whole
        if drop_offenders:
            stats['paths_dropped'] += 1
            return ''  # remove the whole <path .../> or <path ...>...</path>
        # clamp every numeric token in d, alternating x,y
        out, idx = [], 0

        def repl(nm):
            nonlocal idx
            val = float(nm.group(0))
            val = _clamp(val, xlo, xhi) if idx % 2 == 0 else _clamp(val, ylo, yhi)
            idx += 1
            s = f'{val:.1f}'.rstrip('0').rstrip('.')
            return s
        new_d = NUM.sub(repl, d)
        stats['paths_clamped'] += 1
        return whole.replace(d, new_d, 1)

    # self-closing <path .../>
    svg_text = re.sub(r'<path\b[^>]*\bd="([^"]*)"[^>]*/>', fix_path, svg_text)
    # paired <path ...>...</path> (rare for plain paths, but safe)
    svg_text = re.sub(r'<path\b[^>]*\bd="([^"]*)"[^>]*>.*?</path>', fix_path,
                      svg_text, flags=re.S)

    # ---- circles (drop stray far-outside dots) ----
    def fix_circle(mobj):
        whole = mobj.group(0)
        cx = re.search(r'\bcx="(-?[\d.eE+-]+)"', whole)
        cy = re.search(r'\bcy="(-?[\d.eE+-]+)"', whole)
        if not (cx and cy):
            return whole
        x, y = float(cx.group(1)), float(cy.group(1))
        stats['max_before'] = max(stats['max_before'], abs(x), abs(y))
        if x < xout or x > yout or y < yout_lo or y > yout_hi:
            stats['circles_dropped'] += 1
            return ''
        return whole
    svg_text = re.sub(r'<circle\b[^>]*/>', fix_circle, svg_text)
    svg_text = re.sub(r'<circle\b[^>]*>.*?</circle>', fix_circle, svg_text, flags=re.S)

    return svg_text, stats


def process_file(path: Path, pad: float, drop_offenders: bool, dry_run: bool,
                 out: Path | None):
    txt = path.read_text(encoding='utf-8', errors='replace')
    new, stats = sanitize(txt, pad, drop_offenders)
    if 'error' in stats:
        return f'ERROR {stats["error"]}'
    touched = stats['paths_clamped'] + stats['paths_dropped'] + stats['circles_dropped']
    summary = (f"max coord seen {stats['max_before']:,.0f} | "
               f"clamped {stats['paths_clamped']} path(s), "
               f"dropped {stats['paths_dropped']} path(s), "
               f"{stats['circles_dropped']} circle(s)")
    if touched == 0:
        return f'clean ({summary})'
    if dry_run:
        return f'WOULD fix: {summary}'
    dst = out or path
    if out is None:
        bak = path.with_suffix('.orig.svg')
        if not bak.exists():
            bak.write_text(txt, encoding='utf-8')
    dst.write_text(new, encoding='utf-8')
    return f'fixed: {summary}' + (f' -> {dst.name}' if out else
                                  f' (backup {path.stem}.orig.svg)')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', help='SVG file(s) or glob(s).')
    ap.add_argument('--pad', type=float, default=0.05,
                    help='Allowed margin outside the artboard, as a fraction of '
                         'its size, before a coord is treated as out-of-range '
                         '(default 0.05 = 5%%).')
    ap.add_argument('--drop-offenders', action='store_true',
                    help='Delete offending paths (e.g. full-canvas dim overlay) '
                         'instead of clamping them.')
    ap.add_argument('--out', type=Path, default=None,
                    help='Write to this path instead of in-place (single input).')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    files = []
    for pat in args.inputs:
        hits = [Path(p) for p in glob.glob(pat)]
        files.extend(hits if hits else ([Path(pat)] if Path(pat).exists() else []))
    if not files:
        sys.exit("No matching SVG files.")
    if args.out and len(files) > 1:
        sys.exit("--out works with a single input file only.")

    print(f"{'DRY-RUN: ' if args.dry_run else ''}sanitizing {len(files)} SVG(s)\n")
    for f in files:
        print(f"  {f.name}")
        print(f"      {process_file(f, args.pad, args.drop_offenders, args.dry_run, args.out)}")


if __name__ == '__main__':
    main()
