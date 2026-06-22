# SAD Toolkit - Render deployment image (geo pipeline + Flask app).
# Excludes the torch/gradio research track (run locally, not needed for
# draw/view/compare). Pinned to the versions confirmed working locally.

FROM ghcr.io/osgeo/gdal:ubuntu-small-3.9.3

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Python + build tools (GDAL itself comes from the base image)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer-cached unless requirements change).
# GDAL Python bindings are matched to the base image's GDAL via the
# 'gdal==3.9.3' pin pulled from the system; fiona/rasterio build against it.
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt

# App code (data lives on the Render persistent disk, NOT in the image)
COPY code/ /app/code/

# Render provides $PORT for web services; default for local docker runs.
ENV PORT=8000
EXPOSE 8000

# Default command is overridden per-service in render.yaml
#   web service   -> gunicorn serving the Flask app
#   worker service-> the integration worker loop
WORKDIR /app/code
CMD ["python3", "-c", "print('set the start command in render.yaml')"]
