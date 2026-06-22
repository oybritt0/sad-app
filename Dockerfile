# SAD Toolkit — Render deployment image (geo pipeline + Flask app).
# Excludes the torch/gradio research track (run locally). Pinned versions.
#
# The GDAL base image ships some Python packages via apt (numpy, etc.) that pip
# cannot uninstall (no RECORD file). We install our pinned deps into a clean
# virtualenv created WITH --system-site-packages so the GDAL Python bindings
# from the base remain importable, while our packages take precedence in the
# venv. We do NOT re-pin numpy/GDAL: we let the geo wheels use compatible
# versions to avoid breaking the base's compiled bindings.

FROM ghcr.io/osgeo/gdal:ubuntu-small-3.9.3

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev python3-venv build-essential \
    && rm -rf /var/lib/apt/lists/*

# Clean venv that can still see the base image's GDAL python bindings.
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

WORKDIR /app

COPY requirements.txt .
# Install into the venv. --ignore-installed avoids touching the apt-managed
# packages in the system site-packages (which pip can't uninstall).
RUN pip install --upgrade pip \
    && pip install --ignore-installed -r requirements.txt

COPY code/ /app/code/

ENV PORT=8000
EXPOSE 8000

WORKDIR /app/code
CMD ["python3", "-c", "print('set the start command in render.yaml')"]
