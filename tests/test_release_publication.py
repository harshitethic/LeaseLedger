"""Release publication boundaries; all GitHub requests are synthetic fixtures."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.publish_release import GitHub, ReleaseError, publish


SHA = "a" * 40
PREFIX = "/repos/harshitethic/LeaseLedger"


class FakeGitHub:
    def __init__(self):
        self.responses = {PREFIX + "/git/ref/heads/main": {"object": {"sha": SHA}}}
        self.writes = []

    def request(self, method, path, body=None, *, missing_ok=False):
        if method == "POST":
            self.writes.append((path, body))
            return {"html_url": "https://github.com/harshitethic/LeaseLedger/releases/tag/v0.1.3"}
        if path in self.responses:
            return self.responses[path]
        if missing_ok:
            return None
        raise AssertionError("Unexpected request: " + path)


class ReleasePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "leaseledger").mkdir()
        (self.root / "pyproject.toml").write_text('[project]\nversion = "0.1.3"\n')
        (self.root / "leaseledger/__init__.py").write_text('__version__ = "0.1.3"\n')
        (self.root / "CHANGELOG.md").write_text(
            "## 0.1.3 — 2026-10-15\n\n- Fix expiry.\n\n## 0.1.2 — earlier\nOld notes.\n"
        )
        self.api = FakeGitHub()

    def run_publish(self, **overrides):
        args = dict(repository="harshitethic/LeaseLedger", sha=SHA, event="push", ref="refs/heads/main")
        args.update(overrides)
        return publish(self.api, self.root, **args)

    def test_new_release_uses_tested_sha_and_only_matching_changelog(self):
        self.assertEqual(self.run_publish()["status"], "published")
        self.assertEqual(len(self.api.writes), 1)
        path, body = self.api.writes[0]
        self.assertEqual(path, PREFIX + "/releases")
        self.assertEqual(body["tag_name"], "v0.1.3")
        self.assertEqual(body["target_commitish"], SHA)
        self.assertIn("Fix expiry", body["body"])
        self.assertNotIn("Old notes", body["body"])
        self.assertFalse(body["draft"])

    def test_existing_release_is_never_modified(self):
        self.api.responses[PREFIX + "/releases/tags/v0.1.3"] = {"html_url": "existing"}
        self.assertEqual(self.run_publish()["status"], "already_exists")
        self.assertEqual(self.api.writes, [])

    def test_conflicting_tag_is_never_moved(self):
        self.api.responses[PREFIX + "/git/ref/tags/v0.1.3"] = {"object": {"type": "commit", "sha": "b" * 40}}
        with self.assertRaisesRegex(ReleaseError, "refusing to move"):
            self.run_publish()
        self.assertEqual(self.api.writes, [])

    def test_matching_annotated_tag_can_be_released(self):
        self.api.responses[PREFIX + "/git/ref/tags/v0.1.3"] = {"object": {"type": "tag", "sha": "c" * 40}}
        self.api.responses[PREFIX + "/git/tags/" + "c" * 40] = {"object": {"type": "commit", "sha": SHA}}
        self.assertEqual(self.run_publish()["status"], "published")

    def test_superseded_main_commit_does_not_publish(self):
        self.api.responses[PREFIX + "/git/ref/heads/main"] = {"object": {"sha": "b" * 40}}
        self.assertEqual(self.run_publish()["status"], "superseded")
        self.assertEqual(self.api.writes, [])

    def test_pull_request_fork_branch_and_partial_sha_are_rejected(self):
        for args in (
            {"event": "pull_request"},
            {"repository": "other/LeaseLedger"},
            {"ref": "refs/heads/topic"},
            {"sha": "abc"},
        ):
            with self.subTest(args=args), self.assertRaises(ReleaseError):
                self.run_publish(**args)
        self.assertEqual(self.api.writes, [])

    def test_version_disagreement_or_missing_notes_blocks_publication(self):
        for name, content in (
            ("pyproject.toml", '[project]\nversion = "0.1.3rc1"\n'),
            ("leaseledger/__init__.py", '__version__ = "0.1.2"\n'),
            ("CHANGELOG.md", "## 0.1.2 — earlier\nOld notes.\n"),
        ):
            path = self.root / name
            original = path.read_text()
            try:
                path.write_text(content)
                with self.subTest(name=name), self.assertRaises(ReleaseError):
                    self.run_publish()
            finally:
                path.write_text(original)
        self.assertEqual(self.api.writes, [])

    def test_only_explicit_missing_get_404_is_treated_as_absent(self):
        api = GitHub("synthetic-test-token")
        for code in (401, 403, 404, 500):
            error = HTTPError("https://api.github.com/test", code, "test", {}, io.BytesIO(b"private body"))
            with self.subTest(code=code), patch("scripts.publish_release.urlopen", side_effect=error):
                if code == 404:
                    self.assertIsNone(api.request("GET", "/test", missing_ok=True))
                else:
                    with self.assertRaisesRegex(ReleaseError, f"HTTP {code}"):
                        api.request("GET", "/test", missing_ok=True)
        with patch("scripts.publish_release.urlopen", side_effect=HTTPError("url", 404, "test", {}, None)):
            with self.assertRaises(ReleaseError):
                api.request("POST", "/test", {}, missing_ok=True)
