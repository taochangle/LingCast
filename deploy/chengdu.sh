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
# The normal deployment collects in the local visible browser (server IP is restricted).
# An explicitly selected server collector still gets a browser launch preflight.
if test "${XHS_COLLECTOR_LOCATION:-local}" = server; then
# Complete browser download and launch preflight before stopping live services.
(cd worker && /data1/taoxin/.local/bin/uv run --frozen --no-default-groups --group resident-runtime playwright install chromium --only-shell)
(cd worker && /data1/taoxin/.local/bin/uv run --frozen --no-default-groups --group resident-runtime python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()")
fi
"${compose[@]}" build --build-arg "GIT_REVISION=$revision" api-admin api-live api-scheduler frontend-admin
# Models stay private. Prepare the resident services before switching the live worker.
if grep -q '^LIPSYNC_ENGINE=musetalk$' worker/.env.local; then
  cp deploy/lingcast-cosyvoice.service deploy/lingcast-musetalk.service deploy/lingcast-stream.service "$HOME/.config/systemd/user/"
  systemctl --user daemon-reload
  systemctl --user enable lingcast-cosyvoice lingcast-musetalk
  systemctl --user restart lingcast-cosyvoice lingcast-musetalk
  (cd worker && /data1/taoxin/.local/bin/uv run --frozen --no-default-groups --group resident-runtime python wait_resident_ready.py)
fi
# Stop only after all builds succeed. Queued narration remains in Redis.
systemctl --user stop lingcast-stream
"${compose[@]}" up -d mysql redis rustfs srs api-admin api-live api-scheduler frontend-admin
cmp -s worker/resident_service.py /data1/taoxin/lingcast-resident/resident_service.py || {
  cp worker/resident_service.py /data1/taoxin/lingcast-resident/resident_service.py
  systemctl --user restart lingcast-indextts lingcast-latentsync
}
(cd worker && /data1/taoxin/.local/bin/uv sync --frozen --no-default-groups --group resident-runtime)
mkdir -p "$HOME/.config/systemd/user"
cp deploy/lingcast-xhs-collector.service "$HOME/.config/systemd/user/lingcast-xhs-collector.service"
systemctl --user daemon-reload
if test "${XHS_COLLECTOR_LOCATION:-local}" = server; then
  systemctl --user enable lingcast-xhs-collector
  systemctl --user restart lingcast-xhs-collector
else
  systemctl --user disable --now lingcast-xhs-collector
fi
systemctl --user start lingcast-stream
for service in api-admin api-live api-scheduler frontend-admin; do
  container=$("${compose[@]}" ps -q "$service")
  actual=$(docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$container")
  test "$actual" = "$revision" || { echo "Revision mismatch: $service" >&2; exit 1; }
done
mkdir -p runtime
echo "$revision" > runtime/deployed-revision
echo "Deployed revision $revision"
