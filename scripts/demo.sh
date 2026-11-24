#!/usr/bin/env bash
# One-command HiJenny demo: build an isolated workspace (if needed) and start the UI.
#
#   ./scripts/demo.sh            # http://127.0.0.1:8123/
#   PORT=8124 ./scripts/demo.sh
#   OUT=demo_workspace ./scripts/demo.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT="${OUT:-demo_workspace}"
PORT="${PORT:-8123}"

if [ ! -f "$OUT/demo_info.json" ]; then
  echo "⇒ Building isolated demo workspace: $OUT"
  python3 -m hijenny.cli demo-kit --package data/sample_package --out "$OUT"
fi

export HIJENNY_DATA_DIR="$ROOT/$OUT/data"
export HIJENNY_DB_PATH="$HIJENNY_DATA_DIR/hijenny.db"

echo
echo "⇒ HiJenny demo: http://127.0.0.1:$PORT/"
echo "  Project data: $HIJENNY_DATA_DIR"
echo "  Deliverables: $ROOT/$OUT/deliverables/"
echo "  Stop with Ctrl+C"
echo
python3 -m hijenny.cli serve --port "$PORT"
