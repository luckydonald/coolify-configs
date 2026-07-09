from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "°base" / "ai" / "references" / "download-link.py"
SPEC = importlib.util.spec_from_file_location("download_link_live", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@unittest.skipUnless(os.environ.get("DOWNLOAD_LINK_LIVE") == "1", "set DOWNLOAD_LINK_LIVE=1")
class DownloadLinkLiveTests(unittest.TestCase):
    def test_openai_docs_live_returns_useful_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = MODULE.main([
                "--output-root",
                str(Path(tmp) / "refs"),
                "https://developers.openai.com/codex/config-advanced#profiles",
            ])

            self.assertEqual(status, 0)
            files = list((Path(tmp) / "refs").rglob("*.md"))
            self.assertTrue(files)
            self.assertGreater(files[0].stat().st_size, 100)

    def test_github_commit_permalink_live_is_stable(self):
        sha = "49614a0391d83eec442ffeca1d4aa0fdeb119818"
        with tempfile.TemporaryDirectory() as tmp:
            status = MODULE.main([
                "--output-root",
                str(Path(tmp) / "refs"),
                f"https://github.com/openai/codex/blob/{sha}/codex-rs/protocol/src/request_user_input.rs",
            ])

            self.assertEqual(status, 0)
            path = (
                Path(tmp)
                / "refs"
                / "https"
                / "github.com"
                / "openai"
                / "codex"
                / "blob"
                / sha
                / "codex-rs"
                / "protocol"
                / "src"
                / "request_user_input.rs"
            )
            self.assertTrue(path.is_file())
            self.assertIn("RequestUserInputQuestion", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
