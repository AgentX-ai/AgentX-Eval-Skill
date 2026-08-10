#!/usr/bin/env bash
#
# Python bootstrap for an evaluation run on a Server4Agent box.
#
# The box has python3 but no pip and no venv module, and its system Python is
# PEP-668 externally managed, so installing into site-packages is refused
# outright. A virtualenv is the honest way past that: it needs no
# --break-system-packages, it leaves the system Python alone, and it persists in
# the workspace so a second run costs seconds instead of minutes.
#
# Idempotent, and safe to run on a laptop: the apt branch skips itself wherever
# a working pip already exists.
#
# Usage:
#   bash bootstrap.sh                 # installs requirements.txt if present
#   bash bootstrap.sh pkg-a pkg-b     # installs the named packages instead
#
set -euo pipefail

# Run from the repo root, wherever this script was copied to.
cd "${PROJECT_DIR:-$PWD}"

log() { printf '==> %s\n' "$*"; }

if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  log "installing python3-pip and python3-venv"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    python3-pip python3-venv
else
  log "pip and venv already available, skipping apt"
fi

if [ ! -d .venv ]; then
  log "creating .venv"
  python3 -m venv .venv
else
  log "reusing existing .venv"
fi

.venv/bin/python -m pip install --quiet --upgrade pip

if [ "$#" -gt 0 ]; then
  log "installing: $*"
  .venv/bin/python -m pip install --quiet "$@"
elif [ -f requirements.txt ]; then
  log "installing from requirements.txt"
  .venv/bin/python -m pip install --quiet -r requirements.txt
elif [ -f pyproject.toml ]; then
  log "installing from pyproject.toml"
  .venv/bin/python -m pip install --quiet .
else
  # No manifest is a real possibility: plenty of small agent repos document
  # their install as a pip line in the README and never pin anything. Say so
  # loudly rather than proceeding to an import error that looks like a bug.
  echo "no requirements.txt or pyproject.toml, and no packages named on the" >&2
  echo "command line. Pass the dependencies explicitly, or add a manifest to" >&2
  echo "the repo so the next run is reproducible." >&2
  exit 1
fi

# A virtualenv in the working tree is a few hundred megabytes waiting to be
# committed by an over-eager `git add`. Ignoring it here, at the moment it comes
# into existence, is cheaper than noticing later.
if [ -d .git ] && [ -f .gitignore ] && ! grep -qE '^\.?/?\.venv/?$' .gitignore; then
  log "adding .venv/ to .gitignore"
  printf '\n.venv/\n' >> .gitignore
fi

log "installed:"
.venv/bin/python - <<'PY'
from importlib.metadata import distributions
seen = sorted((d.metadata["Name"], d.version) for d in distributions()
              if d.metadata["Name"])
for name, version in seen:
    print(f"    {name} {version}")
PY

log "bootstrap ok"
