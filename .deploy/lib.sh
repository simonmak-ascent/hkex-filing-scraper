#!/usr/bin/env bash
# Shared helpers for the APG CD kind handlers. Sourced, not executed.
set -euo pipefail

: "${MANIFEST:?MANIFEST (path to apps.yaml) must be set}"
: "${APP:?APP (app name) must be set}"

# uv caches under $HOME/.cache by default, but the deploy user's HOME is not
# writable on every app node (ecs-02/03 carry HOME=/srv/apps), which aborted
# `uv pip install` during a fleet deploy. Pin the cache to the app root, which
# the deploy user owns on every node.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/opt/$APP/.cache/uv}"

log()  { printf '[cd%s] %s\n' "${NODE_LABEL:+:$NODE_LABEL}" "$*" >&2; }
die()  { printf '[cd] ERROR: %s\n' "$*" >&2; exit 1; }
as_root() { sudo -n "$@"; }
LAUNCHERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/launchers"

# mf <jq-ish dot path>  ->  print value from this app's manifest entry
# e.g. mf domain ; mf slots.blue ; mf tls.cert
mf() {
  python3 - "$MANIFEST" "$APP" "$1" <<'PY'
import sys, yaml
mf, app, path = sys.argv[1], sys.argv[2], sys.argv[3]
d = yaml.safe_load(open(mf))
apps = {a["name"]: a for a in d.get("apps", [])}
if app not in apps: sys.exit(f"app '{app}' not in manifest")
v = apps[app]
for k in path.split("."):
    v = v[k]
print("true" if v is True else "false" if v is False else (__import__("json").dumps(v) if isinstance(v, (list, dict)) else v))
PY
}

# mf_node <dot path>  ->  value from the node this run targets.
# The node is DEPLOY_NODE when set (one CI job per node), otherwise the app's
# `node:` key. `nodes:<list>` only describes the fleet; a handler still runs on
# one node and writes that node's own paths.
mf_node() {
  python3 - "$MANIFEST" "$APP" "$1" "${DEPLOY_NODE:-}" <<'PY'
import sys, yaml
mf, app, path, override = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
d = yaml.safe_load(open(mf))
a = {x["name"]: x for x in d["apps"]}[app]
node = override or a["node"]
if node not in d["nodes"]:
    sys.exit(f"node '{node}' not in manifest nodes")
v = d["nodes"][node]
for k in path.split("."):
    v = v[k]
print("true" if v is True else "false" if v is False else (__import__("json").dumps(v) if isinstance(v, (list, dict)) else v))
PY
}

# mf_nodes -> JSON array of the nodes this app is deployed to (defaults to [node])
mf_nodes() {
  python3 - "$MANIFEST" "$APP" <<'PY'
import json,sys,yaml
mf, app = sys.argv[1], sys.argv[2]
a = {x["name"]: x for x in yaml.safe_load(open(mf))["apps"]}[app]
print(json.dumps(a.get("nodes") or [a["node"]]))
PY
}

APP_USER="${APP_USER:-deploy}"
APP_NODE="${DEPLOY_NODE:-$(mf node)}"
APP_NODES="$(mf_nodes)"
NODE_LABEL="$APP_NODE"
SRV_ROOT="$(mf_node srv_root)"
APP_ROOT="$SRV_ROOT/$APP"
PREV_FILE="$APP_ROOT/previous-slot"     # slot that was live before the current deploy
CADDY_CONF_DIR="$(mf_node caddy_conf_dir)"
DOMAIN="$(mf domain)"
HEALTH_PATHS="$(mf health)"            # JSON array
KEEP_RELEASES="$(mf keep_releases 2>/dev/null || echo 3)"

slot_port() { mf "slots.$1"; }

active_slot() { cat "$APP_ROOT/active-slot" 2>/dev/null || echo blue; }
other_slot()  { [ "$1" = blue ] && echo green || echo blue; }
# Record the slot that was live before this deploy, so a rollback returns to it
# even when the switch itself failed half-way.
remember_slot() { mkdir -p "$APP_ROOT"; printf '%s\n' "$1" > "$PREV_FILE"; }
previous_slot() { cat "$PREV_FILE" 2>/dev/null || true; }

