#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LLAMA="$ROOT/llama.cpp"
BUILD="$LLAMA/build"

if [[ ! -f "$LLAMA/CMakeLists.txt" ]]; then
  echo "llama.cpp submodule missing or empty. Run:"
  echo "  git submodule update --init --recursive"
  exit 1
fi

# Prefer user-local cmake if present (Linux dev box), otherwise use PATH (Homebrew on macOS).
if [[ -x "${HOME}/.local/cmake/bin/cmake" ]]; then
  export PATH="${HOME}/.local/cmake/bin:${PATH}"
fi

if ! command -v cmake >/dev/null 2>&1; then
  echo "cmake not found. Install it (e.g. brew install cmake) and retry."
  exit 1
fi

if command -v nproc >/dev/null 2>&1; then
  JOBS="$(nproc)"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  JOBS="$(sysctl -n hw.ncpu)"
else
  JOBS=4
fi

cmake -B "$BUILD" -S "$LLAMA" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD" --target node_agent split_gen3_a split_gen3_b split_gen3_c -j"${JOBS}"

echo "Built:"
echo "  $BUILD/bin/node_agent"
echo "  $BUILD/bin/split_gen3_{a,b,c}"
