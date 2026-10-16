#!/usr/bin/env bash
# The same gates as CI (.github/workflows/ci.yml), in one command, for people and agents.
#   tools/check.sh          everything: lint, format, CSS, migrations, tests
#   tools/check.sh quick    lint, format and the tests in tests/ that match $CHECK_TESTS (default: all)
# Exit code 0 means every gate passed. Run it from the repository root, inside the virtualenv.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n== %s\n' "$1"; }

step 'Lint (ruff check)'
ruff check .
step 'Format (ruff format --check)'
ruff format --check .

if [[ "${1:-}" == quick ]]; then
  step "Tests ${CHECK_TESTS:-tests}"
  python -m pytest -q -p no:warnings ${CHECK_TESTS:-tests}
  exit 0
fi

step 'CSS is built from the templates'
tools/build-css.sh >/dev/null
git diff --exit-code -- community/static/app.css \
  || { echo 'app.css is out of date: commit the result of tools/build-css.sh'; exit 1; }

step 'Migrations: one head, upgrade, check, downgrade, upgrade'
db="$(mktemp -d)/check.db"
export APP_ENV=development SQLALCHEMY_DATABASE_URI="sqlite:///$db"
test "$(flask --app community db heads | grep -c '(head)')" -eq 1
flask --app community db upgrade >/dev/null
flask --app community db check
flask --app community db downgrade base >/dev/null
flask --app community db upgrade >/dev/null
unset APP_ENV SQLALCHEMY_DATABASE_URI

step 'Tests'
python -m pytest -q -p no:warnings

echo
echo 'All checks passed.'
