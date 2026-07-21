#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
E2E_PROJECT_NAME=${E2E_PROJECT_NAME:-hennongxi-e2e}

cd "$REPOSITORY_ROOT"

compose() {
  docker compose \
    -p "$E2E_PROJECT_NAME" \
    -f docker-compose.yml \
    -f tests/e2e/compose.yml \
    "$@"
}

save_failure_logs() {
  mkdir -p tests/e2e/test-results
  compose logs --no-color > tests/e2e/test-results/compose.log 2>&1 || true
  printf '%s\n' \
    "E2E 失败诊断已保留在 tests/e2e/test-results/、tests/e2e/playwright-report/。" \
    >&2
}

if ! compose build master-agent web postgis e2e; then
  save_failure_logs
  exit 1
fi

if ! compose up -d --wait --remove-orphans; then
  save_failure_logs
  exit 1
fi

if compose run --rm e2e; then
  exit 0
else
  result=$?
  save_failure_logs
  exit "$result"
fi
