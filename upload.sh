#!/bin/sh
# Upload all ColDP archives in output/ to ChecklistBank; asks for username and password.
# Options: archive names, --skip=a,b, --dry-run (see ubio_coldp/clb_upload.py)
cd "$(dirname "$0")" && exec uv run python -m ubio_coldp.clb_upload "$@"
