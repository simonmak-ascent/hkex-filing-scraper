#!/usr/bin/env bash
# kind: static — build an MkDocs site (docs-native app) and serve it behind Caddy
# at /docs, with an optional MCP/REST python service in blue/green slots.
#
# Manifest keys (app): docs.build, docs.dir, docs.path (default /docs);
#                      python.* (see lib.sh), routes.python; tls.*; health[].
#
# Verbs: build | deploy <slot> | switch <slot> | preview <ts> | rollback [failed-slot] | smoke
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

REPO_DIR="${REPO_DIR:-$PWD}"
PROD_FRAG="$CADDY_CONF_DIR/${APP}.caddy"
PREVIEW_FRAG="$CADDY_CONF_DIR/${APP}-preview.caddy"
ACTIVE_FILE="$APP_ROOT/active-slot"
# Route the python health probe through the app's own health path (manifest
# `python.health` when the app names one).
PY_HEALTH_PATH="${PY_HEALTH_PATH:-$(mf python.health 2>/dev/null || echo /api/health)}"

cmd_build() {
  log "build docs for $APP in $REPO_DIR"
  docs_build
  log "docs build ok"
  if py_has; then py_build; fi
}

cmd_deploy() { # <slot>  (static: publish docs; python slot optional)
  local slot="$1"
  mkdir -p "$APP_ROOT" "/etc/$APP" "$CADDY_CONF_DIR"
  remember_slot "$(active_slot)"
  docs_publish
  if py_has && [ -n "$(py_slot_port "$slot" 2>/dev/null || true)" ]; then py_deploy "$slot"; fi
  cmd_health "$slot"
}

# A candidate slot must answer on its own port before traffic moves to it, so a
# broken release fails the deploy instead of taking the node down.
cmd_health() { # <slot>
  local slot="$1" port code i
  [ -f "/srv/sites/$APP/docs/index.html" ] || die "docs not published at /srv/sites/$APP/docs"
  if py_has; then
    port="$(py_slot_port "$slot")"
    # `py_deploy` restarts the slot, and a WSGI app needs a moment to bind; poll
    # instead of failing on the first connection refused.
    code=000
    for i in $(seq 1 15); do
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${port}${PY_HEALTH_PATH}" || echo 000)"
      [ "$code" = "200" ] && break
      sleep 2
    done
    [ "$code" = "200" ] || die "health FAIL python :$port$PY_HEALTH_PATH -> $code after 30s"
    log "health OK python :$port$PY_HEALTH_PATH"
  fi
  log "health OK docs $APP"
}

cmd_switch() { # <slot>
  write_app_vhost "$PROD_FRAG" "$1" static
  if py_has && [ -n "$(py_slot_port "$1" 2>/dev/null || true)" ]; then py_restart_slot "$1"; fi
  caddy_reload
  printf '%s\n' "$1" > "$ACTIVE_FILE"
  log "prod docs /docs${py_has:+ · py :$(py_slot_port "$1" 2>/dev/null || echo -)}"
}

