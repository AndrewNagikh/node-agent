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

detect_gpu_cmake_args() {
  GPU_CMAKE_EXTRA=()

  if [[ -n "${GGML_CUDA:-}" ]]; then
    GPU_CMAKE_EXTRA=(-DGGML_CUDA="${GGML_CUDA}")
    echo "build: GGML_CUDA=${GGML_CUDA} (override)"
    return
  fi

  if [[ -n "${GGML_METAL:-}" ]]; then
    GPU_CMAKE_EXTRA=(-DGGML_METAL="${GGML_METAL}")
    echo "build: GGML_METAL=${GGML_METAL} (override)"
    return
  fi

  if [[ "$(uname -s)" == "Darwin" ]]; then
    GPU_CMAKE_EXTRA=(-DGGML_METAL=ON)
    echo "build: macOS detected → Metal GPU enabled"
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    GPU_CMAKE_EXTRA=(-DGGML_CUDA=ON)
    echo "build: NVIDIA GPU detected → CUDA enabled"
  else
    echo "build: no GPU backend detected → CPU only"
  fi
}

BUILD_MODE="${1:-agents}"
shift || true

case "$BUILD_MODE" in
  all)
    TARGETS=(node_agent split_gen3_a split_gen3_b split_gen3_c orchestrator)
    ;;
  orchestrator)
    TARGETS=(orchestrator)
    ;;
  agents)
    TARGETS=(node_agent split_gen3_a split_gen3_b split_gen3_c)
    ;;
  *)
    echo "usage: $0 [agents|orchestrator|all]" >&2
    exit 1
    ;;
esac

detect_gpu_cmake_args

CMAKE_ARGS=(
  -B "$BUILD"
  -S "$LLAMA"
  -DCMAKE_BUILD_TYPE=Release
  -DLLAMA_BUILD_TESTS=OFF
  "${GPU_CMAKE_EXTRA[@]}"
)

echo "build: configuring..."
cmake "${CMAKE_ARGS[@]}"

echo "build: compiling ${TARGETS[*]} ..."
cmake --build "$BUILD" --target "${TARGETS[@]}" -j"${JOBS}"

echo ""
echo "Built:"
for t in "${TARGETS[@]}"; do
  echo "  $BUILD/bin/$t"
done
