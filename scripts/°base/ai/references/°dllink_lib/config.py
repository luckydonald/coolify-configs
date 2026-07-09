from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def settings_path() -> Path:
    return repo_root() / "ai" / "tool-settings" / "settings.json"


def local_settings_path() -> Path:
    return repo_root() / "ai" / "tool-settings" / "settings.local.json"


@dataclass(frozen=True)
class DownloadLinkSettings:
    ide: str = "pycharm"


def load_download_link_settings() -> DownloadLinkSettings:
    data: dict[str, object] = {}
    for path in (settings_path(), local_settings_path()):
        if not path.is_file():
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(candidate, dict):
            data.update(candidate)
    download_link = data.get("download_link")
    if not isinstance(download_link, dict):
        return DownloadLinkSettings()
    ide = download_link.get("ide")
    if not isinstance(ide, str) or not ide.strip():
        return DownloadLinkSettings()
    return DownloadLinkSettings(ide=ide.strip())
