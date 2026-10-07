#!/usr/bin/env bash
# Zip the project (tracked + untracked files, honouring .gitignore, so .env files,
# local databases and .venv stay out) and copy it to a VM over scp.
#
# Usage: scripts/upload-to-vm.sh user@host [remote_dir]
# Env:   SSH_KEY  path to a private key (optional)
#        SSH_PORT ssh port (default 22)
#        UNZIP=1  also unzip on the VM into <remote_dir>/latterboard (needs unzip there)
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 user@host [remote_dir]" >&2
  exit 1
fi

target="$1"
remote_dir="${2:-~}"
port="${SSH_PORT:-22}"

ssh_opts=(-P "$port")
ssh_cmd_opts=(-p "$port")
if [[ -n "${SSH_KEY:-}" ]]; then
  ssh_opts+=(-i "$SSH_KEY")
  ssh_cmd_opts+=(-i "$SSH_KEY")
fi

repo_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$repo_root"

stamp="$(date +%Y%m%d-%H%M%S)"
archive_name="latterboard-${stamp}.zip"
archive="$(mktemp -d)/${archive_name}"
trap 'rm -rf "$(dirname "$archive")"' EXIT

echo "Creating ${archive_name}..."
git ls-files -co --exclude-standard -z | python3 -c '
import sys, zipfile, os
names = [n for n in sys.stdin.buffer.read().decode().split("\0") if n and os.path.isfile(n)]
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as zf:
    for name in names:
        zf.write(name, os.path.join("latterboard", name))
print(f"  {len(names)} files")
' "$archive"
du -h "$archive" | cut -f1 | sed 's/^/  /'

echo "Uploading to ${target}:${remote_dir}/..."
scp "${ssh_opts[@]}" "$archive" "${target}:${remote_dir}/"

if [[ "${UNZIP:-0}" == "1" ]]; then
  echo "Unzipping on ${target}..."
  ssh "${ssh_cmd_opts[@]}" "$target" \
    "cd ${remote_dir} && unzip -oq ${archive_name} && rm ${archive_name}"
  echo "Done: ${remote_dir}/latterboard"
else
  echo "Done: ${remote_dir}/${archive_name}"
fi
