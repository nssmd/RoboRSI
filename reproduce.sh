#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${ROOT}/artifacts/reproduction"
RUN_SETUP=1

usage() {
  cat <<'EOF'
Usage: ./reproduce.sh [--skip-setup] [--output-dir PATH]

Replay the packaged RoboRSI result and generate a standalone Web report.

Options:
  --skip-setup       Reuse the current Python environment.
  --output-dir PATH  Write outputs under PATH.
  -h, --help         Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --skip-setup)
      RUN_SETUP=0
      shift
      ;;
    --output-dir)
      if (($# < 2)); then
        echo "error: --output-dir requires a path" >&2
        exit 2
      fi
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ((RUN_SETUP)); then
  "${ROOT}/setup.sh" --core-only
fi

mkdir -p "${OUTPUT_DIR}"
RESULT="${OUTPUT_DIR}/replay.json"
DASHBOARD="${OUTPUT_DIR}/dashboard.html"

"${ROOT}/roborsi" results replay \
  --manifest "${ROOT}/evidence/adaptive-coverage-v1/manifest.json" \
  --json "${RESULT}"
"${ROOT}/roborsi" web \
  --result "${RESULT}" \
  --output "${DASHBOARD}" \
  --no-browser

printf '\nRoboRSI reproduction complete.\n'
printf 'Result:    %s\n' "${RESULT}"
printf 'Dashboard: %s\n' "${DASHBOARD}"
