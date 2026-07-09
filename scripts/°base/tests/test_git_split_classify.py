from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

LIB_ROOT = Path(__file__).resolve().parents[1] / "git"
sys.path.insert(0, str(LIB_ROOT))

classify = importlib.import_module("°split_lib.classify")


class IsAiBasePathTests(unittest.TestCase):
    def test_ai_dir_is_ai_content(self):
        self.assertTrue(classify.is_ai_base_path("ai/query.md"))
        self.assertTrue(classify.is_ai_base_path("ai/°base/plans/001_foo.md"))

    def test_claude_dir_is_ai_content(self):
        self.assertTrue(classify.is_ai_base_path(".claude/settings.json"))

    def test_codex_dir_is_ai_content(self):
        self.assertTrue(classify.is_ai_base_path(".codex/config.toml"))

    def test_exact_paths_are_ai_content(self):
        self.assertTrue(classify.is_ai_base_path(".mcp.json"))
        self.assertTrue(classify.is_ai_base_path("AGENTS.md"))
        self.assertTrue(classify.is_ai_base_path("CLAUDE.md"))

    def test_base_segment_anywhere_is_ai_content(self):
        self.assertTrue(classify.is_ai_base_path("scripts/°base/git/split.py"))
        self.assertTrue(classify.is_ai_base_path("deep/nested/°base/thing.py"))

    def test_code_paths_are_not_ai_content(self):
        self.assertFalse(classify.is_ai_base_path("src/main.py"))
        self.assertFalse(classify.is_ai_base_path("README.md"))

    def test_similar_but_non_matching_names_are_not_ai_content(self):
        self.assertFalse(classify.is_ai_base_path("ai-notes.txt"))
        self.assertFalse(classify.is_ai_base_path("claude-thing/x.py"))
        self.assertFalse(classify.is_ai_base_path("base/x.py"))  # no degree sign


class AiSubjectRegexTests(unittest.TestCase):
    def test_matches_plain_ai_prefix(self):
        self.assertTrue(classify.AI_SUBJECT_RE.match("ai: updated prompt"))

    def test_matches_bracketed_topic_convention(self):
        self.assertTrue(classify.AI_SUBJECT_RE.match("[base] topic: ai: Run: did a thing."))
        self.assertTrue(classify.AI_SUBJECT_RE.match("[dumper] init script: ai: Run: extended linking."))

    def test_does_not_match_unrelated_subjects(self):
        self.assertFalse(classify.AI_SUBJECT_RE.match("aisle: fix typo"))
        self.assertFalse(classify.AI_SUBJECT_RE.match("said: hello"))
        self.assertFalse(classify.AI_SUBJECT_RE.match("[base] fix a bug."))


class ClassifyCommitTests(unittest.TestCase):
    def test_ai_only_commit(self):
        result = classify.classify_commit("abc", "ai: updated prompt", ["ai/query.md"])
        self.assertTrue(result.is_ai_only_commit)
        self.assertTrue(result.is_ai_tainted_commit)
        self.assertFalse(result.is_code_containing_commit)

    def test_pure_code_commit(self):
        result = classify.classify_commit("abc", "Add export button", ["src/export.py"])
        self.assertFalse(result.is_ai_only_commit)
        self.assertFalse(result.is_ai_tainted_commit)
        self.assertTrue(result.is_code_containing_commit)

    def test_mixed_commit_is_ai_tainted_and_code_containing(self):
        result = classify.classify_commit(
            "abc", "Add export button", ["src/export.py", "ai/query.md"]
        )
        self.assertFalse(result.is_ai_only_commit)
        self.assertTrue(result.is_ai_tainted_commit)
        self.assertTrue(result.is_code_containing_commit)

    def test_empty_paths_with_ai_subject_is_tainted_but_not_ai_only(self):
        result = classify.classify_commit("abc", "ai: empty marker commit", [])
        self.assertFalse(result.is_ai_only_commit)
        self.assertTrue(result.is_ai_tainted_commit)
        self.assertFalse(result.is_code_containing_commit)

    def test_empty_paths_with_plain_subject_is_untainted(self):
        result = classify.classify_commit("abc", "empty marker commit", [])
        self.assertFalse(result.is_ai_only_commit)
        self.assertFalse(result.is_ai_tainted_commit)
        self.assertFalse(result.is_code_containing_commit)

    def test_code_commit_with_ai_flavored_subject_is_tainted_by_subject(self):
        result = classify.classify_commit(
            "abc", "[base] topic: ai: Run: refactor.", ["src/export.py"]
        )
        self.assertTrue(result.is_ai_tainted_commit)
        self.assertTrue(result.is_code_containing_commit)


if __name__ == "__main__":
    unittest.main()