cmd_preview() { # <ts>
  local ts="$1" ph; ph="$(mf preview_host | sed "s/{{ts}}/$ts/")"
  docs_publish
  local api="" pyport=""
  if py_has; then
    py_deploy blue 2>/dev/null || true   # preview reuses the blue python (no preview slot)
    pyport="$(py_slot_port blue 2>/dev/null || true)"
    [ -n "$pyport" ] && api="$(printf '\thandle /api/* {\n\t\treverse_proxy 127.0.0.1:%s\n\t}' "$pyport")"
  fi
  local cert key; cert="$(mf tls.cert)"; key="$(mf tls.key)"
  cat <<CADDY | write_file_atomic "$PREVIEW_FRAG"
$ph {
	encode zstd gzip
	tls $cert $key
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "SAMEORIGIN"
		Referrer-Policy "strict-origin-when-cross-origin"
		X-Robots-Tag "noindex, nofollow"
		X-Request-Id "{http.request.uuid}"
	}
	handle_path /docs/* {
		root * /srv/sites/$APP/docs
		file_server
	}
$api
	handle {
		@docs_mount path / /docs
		redir @docs_mount /docs/ 302
		respond "<!doctype html><meta charset=utf-8><title>Not found</title><p>Not found. See <a href=/docs/>the documentation</a>.</p>" 404
	}
$(emit_site_extras)
}
CADDY
  caddy_reload
  echo "$ph"
}

cmd_rollback() { # [failed-slot] — return to the slot recorded before the deploy
  local target=""
  if [ -n "${1:-}" ]; then
    # Explicit failed slot: the pass that was being switched to never went live,
    # so the previous slot is simply the other one (works even when the deploy
    # job failed before it could record previous-slot).
    target="$(other_slot "$1")"
  else
    target="$(previous_slot)"
  fi
  [ -n "$target" ] || die "rollback: no previous slot recorded"
  # A node that never ran the previous slot (first deploy after joining the
  # fleet) has nothing to go back to; keep whatever is live rather than point
  # the vhost at a port with no service.
  if py_has && [ ! -d "$(py_work "$target")" ]; then
    log "rollback: slot $target was never deployed on $APP_NODE; leaving $(active_slot) live"
    return 0
  fi
  cmd_switch "$target"
  cmd_smoke
  log "rolled back to $target"
}

# probe_public <path> -> last HTTP status, polling while Caddy reloads.
# The vhost answers 301 on :80 (auto-HTTPS redirect), so a probe on :80 can never
# see the docs' own status. Probe the public vhost instead: the same path a user
# takes, and the only one that proves the site is reachable end to end.
probe_public() {
  local path="$1" code=000 i
  for i in $(seq 1 10); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://${DOMAIN}${path}" || echo 000)"
    [ "$code" = "200" ] && break
    sleep 2
  done
  printf '%s' "$code"
}

# probe_redirect <path> -> "<status> <location>", polling while Caddy reloads.
probe_redirect() {
  local path="$1" code=000 loc="" i
  for i in $(seq 1 10); do
    read -r code loc < <(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' \
      --max-time 8 "https://${DOMAIN}${path}" || echo "000 ")
    [ "$code" != "000" ] && break
    sleep 2
  done
  printf '%s %s' "$code" "$loc"
}

cmd_smoke() {
  local slot port code loc file
  slot="$(active_slot)"
  code="$(probe_public /docs/)"
  [ "$code" = "200" ] || die "smoke FAIL https://$DOMAIN/docs/ -> $code after 20s"

  # Discovery files must answer at the root. robots.txt and sitemap.xml are what
  # crawlers fetch; llms.txt and llms-full.txt are what agents fetch. All four were
  # silently unreachable for a while because the catch-all swallowed them, and
  # `/docs/` returning 200 did not notice.
  for file in /robots.txt /sitemap.xml /llms.txt /llms-full.txt; do
    code="$(probe_public "$file")"
    [ "$code" = "200" ] || die "smoke FAIL https://$DOMAIN$file -> $code after 20s"
  done

  # `/` and `/docs` are permanent aliases for the mount; a 302 would call a fixed
  # structure temporary and pass no link equity.
  for path in / /docs; do
    read -r code loc <<EOF
$(probe_redirect "$path")
EOF
    [ "$code" = "301" ] || die "smoke FAIL https://$DOMAIN$path -> $code (expected 301)"
    case "$loc" in
      */docs/) ;;
      *) die "smoke FAIL https://$DOMAIN$path -> 301 to '$loc' (expected .../docs/)" ;;
    esac
  done

  # Anything else is a genuine miss. If this returns 200 or 301 the catch-all is
  # redirecting the whole site again, which is the soft-404 regression above.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
    "https://${DOMAIN}/__smoke-not-a-page" || echo 000)"
  [ "$code" = "404" ] || die "smoke FAIL https://$DOMAIN/__smoke-not-a-page -> $code (expected 404)"

  if py_has; then
    port="$(py_slot_port "$slot")"
    code="$(probe_public "$PY_HEALTH_PATH")"
    [ "$code" = "200" ] || die "smoke FAIL https://$DOMAIN$PY_HEALTH_PATH -> $code (slot $slot :$port)"
  fi
  log "smoke OK $DOMAIN (/docs/, discovery files, redirects, 404) (slot $slot)"
}

verb="${1:?verb required}"; shift || true
case "$verb" in
  build) cmd_build ;;
  deploy) cmd_deploy "$@" ;;
  switch) cmd_switch "$@" ;;
  preview) cmd_preview "$@" ;;
  rollback) cmd_rollback "$@" ;;
  smoke) cmd_smoke "$@" ;;
  *) die "unknown verb: $verb" ;;
esac
