"""
wsgi.py — gunicorn entry point for the SAD app on Render.

Keeps the app construction out of the render.yaml command line (where nested
quotes got mangled). gunicorn imports `app` from this module.

Data dir, Census key, and ACS year come from environment variables so the same
entry point works locally and on Render.
"""
import os
from pathlib import Path

import sad_match_server

DATA_DIR = Path(os.environ.get("SAD_DATA_DIR", "/data"))
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "")
ACS_YEAR = int(os.environ.get("SAD_ACS_YEAR", "2023"))

app = sad_match_server.make_app(DATA_DIR, CENSUS_API_KEY, ACS_YEAR)
