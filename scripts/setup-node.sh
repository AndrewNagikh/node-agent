#!/usr/bin/env bash
# Install build dependencies and compile node-agent for Linux / WSL / macOS.
#
# Usage:
#   ./scripts/setup-node.sh              # build agents (default)
#   ./scripts/setup-node.sh all          # orchestrator + agents + verify tools
#   ./scripts/setup-node.sh orchestrator
#
# Options via env:
#   SKIP_DEPS=1   skip package manager install
#   GGML_CUDA=ON  force CUDA (WSL/Linux with NVIDIA toolkit)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_MODE="${1:-agents}"

log() { echo "setup-node: $*"; }

have() { command -v "$1" >/dev/null 2>&1; }

install_deps_linux() {
  if [[ "${SKIP_DEPS:-}" == "1" ]]; then
    log "SKIP_DEPS=1 — skipping apt"
    return
  fi
  if ! have apt-get; then
    log "no apt-get — install build-essential, cmake, git, libssl-dev manually"
    return
  fi
  log "installing apt packages (sudo may prompt)..."
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends \
    build-essential cmake git curl ca-certificates pkg-config \
    libssl-dev
}

install_deps_macos() {
  if [[ "${SKIP_DEPS:-}" == "1" ]]; then
    return
  fi
  if ! have brew; then
    log "Homebrew not found — install from https://brew.sh then: brew install cmake git"
    return
  fi
  local missing=()
  have cmake || missing+=(cmake)
  have git   || missing+=(git)
  if ((${#missing[@]})); then
    log "brew install ${missing[*]}"
    brew install "${missing[@]}"
  fi
}

install_deps() {
  case "$(uname -s)" in
    Linux)  install_deps_linux ;;
    Darwin) install_deps_macos ;;
    *)      log "unknown OS — ensure cmake, git, C++ compiler are installed" ;;
  esac
}

ensure_submodule() {
  if [[ ! -f "$ROOT/llama.cpp/CMakeLists.txt" ]]; then
    log "initializing llama.cpp submodule..."
    git -C "$ROOT" submodule update --init --recursive
  fi
}

main() {
  install_deps
  ensure_submodule
  log "building ($BUILD_MODE)..."
  "$ROOT/build.sh" "$BUILD_MODE"
  log "done — binaries in llama.cpp/build/bin/"
}

main
