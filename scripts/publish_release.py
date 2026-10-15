"""Publish an immutable GitHub release after CI gates; never overwrite a tag."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import tomllib
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ReleaseError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def request(self, method: str, path: str, body=None, *, missing_ok=False):
        request = Request(
            "https://api.github.com" + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if missing_ok and method == "GET" and exc.code == 404:
                return None
            raise ReleaseError(f"GitHub {method} {path} returned HTTP {exc.code}") from None


def release_notes(root: Path):
    config = tomllib.loads((root / "pyproject.toml").read_text())
    version = config["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError("Release version must be a stable three-part version")
    package = ast.parse((root / "leaseledger/__init__.py").read_text())
    versions = [
        ast.literal_eval(node.value)
        for node in package.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
    ]
    if versions != [version]:
        raise ReleaseError("Package and project versions disagree")
    changelog = (root / "CHANGELOG.md").read_text()
    section = re.search(r"^## " + re.escape(version) + r" — [^\n]+\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if section is None or not section.group(1).strip():
        raise ReleaseError("Version needs nonempty changelog notes")
    return "v" + version, section.group(1).strip()


def publish(api: GitHub, root: Path, *, repository: str, sha: str, event: str, ref: str):
    if repository != "harshitethic/LeaseLedger" or event != "push" or ref != "refs/heads/main":
        raise ReleaseError("Publication is restricted to this repository's main push workflow")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ReleaseError("A full tested commit SHA is required")
    tag, notes = release_notes(root)
    prefix = f"/repos/{repository}"
    main = api.request("GET", prefix + "/git/ref/heads/main")
    if main["object"]["sha"] != sha:
        return {"status": "superseded", "tag": tag}
    existing = api.request("GET", prefix + "/releases/tags/" + tag, missing_ok=True)
    if existing is not None:
        return {"status": "already_exists", "tag": tag, "url": existing["html_url"]}
    reference = api.request("GET", prefix + "/git/ref/tags/" + tag, missing_ok=True)
    if reference is not None:
        target = reference["object"]
        for _ in range(5):
            if target["type"] != "tag":
                break
            target = api.request("GET", prefix + "/git/tags/" + target["sha"])["object"]
        if target["type"] != "commit" or target["sha"] != sha:
            raise ReleaseError(
                "Existing version tag does not identify the tested commit; refusing to move it"
            )
    result = api.request(
        "POST",
        prefix + "/releases",
        {
            "tag_name": tag,
            "target_commitish": sha,
            "name": tag,
            "body": notes
            + f"\n\nTested commit: `{sha}`.\n\nFull changelog: "
            + f"https://github.com/{repository}/blob/{sha}/CHANGELOG.md",
            "draft": False,
            "prerelease": False,
        },
    )
    return {"status": "published", "tag": tag, "url": result["html_url"]}


def main():
    result = publish(
        GitHub(os.environ["GH_TOKEN"]),
        Path(__file__).resolve().parents[1],
        repository=os.environ["GITHUB_REPOSITORY"],
        sha=os.environ["GITHUB_SHA"],
        event=os.environ["GITHUB_EVENT_NAME"],
        ref=os.environ["GITHUB_REF"],
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
