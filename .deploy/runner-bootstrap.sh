#!/usr/bin/env bash
# Register a self-hosted GitHub Actions runner on this node (run as root).
#
#   RUNNER_TOKEN=<token> [RUNNER_USER=deploy] ./runner-bootstrap.sh <owner/repo> [labels]
#
# The registration token is short-lived; mint it on the workstation with:
#   gh api -X POST repos/<owner/repo>/actions/runners/registration-token --jq .token
# (A separate runner per repo is required because simonmak-ascent is a *user*,
#  not an organization — org-level runners are not available.)
#
# RUNNER_USER (default deploy) is the account the runner job runs as. The deploy
# handlers elevate only the systemctl/caddy calls through /etc/sudoers.d/apg-deploy,
# so a job must never need root for the workspace itself.
set -euo pipefail
REPO="${1:?usage: RUNNER_TOKEN=… runner-bootstrap.sh owner/repo [labels]}"
LABELS="${2:-apg-ecs-01}"
RUNNER_USER="${RUNNER_USER:-deploy}"
SLUG="$(printf '%s' "$REPO" | tr '/' '-')"
DIR="${RUNNER_DIR:-/opt/actions-runner-$SLUG}"
# Unique per repo (a runner name must be unique within the repo set; the previous
# HOSTNAME-LABELS form collided across repos and --replace nuked the other one).
NAME="${HOSTNAME}-${LABELS}-${SLUG##*-}"

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
: "${RUNNER_TOKEN:?RUNNER_TOKEN env required (repo registration token)}"
id "$RUNNER_USER" >/dev/null 2>&1 || { echo "no such user: $RUNNER_USER" >&2; exit 1; }

VER="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
        | python3 -c 'import json,sys;print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"

mkdir -p "$DIR"
if [ ! -x "$DIR/config.sh" ]; then
  echo "[runner] downloading actions-runner v$VER"
  curl -fsSL -o /tmp/runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${VER}/actions-runner-linux-x64-${VER}.tar.gz"
  tar -xzf /tmp/runner.tar.gz -C "$DIR"
  [ -f "$DIR/bin/installdependencies.sh" ] && "$DIR/bin/installdependencies.sh" || true
fi
chown -R "$RUNNER_USER:$RUNNER_USER" "$DIR"

cd "$DIR"
if [ "$RUNNER_USER" = root ]; then
  export RUNNER_ALLOW_RUNASROOT=1   # root runner: no sudo, can manage systemd/caddy directly
  ./config.sh --url "https://github.com/${REPO}" --token "$RUNNER_TOKEN" \
    --name "$NAME" --labels "$LABELS" --work "_work" --unattended --replace
  ./svc.sh install root
else
  # Non-root: the runner's own workspace is its home; privileged calls go through
  # sudo (see /etc/sudoers.d/apg-deploy), so config must be done as that user.
  su -s /bin/bash "$RUNNER_USER" -c "cd '$DIR' && ./config.sh --url https://github.com/${REPO} \
      --token '${RUNNER_TOKEN}' --name '${NAME}' --labels '${LABELS}' --work _work --unattended --replace"
  ./svc.sh install "$RUNNER_USER"
fi
./svc.sh start
sleep 2
./svc.sh status || true
echo "[runner] registered: $REPO as '$NAME' labels=[self-hosted,linux,x64,$LABELS] user=$RUNNER_USER"
