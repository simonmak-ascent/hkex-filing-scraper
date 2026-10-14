#!/usr/bin/env bash
# kind: static — build an MkDocs site (docs-native app) and serve it behind Caddy
# at /docs, with an optional MCP/REST python service in blue/green slots.
#
# Manifest keys (app): docs.build, docs.dir, docs.path (default /docs);
#                      python.* (see lib.sh), routes.python; tls.*; health[].
#
# Verbs: build | deploy <slot> | switch <slot> | preview <ts> | rollback | smoke [port] [host]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

REPO_DIR="${REPO_DIR:-$PWD}"
PROD_FRAG="$CADDY_CONF_DIR/${APP}.caddy"
PREVIEW_FRAG="$CADDY_CONF_DIR/${APP}-preview.caddy"
ACTIVE_FILE="$APP_ROOT/active-slot"

cmd_build() {
  log "build docs for $APP in $REPO_DIR"
  docs_build
  log "docs build ok"
  py_has && py_build
}

cmd_deploy() { # <slot>  (static: publish docs; python slot optional)
  local slot="$1"
  mkdir -p "$APP_ROOT" "/etc/$APP" "$CADDY_CONF_DIR"
  docs_publish
  if py_has && [ -n "$(py_slot_port "$slot" 2>/dev/null || true)" ]; then py_deploy "$slot"; fi
}

cmd_switch() { # <slot>
  write_app_vhost "$PROD_FRAG" "$1" static
  if py_has && [ -n "$(py_slot_port "$1" 2>/dev/null || true)" ]; then systemctl restart "${APP}-mcp@$1"; fi
  caddy_reload
  printf '%s\n' "$1" > "$ACTIVE_FILE"
  log "prod docs /docs${py_has:+ · py :$(py_slot_port "$1" 2>/dev/null || echo -)}"
}

cmd_preview() { # <ts>
  local ts="$1" ph; ph="$(mf preview_host | sed "s/{{ts}}/$ts/")"
  docs_publish
  if py_has; then py_deploy "${2:-blue}" 2>/dev/null || true; fi
  local cert key; cert="$(mf tls.cert)"; key="$(mf tls.key)"
  cat > "$PREVIEW_FRAG" <<CADDY
$ph {
	encode zstd gzip
	tls $cert $key
	handle_path /docs/* {
		root * /srv/sites/$APP/docs
		file_server
	}
	handle {
		redir /docs 302
	}
}
CADDY
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

cmd_smoke() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -H "Host: $DOMAIN" "http://127.0.0.1:$(py_slot_port "$(cat "$ACTIVE_FILE" 2>/dev/null || echo blue)" 2>/dev/null || echo 8040)/api/healthz" || echo 000)"
  log "smoke ${DOMAIN}/api/healthz -> $code"
}

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
