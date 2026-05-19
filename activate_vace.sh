#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$ROOT_DIR/.venv"
REPO_PATH="$ROOT_DIR/repos/VACE"

if [[ ! -d "$VENV_PATH" ]]; then
  echo "Missing virtual environment: $VENV_PATH" >&2
  return 1 2>/dev/null || exit 1
fi

if [[ ! -f "$VENV_PATH/bin/activate" ]]; then
  echo "Missing activation script: $VENV_PATH/bin/activate" >&2
  return 1 2>/dev/null || exit 1
fi

# shellcheck disable=SC1091
source "$VENV_PATH/bin/activate"

export VACE_WORKSPACE_ROOT="$ROOT_DIR"
export VACE_REPO_ROOT="$REPO_PATH"
export VACE_INPUT_ROOT="$ROOT_DIR/workspace/inputs"
export VACE_OUTPUT_ROOT="$ROOT_DIR/workspace/outputs"
export VACE_MODEL_ROOT="$ROOT_DIR/models"
export HF_HOME="$ROOT_DIR/cache/hf_home"
export MODELSCOPE_CACHE="$ROOT_DIR/cache/modelscope"
export MODELSCOPE_HOME="$ROOT_DIR/cache/modelscope"
export PYTHONPATH="$REPO_PATH${PYTHONPATH:+:$PYTHONPATH}"

cd "$REPO_PATH"

echo "Activated VACE environment at $VENV_PATH"
echo "Working directory: $REPO_PATH"
