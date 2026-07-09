from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Callable


class DownloadError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Response:
    url: str
    status: int
    content: bytes
    content_type: str = ""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


@dataclasses.dataclass(frozen=True)
class DownloadPlan:
    source_url: str
    download_url: str
    output_path: Path
    convert_html: bool


Fetch = Callable[[str, str], Response]
