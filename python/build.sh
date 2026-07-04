#!/usr/bin/env bash
# Build the openyuanrong-sandbox distribution (pure-Python wheel + sdist).
#
# This is the single release/packaging entrypoint, callable standalone or from the
# yuanrong build pipeline (Makefile `agentruntime` target). openyuanrong-sandbox is a
# pure-Python package (py3-none-any) — no toolchain/compile needed.
#
# Usage:
#   bash build.sh [OUTDIR]   # default OUTDIR=dist
# Env:
#   PYTHON   python interpreter to use (default: python3)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTDIR="${1:-${SCRIPT_DIR}/dist}"
PYTHON="${PYTHON:-python3}"

cd "${SCRIPT_DIR}"
rm -rf build ./*.egg-info
mkdir -p "${OUTDIR}"

# Version is tag-based (setuptools_scm). In CI a release tag is provided via
# YR_RELEASE_TAG / BUILDKITE_TAG; pin it explicitly (handles shallow clones /
# submodules without full tag history). Untagged trees use fallback_version.
tag="${YR_RELEASE_TAG:-${BUILDKITE_TAG:-}}"
tag="${tag#refs/tags/}"
tag="${tag#v}"
if [ -n "${tag}" ]; then
	export SETUPTOOLS_SCM_PRETEND_VERSION="${tag}"
	echo "[openyuanrong-sandbox] building tag version ${tag} -> ${OUTDIR}"
else
	echo "[openyuanrong-sandbox] building (version from git tags / fallback) -> ${OUTDIR}"
fi

# Prefer the PEP 517 'build' frontend (wheel + sdist); fall back to pip wheel
# (wheel only) when 'build' is unavailable in the environment.
if ${PYTHON} -c "import build" >/dev/null 2>&1; then
	${PYTHON} -m build --wheel --sdist --outdir "${OUTDIR}"
else
	echo "[openyuanrong-sandbox] 'build' module absent; falling back to 'pip wheel' (wheel only)"
	${PYTHON} -m pip wheel . --no-deps -w "${OUTDIR}"
fi

echo "[openyuanrong-sandbox] artifacts:"
ls -1 "${OUTDIR}"/openyuanrong_sandbox-*
