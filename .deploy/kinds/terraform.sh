#!/usr/bin/env bash
# kind: terraform — infra-as-code deploy. plan on PR, apply on merge.
# Driven by the manifest; ALICLOUD_ACCESS_KEY/SECRET + TF_* come from secrets.
#
# Verbs: plan | apply
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

REPO_DIR="${REPO_DIR:-$PWD}"
TF_DIR="$REPO_DIR/$(mf tf_dir)"

tf_vars() { # -> -var k=v ...
  mf tf_vars | python3 -c "import json,sys;[print(f'-var {k}={v}') for k,v in json.load(sys.stdin).items()]"
}

tf_init() {
  ( cd "$TF_DIR" && terraform init -input=false -no-color )
}

cmd_plan() {
  tf_init
  ( cd "$TF_DIR" && terraform plan -input=false -no-color $(tf_vars) -out=tfplan )
  ( cd "$TF_DIR" && terraform show -no-color tfplan | tail -60 )
}

cmd_apply() {
  tf_init
  ( cd "$TF_DIR" && terraform apply -input=false -no-color -auto-approve $(tf_vars) )
}

verb="${1:?verb required}"; shift || true
case "$verb" in
  plan) cmd_plan ;;
  apply) cmd_apply ;;
  *) die "unknown verb: $verb" ;;
esac
