#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/install.sh [--prepare-only]

Creates .env only when it does not exist, generates independent local tokens,
and starts the AcouAgent stack from the checked-out source. --prepare-only
stops after safe configuration preparation so a model API key can be added.
EOF
}

prepare_only=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prepare-only) prepare_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

random_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

read_env_value() {
  local key="$1"
  awk -F= -v wanted="$key" \
    '$1 == wanted {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' .env
}

if [[ ! -f .env ]]; then
  [[ -f .env.example ]] || { echo "missing .env.example" >&2; exit 1; }
  umask 077
  cp .env.example .env
  mcp_token="$(random_token)"
  executor_token="$(random_token)"
  while [[ "$executor_token" == "$mcp_token" ]]; do
    executor_token="$(random_token)"
  done
  sed -i.bak \
    -e "s/replace-with-at-least-32-random-characters/$mcp_token/" \
    -e "s/replace-with-a-different-32-character-secret/$executor_token/" \
    .env
  rm -f .env.bak
  chmod 600 .env
  echo "created .env with independent local tokens"
else
  echo "using existing .env without overwriting it"
fi

if [[ "$(grep -c '^MCP_AUTH_TOKEN=' .env || true)" != 1 || \
      "$(grep -c '^EXECUTOR_SHARED_TOKEN=' .env || true)" != 1 ]]; then
  echo "MCP_AUTH_TOKEN and EXECUTOR_SHARED_TOKEN must each appear exactly once in .env" >&2
  exit 1
fi
mcp_token="$(read_env_value MCP_AUTH_TOKEN)"
executor_token="$(read_env_value EXECUTOR_SHARED_TOKEN)"
if [[ ${#mcp_token} -lt 32 || ${#executor_token} -lt 32 ]]; then
  echo "MCP_AUTH_TOKEN and EXECUTOR_SHARED_TOKEN must each contain at least 32 characters" >&2
  exit 1
fi
if [[ "$mcp_token" == replace-* || "$executor_token" == replace-* ]]; then
  echo "replace the token placeholders in .env before starting" >&2
  exit 1
fi
if [[ "$mcp_token" == "$executor_token" ]]; then
  echo "MCP_AUTH_TOKEN and EXECUTOR_SHARED_TOKEN must be different" >&2
  exit 1
fi

if [[ "$prepare_only" == true ]]; then
  echo "configuration prepared; add a server model credential to .env when natural-language chat is required"
  exit 0
fi

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }

docker compose config --quiet
docker compose pull jwave-executor
docker compose up -d --build

gateway_ready=false
for _ in $(seq 1 60); do
  if [[ "$(curl -fsS http://127.0.0.1:8001/health 2>/dev/null || true)" == "ok" ]]; then
    gateway_ready=true
    break
  fi
  sleep 2
done
if [[ "$gateway_ready" != true ]]; then
  docker compose logs dify-mcp >&2 || true
  echo "gateway did not become healthy" >&2
  exit 1
fi

bootstrap_done=false
for _ in $(seq 1 180); do
  state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' acouagent-skill-bootstrap 2>/dev/null || true)"
  if [[ "$state" == "exited 0" ]]; then
    bootstrap_done=true
    break
  fi
  if [[ "$state" == exited\ * && "$state" != "exited 0" ]]; then
    docker compose logs skill-bootstrap >&2 || true
    echo "fixed-skill bootstrap failed" >&2
    exit 1
  fi
  sleep 5
done
if [[ "$bootstrap_done" != true ]]; then
  docker compose logs skill-bootstrap >&2 || true
  echo "fixed-skill bootstrap timed out" >&2
  exit 1
fi

docker exec dify-mcp python -m scripts.skill_library.local_basic status-all >/dev/null
echo "AcouAgent is ready at http://127.0.0.1:8001/portal"
if [[ -z "$(read_env_value DEEPSEEK_API_KEY)" ]]; then
  echo "natural-language chat remains unavailable until a server model credential is configured in .env"
fi
