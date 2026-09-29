#!/usr/bin/env bash
set -euo pipefail
FAMILY_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$FAMILY_DIR/../../.venv/bin/python" "$FAMILY_DIR/family.py" stop
