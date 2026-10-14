#!/usr/bin/env sh
set -eu

# Build a disposable development image; the production compose image stays lean.
docker build --build-arg INSTALL_DEV=true -t local/dify-mcp:tests-phase0 .
docker run --rm --user 0:0 -v "$(pwd):/workspace:ro" -w /workspace --entrypoint python \
  local/dify-mcp:tests-phase0 -m pytest tests/unit