health_check() { # <port> <host> [paths-json]
  local port="$1" host="$2" paths="${3:-$HEALTH_PATHS}" p
  for p in $(python3 -c "import json,sys;print(' '.join(json.loads(sys.argv[1])))" "$paths"); do
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -H "Host: $host" "http://127.0.0.1:$port$p" || echo 000)"
    [ "$code" = "200" ] || { log "health FAIL $host$p on :$port -> $code"; return 1; }
  done
  log "health OK $host on :$port"
}

write_file_atomic() { # <dest> — never expose a partially-written Caddy fragment
  local dest="$1" tmp
  tmp="$(mktemp "${dest}.tmp.XXXXXX")"
  cat > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$dest"
}

caddy_reload() {
  as_root caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || die "invalid Caddy config; not reloading"
  # Use `restart`, not `reload`: the admin-API reload re-inits standard modules
  # but does NOT re-register the geoip2 handler's per-request replacer, so a
  # geo/routing change silently fails to take effect. Serialize restarts across
  # app repos: concurrent deploys otherwise race and fail each other's reload.
  exec 9>/run/lock/caddy-deploy.lock
  flock -w 120 9 || die "timed out waiting for the Caddy deploy lock"
  as_root systemctl restart caddy
}

keep_last_releases() { # <dir> <n>
  local dir="$1" n="${2:-3}"
  ls -1dt "$dir"/*/ 2>/dev/null | tail -n +"$((n+1))" | xargs -r rm -rf
}

# ── Python MCP/REST service (uv venv + systemd template <app>-mcp@.service) ──
# Manifest keys (app):
#   python.slots.{blue,green}  ports
#   python.package             install target relative to repo (default ".")
#   python.requirements         optional requirements file relative to repo
#   python.cmd                  launch, with {port} substituted (e.g.
#                               "fair-value-mcp --http --host 127.0.0.1 --port {port}")
py_has()        { mf python >/dev/null 2>&1; }
py_root()       { printf '/opt/%s' "$APP"; }
py_venv()       { printf '%s/venv' "$(py_root)"; }
py_work()       { printf '%s/work/%s' "$(py_root)" "$1"; }
py_unit()       { printf '/etc/systemd/system/%s-mcp@.service' "$APP"; }
py_envfile()    { printf '/etc/%s/mcp-%s.env' "$APP" "$1"; }
py_slot_port()  { mf "python.slots.$1"; }

py_build() { # create/refresh the venv + install deps and the package
  local root venv req pkg; root="$(py_root)"; venv="$(py_venv)"
  log "python build ($APP): venv $venv"
  mkdir -p "$root" "$UV_CACHE_DIR"
  [ -x "$venv/bin/python" ] || uv venv "$venv" >/dev/null
  req="$(mf python.requirements 2>/dev/null || true)"
  if [ -n "$req" ] && [ -f "$REPO_DIR/$req" ]; then
    uv pip install --python "$venv/bin/python" -r "$REPO_DIR/$req" >/dev/null
  fi
  local req pkg; req="$(mf python.requirements 2>/dev/null || true)"
  if [ -n "$req" ] && [ -f "$REPO_DIR/$req" ]; then
    uv pip install --python "$venv/bin/python" -r "$REPO_DIR/$req" >/dev/null
  fi
  pkg="$(mf python.package 2>/dev/null || echo .)"
  uv pip install --python "$venv/bin/python" "$REPO_DIR/$pkg" >/dev/null
  case "$(mf python.cmd 2>/dev/null || echo '')" in
    *gunicorn*) uv pip install --python "$venv/bin/python" gunicorn >/dev/null ;;
    *uvicorn*)  uv pip install --python "$venv/bin/python" uvicorn >/dev/null ;;
  esac
  if py_is_multi && mf python.endpoints 2>/dev/null | grep -q uvicorn; then
    uv pip install --python "$venv/bin/python" uvicorn >/dev/null
  fi
}

# ── python endpoints: one service per endpoint (ports = slot base + index) ────
# Manifest: python.endpoints = [{name, app, launcher?, health?, paths:[...]}, ...]
# Legacy single endpoint: python.cmd (+ routes.python) → name "api".
py_endpoints() { # TSV: name<TAB>index<TAB>cmd(with {port})
  if mf python.endpoints >/dev/null 2>&1; then
    python3 - "$(mf python.endpoints)" <<'PY'
import json,sys
for i,e in enumerate(json.loads(sys.argv[1])):
    la=e.get("launcher","pylaunch"); app=e["app"]
    if la=="uvicorn":
        cmd=f"uvicorn {app} --host 127.0.0.1 --port {{port}}"
    else:
        extra=f" --health {e['health']}" if e.get("health") else ""
        cmd=f"python {la}.py --app {app}{extra} --port {{port}}"
    print(f"{e['name']}\t{i}\t{cmd}")
PY
  else
    printf 'api\t0\t%s\n' "$(mf python.cmd)"
  fi
}
py_is_multi()  { mf python.endpoints >/dev/null 2>&1; }
py_unitname()  { if py_is_multi; then printf '%s-%s@.service' "$APP" "$1"; else printf '%s-mcp@.service' "$APP"; fi; }
py_envname()   { if py_is_multi; then printf '/etc/%s/mcp-%s-%s.env' "$APP" "$1" "$2"; else printf '/etc/%s/mcp-%s.env' "$APP" "$2"; fi; }

py_write_unit() { # <name> <cmd>
  local name="$1" cmd="$2"; cmd="${cmd//\{port\}/\$PORT}"
  local vbin; vbin="$(py_root)/venv/bin"
  local unit="/etc/systemd/system/$(py_unitname "$name")"
  if [ -f "$unit" ]; then return 0; fi
  cat > "$unit" <<UNIT
[Unit]
Description=${APP} python endpoint '$name' slot %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$(py_root)/work/%i
EnvironmentFile=-$(py_envname "$name" '%i')
# put the app venv first on PATH so \`python\` and the console scripts resolve
ExecStart=/bin/bash -lc 'export PATH=$vbin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin; set -a; . $(py_envname "$name" '%i'); set +a; cd $(py_root)/work/%i; exec $cmd'
Restart=always
RestartSec=5
User=${APP_USER}

[Install]
WantedBy=multi-user.target
UNIT
  as_root systemctl daemon-reload
}

py_deploy() { # <slot>
  local slot="$1" work; work="$(py_work "$slot")"
  mkdir -p "$work" "/etc/$APP"
  rsync -a --delete --exclude .git --exclude node_modules --exclude .next \
        --exclude .venv --exclude '__pycache__' --exclude '*.pyc' "$REPO_DIR/" "$work/"
  [ -d "$LAUNCHERS_DIR" ] && cp -a "$LAUNCHERS_DIR/." "$work/" || true
  local name idx cmd port svc base
  base="$(py_slot_port "$slot")"
  while IFS=$'\t' read -r name idx cmd; do
    [ -z "$name" ] && continue
    port=$(( base + idx ))
    printf 'PORT=%s\n' "$port" > "$(py_envname "$name" "$slot")"
    py_write_unit "$name" "$cmd"
    svc="$(py_unitname "$name")"; svc="${svc%@.service}@${slot}"
    as_root systemctl enable --now "$svc" >/dev/null 2>&1 || true
    as_root systemctl restart "$svc"
    log "python $APP/$name -> :$port ($svc)"
  done < <(py_endpoints)
}

py_restart_slot() { # <slot> — restart every python endpoint unit for a slot
  local name idx cmd svc
  while IFS=$'\t' read -r name idx cmd; do
    [ -z "$name" ] && continue
    svc="$(py_unitname "$name")"; svc="${svc%@.service}@$1"
    as_root systemctl restart "$svc" 2>/dev/null || true
  done < <(py_endpoints)
}

py_caddy_handles() { # <slot>  — emit a handle per python route (longest path first)
  local base; base="$(py_slot_port "$1" 2>/dev/null || py_slot_port blue)"
  if py_is_multi; then
    python3 - "$(mf python.endpoints)" "$base" <<'PY'
import json,sys
base=int(sys.argv[2]); items=[]
for i,e in enumerate(json.loads(sys.argv[1])):
    for p in e.get("paths",[]):
        items.append((p, base+i))
for p,port in sorted(items, key=lambda x:-len(x[0])):
    print(f"\thandle {p} {{\n\t\treverse_proxy 127.0.0.1:{port}\n\t}}")
PY
  else
    python3 -c "import json,sys;[print(p) for p in json.loads(sys.argv[1])]" "$(mf routes.python 2>/dev/null || echo '[]')" 2>/dev/null | while IFS= read -r pat; do
      [ -z "$pat" ] && continue
      printf '\thandle %s {\n\t\treverse_proxy 127.0.0.1:%s\n\t}\n' "$pat" "$base"
    done
  fi
}

# ── mkdocs static docs → /srv/sites/<app> ─────────────────────────────────────
# Manifest keys (app): docs.build (default "mkdocs build --strict"),
#                      docs.dir (default "site")
docs_build() {
  local b; b="$(mf docs.build 2>/dev/null || echo 'mkdocs build --strict')"
  local vpy; vpy="$(py_venv)/bin/python"
  if command -v mkdocs >/dev/null 2>&1; then
    ( cd "$REPO_DIR" && eval "$b" )
  elif [ -x "$vpy" ]; then
    # The app venv already has the package installed; mkdocstrings must be able
    # to import it, so add the docs toolchain there and run from the venv.
    uv pip install --python "$vpy" mkdocs-material 'mkdocstrings[python]' \
        pymdown-extensions mkdocs >/dev/null 2>&1 || true
    ( cd "$REPO_DIR" && PATH="$(py_venv)/bin:$PATH" eval "$b" )
  else
    # no system mkdocs and no app venv: run under uvx (material + pymdown + mkdocstrings).
    ( cd "$REPO_DIR" && uvx --from mkdocs-material --with 'mkdocstrings[python]' \
        --with pymdown-extensions --with mkdocs bash -lc "$b" )
  fi
}
docs_publish() {
  local dir; dir="$(mf docs.dir 2>/dev/null || echo site)"
  local dest="/srv/sites/$APP"
  mkdir -p "$dest"
  rm -rf "$dest/docs.tmp"; cp -a "$REPO_DIR/$dir" "$dest/docs.tmp"
  rm -rf "$dest/docs"; mv "$dest/docs.tmp" "$dest/docs"
  log "docs published -> $dest/docs"
}

# ── shared Caddy vhost wrapper (ordering proven on ecs-01) ────────────────────
# write_app_vhost <outfile> <slot> <frontend>
#   frontend = next   -> catch-all reverse_proxy 127.0.0.1:<next slot port>
#   frontend = static -> catch-all redir /docs 302 (docs-native app; docs at /docs)
# Emits: geo country header + docs (/docs) + python routes + coraza + rate_limit
# inside a catch-all `handle`; specific `handle`s for /docs and the python routes
# come first (Caddy `handle` blocks are mutually exclusive, first match wins).
# geoip2_vars runs at site level and resolves the visitor IP. For `next` apps the
# entry locale is decided in the app from text-first signals (cookie →
# Accept-Language → country); the vhost injects the country (`header_up
# X-Geo-Country`) so the app can use it, and keeps a country redirect that fires
# ONLY when the client states no language (belt-and-braces: the country fallback
# then never depends on the injected header surviving the proxy chain).
# IndexNow key: the key file (<key>.txt) is served at /<key>.txt on every app
# vhost so search engines that adopt IndexNow (Bing/Microsoft, Yandex, Seznam,
# Naver, …) can verify submissions.
INDEXNOW_KEY="${INDEXNOW_KEY:-5b6db8fc53dbad8e8a4e85d5baf92f19}"

# Response-hardening headers emitted by every vhost (production + preview).
# HSTS without `preload` (reversible); includeSubDomains is safe — every
# *.ascent-partners.com host is HTTPS-only behind Caddy.
# X-Request-Id carries Caddy's per-request id so a client can quote it in a
# report and it matches the `request_id` field appended to the access log.
emit_security_headers() {
  printf '\theader {\n'
  printf '\t\tStrict-Transport-Security "max-age=31536000; includeSubDomains"\n'
  printf '\t\tX-Content-Type-Options "nosniff"\n'
  printf '\t\tX-Frame-Options "SAMEORIGIN"\n'
  printf '\t\tReferrer-Policy "strict-origin-when-cross-origin"\n'
  printf '\t\tX-Request-Id "{http.request.uuid}"\n'
  printf '\t}\n'
}

# Branded error pages + request-id logging, emitted by every vhost (site level).
# Optional args are the statuses this site's `handle_errors` answers for:
#   app vhosts   -> 403 429 502 503 504  (only edge-originated errors; a 404/500
#                   from the app itself passes through untouched, so the app's own
#                   localised not-found/error pages are preserved)
#   wildcard     -> 404 (unknown host) + the same edge statuses
# Inside handle_errors the response is /srv/edge/errors/<status>.<lang>.html
# (language chosen from Accept-Language; JSON for API/MCP paths), rendered by
# Caddy's `templates` handler with the request id. The files are generated by
# apg-website scripts/gen-error-pages.mjs and published by `errors_publish`.
emit_site_extras() {
  local statuses="${*:-403 429 502 503 504}"
  # Access logging is opt-in PER SITE in Caddy; every vhost writes the same
  # rolling JSON file, which the edge-log-shipper tails into PostgreSQL. The
  # `log_append` fields (request_id, country) land on each access entry.
  printf '\tlog {\n'
  printf '\t\toutput file /var/log/caddy/access.jsonl {\n'
  printf '\t\t\troll_size 20MiB\n\t\t\troll_keep 10\n\t\t\troll_keep_for 168h\n'
  printf '\t\t}\n\t\tformat json\n\t\tlevel INFO\n\t}\n'
  printf '\tlog_append request_id {http.request.uuid}\n'
  printf '\tlog_append country {geoip2.country_code}\n'
  printf '\thandle_errors %s {\n' "$statuses"
  # machine-readable variants (MCP / REST / well-known) — never HTML
  printf '\t\t@api path /mcp /mcp/* /api /api/* /v1 /v1/* /.well-known/mcp*\n'
  printf '\t\thandle @api {\n'
  printf '\t\t\theader Content-Type "application/json"\n'
  printf '\t\t\trespond `{"error":true,"status":{http.error.status_code},"requestId":"{http.request.uuid}"}` {http.error.status_code}\n'
  printf '\t\t}\n'
  # human-facing branded page. Each branch must FULLY serve (Caddy `handle`
  # blocks are terminal, so `templates`/`file_server` cannot be siblings after
  # them): pick the language, then try <status>.<lang>.html -> 500.<lang>.html
  # -> 500.en.html. `try_files` rewrites to the first hit; `templates` fills the
  # request-id placeholder; `file_server` serves it from the shared edge root.
  local lang matcher
  for lang in hk zh ja ar; do
    case "$lang" in
      hk) matcher='(?i)(zh-hant|zh-hk|zh-tw|zh-mo|hant)' ;;
      zh) matcher='(?i)^zh' ;;
      ja) matcher='(?i)^ja' ;;
      ar) matcher='(?i)^ar' ;;
    esac
    printf '\t\t@err_%s header_regexp Accept-Language %s\n' "$lang" "$matcher"
    printf '\t\thandle @err_%s {\n' "$lang"
    printf '\t\t\troot * /srv/edge/errors\n'
    printf '\t\t\theader Cache-Control "no-store"\n'
    printf '\t\t\theader X-Robots-Tag "noindex"\n'
    printf '\t\t\ttry_files /{http.error.status_code}.%s.html /500.%s.html /500.en.html\n' "$lang" "$lang"
    printf '\t\t\ttemplates\n\t\t\tfile_server\n\t\t}\n'
  done
  printf '\t\thandle {\n'
  printf '\t\t\troot * /srv/edge/errors\n'
  printf '\t\t\theader Cache-Control "no-store"\n'
  printf '\t\t\theader X-Robots-Tag "noindex"\n'
  printf '\t\t\ttry_files /{http.error.status_code}.en.html /500.en.html\n'
  printf '\t\t\ttemplates\n\t\t\tfile_server\n\t\t}\n'
  printf '\t}\n'
}

# ── edge error pages (brand-level) ────────────────────────────────────────────
# Manifest key (app): edge_errors: true  ->  the app ships public/errors/ (see
# apg-website scripts/gen-error-pages.mjs). Publishes the active slot's copy to
# the shared edge root /srv/edge/errors and (re)writes the catch-all wildcard
# vhost that serves the branded 404 for any unconfigured *.ascent-partners.com host.
EDGE_ERRORS_DIR="${EDGE_ERRORS_DIR:-/srv/edge/errors}"
edge_errors_enabled() { [ "$(mf edge_errors 2>/dev/null || echo false)" = "true" ]; }

write_wildcard_vhost() {
  local cert key frag="$CADDY_CONF_DIR/50-${APP}-wildcard.caddy"
  cert="$(mf tls.cert)"; key="$(mf tls.key)"
  {
    printf '# Generated by apg-infra deploy (app=%s, edge_errors). Do not edit.\n' "$APP"
    printf '*.%s {\n' "$DOMAIN"
    printf '\tencode zstd gzip\n'
    printf '\ttls %s %s\n' "$cert" "$key"
    emit_security_headers
    # Unknown host: raise a 404 *error* (a plain `respond 404` is a normal
    # response and would NOT invoke handle_errors, leaving an empty body).
    printf '\terror 404\n'
    emit_site_extras 404 403 429 502 503 504
    printf '}\n'
  } | write_file_atomic "$frag"
  log "wildcard vhost written -> $frag"
}

errors_publish() { # <slot>
  edge_errors_enabled || return 0
  local src="$APP_ROOT/$1/public/errors"
  [ -d "$src" ] || { log "errors_publish: no $src yet (skipping)"; return 0; }
  mkdir -p "$EDGE_ERRORS_DIR"
  rsync -a --delete "$src/" "$EDGE_ERRORS_DIR/"
  write_wildcard_vhost
  log "edge error pages -> $EDGE_ERRORS_DIR ($(find "$EDGE_ERRORS_DIR" -name '*.html' | wc -l) pages)"
}


write_app_vhost() {
  local out="$1" slot="$2" frontend="$3"
  local cert key; cert="$(mf tls.cert)"; key="$(mf tls.key)"
  local alts; alts="$(mf aliases 2>/dev/null || echo '[]')"
  local aliases; aliases="$(python3 -c "import json,sys;print(', '.join(json.loads(sys.argv[1])))" "$alts" 2>/dev/null || true)"
  {
    printf '%s%s {\n' "$DOMAIN" "${aliases:+, $aliases}"
    printf '\tencode zstd gzip\n'
    printf '\ttls %s %s\n' "$cert" "$key"
    printf '\tgeoip2_vars strict\n'
    emit_security_headers
    # One canonical origin: permanently move any "www." alias to the apex host
    # (path + query preserved) so there is a single indexable origin. Other
    # aliases (e.g. a China edge host) stay as served copies of the same site.
    case ",$aliases," in
      *",www.$DOMAIN,"*) printf '\t@www host www.%s\n\tredir @www https://%s{uri} 301\n' "$DOMAIN" "$DOMAIN" ;;
    esac
    printf '\thandle /%s.txt {\n\t\trespond "%s"\n\t}\n' "$INDEXNOW_KEY" "$INDEXNOW_KEY"
    # docs (mkdocs) at /docs
    if mf docs >/dev/null 2>&1; then
      local dpath; dpath="$(mf docs.path 2>/dev/null || echo /docs)"
      printf '\thandle_path %s/* {\n\t\troot * /srv/sites/%s/docs\n\t\tfile_server\n\t}\n' "$dpath" "$APP"
    fi
    # python MCP/REST routes
    py_has && py_caddy_handles "$slot"
    # catch-all
    printf '\thandle {\n'
    # Country fallback, fired ONLY when the client sent no Accept-Language: when a
    # language is stated the app decides (so language always outranks the IP), and
    # this path is skipped. Uses the edge's own geoip2 matcher, so the country
    # fallback does not depend on the injected header surviving to the app.
    if [ "$(mf geo 2>/dev/null || echo false)" = "true" ]; then
      printf '\t\t@geo_zh {\n\t\t\tpath /\n\t\t\texpression `{geoip2.country_code} == "CN"`\n\t\t\tnot header_regexp NEXT_LOCALE Cookie .\n\t\t\tnot header_regexp Accept-Language .\n\t\t}\n\t\tredir @geo_zh /zh 302\n'
      printf '\t\t@geo_hk {\n\t\t\tpath /\n\t\t\texpression `{geoip2.country_code} in ["TW", "HK", "MO"]`\n\t\t\tnot header_regexp NEXT_LOCALE Cookie .\n\t\t\tnot header_regexp Accept-Language .\n\t\t}\n\t\tredir @geo_hk /hk 302\n'
      printf '\t\t@geo_ja {\n\t\t\tpath /\n\t\t\texpression `{geoip2.country_code} == "JP"`\n\t\t\tnot header_regexp NEXT_LOCALE Cookie .\n\t\t\tnot header_regexp Accept-Language .\n\t\t}\n\t\tredir @geo_ja /ja 302\n'
      printf '\t\t@geo_ar {\n\t\t\tpath /\n\t\t\texpression `{geoip2.country_code} in ["DZ", "BH", "KM", "DJ", "EG", "IQ", "JO", "KW", "LB", "LY", "MA", "MR", "OM", "PS", "QA", "SA", "SO", "SD", "SY", "TN", "AE", "YE"]`\n\t\t\tnot header_regexp NEXT_LOCALE Cookie .\n\t\t\tnot header_regexp Accept-Language .\n\t\t}\n\t\tredir @geo_ar /ar 302\n'
    fi
    printf '\t\tcoraza_waf {\n\t\t\tload_owasp_crs\n\t\t\tdirectives `\n\t\t\t\tInclude @coraza.conf-recommended\n\t\t\t\tInclude @crs-setup.conf.example\n\t\t\t\tInclude @owasp_crs/*.conf\n\t\t\t\tSecRuleEngine DetectionOnly\n\t\t\t`\n\t\t}\n'
    printf '\t\trate_limit {\n\t\t\tzone perip {\n\t\t\t\tkey {remote_host}\n\t\t\t\tevents 1500\n\t\t\t\twindow 1m\n\t\t\t}\n\t\t}\n'
    case "$frontend" in
      next)
        # Inject the resolved country; the app owns entry-locale routing.
        if [ "$(mf geo 2>/dev/null || echo false)" = "true" ]; then
          printf '\t\treverse_proxy 127.0.0.1:%s {\n\t\t\theader_up X-Geo-Country {geoip2.country_code}\n\t\t}\n' "$(slot_port "$slot")"
        else
          printf '\t\treverse_proxy 127.0.0.1:%s\n' "$(slot_port "$slot")"
        fi
        ;;
      static) printf '\t\tredir /docs 302\n' ;;
      node) printf '\t\treverse_proxy 127.0.0.1:%s\n' "$(slot_port "$slot")" ;;
      *) die "write_app_vhost: unknown frontend '$frontend'" ;;
    esac
    printf '\t}\n'
    emit_site_extras
    printf '}\n'
  } | write_file_atomic "$out"
}
