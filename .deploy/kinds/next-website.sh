#!/usr/bin/env bash
# kind: next-website — build a Next.js standalone app and run it in systemd slots
# behind Caddy, with a per-PR preview slot. App-generic (driven by the manifest).
#
# Optional extras (manifest): `python:` -> an MCP/REST service in blue/green slots;
# `docs:` -> mkdocs published to /srv/sites/<app>/docs and served at /docs;
# `routes.python:` -> Caddy path matchers routed to the python service.
#
# Verbs: build | deploy <slot> | switch <slot> | preview <ts> | rollback | smoke [port] [host]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

REPO_DIR="${REPO_DIR:-$PWD}"          # checkout of the app repo (set by the workflow)
UNIT_TMPL="/etc/systemd/system/${APP}@.service"
ENVDIR="/etc/$APP"
PROD_FRAG="$CADDY_CONF_DIR/${APP}.caddy"
PREVIEW_FRAG="$CADDY_CONF_DIR/${APP}-preview.caddy"
ACTIVE_FILE="$APP_ROOT/active-slot"

ensure_layout() {
  mkdir -p "$APP_ROOT" "$ENVDIR" "$CADDY_CONF_DIR"
  local s
  for s in $(mf "slots" | python3 -c "import json,sys;print(' '.join(json.load(sys.stdin).keys()))"); do
    printf 'PORT=%s\n' "$(slot_port "$s")" > "$ENVDIR/$s.env"
  done
}

write_unit() { # app-generic template unit; %i = slot
  cat > "$UNIT_TMPL" <<UNIT
[Unit]
Description=${APP} (Next.js standalone) slot %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_ROOT/%i
Environment=NODE_ENV=production
Environment=HOSTNAME=0.0.0.0
Environment=NEXT_TELEMETRY_DISABLED=1
EnvironmentFile=$ENVDIR/%i.env
ExecStart=/usr/bin/node server.js
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
}

cmd_build() {
  log "build $APP in $REPO_DIR"
  ( cd "$REPO_DIR" && eval "$(mf build)" )
  # assemble the standalone bundle (Next does not copy these)
  local sd="$REPO_DIR/$(mf artifact)"
  mkdir -p "$sd/.next"
  rm -rf "$sd/.next/static" "$sd/public"
  cp -r "$REPO_DIR/.next/static" "$sd/.next/static"
  [ -d "$REPO_DIR/public" ] && cp -r "$REPO_DIR/public" "$sd/public" || mkdir -p "$sd/public"
  log "build ok -> $sd"
  py_has && py_build
  mf docs >/dev/null 2>&1 && docs_build
}

cmd_deploy() { # <slot>
  local slot="$1" dest="$APP_ROOT/$1"
  ensure_layout; write_unit
  mf docs >/dev/null 2>&1 && docs_publish
  rm -rf "$dest"; mkdir -p "$dest"
  cp -a "$REPO_DIR/$(mf artifact)/." "$dest/"
  systemctl enable --now "${APP}@${slot}" >/dev/null 2>&1 || true
  systemctl restart "${APP}@${slot}"
  sleep 3
  health_check "$(slot_port "$slot")" "$DOMAIN"
  # python service (skip when this slot has no python slot, e.g. preview)
  if py_has && [ -n "$(py_slot_port "$slot" 2>/dev/null || true)" ]; then py_deploy "$slot"; fi
}

cmd_switch() { # <slot>  (prod)
  write_app_vhost "$PROD_FRAG" "$1" next
  if py_has && [ -n "$(py_slot_port "$1" 2>/dev/null || true)" ]; then py_restart_slot "$1"; fi
  caddy_reload
  printf '%s\n' "$1" > "$ACTIVE_FILE"
  log "prod -> $1 (next :$(slot_port "$1")${py_has:+ py :$(py_slot_port "$1" 2>/dev/null || echo -)})"
}

cmd_preview() { # <ts>
  local ts="$1" ph; ph="$(mf preview_host | sed "s/{{ts}}/$ts/")"
  cmd_deploy preview
  mf docs >/dev/null 2>&1 && docs_publish
  if py_has; then py_deploy blue 2>/dev/null || true; fi   # preview reuses blue python
  local cert key; cert="$(mf tls.cert)"; key="$(mf tls.key)"
  {
    printf '%s {\n' "$ph"
    printf '\tencode zstd gzip\n\ttls %s %s\n' "$cert" "$key"
    if mf docs >/dev/null 2>&1; then
      printf '\thandle_path /docs/* {\n\t\troot * /srv/sites/%s/docs\n\t\tfile_server\n\t}\n' "$APP"
    fi
    py_has && py_caddy_handles blue
    printf '\thandle {\n\t\treverse_proxy 127.0.0.1:%s\n\t}\n}\n' "$(slot_port preview)"
  } > "$PREVIEW_FRAG"
  caddy_reload
  echo "$ph"
}

cmd_rollback() {
  local cur prev
  cur="$(cat "$ACTIVE_FILE" 2>/dev/null || echo blue)"
  [ "$cur" = "blue" ] && prev=green || prev=blue
  cmd_switch "$prev"
  log "rolled back to $prev"
}

cmd_smoke() { health_check "${1:-$(slot_port "$(cat "$ACTIVE_FILE" 2>/dev/null || echo blue)")}" "${2:-$DOMAIN}"; }

verb="${1:?verb required}"; shift || true
case "$verb" in
  build) cmd_build ;;
  deploy) cmd_deploy "$@" ;;
  switch) cmd_switch "$@" ;;
  preview) cmd_preview "$@" ;;
  rollback) cmd_rollback ;;
  smoke) cmd_smoke "$@" ;;
  *) die "unknown verb: $verb" ;;
esac
