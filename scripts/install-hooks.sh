#!/bin/sh
# Install git hooks from scripts/hooks/ into this repository's .git/hooks directory.

set -e

root=$(git rev-parse --show-toplevel)
hooks_dir=$(git -C "$root" rev-parse --git-path hooks)
src="$root/scripts/hooks/commit-msg"
dest="$root/$hooks_dir/commit-msg"

if [ ! -f "$src" ]; then
	echo "install-hooks: missing $src" >&2
	exit 1
fi

mkdir -p "$root/$hooks_dir"
cp "$src" "$dest"
chmod +x "$dest"
echo "Installed commit-msg hook at $dest"
