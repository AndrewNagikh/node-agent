#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LLAMA="$ROOT/llama.cpp"
BUILD="$LLAMA/build"

if [[ ! -d "$LLAMA/.git" ]]; then
  echo "llama.cpp submodule missing. Run:"
  echo "  git submodule update --init --recursive"
  exit 1
fi

export PATH="${HOME}/.local/cmake/bin:${PATH}"

cmake -B "$BUILD" -S "$LLAMA" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD" --target node_agent split_gen3_a split_gen3_b split_gen3_c -j"$(nproc)"

echo "Built:"
echo "  $BUILD/bin/node_agent"
echo "  $BUILD/bin/split_gen3_{a,b,c}"
