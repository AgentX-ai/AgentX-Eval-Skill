#!/usr/bin/env bash
#
# Make the repo under test runnable.
#
# Dispatches on whichever manifest it finds, because the agents this skill triages
# are written in whatever their authors chose - the evaluation reaches them over
# HTTP or through a harness, and neither cares about the language. A Python-only
# bootstrap turned "your agent is not Python" into "this skill does not work here".
#
# Usage:
#   bash bootstrap.sh                 # detect and install
#   bash bootstrap.sh pkg-a pkg-b     # Python only: install these instead of a manifest
#
set -euo pipefail

cd "${PROJECT_DIR:-$PWD}"

log() { printf '==> %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
bootstrap_python() {
  # A virtualenv rather than a system install, because a system Python is often
  # PEP-668 externally managed and refuses site-packages writes outright. A venv
  # sidesteps that honestly: no --break-system-packages, the system Python left
  # alone, and it persists so a second run costs seconds instead of minutes.
  if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
    log "installing python3-pip and python3-venv"
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      python3-pip python3-venv
  fi

  [ -d .venv ] || { log "creating .venv"; python3 -m venv .venv; }
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
    echo "command line. Pass the dependencies explicitly, or add a manifest." >&2
    exit 1
  fi

  # A virtualenv in the working tree is a few hundred megabytes waiting to be
  # committed by an over-eager `git add`. Ignoring it here, at the moment it comes
  # into existence, is cheaper than noticing later.
  if [ -d .git ] && [ -f .gitignore ] && ! grep -qE '^\.?/?\.venv/?$' .gitignore; then
    log "adding .venv/ to .gitignore"
    printf '\n.venv/\n' >> .gitignore
  fi
  log "python ready: .venv/bin/python"
}

# ---------------------------------------------------------------------------
# Node - lockfile picks the package manager, so the install matches what the
# repo's own authors get rather than resolving a different tree.
# ---------------------------------------------------------------------------
bootstrap_node() {
  if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null; then log "pnpm install"; pnpm install --frozen-lockfile
  elif [ -f yarn.lock ] && command -v yarn >/dev/null; then log "yarn install"; yarn install --frozen-lockfile
  elif [ -f bun.lockb ] && command -v bun >/dev/null;   then log "bun install";  bun install --frozen-lockfile
  elif [ -f package-lock.json ];                        then log "npm ci";       npm ci
  else log "npm install"; npm install
  fi
  log "node ready"
}

# ---------------------------------------------------------------------------
# Everything else
# ---------------------------------------------------------------------------
main() {
  if [ -f requirements.txt ] || [ -f pyproject.toml ] || [ "$#" -gt 0 ]; then
    bootstrap_python "$@"
  elif [ -f package.json ]; then
    bootstrap_node
  elif [ -f go.mod ]; then
    log "go mod download"; go mod download; log "go ready"
  elif [ -f Cargo.toml ]; then
    log "cargo fetch"; cargo fetch; log "rust ready"
  elif [ -f Gemfile ]; then
    log "bundle install"; bundle install; log "ruby ready"
  elif [ -f pom.xml ]; then
    log "mvn dependency:resolve"; mvn -q dependency:resolve; log "java ready"
  else
    # Better to stop with a readable message than to guess and half-install.
    echo "no manifest found (requirements.txt, pyproject.toml, package.json," >&2
    echo "go.mod, Cargo.toml, Gemfile, pom.xml). If this repo installs some" >&2
    echo "other way, run that yourself and skip this script - the triage only" >&2
    echo "needs the repo runnable, not bootstrapped by this particular script." >&2
    exit 1
  fi
}

main "$@"
