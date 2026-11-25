#!/usr/bin/env bash
# Create or update .env from .env.example. Existing values are never modified.
#
#   no .env yet   copy .env.example, replacing each __GENERATE__ with a random secret
#   .env exists   append every variable from .env.example that .env does not define
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
readonly example=".env.example" target=".env" placeholder="__GENERATE__"

secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

render() { # print a line of .env.example, generating a value for KEY=__GENERATE__
  if [[ "$1" =~ ^([A-Z][A-Z0-9_]*)=${placeholder}$ ]]; then
    printf '%s=%s\n' "${BASH_REMATCH[1]}" "$(secret)"
  else
    printf '%s\n' "$1"
  fi
}

umask 077
if [[ ! -f "$target" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do render "$line"; done <"$example" >"$target"
  echo "created $target with freshly generated secrets"
  exit 0
fi

added=()
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]] || continue
  key="${BASH_REMATCH[1]}"
  grep -qE "^${key}=" "$target" && continue
  if ((${#added[@]} == 0)); then
    printf '\n# added by scripts/bootstrap-env.sh on %s\n' "$(date -u +%Y-%m-%d)" >>"$target"
  fi
  render "$line" >>"$target"
  added+=("$key")
done <"$example"
chmod 600 "$target"

if ((${#added[@]} == 0)); then
  echo "$target is up to date"
else
  echo "added to $target: ${added[*]}"
fi
