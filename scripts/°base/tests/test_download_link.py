from __future__ import annotations

import importlib.util
import importlib
import io
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
LIB_ROOT = ROOT / "scripts" / "°base" / "ai" / "references"
sys.path.insert(0, str(LIB_ROOT))
MODULE = importlib.import_module("°dllink_lib")
cli = importlib.import_module("°dllink_lib.cli")
providers = importlib.import_module("°dllink_lib.providers")
generic_provider = importlib.import_module("°dllink_lib.providers.generic")
config = importlib.import_module("°dllink_lib.config")

ENTRYPOINT_PATH = LIB_ROOT / "download-link.py"
SPEC = importlib.util.spec_from_file_location("download_link_entrypoint", ENTRYPOINT_PATH)
ENTRYPOINT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = ENTRYPOINT
SPEC.loader.exec_module(ENTRYPOINT)


class FakeFetch:
    def __init__(self, responses: dict[tuple[str, str], MODULE.Response]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, method: str = "GET") -> MODULE.Response:
        self.calls.append((url, method))
        return self.responses.get(
            (url, method),
            MODULE.Response(url=url, status=404, content=b"not found", content_type="text/plain"),
        )


class DownloadLinkPathTests(unittest.TestCase):
    def test_openai_html_prefers_markdown_candidate(self):
        url = "https://developers.openai.com/codex/config-advanced#profiles"
        md_url = "https://developers.openai.com/codex/config-advanced.md"
        fetch = FakeFetch({
            (md_url, "GET"): MODULE.Response(
                url=md_url,
                status=200,
                content=b"# Config advanced\n",
                content_type="text/markdown",
            )
        })

        plan = MODULE.resolve_plan(url, Path("ai/references"), fetch)
        plan = MODULE.markdown_plan_if_available(plan, Path("ai/references"), fetch)

        self.assertEqual(
            plan.output_path,
            Path("ai/references/https/developers.openai.com/codex/config-advanced.md"),
        )
        self.assertEqual(plan.download_url, md_url)
        self.assertFalse(plan.convert_html)

    def test_github_branch_blob_resolves_to_commit_permalink_and_raw_url(self):
        url = "https://github.com/j-shelfwood/bugsink-mcp/blob/main/README.md"
        sha = "3010d119bca3a48eced460e8f51f52cda4b51d5b"
        api = "https://api.github.com/repos/j-shelfwood/bugsink-mcp/git/ref/heads/main"
        fetch = FakeFetch({
            (api, "GET"): MODULE.Response(
                url=api,
                status=200,
                content=(f'{{"object": {{"sha": "{sha}"}}}}').encode(),
                content_type="application/json",
            )
        })

        plan = MODULE.resolve_plan(url, Path("ai/references"), fetch)

        self.assertEqual(
            plan.output_path,
            Path(
                "ai/references/https/github.com/j-shelfwood/bugsink-mcp/"
                f"blob/{sha}/README.md"
            ),
        )
        self.assertEqual(
            plan.download_url,
            f"https://raw.githubusercontent.com/j-shelfwood/bugsink-mcp/{sha}/README.md",
        )

    def test_github_commit_blob_does_not_call_api(self):
        sha = "3010d119bca3a48eced460e8f51f52cda4b51d5b"
        url = f"https://github.com/j-shelfwood/bugsink-mcp/blob/{sha}/README.md"
        fetch = FakeFetch({})

        plan = MODULE.resolve_plan(url, Path("ai/references"), fetch)

        self.assertEqual(fetch.calls, [])
        self.assertEqual(
            plan.download_url,
            f"https://raw.githubusercontent.com/j-shelfwood/bugsink-mcp/{sha}/README.md",
        )

    def test_github_branch_with_slash_tries_prefixes_until_ref_resolves(self):
        sha = "1234567890abcdef1234567890abcdef12345678"
        url = "https://github.com/owner/repo/blob/feature/docs/README.md"
        bad = "https://api.github.com/repos/owner/repo/git/ref/heads/feature"
        good = "https://api.github.com/repos/owner/repo/git/ref/heads/feature/docs"
        fetch = FakeFetch({
            (bad, "GET"): MODULE.Response(url=bad, status=404, content=b"{}", content_type="application/json"),
            (good, "GET"): MODULE.Response(
                url=good,
                status=200,
                content=(f'{{"object": {{"sha": "{sha}"}}}}').encode(),
                content_type="application/json",
            ),
        })

        plan = MODULE.resolve_plan(url, Path("ai/references"), fetch)

        self.assertEqual(plan.download_url, f"https://raw.githubusercontent.com/owner/repo/{sha}/README.md")

    def test_readthedocs_html_uses_footer_revision_filename(self):
        url = "https://pyte.readthedocs.io/en/latest/api.html"
        html = """
        <html><body><main><h1>API reference</h1></main>
        <span class="commit">Revision <code>a267d4ae</code>.</span>
        </body></html>
        """
        fetch = FakeFetch({
            ("https://pyte.readthedocs.io/en/latest/api.md", "GET"): MODULE.Response(
                url="https://pyte.readthedocs.io/en/latest/api.md",
                status=404,
                content=b"",
                content_type="text/plain",
            ),
            ("https://pyte.readthedocs.io/en/latest/api.html.md", "GET"): MODULE.Response(
                url="https://pyte.readthedocs.io/en/latest/api.html.md",
                status=404,
                content=b"",
                content_type="text/plain",
            ),
            (url, "GET"): MODULE.Response(url=url, status=200, content=html.encode(), content_type="text/html"),
        })

        plan = MODULE.resolve_plan(url, Path("ai/references"), fetch)
        plan = MODULE.markdown_plan_if_available(plan, Path("ai/references"), fetch)
        path, content = MODULE.download(plan, fetch)

        self.assertEqual(
            path,
            Path("ai/references/https/pyte.readthedocs.io/en/latest/api.html/a267d4ae.md"),
        )
        self.assertIn(b"API reference", content)

    def test_html_fallback_writes_underscore_markdown(self):
        url = "https://www.equestriadaily.com/2016/02/oc-pony-spotlight-littlepip.html"
        fetch = FakeFetch({
            ("https://www.equestriadaily.com/2016/02/oc-pony-spotlight-littlepip.md", "GET"): MODULE.Response(
                url="https://www.equestriadaily.com/2016/02/oc-pony-spotlight-littlepip.md",
                status=404,
                content=b"",
            ),
            ("https://www.equestriadaily.com/2016/02/oc-pony-spotlight-littlepip.html.md", "GET"): MODULE.Response(
                url="https://www.equestriadaily.com/2016/02/oc-pony-spotlight-littlepip.html.md",
                status=404,
                content=b"",
            ),
            (url, "GET"): MODULE.Response(
                url=url,
                status=200,
                content=b"<html><body><h1>Spotlight</h1></body></html>",
                content_type="text/html",
            ),
        })

        plan = MODULE.resolve_plan(url, Path("ai/references"), fetch)
        plan = MODULE.markdown_plan_if_available(plan, Path("ai/references"), fetch)
        path, _ = MODULE.download(plan, fetch)

        self.assertEqual(
            path,
            Path(
                "ai/references/https/www.equestriadaily.com/2016/02/"
                "oc-pony-spotlight-littlepip.html/_.md"
            ),
        )

    @mock.patch.object(generic_provider, "git_ls_remote_sha", return_value="abcdefabcdefabcdefabcdefabcdefabcdefabcd")
    def test_selfhosted_gitlab_shape_resolves_without_gitlab_hostname(self, _ls_remote):
        url = "https://git.example.test/group/project/-/blob/main/docs/readme.md"

        plan = MODULE.resolve_plan(url, Path("ai/references"), FakeFetch({}))

        self.assertEqual(
            plan.output_path,
            Path(
                "ai/references/https/git.example.test/group/project/-/blob/"
                "abcdefabcdefabcdefabcdefabcdefabcdefabcd/docs/readme.md"
            ),
        )
        self.assertEqual(
            plan.download_url,
            "https://git.example.test/group/project/-/raw/main/docs/readme.md",
        )


