# APG deploy platform

Manifest-driven CD for the ECS estate. One manifest (`apps.yaml`) + one handler
per stack (`kinds/`) + one reusable workflow (`.github/workflows/app-cd.yml`).

> **This repo is private**, so a caller in another repo **cannot** `uses:` the workflow here
> (cross-repo `workflow_call` needs the called repo **public**, or an org). Until a shared **public**
> `apg-cd` repo (or an org) exists, each app **vendors** this directory into its own repo
> (`apg-website/.deploy/` + `.github/workflows/{deploy,app-cd}.yml`) and keeps it in sync. The
> examples below show the target shape; today they mirror the vendored copy.

## How it works

```
app repo (apg-website)                     apg-infra (this repo)
  .github/workflows/deploy.yml  ───uses──▶  .github/workflows/app-cd.yml
        (app: apg-website)                        │
                                                  ├─ checkout infra (manifest + kinds)
                                                  ├─ checkout app repo
                                                  ├─ kinds/<kind>.sh build
                                                  ├─ PR  → kinds/<kind>.sh preview <ts> + comment
                                                  └─ main→ deploy inactive slot → switch → smoke
                                                          └─ rollback on failure
```

Handlers implement a fixed verb set: **build · deploy `<slot>` · switch `<slot>` ·
preview `<ts>` · rollback · smoke**.

## Add a new app / sub-domain (scale)

1. Add an entry to `apps.yaml` (`name`, `repo`, `node`, `kind`, `domain`, `slots`, …).
2. Add a caller workflow to that repo (vendored copy → local path; or the shared public repo once it exists):
   ```yaml
   jobs: { cd: { uses: ./.github/workflows/app-cd.yml,          # or <shared-repo>/.github/workflows/app-cd.yml@main
                 with: { app: <name>, runner: apg-ecs-01 } } }
   ```
3. Ensure the DNS host exists (AliDNS) and the TLS cert covers it (already a
   `*.ascent-partners.com` wildcard on the edge → sub-domains need no new cert).
4. No changes to the reusable workflow or the Caddyfile — the handler writes a new
   `conf.d/<app>.caddy`.

A new **stack** = a new `kinds/<kind>.sh` implementing the verb set (stubs for
`docker-service`, `static`, `node-service` are included).

## Add a new node (scale)

Add it under `nodes:` (`host`, `eip`, `runner` labels, `srv_root`, `caddy_conf_dir`),
register a runner there (`runner-bootstrap.sh`), and point an app's `node:` at it.

## Per-app IP isolation (scale, optional)

Default is one shared EIP + Caddy Host routing. To isolate an app on its own IP,
attach a secondary private IP (+ EIP) to the node's ENI and set `bind_ip` on the
app; the handler binds that slot to the IP. `ecs.g7.xlarge` supports 4 ENIs ×
15 private IPs; the only real limit is the account EIP quota (cn-hongkong).

## Files

- `apps.yaml` — manifest (apps + nodes).
- `lib.sh` — shared helpers (manifest reads, health, Caddy, retention).
- `kinds/next-website.sh` — Next.js standalone (build → slots → Caddy).
- `kinds/terraform.sh` — infra (plan on PR, apply on main).
- `kinds/{docker-service,static,node-service}.sh` — stubs.
- `runner-bootstrap.sh` — register a self-hosted runner on a node.
- `../.github/workflows/app-cd.yml` — reusable workflow.
- `../website-host/provision-cd.sh` — one-time node bootstrap (layout, units, Caddy import).
