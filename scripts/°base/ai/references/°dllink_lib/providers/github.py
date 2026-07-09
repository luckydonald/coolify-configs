from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from ..models import DownloadError, DownloadPlan, Fetch
from ..paths import output_path_for_url, strip_fragment


def github_api_json(url: str, fetch: Fetch) -> dict:
    response = fetch(url, "GET")
    if response.status != 200:
        raise DownloadError(f"GitHub API returned HTTP {response.status}: {url}")
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise DownloadError(f"GitHub API did not return JSON: {url}") from exc
    if not isinstance(data, dict):
        raise DownloadError(f"GitHub API returned unexpected JSON: {url}")
    return data


def github_ref_sha(owner: str, repo: str, ref: str, fetch: Fetch) -> str:
    api_ref = urllib.parse.quote(f"heads/{ref}", safe="/")
    data = github_api_json(f"https://api.github.com/repos/{owner}/{repo}/git/ref/{api_ref}", fetch)
    obj = data.get("object") if isinstance(data, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha):
        return sha

    api_ref = urllib.parse.quote(f"tags/{ref}", safe="/")
    data = github_api_json(f"https://api.github.com/repos/{owner}/{repo}/git/ref/{api_ref}", fetch)
    obj = data.get("object") if isinstance(data, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha):
        return sha
    raise DownloadError(f"could not resolve GitHub ref {owner}/{repo}@{ref}")


def parse_github_blob(url: str) -> tuple[str, str, list[str]] | None:
    parts = urllib.parse.urlsplit(strip_fragment(url))
    if parts.netloc.lower() != "github.com":
        return None
    items = [part for part in parts.path.split("/") if part]
    if len(items) < 5 or items[2] != "blob":
        return None
    owner, repo = items[0], items[1]
    rest = items[3:]
    return owner, repo, rest


def github_plan(url: str, output_root: Path, fetch: Fetch) -> DownloadPlan | None:
    parsed = parse_github_blob(url)
    if parsed is None:
        return None
    owner, repo, rest = parsed
    if re.fullmatch(r"[0-9a-f]{40}", rest[0]):
        commit = rest[0]
        path = "/".join(rest[1:])
    else:
        commit = ""
        path = ""
        for index in range(1, len(rest)):
            ref = "/".join(rest[:index])
            candidate_path = "/".join(rest[index:])
            try:
                commit = github_ref_sha(owner, repo, ref, fetch)
            except DownloadError:
                continue
            path = candidate_path
            break
        if not commit or not path:
            raise DownloadError(f"could not resolve GitHub blob URL: {url}")
    permalink = f"https://github.com/{owner}/{repo}/blob/{commit}/{path}"
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{path}"
    return DownloadPlan(
        source_url=permalink,
        download_url=raw,
        output_path=output_path_for_url(output_root, permalink),
        convert_html=not path.endswith(".md"),
    )
