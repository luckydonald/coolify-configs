from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from .config import DownloadLinkSettings, load_download_link_settings, repo_root
from .http import fetch_url
from .models import DownloadError
from .planner import download, markdown_plan_if_available, resolve_plan


def read_url_from_input(argv_url: str | None) -> str:
    if argv_url:
        return argv_url.strip()
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data.splitlines()[0].strip()
        raise DownloadError(
            "No URL provided. Use `download-link.py URL` or pipe one with `echo URL | download-link.py`."
        )
    return input("URL: ").strip()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download documentation into ai/references.")
    parser.add_argument("url", nargs="?")
    parser.add_argument("--output-root", default="ai/references")
    parser.add_argument("--open-ide", "--open", "--ide", "--ide-open", dest="open_ide", default=None, metavar="COMMAND")
    parser.add_argument("--no-git-add", "--no-git", "--no-add-git", action="store_true")
    parser.add_argument("--no-open-ide", "--no-ide", "--no-ide-open", action="store_true")
    return parser.parse_args(argv)


def _git_add(path: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_root()), "add", "--", str(path.resolve())],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    details = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    raise DownloadError(f"git add failed for {path}: {details}")


def _open_ide(path: Path, command: str) -> bool:
    parts = shlex.split(command)
    if not parts:
        raise DownloadError("open-ide command is empty")
    result = subprocess.run([*parts, str(path)], capture_output=True, text=True)
    if result.returncode == 0:
        return True
    details = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    print(f"download-link: could not open {path} with {command}: {details}", file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        settings: DownloadLinkSettings = load_download_link_settings()
        url = read_url_from_input(args.url)
        if not url:
            raise DownloadError("No URL provided.")
        output_root = Path(args.output_root)
        plan = resolve_plan(url, output_root, fetch_url)
        plan = markdown_plan_if_available(plan, output_root, fetch_url)
        path, content = download(plan, fetch_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if not args.no_git_add:
            _git_add(path)
        opened = False
        if not args.no_open_ide:
            opened = _open_ide(path, args.open_ide or settings.ide)
    except DownloadError as exc:
        print(f"download-link: {exc}", file=sys.stderr)
        return 1

    print(f"download: {plan.download_url}")
    print(f"wrote: {path}")
    if not args.no_git_add:
        print(f"git add: {path}")
    if not args.no_open_ide and opened:
        print(f"open: {args.open_ide or settings.ide} {path}")
    return 0
