#!/usr/bin/env bash
# Boot sequence for the hosted live-questions server (run by systemd: live.service).
#
#   1. update the code (git pull main) and dependencies
#   2. write .env: settings from /etc/live/config.env, secrets from SSM (mode 600)
#   3. start the lakehouse (Docker, hosted overrides) and bootstrap the warehouse
#   4. build the semantic layer; load the data on first boot, else refresh it in
#      the background when it's more than REFRESH_AFTER_DAYS old
#   5. start the live API on localhost, open a Cloudflare quick tunnel to it,
#      and publish the tunnel URL + "ready" to SSM only once the API answers
#      through the tunnel
#
# Progress goes to SSM ($PARAM_PREFIX/stage) so the website can show it.
# If anything fails, the stage says so and the watchdog stops the server.
set -euo pipefail

source /etc/live/config.env  # REPO_DIR, PARAM_PREFIX, AWS_REGION, ... (written by Terraform user-data)
cd "$REPO_DIR"
STATE_DIR=/var/lib/live
API_PORT=8787
REFRESH_AFTER_DAYS=3

put() { aws ssm put-parameter --region "$AWS_REGION" --name "$PARAM_PREFIX/$1" --value "$2" --type String --overwrite >/dev/null; }
secret() { aws ssm get-parameter --region "$AWS_REGION" --name "$PARAM_PREFIX/$1" --with-decryption --query Parameter.Value --output text; }
stage() { echo "[stage] $1"; put stage "$1"; }
trap 'stage "failed"; put url "-"' ERR

stage "updating code"
git fetch --quiet origin main && git reset --quiet --hard origin/main
uv sync --frozen --quiet

stage "writing settings"
oe_key=$(secret openelectricity-key)
ts_secret=$(secret turnstile-secret)
if [[ "$oe_key" == set-me || "$ts_secret" == set-me ]]; then
  # Secrets are set with the AWS CLI after `terraform apply`; until then, stop cleanly.
  trap - ERR
  stage "missing secrets"
  exit 0
fi
owner_pass=$(secret owner-pass 2>/dev/null || echo set-me)  # optional: off until you set it
umask 077
{
  cat /etc/live/config.env
  echo "OPENELECTRICITY_API_KEY=$oe_key"
  echo "DEMO_TURNSTILE_SECRET=$ts_secret"
  echo "DEMO_TURNSTILE_SITE_KEY=$(secret turnstile-site-key)"
  [[ "$owner_pass" == set-me ]] || echo "DEMO_OWNER_PASS=$owner_pass"
} > .env
set -a; source .env; set +a

stage "starting the lakehouse"
docker compose -f docker/compose.yaml -f docker/compose.hosted.yaml up -d --wait --quiet-pull
uv run python scripts/bootstrap_catalog.py

stage "building the semantic layer"
grep -q agent-analytics-trino ~/.wren/profiles.yml 2>/dev/null \
  || uv run wren profile add agent-analytics-trino --from-file wren/profile.yml --activate --no-validate
(cd wren && uv run wren context build && uv run wren memory index)

refresh_data() {
  uv run python scripts/fetch_energy_data.py && date -u +%s > "$STATE_DIR/data-loaded-at"
}
loaded_at=$(cat "$STATE_DIR/data-loaded-at" 2>/dev/null || echo 0)
if [[ "$loaded_at" == 0 ]]; then
  stage "loading data (first boot)"
  refresh_data
elif (( $(date -u +%s) - loaded_at > REFRESH_AFTER_DAYS * 86400 )); then
  (refresh_data > "$STATE_DIR/refresh.log" 2>&1 || echo "background refresh failed" >&2) &
fi

stage "starting the agent"
uv run python -m uvicorn api.server:build_app --factory --app-dir src \
  --host 127.0.0.1 --port "$API_PORT" --log-level warning &
api_pid=$!
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$API_PORT/api/health" >/dev/null && break; sleep 2; done
curl -fs "http://127.0.0.1:$API_PORT/api/health" >/dev/null

stage "opening the tunnel"
cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$API_PORT" > "$STATE_DIR/tunnel.log" 2>&1 &
tunnel_pid=$!
url=""
for _ in $(seq 60); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$STATE_DIR/tunnel.log" | head -1 || true)
  [[ -n "$url" ]] && curl -fs "$url/api/health" >/dev/null && break
  url=""; sleep 2
done
[[ -n "$url" ]]

put url "$url"
date -u +%s > "$STATE_DIR/ready-at"
stage "ready"

# Stay up while both run; if either dies, stop advertising the address.
wait -n "$api_pid" "$tunnel_pid" || true
put url "-"
stage "failed"
exit 1
