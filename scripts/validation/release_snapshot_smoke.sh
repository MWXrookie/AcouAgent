#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/validation/release_snapshot_smoke.sh [--ref GIT_REF] [--with-executor]

Build and test only files present in the selected Git ref. The script creates
temporary images, containers, volumes and an archive directory, then removes
all of them on exit. Set RELEASE_AUDIT_PORT to choose the loopback test port.
EOF
}

ref=HEAD
with_executor=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) ref="${2:?missing ref}"; shift 2 ;;
    --with-executor) with_executor=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

root="$(git rev-parse --show-toplevel)"
sha="$(git -C "$root" rev-parse "$ref^{commit}")"
suffix="${sha:0:12}-$$"
audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/acouagent-release-${suffix}-XXXXXX")"
gateway_image="local/acouagent-release-gateway:${suffix}"
executor_image="local/acouagent-release-executor:${suffix}"
container="acouagent-release-${suffix}"
volume="acouagent_release_${suffix//-/_}"
port="${RELEASE_AUDIT_PORT:-18091}"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  docker image rm "$gateway_image" >/dev/null 2>&1 || true
  docker image rm "$executor_image" >/dev/null 2>&1 || true
  rm -rf "$audit_dir"
}
trap cleanup EXIT

git -C "$root" archive "$sha" | tar -x -C "$audit_dir"
chmod 755 "$audit_dir"
cd "$audit_dir"

required=(
  README.md AGENTS.md compose.yaml Dockerfile .env.example
  requirements.lock.txt requirements.dev.lock.txt
  executor/Dockerfile executor/executor.py executor/validation_baseline.py
  app/server_safe.py app/tools.py app/skill_store.py
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "missing required release file: $path" >&2; exit 1; }
done

cp .env.example .env
python3 - <<'PY'
from pathlib import Path
p = Path('.env')
text = p.read_text(encoding='utf-8')
text = text.replace('replace-with-at-least-32-random-characters', 'a' * 40)
text = text.replace('replace-with-a-different-32-character-secret', 'b' * 40)
p.write_text(text, encoding='utf-8')
PY

docker compose config --quiet
echo "snapshot_sha=$sha"
echo "required_files=ok"
echo "compose_config=ok"
if docker compose config | grep -q 'external: true'; then
  echo "external_network_dependency=present"
else
  echo "external_network_dependency=none"
fi

docker build --pull=false --build-arg INSTALL_DEV=true -t "$gateway_image" .
docker volume create "$volume" >/dev/null
docker run -d --rm --name "$container" \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e MCP_AUTH_TOKEN="$(printf 'a%.0s' {1..40})" \
  -e EXECUTOR_SHARED_TOKEN="$(printf 'b%.0s' {1..40})" \
  -v "$volume:/app/data" -p "127.0.0.1:${port}:8001" "$gateway_image" >/dev/null

healthy=false
for _ in $(seq 1 30); do
  if [[ "$(curl -fsS "http://127.0.0.1:${port}/health" 2>/dev/null || true)" == ok ]]; then
    healthy=true
    break
  fi
  sleep 1
done
if [[ "$healthy" != true ]]; then
  docker logs "$container" >&2 || true
  exit 1
fi
echo "gateway_health=ok"
docker rm -f "$container" >/dev/null

docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=2g \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e MCP_AUTH_TOKEN="$(printf 'a%.0s' {1..40})" \
  -e EXECUTOR_SHARED_TOKEN="$(printf 'b%.0s' {1..40})" \
  -v "$audit_dir:/src:ro" -w /src "$gateway_image" \
  python -m pytest -q -p no:cacheprovider \
    tests/unit/test_skill_learning_pipeline.py \
    tests/unit/test_candidate_intake.py \
    tests/unit/test_skill_local_profile.py
echo "focused_tests=ok"

if [[ "$with_executor" == true ]]; then
  docker build --pull=false -t "$executor_image" executor
  docker run --rm --network none --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=1g \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -v "$audit_dir/executor/validation_baseline.py:/tmp/validation_baseline.py:ro" \
    "$executor_image" python /tmp/validation_baseline.py
  echo "executor_val1=ok"
fi
