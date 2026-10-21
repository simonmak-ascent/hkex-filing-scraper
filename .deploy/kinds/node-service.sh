#!/usr/bin/env bash
# kind: node-service — a long-running Node HTTP service (MCP/REST) run in systemd
# blue/green slots behind Caddy, with a per-PR preview slot. App-generic
# (driven by the manifest).
#
# Manifest keys (app):
#   build    — build command run in the repo checkout
#   include  — paths copied into the release dir (default: dist public server.mjs
#              package.json pnpm-lock.yaml)
#   install  — prod-dependency install run in the release dir
#              (default: corepack pnpm install --prod --frozen-lockfile)
#   entry    — entry file ExecStart runs (default: server.mjs); reads $PORT
#   slots / health[] / tls.* / preview_host / keep_releases — as for the other kinds
#
# Verbs: build | deploy <slot> | switch <slot> | preview <ts> | rollback | smoke [port] [host]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

REPO_DIR="${REPO_DIR:-$PWD}"
UNIT_TMPL="/etc/systemd/system/${APP}@.service"
ENVDIR="/etc/$APP"
PROD_FRAG="$CADDY_CONF_DIR/${APP}.caddy"
PREVIEW_FRAG="$CADDY_CONF_DIR/${APP}-preview.caddy"
ACTIVE_FILE="$APP_ROOT/active-slot"
DEFAULT_INCLUDE='["dist","public","server.mjs","package.json","pnpm-lock.yaml"]'

ensure_layout() {
  mkdir -p "$APP_ROOT" "$ENVDIR" "$CADDY_CONF_DIR"
  local s
  for s in $(mf "slots" | python3 -c "import json,sys;print(' '.join(json.load(sys.stdin).keys()))"); do
    # Never clobber operator-added env (secrets/config): ensure the file exists
    # and keep PORT in step with the manifest, leaving other keys untouched.
    touch "$ENVDIR/$s.env"
    if grep -q '^PORT=' "$ENVDIR/$s.env"; then
      sed -i "s|^PORT=.*|PORT=$(slot_port "$s")|" "$ENVDIR/$s.env"
    else
      printf 'PORT=%s\n' "$(slot_port "$s")" >> "$ENVDIR/$s.env"
    fi
  done
}

write_unit() { # app-generic template unit; %i = slot
  if [ -f "$UNIT_TMPL" ]; then return 0; fi
  cat > "$UNIT_TMPL" <<UNIT
[Unit]
Description=${APP} (node-service) slot %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_ROOT/%i
Environment=NODE_ENV=production
EnvironmentFile=$ENVDIR/%i.env
ExecStart=/usr/bin/node $(mf entry 2>/dev/null || echo server.mjs)
Restart=always
RestartSec=5
User=${APP_USER}

[Install]
WantedBy=multi-user.target
UNIT
  as_root systemctl daemon-reload
}

cmd_build() {
  log "build $APP in $REPO_DIR"
  ( cd "$REPO_DIR" && eval "$(mf build)" )
  log "build ok"
}

cmd_deploy() { # <slot>
  local slot="$1" dest="$APP_ROOT/$1" inc
  ensure_layout || die "ensure_layout failed for $slot"
  write_unit || die "write_unit failed for $slot"
  inc="$(mf include 2>/dev/null || echo "$DEFAULT_INCLUDE")"
  log "deploy $slot: stage release ($inc)"
  rm -rf "$dest" || die "could not clear $dest"
  mkdir -p "$dest" || die "could not create $dest"
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    if [ -e "$REPO_DIR/$p" ]; then cp -a "$REPO_DIR/$p" "$dest/"; else log "  (skip missing $p)"; fi
  done < <(python3 -c "import json,sys;[print(p) for p in json.loads(sys.argv[1])]" "$inc")
  log "deploy $slot: install prod deps"
  ( cd "$dest" && eval "$(mf install 2>/dev/null || echo 'corepack pnpm install --prod --frozen-lockfile')" )
  log "deploy $slot: restart unit ${APP}@${slot}"
  as_root systemctl enable --now "${APP}@${slot}" >/dev/null 2>&1 || true
  as_root systemctl restart "${APP}@${slot}" || die "as_root systemctl restart ${APP}@${slot} failed"
  sleep 3
  health_check "$(slot_port "$slot")" "$DOMAIN" || die "health check failed for $slot on :$(slot_port "$slot")"
}

cmd_switch() { # <slot>  (prod)
  write_app_vhost "$PROD_FRAG" "$1" node
  caddy_reload
  printf '%s\n' "$1" > "$ACTIVE_FILE"
  log "prod -> $1 (node :$(slot_port "$1"))"
}

cmd_preview() { # <ts>
  local ts="$1" ph; ph="$(mf preview_host | sed "s/{{ts}}/$ts/")"
  cmd_deploy preview
  local cert key; cert="$(mf tls.cert)"; key="$(mf tls.key)"
  {
    printf '%s {\n' "$ph"
    printf '\tencode zstd gzip\n\ttls %s %s\n' "$cert" "$key"
    printf '\theader {\n\t\tStrict-Transport-Security "max-age=31536000; includeSubDomains"\n\t\tX-Content-Type-Options "nosniff"\n\t\tX-Frame-Options "SAMEORIGIN"\n\t\tReferrer-Policy "strict-origin-when-cross-origin"\n\t\tX-Robots-Tag "noindex, nofollow"\n\t\tX-Request-Id "{http.request.uuid}"\n\t}\n'
    printf '\thandle {\n\t\treverse_proxy 127.0.0.1:%s\n\t}\n' "$(slot_port preview)"
    emit_site_extras
    printf '}\n'
  } | write_file_atomic "$PREVIEW_FRAG"
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
