from __future__ import annotations

import dataclasses
import re
import subprocess
import urllib.parse
from pathlib import Path

from ..models import DownloadPlan
from ..paths import output_path_for_url, strip_fragment


@dataclasses.dataclass(frozen=True)
class GenericForgePattern:
    host_regex: str
    marker: str
    raw_template: str
    commit_template: str
    repo_parts: int = 2


GENERIC_FORGES = [
    GenericForgePattern(
        host_regex=r".*",
        marker="-/blob",
        raw_template="{scheme}://{host}/{repo}/-/raw/{ref}/{path}",
        commit_template="{scheme}://{host}/{repo}/-/blob/{commit}/{path}",
    ),
    GenericForgePattern(
        host_regex=r".*",
        marker="src/branch",
        raw_template="{scheme}://{host}/{repo}/raw/branch/{ref}/{path}",
        commit_template="{scheme}://{host}/{repo}/src/commit/{commit}/{path}",
    ),
    GenericForgePattern(
        host_regex=r".*",
        marker="src/tag",
        raw_template="{scheme}://{host}/{repo}/raw/tag/{ref}/{path}",
        commit_template="{scheme}://{host}/{repo}/src/commit/{commit}/{path}",
    ),
    GenericForgePattern(
        host_regex=r".*",
        marker="src/commit",
        raw_template="{scheme}://{host}/{repo}/raw/commit/{ref}/{path}",
        commit_template="{scheme}://{host}/{repo}/src/commit/{commit}/{path}",
    ),
    GenericForgePattern(
        host_regex=r"(^bitbucket\.org$)",
        marker="src",
        raw_template="{scheme}://{host}/{repo}/raw/{ref}/{path}",
        commit_template="{scheme}://{host}/{repo}/src/{commit}/{path}",
    ),
]


def git_ls_remote_sha(repo_url: str, ref: str) -> str | None:
    candidates = [f"refs/heads/{ref}", f"refs/tags/{ref}", ref]
    for candidate in candidates:
        result = subprocess.run(
            ["git", "ls-remote", repo_url, candidate],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            continue
        first = (result.stdout or "").splitlines()
        if not first:
            continue
        sha = first[0].split()[0]
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    return None


def generic_forge_plan(url: str, output_root: Path) -> DownloadPlan | None:
    parts = urllib.parse.urlsplit(strip_fragment(url))
    host = parts.netloc.lower()
    items = [part for part in parts.path.split("/") if part]
    for pattern in GENERIC_FORGES:
        if not re.search(pattern.host_regex, host):
            continue
        try:
            marker_parts = pattern.marker.split("/")
            marker_index = next(
                i for i in range(len(items))
                if items[i:i + len(marker_parts)] == marker_parts
            )
        except StopIteration:
            continue
        if marker_index < pattern.repo_parts:
            continue
        repo = "/".join(items[:marker_index])
        rest = items[marker_index + len(marker_parts):]
        if len(rest) < 2:
            continue
        ref = rest[0]
        path = "/".join(rest[1:])
        commit = ref if re.fullmatch(r"[0-9a-f]{40}", ref) else None
        if commit is None:
            repo_url = f"{parts.scheme}://{parts.netloc}/{repo}.git"
            commit = git_ls_remote_sha(repo_url, ref) or ref
        raw = pattern.raw_template.format(
            scheme=parts.scheme,
            host=parts.netloc,
            repo=repo,
            ref=ref,
            commit=commit,
            path=path,
        )
        permalink = pattern.commit_template.format(
            scheme=parts.scheme,
            host=parts.netloc,
            repo=repo,
            ref=ref,
            commit=commit,
            path=path,
        )
        return DownloadPlan(
            source_url=permalink,
            download_url=raw,
            output_path=output_path_for_url(output_root, permalink),
            convert_html=not path.endswith(".md"),
        )
    return None


def unsupported_forge_reason(url: str) -> str | None:
    host = urllib.parse.urlsplit(strip_fragment(url)).netloc.lower()
    names = {
        "sourceforge.net": "SourceForge",
        "git.sr.ht": "SourceHut",
        "launchpad.net": "Launchpad",
        "console.aws.amazon.com": "AWS CodeCommit",
    }
    if "radicle" in host:
        return "Radicle URLs are not standardized enough to map to raw files safely."
    for suffix, name in names.items():
        if host == suffix or host.endswith("." + suffix):
            return f"{name} URL shape is not supported yet for safe raw-file downloads."
    return None
