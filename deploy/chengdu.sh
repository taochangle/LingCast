#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
revision=$(git rev-parse HEAD)
if test -n "$(git status --porcelain --untracked-files=no)"; then
  echo 'Refusing deployment from modified tracked sources' >&2
  exit 1
fi
compose=(docker compose -p lingcast-chengdu -f docker-compose.yml -f deploy/chengdu.override.yml)
"${compose[@]}" config --quiet
"${compose[@]}" build --build-arg "GIT_REVISION=$revision" api-admin api-live api-scheduler frontend-admin
# Stop only after all builds succeed. Queued narration remains in Redis.
systemctl --user stop lingcast-stream
"${compose[@]}" up -d mysql redis rustfs srs api-admin api-live api-scheduler frontend-admin
cmp -s worker/resident_service.py /data1/taoxin/lingcast-resident/resident_service.py || {
  cp worker/resident_service.py /data1/taoxin/lingcast-resident/resident_service.py
  systemctl --user restart lingcast-indextts lingcast-latentsync
}
(cd worker && /data1/taoxin/.local/bin/uv sync --frozen --no-default-groups --group resident-runtime)
systemctl --user start lingcast-stream
for service in api-admin api-live api-scheduler frontend-admin; do
  container=$("${compose[@]}" ps -q "$service")
  actual=$(docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$container")
  test "$actual" = "$revision" || { echo "Revision mismatch: $service" >&2; exit 1; }
done
mkdir -p runtime
echo "$revision" > runtime/deployed-revision
echo "Deployed revision $revision"
