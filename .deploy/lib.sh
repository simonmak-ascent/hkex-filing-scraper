#!/usr/bin/env bash
# Shared helpers for the APG CD kind handlers. Sourced, not executed.
set -euo pipefail

: "${MANIFEST:?MANIFEST (path to apps.yaml) must be set}"
: "${APP:?APP (app name) must be set}"

log()  { printf '[cd] %s\n' "$*" >&2; }
die()  { printf '[cd] ERROR: %s\n' "$*" >&2; exit 1; }
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
print(v if not isinstance(v, (list, dict)) else __import__("json").dumps(v))
PY
}

# mf_node <dot path>  ->  value from this app's node entry
mf_node() {
  python3 - "$MANIFEST" "$APP" "$1" <<'PY'
import sys, yaml
mf, app, path = sys.argv[1], sys.argv[2], sys.argv[3]
d = yaml.safe_load(open(mf))
a = {x["name"]: x for x in d["apps"]}[app]
n = d["nodes"][a["node"]]
v = n
for k in path.split("."):
    v = v[k]
print(v if not isinstance(v, (list, dict)) else __import__("json").dumps(v))
PY
}

APP_NODE="$(mf node)"
SRV_ROOT="$(mf_node srv_root)"
APP_ROOT="$SRV_ROOT/$APP"
CADDY_CONF_DIR="$(mf_node caddy_conf_dir)"
DOMAIN="$(mf domain)"
HEALTH_PATHS="$(mf health)"            # JSON array
KEEP_RELEASES="$(mf keep_releases 2>/dev/null || echo 3)"

slot_port() { mf "slots.$1"; }

health_check() { # <port> <host> [paths-json]
  local port="$1" host="$2" paths="${3:-$HEALTH_PATHS}" p
  for p in $(python3 -c "import json,sys;print(' '.join(json.loads(sys.argv[1])))" "$paths"); do
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -H "Host: $host" "http://127.0.0.1:$port$p" || echo 000)"
    [ "$code" = "200" ] || { log "health FAIL $host$p on :$port -> $code"; return 1; }
  done
  log "health OK $host on :$port"
}

caddy_reload() {
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || die "invalid Caddy config; not reloading"
  # Use `restart`, not `reload`: the admin-API reload re-inits standard modules
  # but does NOT re-register the geoip2 handler's per-request replacer, so a
  # geo/routing change silently fails to take effect. Restart reliably re-applies
  # (brief edge blip; the app slots behind it are untouched).
  systemctl restart caddy
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
  mkdir -p "$root"
  [ -x "$venv/bin/python" ] || uv venv "$venv" >/dev/null
  req="$(mf python.requirements 2>/dev/null || true)"
  if [ -n "$req" ] && [ -f "$REPO_DIR/$req" ]; then
    uv pip install --python "$venv/bin/python" -r "$REPO_DIR/$req" >/dev/null
  fi
  pkg="$(mf python.package 2>/dev/null || echo .)"
  uv pip install --python "$venv/bin/python" "$REPO_DIR/$pkg" >/dev/null
  case "$(mf python.cmd)" in *gunicorn*) uv pip install --python "$venv/bin/python" gunicorn >/dev/null ;; esac
}

py_write_unit() {
  local cmd; cmd="$(mf python.cmd)"; cmd="${cmd//\{port\}/\$PORT}"
<<<<<<< HEAD
  local vbin; vbin="$(py_root)/venv/bin"
=======
>>>>>>> 6c944ea (feat(cd): host the site on ecs-01 (vendored CD + MkDocs static + WSGI MCP))
  cat > "$(py_unit)" <<UNIT
[Unit]
Description=${APP} Python MCP/REST slot %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$(py_root)/work/%i
EnvironmentFile=-/etc/$APP/mcp-%i.env
<<<<<<< HEAD
# put the app venv first on PATH so \`python\` and the console scripts resolve
ExecStart=/bin/bash -lc 'export PATH=$vbin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin; set -a; . /etc/$APP/mcp-%i.env; set +a; cd $(py_root)/work/%i; exec $cmd'
=======
ExecStart=/bin/bash -lc 'set -a; . /etc/$APP/mcp-%i.env; set +a; exec $cmd'
>>>>>>> 6c944ea (feat(cd): host the site on ecs-01 (vendored CD + MkDocs static + WSGI MCP))
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
}

