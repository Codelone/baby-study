#!/usr/bin/env bash
set -euo pipefail
umask 077
FAMILY_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$FAMILY_DIR/../../.venv/bin/python" "$FAMILY_DIR/family.py" start "$@"
