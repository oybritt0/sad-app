# SAD Toolkit

Web application for analyzing and comparing **Sports-Anchored Districts** (SADs) —
urban districts built around major stadiums and arenas across North America.

## What it does

- **Viewer** — per-district analysis: buildings, program, morphology, transit,
  walksheds, census, and more, for ~50 districts.
- **Map** — corpus-wide map view comparing all districts.
- **Field** — census/embedding field view across the corpus.
- **Draw a district** — draw a new area; the app pulls live data (OSM, Overture,
  Census, GTFS) and runs the full analysis pipeline to add it to the set.

## Architecture

Single Flask service (`code/sad_match_server.py`) that serves the viewer and
handles the draw → pipeline flow. Deployed on Render via `render.yaml` as one
web service with a persistent disk holding the data corpus.

- `code/` — pipeline modules (M1–M22, cross-SAD), viewer assets, Flask app
- `Dockerfile` — geospatial runtime (GDAL, geopandas, OSMnx, DuckDB)
- `render.yaml` — Render deployment (web service + persistent disk)
- `requirements.txt` — pinned Python dependencies

## Deployment

Push to this repo → Render auto-builds and redeploys.

The data corpus lives on the Render persistent disk (not in this repo) and is
uploaded separately. Set `CENSUS_API_KEY` as a secret in the Render dashboard.

## Local development

Run the Flask app directly:

    cd code
    python sad_match_server.py --data-dir <path-to-data>

Then open http://localhost:8000/_ui/

## Notes

Drawing requires internet access (live data pulls) and the GTFS feed cache on
the data disk. The research/embedding track (torch/GAN/VAE) runs locally and is
excluded from the deployed image.
