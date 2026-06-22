"""
shrink_svg.py - standalone SVG shrinker

Usage:
    python shrink_svg.py <input.svg>
    python shrink_svg.py <input.svg> <output.svg>

If no output is given, writes <input>_shrunk.svg next to the input.

What it removes:
    - All <image> elements (satellite raster + legend PNG logo)
    - Any clipPath subpaths whose coordinates are wildly off-canvas
      (the "M5047611, -2697845" bug)
    - All empty attributes

Why this exists as a separate script:
    The in-browser shrink ran into update issues (caching, copy paths,
    timing of window.ExportShrink). This bypasses all of that — export
    the SVG normally from the viewer, then run this on the file before
    opening in Illustrator. Guaranteed to work because we control the
    environment.
"""
import re
import sys
from pathlib import Path


def shrink(in_path: Path, out_path: Path):
    print(f"reading: {in_path}")
    content = in_path.read_text(encoding='utf-8')
    orig_size = len(content)
    print(f"input:  {orig_size:>12,} bytes ({orig_size/1024/1024:.2f} MB)")

    # 1) Strip every <image> element (PNG-encoded rasters trigger Illustrator's
    #    outline-mode fallback during preview, regardless of size).
    n_images_before = content.count('<image')
    content = re.sub(r'<image\b[^>]*?/>', '', content, flags=re.DOTALL)
    content = re.sub(r'<image\b[^>]*?>.*?</image>', '', content, flags=re.DOTALL)
    n_images_after = content.count('<image')
    print(f"  images dropped: {n_images_before - n_images_after}")

    # 2) Fix the wild clipPath bug. If clipPath#crop-inside contains a path
    #    with multiple M subpaths (the second one having millions-coordinates),
    #    truncate it to just the first subpath.
    def fix_clip(match):
        d = match.group(1)
        # Find first M, then second M
        first_m = re.search(r'[Mm]', d)
        if not first_m:
            return match.group(0)
        i = first_m.start()
        second_m = d.find('M', i + 1)
        if second_m == -1:
            return match.group(0)
        first_subpath = d[i:second_m].rstrip()
        if not re.search(r'[Zz]\s*$', first_subpath):
            first_subpath += 'Z'
        return match.group(0).replace(d, first_subpath)

    new_content = re.sub(
        r'<clipPath[^>]*id="crop-inside"[^>]*>.*?<path[^>]*\bd="([^"]+)"',
        fix_clip, content, flags=re.DOTALL)
    if new_content != content:
        print("  wild clipPath: FIXED")
        content = new_content

    # 3) Strip empty attributes that pad every <g> element.
    n_attrs = 0
    for attr in ['fill', 'stroke', 'stroke-width', 'style', 'class',
                 'pointer-events', 'transform']:
        pat = re.compile(rf'\s+{attr}=""')
        n = len(pat.findall(content))
        n_attrs += n
        content = pat.sub('', content)
    print(f"  empty attrs removed: {n_attrs}")

    # 4) Round path coordinates to one decimal place (aggressive: integers if
    #    you want; tweak the format string below). Saves ~30-50% on path data.
    def round_coords(match):
        d = match.group(1)
        return f'd="{round_path_d(d)}"'

    def round_path_d(d):
        # Replace every floating-point number with its rounded version.
        return re.sub(r'-?\d+\.\d+',
                      lambda m: f"{float(m.group(0)):.1f}", d)

    saved_d = 0
    def round_and_count(m):
        nonlocal saved_d
        d_orig = m.group(1)
        d_new = round_path_d(d_orig)
        saved_d += len(d_orig) - len(d_new)
        return f'd="{d_new}"'

    content = re.sub(r'd="([^"]+)"', round_and_count, content)
    print(f"  path data rounded: {saved_d/1024:.1f} KB saved")

    new_size = len(content)
    pct = 100 * (1 - new_size / orig_size)
    print(f"output: {new_size:>12,} bytes ({new_size/1024/1024:.2f} MB)  [{pct:.1f}% smaller]")

    out_path.write_text(content, encoding='utf-8')
    print(f"wrote: {out_path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    in_path = Path(sys.argv[1])
    if not in_path.exists():
        sys.exit(f"input not found: {in_path}")
    out_path = (Path(sys.argv[2]) if len(sys.argv) >= 3
                else in_path.with_name(in_path.stem + '_shrunk.svg'))
    shrink(in_path, out_path)


if __name__ == '__main__':
    main()