class DownloadLinkInputTests(unittest.TestCase):
    def test_open_ide_aliases_parse_to_open_ide(self):
        for option in ("--open-ide", "--open", "--ide", "--ide-open"):
            with self.subTest(option=option):
                args = cli.parse_args([f"{option}=code", "https://example.com/docs/page.md"])
                self.assertEqual(args.open_ide, "code")

    def test_disable_aliases_parse_to_existing_flags(self):
        args = cli.parse_args(
            [
                "--no-git",
                "--no-add-git",
                "--no-ide",
                "--no-ide-open",
                "https://example.com/docs/page.md",
            ]
        )

        self.assertTrue(args.no_git_add)
        self.assertTrue(args.no_open_ide)

    def test_empty_non_tty_stdin_explains_usage(self):
        stdin = io.StringIO("")
        stdin.isatty = lambda: False  # type: ignore[method-assign]
        with mock.patch.object(cli.sys, "stdin", stdin):
            with self.assertRaisesRegex(MODULE.DownloadError, "download-link.py URL"):
                MODULE.read_url_from_input(None)

    def test_main_writes_file(self):
        url = "https://example.com/docs/page.md"
        response = MODULE.Response(url=url, status=200, content=b"# Page\n", content_type="text/markdown")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli, "fetch_url", FakeFetch({(url, "GET"): response})):
                status = MODULE.main(
                    ["--output-root", str(Path(tmp) / "refs"), "--no-git-add", "--no-open-ide", url]
                )

            self.assertEqual(status, 0)
            self.assertEqual(
                (Path(tmp) / "refs" / "https" / "example.com" / "docs" / "page.md").read_text(),
                "# Page\n",
            )

    def test_main_adds_and_opens_by_default(self):
        url = "https://example.com/docs/page.md"
        response = MODULE.Response(url=url, status=200, content=b"# Page\n", content_type="text/markdown")

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = config.repo_root()
            calls: list[list[str]] = []

            def fake_run(cmd, capture_output=False, text=False):
                calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(cli, "load_download_link_settings", return_value=config.DownloadLinkSettings(ide="pycharm")):
                with mock.patch.object(cli.subprocess, "run", side_effect=fake_run):
                    with mock.patch.object(cli, "fetch_url", FakeFetch({(url, "GET"): response})):
                        status = cli.main(["--output-root", str(Path(tmp) / "refs"), url])

            self.assertEqual(status, 0)
            self.assertEqual(calls[0][:4], ["git", "-C", str(repo_root), "add"])
            self.assertEqual(calls[1][0], "pycharm")
            self.assertEqual(calls[1][-1], str(Path(tmp) / "refs" / "https" / "example.com" / "docs" / "page.md"))

    def test_open_ide_override_wins_over_settings(self):
        url = "https://example.com/docs/page.md"
        response = MODULE.Response(url=url, status=200, content=b"# Page\n", content_type="text/markdown")

        with tempfile.TemporaryDirectory() as tmp:
            calls: list[list[str]] = []

            def fake_run(cmd, capture_output=False, text=False):
                calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(cli, "load_download_link_settings", return_value=config.DownloadLinkSettings(ide="pycharm")):
                with mock.patch.object(cli.subprocess, "run", side_effect=fake_run):
                    with mock.patch.object(cli, "fetch_url", FakeFetch({(url, "GET"): response})):
                        status = cli.main(["--output-root", str(Path(tmp) / "refs"), "--open-ide=code", url])

            self.assertEqual(status, 0)
            self.assertEqual(calls[1][0], "code")
            self.assertEqual(calls[1][-1], str(Path(tmp) / "refs" / "https" / "example.com" / "docs" / "page.md"))

    def test_no_open_ide_wins_over_override_and_no_git_add_skips_git(self):
        url = "https://example.com/docs/page.md"
        response = MODULE.Response(url=url, status=200, content=b"# Page\n", content_type="text/markdown")

        with tempfile.TemporaryDirectory() as tmp:
            calls: list[list[str]] = []

            def fake_run(cmd, capture_output=False, text=False):
                calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(cli, "load_download_link_settings", return_value=config.DownloadLinkSettings(ide="pycharm")):
                with mock.patch.object(cli.subprocess, "run", side_effect=fake_run):
                    with mock.patch.object(cli, "fetch_url", FakeFetch({(url, "GET"): response})):
                        status = cli.main(
                            [
                                "--output-root",
                                str(Path(tmp) / "refs"),
                                "--no-git-add",
                                "--no-open-ide",
                                "--open-ide=code",
                                url,
                            ]
                        )

            self.assertEqual(status, 0)
            self.assertEqual(calls, [])

    def test_entrypoint_exposes_main(self):
        self.assertIs(ENTRYPOINT.main, cli.main)


if __name__ == "__main__":
    unittest.main()