py_deploy() { # <slot>
  local slot="$1" work; work="$(py_work "$slot")"
  mkdir -p "$work" "/etc/$APP"
  rsync -a --delete --exclude .git --exclude node_modules --exclude .next \
        --exclude .venv --exclude '__pycache__' --exclude '*.pyc' "$REPO_DIR/" "$work/"
  [ -d "$LAUNCHERS_DIR" ] && cp -a "$LAUNCHERS_DIR/." "$work/" || true
  printf 'PORT=%s\n' "$(py_slot_port "$slot")" > "$(py_envfile "$slot")"
  py_write_unit
  systemctl enable --now "${APP}-mcp@${slot}" >/dev/null 2>&1 || true
  systemctl restart "${APP}-mcp@${slot}"
}

# ── mkdocs static docs → /srv/sites/<app> ─────────────────────────────────────
# Manifest keys (app): docs.build (default "mkdocs build --strict"),
#                      docs.dir (default "site")
docs_build() {
  local b; b="$(mf docs.build 2>/dev/null || echo 'mkdocs build --strict')"
  if command -v mkdocs >/dev/null 2>&1; then
    ( cd "$REPO_DIR" && eval "$b" )
  else
    # no system mkdocs on the runner: run the repo's docs build under uvx, which
    # puts mkdocs (material theme + pymdown + mkdocstrings) on PATH for the command.
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
# Emits: geo redirect + docs (/docs) + python routes + coraza + rate_limit inside
# a catch-all `handle`; specific `handle`s for /docs and the python routes come
# first (Caddy `handle` blocks are mutually exclusive, first match wins).
# geoip2_vars runs at site level; the geo matchers live in the catch-all handle
# (verified working: HK -> /hk while /mcp,/docs,/about route correctly).
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
    # docs (mkdocs) at /docs
    if mf docs >/dev/null 2>&1; then
      local dpath; dpath="$(mf docs.path 2>/dev/null || echo /docs)"
      printf '\thandle_path %s/* {\n\t\troot * /srv/sites/%s/docs\n\t\tfile_server\n\t}\n' "$dpath" "$APP"
    fi
    # python MCP/REST routes
    if py_has; then
      local pport; pport="$(py_slot_port "$slot" 2>/dev/null || py_slot_port blue)"
      python3 -c "import json,sys;[print(p) for p in json.loads(sys.argv[1])]" "$(mf routes.python 2>/dev/null || echo '[]')" 2>/dev/null | while IFS= read -r pat; do
        [ -z "$pat" ] && continue
        printf '\thandle %s {\n\t\treverse_proxy 127.0.0.1:%s\n\t}\n' "$pat" "$pport"
      done
    fi
    # catch-all
    printf '\thandle {\n'
    if [ "$(mf geo 2>/dev/null || echo false)" = "true" ]; then
      printf '\t\t@geo_zh {\n\t\t\tpath /\n\t\t\texpression `{geoip2.country_code} == "CN"`\n\t\t\tnot header_regexp NEXT_LOCALE Cookie .\n\t\t}\n\t\tredir @geo_zh /zh 302\n'
      printf '\t\t@geo_hk {\n\t\t\tpath /\n\t\t\texpression `{geoip2.country_code} in ["TW", "HK", "MO"]`\n\t\t\tnot header_regexp NEXT_LOCALE Cookie .\n\t\t}\n\t\tredir @geo_hk /hk 302\n'
    fi
    printf '\t\tcoraza_waf {\n\t\t\tload_owasp_crs\n\t\t\tdirectives `\n\t\t\t\tInclude @coraza.conf-recommended\n\t\t\t\tInclude @crs-setup.conf.example\n\t\t\t\tInclude @owasp_crs/*.conf\n\t\t\t\tSecRuleEngine DetectionOnly\n\t\t\t`\n\t\t}\n'
    printf '\t\trate_limit {\n\t\t\tzone perip {\n\t\t\t\tkey {remote_host}\n\t\t\t\tevents 1500\n\t\t\t\twindow 1m\n\t\t\t}\n\t\t}\n'
    case "$frontend" in
      next)   printf '\t\treverse_proxy 127.0.0.1:%s\n' "$(slot_port "$slot")" ;;
      static) printf '\t\tredir /docs 302\n' ;;
      *) die "write_app_vhost: unknown frontend '$frontend'" ;;
    esac
    printf '\t}\n'
    printf '\thandle_errors 403 {\n\t\trespond "Blocked. Request ID: {http.request.header.x-request-id}" 403\n\t}\n}\n'
  } > "$out"
}
