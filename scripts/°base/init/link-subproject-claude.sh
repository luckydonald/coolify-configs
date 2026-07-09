#!/usr/bin/env bash
# scripts/°base/init/link-subproject-claude.sh
#
# Idempotent per-subfolder setup for the monorepo case: creates relative
# symlinks at <cwd>/.claude, <cwd>/.codex, <cwd>/ai/tool-settings and
# <cwd>/.mcp.json pointing at their monorepo-root counterparts, plus an
# <cwd>/AGENTS.md -> CLAUDE.md symlink (moving any pre-existing AGENTS.md
# into CLAUDE.md first, mirroring the root layout). This lets Claude Code
# and Codex find the shared hooks/perms/MCP config when launched from
# inside a subfolder of a monorepo that has the `base` repo merged at its
# top level.
#
# Run once from inside the subfolder:
#
#   cd monorepo/some_project
#   ../scripts/°base/init/link-subproject-claude.sh
#
# Safe to run multiple times — already-correct symlinks are left alone.
# Anything pre-existing that would be clobbered is moved aside first, as
# `{name}.YYYY-MM-DD_HH-MM-SS.bak.{ext}` (via `git mv` when tracked). New
# symlinks (and moved files) are `git add`ed.

set -euo pipefail

sub_dir="$(pwd -P)"
git_root="$(cd "$(git rev-parse --show-toplevel)" && pwd -P)"
root_claude="$git_root/.claude"

if [ "$sub_dir" = "$git_root" ]; then
  echo "$sub_dir is the git root — no symlinks needed." >&2
  exit 0
fi

if [ ! -d "$root_claude" ]; then
  echo "no $root_claude — did you merge base/base at the repo root?" >&2
  exit 1
fi

realpath_of() {
  python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

relpath_of() {
  # relpath_of <target> <from_dir>
  python3 -c 'import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$1" "$2"
}

is_tracked() {
  git -C "$sub_dir" ls-files --error-unmatch -- "$1" >/dev/null 2>&1
}

# backup_path <rel-to-sub_dir> — moves an existing path aside as
# {stem}.YYYY-MM-DD_HH-MM-SS.bak{ext}, using `git mv` if tracked.
backup_path() {
  local rel="$1"
  local timestamp
  timestamp="$(date +%Y-%m-%d_%H-%M-%S)"
  local bak_rel
  bak_rel="$(python3 -c '
import os, sys
rel, timestamp = sys.argv[1], sys.argv[2]
d, base = os.path.split(rel)
stem, ext = os.path.splitext(base)
print(os.path.join(d, f"{stem}.{timestamp}.bak{ext}"))
' "$rel" "$timestamp")"

  if is_tracked "$rel"; then
    git -C "$sub_dir" mv -- "$rel" "$bak_rel"
  else
    mv -- "$sub_dir/$rel" "$sub_dir/$bak_rel"
  fi
  echo "backed up $sub_dir/$rel -> $sub_dir/$bak_rel" >&2
}

# link_shared <rel> — symlinks <sub_dir>/<rel> -> <git_root>/<rel>,
# backing up whatever is already at the target if it's not already the
# right symlink.
link_shared() {
  local rel="$1"
  local source="$git_root/$rel"
  local target="$sub_dir/$rel"
  local target_dir
  target_dir="$(dirname "$target")"

  if [ ! -e "$source" ]; then
    echo "no $source — skipping $rel" >&2
    return
  fi

  mkdir -p "$target_dir"

  if [ -L "$target" ]; then
    if [ "$(realpath_of "$target")" = "$(realpath_of "$source")" ]; then
      echo "$target already linked to $source"
      return
    fi
    echo "$target is a symlink but points elsewhere ($(readlink "$target")) — backing up." >&2
    backup_path "$rel"
  elif [ -e "$target" ]; then
    echo "$target exists and is not a symlink — backing up." >&2
    backup_path "$rel"
  fi

  local rel_link
  rel_link="$(relpath_of "$source" "$target_dir")"
  ln -s "$rel_link" "$target"
  echo "linked $target -> $rel_link"
  git -C "$sub_dir" add -- "$rel"
}

# link_agents_claude — ensures <sub_dir>/AGENTS.md -> CLAUDE.md, moving a
# pre-existing real AGENTS.md into CLAUDE.md first (like the repo root).
link_agents_claude() {
  local agents="$sub_dir/AGENTS.md"
  local claude="$sub_dir/CLAUDE.md"

  if [ -L "$agents" ]; then
    if [ -e "$claude" ] && [ "$(realpath_of "$agents")" = "$(realpath_of "$claude")" ]; then
      echo "$agents already linked to $claude"
      return
    fi
    echo "$agents is a symlink but points elsewhere ($(readlink "$agents")) — backing up." >&2
    backup_path "AGENTS.md"
  elif [ -e "$agents" ]; then
    if [ -e "$claude" ]; then
      echo "$agents and $claude both exist — backing up $agents." >&2
      backup_path "AGENTS.md"
    else
      echo "moving $agents -> $claude" >&2
      if is_tracked "AGENTS.md"; then
        git -C "$sub_dir" mv -- "AGENTS.md" "CLAUDE.md"
      else
        mv -- "$agents" "$claude"
      fi
    fi
  fi

  if [ ! -e "$claude" ]; then
    echo "no $claude — nothing to point AGENTS.md at, skipping." >&2
    return
  fi

  ln -s "CLAUDE.md" "$agents"
  echo "linked $agents -> CLAUDE.md"
  git -C "$sub_dir" add -- "AGENTS.md" "CLAUDE.md"
}

link_shared ".claude"
link_shared ".codex"
link_shared "ai/tool-settings"
link_shared ".mcp.json"
link_agents_claude
