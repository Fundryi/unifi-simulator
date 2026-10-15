#!/bin/sh
set -eu
cd /workspace/unifi-simulator
export UV_CACHE_DIR=/workspace/.cache/uv
export BUILDX_CONFIG=/workspace/.cache/buildx
uv sync --frozen --extra dev
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
docker build --secret id=build_ca,src=/etc/ssl/certs/ca-certificates.crt -t mtunifi:dev .
.venv/bin/python scripts/docker-smoke.py
