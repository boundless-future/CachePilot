#!/usr/bin/env bash
# Rebuild the pinned research commit against the ACTIVE environment's torch.
# The archive has no Git metadata, so use an explicit local package version.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMIT=1a4b40b1d79b0e76244f127f96ee0982f8bd270f
ARCHIVE="$PROJECT_ROOT/vendor/lmcache-source.tar.gz"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-13.0}"
export PATH="$CUDA_HOME/bin:$PATH"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-8.9}"
export MAX_JOBS="${MAX_JOBS:-4}"
export NVCC_THREADS="${NVCC_THREADS:-2}"
export SETUPTOOLS_SCM_PRETEND_VERSION=0.5.5+g1a4b40b1d
python -c 'import torch; assert torch.__version__.split("+")[0] == "2.13.0"; assert torch.version.cuda == "13.0"'
mkdir -p "$PROJECT_ROOT/vendor/wheels" "$PROJECT_ROOT/artifacts/build"
python -m pip freeze > "$PROJECT_ROOT/artifacts/build/pip-before.txt"
curl -fL --connect-timeout 15 --max-time 120 \
  "https://codeload.github.com/LMCache/LMCache/tar.gz/$COMMIT" -o "$ARCHIVE"
printf '%s  %s\n' 25e6fe48aad218e67ed75b8c8c7bc687c38aa3ae60ae9ebebe437d6eed853d2d "$ARCHIVE" | sha256sum -c -
tar -xzf "$ARCHIVE" -C "$PROJECT_ROOT/vendor"
python -m pip install grpcio-tools==1.78.0
python -m pip wheel "$PROJECT_ROOT/vendor/LMCache-$COMMIT" \
  --no-deps --no-build-isolation -w "$PROJECT_ROOT/vendor/wheels"
python -m pip install --no-deps \
  "$PROJECT_ROOT/vendor/wheels/lmcache-0.5.5+g1a4b40b1d-cp312-cp312-linux_x86_64.whl"
python -m pip check
python -m pip freeze > "$PROJECT_ROOT/artifacts/build/pip-after.txt"
