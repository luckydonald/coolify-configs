#!/usr/bin/env sh
# Combined pre-push hook: preserves the existing git-lfs pre-push behavior,
# then enforces the base branch-split push-name/content policy (see
# scripts/°base/git/°split_lib/push_checks.py, ai/°base/todo.md lines 155-163).
#
# `.git/hooks/pre-push` is a tiny generated trampoline (written by
# scripts/°base/git/hooks/install) that execs this tracked script, so edits
# here take effect without re-running the installer.
#
# argv: <remote-name> <remote-url>
# stdin: 0+ lines of "<local ref> <local sha> <remote ref> <remote sha>"

set -u
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.pyenv/shims:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:$PATH"

remote_name="${1:-}"
remote_url="${2:-}"
script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel)"

stdin_buf="$(mktemp)"
trap 'rm -f "$stdin_buf"' EXIT
cat >"$stdin_buf"

status=0

if command -v git-lfs >/dev/null 2>&1; then
    git lfs pre-push "$remote_name" "$remote_url" <"$stdin_buf" || status=$?
else
    echo "git-lfs was not found on PATH for the pre-push hook." >&2
    status=2
fi

python3 "$repo_root/scripts/°base/git/split.py" check-push \
    --remote-name "$remote_name" --remote-url "$remote_url" \
    <"$stdin_buf" || status=$?

exit "$status"
