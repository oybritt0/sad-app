"""
ui_server.py

Local web server that hosts the SAD Pipeline Viewer. Run this after
build_ui_manifest.py has created data/_ui/manifest.json and copied
the viewer assets.

USAGE
  python ui_server.py --data-dir <data_dir> [--port 8765] [--no-open]

This will:
  1. Verify the viewer is staged at <data_dir>/_ui/index.html
  2. Start an HTTP server on the chosen port serving <data_dir>/
  3. Open your default browser to /_ui/index.html

Ctrl+C to stop. No external dependencies beyond Python stdlib.
"""
from __future__ import annotations
import argparse
import functools
import http.server
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path


# Quiet down the default log spam from http.server
class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Only log non-200 responses (errors, 404s)
        msg = format % args
        if '" 200 ' in msg or '" 304 ' in msg:
            return
        sys.stderr.write(f"  {self.log_date_time_string()}  {msg}\n")

    def end_headers(self):
        # Disable caching so file edits show up on reload
        self.send_header('Cache-Control',
                          'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-open', action='store_true',
                    help="Don't auto-open browser")
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    if not data_dir.exists():
        sys.exit(f"data dir does not exist: {data_dir}")

    ui_dir = data_dir / '_ui'
    index_path = ui_dir / 'index.html'
    manifest_path = ui_dir / 'manifest.json'

    if not index_path.exists() or not manifest_path.exists():
        sys.exit(
            f"viewer assets not found at {ui_dir}.\n"
            f"Run first:\n"
            f"  python build_ui_manifest.py --data-dir \"{data_dir}\"\n"
        )

    # Serve from data_dir so URLs like /_ui/manifest.json and
    # /02_Sportsmans-Park.../source/*.geojson all work directly.
    handler = functools.partial(QuietHandler, directory=str(data_dir))

    # Use ThreadingTCPServer for parallel requests (multiple GeoJSON loads)
    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        httpd = ReusableServer(('127.0.0.1', args.port), handler)
    except OSError as e:
        sys.exit(f"failed to bind to port {args.port}: {e}\n"
                 f"try a different port with --port")

    url = f"http://127.0.0.1:{args.port}/_ui/index.html"
    print(f"")
    print(f"  SAD Pipeline Viewer")
    print(f"  -------------------")
    print(f"  serving:  {data_dir}")
    print(f"  url:      {url}")
    print(f"")
    print(f"  Ctrl+C to stop")
    print(f"")

    if not args.no_open:
        # Give the server a moment to start before opening browser
        def open_browser():
            time.sleep(0.4)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping server...")
        httpd.shutdown()
        httpd.server_close()


if __name__ == '__main__':
    main()
