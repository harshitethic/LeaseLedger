# Verified GitHub releases

LeaseLedger publishes a GitHub release from a successful `main` push when
`pyproject.toml` names a version that has no release yet. This publishes source
archives and changelog notes on GitHub. It does not publish to PyPI or deploy
a running service.

## Gates and permissions

The release job needs every Python 3.11–3.13 test/packaging job, the four-process
benchmark smoke, Ruff checks and the reusable CodeQL job to succeed. It never
runs for pull requests, other branches or forks. The same CodeQL workflow also
runs weekly. Test jobs retain read-only repository access; only the final
publication job receives `contents: write`. Its checkout does not persist Git
credentials, and the token is supplied only to the publication step.

`scripts/publish_release.py` uses the GitHub API with a request timeout. It:

1. Checks the repository, push event, main ref and full tested commit SHA.
2. Requires a stable `major.minor.patch` version that matches `__version__`.
3. Extracts nonempty notes for that exact version from `CHANGELOG.md`.
4. Checks that main still identifies the tested commit at the time of the check.
5. Leaves any existing release unchanged; a later docs commit is not a release bump.
6. If an unreleased tag already exists, resolves it and refuses any different
   commit. It never deletes, moves or force-updates a tag.
7. Publishes the release with the tested SHA as the target and links to that
   commit's changelog. Concurrent publication jobs are serialized.

Only an explicit GET 404 is treated as missing. Authentication, permission,
rate-limit and server errors fail the job instead of being interpreted as
permission to create a new release. A partial API failure does not trigger
blind retry or mutation of an existing release. The script reads source text
and package metadata; it does not import package code to discover the version.

The main-ref check is not an atomic lock against other pushes. A push after
that check may leave a release pointing at the earlier, successfully tested
commit; the immutable target is still the verified SHA. Version tags prevent
this workflow from quietly retargeting that release.

## Maintainer procedure

- Update both `pyproject.toml` and `leaseledger/__init__.py` in a normal PR.
- Add `## VERSION — DATE` and substantive notes to the top of `CHANGELOG.md`.
- Verify tests and review the change. Merge only after CI is green.
- Check the main workflow's **Publish verified version** result and the tag's
  commit. PR checks do not themselves publish a release.
- If publication fails, inspect the failure before retrying the failed main
  workflow job. Permission errors must be resolved rather than ignored.
- If a tag conflicts, investigate who created it. Do not force-move a public tag;
  use a reviewed new version if appropriate. Already published releases are
  left untouched, including drafts created outside this workflow.

Eight offline tests cover the successful request, immutable existing releases,
conflicting and annotated tags, superseded main, forbidden contexts, inconsistent
metadata and API error classification:

```bash
python -m unittest tests.test_release_publication -v
```

These tests use synthetic API fixtures. The first successful main publication
provides live integration evidence; unit tests alone do not prove token scope
or GitHub service availability.
