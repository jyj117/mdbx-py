# Release checklist

Released versions and filenames on PyPI are immutable. `clibmdbx` therefore
uses one reviewed artifact set which is uploaded to TestPyPI, verified by exact
filename and SHA-256, and only then promoted unchanged to PyPI. Do not rebuild
between indexes and never enable `skip-existing` for the production upload.

The authoritative workflow is `.github/workflows/clibmdbx-release.yml`. An
ordinary branch push cannot publish. A `clibmdbx-v*` tag starts five native
wheel builds, one reproducible sdist build, artifact-set validation, protected
TestPyPI publishing, clean-index smoke tests, protected PyPI publishing, and
post-publish verification. Only the two upload jobs receive `id-token: write`;
all third-party actions in this workflow are pinned to immutable commits.

## Audit against the supplied PyPI guide

The supplied [BACH Studio publishing guide](https://github.com/BACH-AI-Tools/hot-news-bachstudio/blob/main/PYPI%E5%8F%91%E5%B8%83%E6%8C%87%E5%8D%97.md)
correctly requires a version bump, clean build, `twine check`, TestPyPI-first
upload, clean installation test, immutable version and only then a PyPI upload.
Every one of those gates is present here. The clibmdbx workflow deliberately
strengthens the generic guide for a compiled multi-platform package:

- Trusted Publishing OIDC replaces long-lived tokens in `~/.pypirc`; a
  project-scoped token remains only as an explicitly approved fallback.
- The tag is checked against every embedded version declaration before build.
- Twenty-five platform/CPython wheels and one reproducible sdist are assembled
  into one immutable artifact set; production never rebuilds TestPyPI files.
- Each archive is checked for ABI/platform tags, licenses, private data,
  metadata and unexpected external libraries before it can be published.
- TestPyPI and PyPI each have separate protected environments and approvals;
  both indexes are read back and compared by exact filename and SHA-256.
- A clean venv installs from each index and exercises import, CRUD, thread,
  lifecycle and fork behavior after upload.

Do not run the guide's example `publish.sh`, copy its `hot-news-mcp` package
name, or put tokens into a persistent `.pypirc` for this project. The tag-driven
workflow and the exact commands below are the clibmdbx release procedure.

## One-time publisher setup (required)

The public release repository is
[`jyj117/mdbx-py`](https://github.com/jyj117/mdbx-py). Keep the workflow filename,
repository name, package name and environment names below unchanged because
PyPI matches all of them against the GitHub OIDC identity.

Then complete all of the following on both PyPI and TestPyPI:

1. Create separate accounts as required by the two services, enable 2FA, retain
   recovery codes securely, and verify the email addresses.
2. In the GitHub repository create protected environments named exactly
   `testpypi` and `pypi`. Add required reviewers; permit deployment only from
   protected tags matching `clibmdbx-v*`.
3. Protect tags matching `clibmdbx-v*` so only release maintainers can create or
   update them. Protect changes to the release workflow with code review.
4. Register a pending Trusted Publisher for `clibmdbx` separately on TestPyPI
   and PyPI. Supply the exact GitHub owner/repository, workflow filename
   `clibmdbx-release.yml`, and environment `testpypi` or `pypi` respectively.
5. Verify the repository, issue tracker and changelog URLs in `pyproject.toml`.
   Enable GitHub private vulnerability reporting so the form linked from
   `SECURITY.md` is available before the repository is announced.

The pending publisher does not reserve the name. Checks on 2026-08-28 returned
HTTP 404 for `clibmdbx` on PyPI and TestPyPI, but the final name claim happens
only when the first upload succeeds. Do the pending-publisher setup and first
release close together.

## Release gate

- [ ] Version is new and PEP 440 compliant. It agrees in `pyproject.toml`,
      `_version.py`, `setup.py`, C diagnostics, `CHANGELOG.md`, and the SBOM.
- [ ] `python scripts/check_version.py <version>`, vendor verification and the
      C API coverage check pass.
- [ ] Strict tests pass on CPython 3.10-3.14; ASan/UBSan, Valgrind and
      `mdbx_chk` pass on Linux.
- [ ] The candidate commit's wheel workflow passes natively on Linux x86_64 and
      aarch64, macOS x86_64 and arm64, and Windows AMD64. Every wheel's own
      cibuildwheel test must pass.
- [ ] `auditwheel`, `delocate-listdeps`, and Windows PE inspection show no
      external libmdbx or unexpected dependency.
- [ ] Two `SOURCE_DATE_EPOCH` sdists are byte-identical. Archives contain all
      licenses/notices and no credentials, MDBX databases, private FSJ data or
      business code.
- [ ] `python -m twine check --strict dist/*`, artifact inspection and clean
      wheel/sdist install, import, CRUD, concurrency, fork and uninstall tests
      pass.
- [ ] Three benchmark runs show no unexplained regression and retain raw JSON.
- [ ] Changelog, package description on `twine check`, repository links,
      security channel, GitHub environments and both pending publishers are
      reviewed by a second maintainer.

## Tag-driven release

Run the final local checks from the standalone repository root; these commands
do not upload:

```bash
python scripts/check_version.py 1.0.3
python scripts/check_release_configuration.py
python scripts/verify_vendor.py
python scripts/check_api_coverage.py
python -m pytest -q
python scripts/audit_production_edges.py
# Run probe_disk_full.py only on a disposable, strictly limited filesystem.
python -m build
python -m twine check --strict dist/*
python scripts/inspect_artifacts.py dist/*
```

After every prerequisite above is satisfied, create and push a signed annotated
tag to the configured GitHub repository:

```bash
git tag -s clibmdbx-v1.0.3 -m "clibmdbx 1.0.3"
git push <github-remote> clibmdbx-v1.0.3
```

The workflow then performs this fixed sequence:

1. Validate tag, package versions, vendored source and API coverage.
2. Build and test 25 CPython-specific wheels (3.10-3.14 across five native
   platform/architecture targets) plus one sdist.
3. Reject missing/extra targets, run strict metadata/archive checks and save a
   `SHA256SUMS` artifact.
4. Wait for approval on the `testpypi` environment, publish with OIDC and
   attestations, compare the complete TestPyPI release to local SHA-256 values,
   then install the wheel in a clean venv and test lifecycle/concurrency/fork.
5. Wait for separate approval on the `pypi` environment and upload the same
   `release-dist` artifact with OIDC and attestations.
6. Compare the complete PyPI release to the reviewed hashes and repeat the clean
   install tests from the production index.

Install the exact reviewed stable release:

```bash
python -m pip install --only-binary=:all: "clibmdbx==1.0.3"
```

## Failure and rerun rules

- If a build, test, metadata or artifact-set job fails, fix it, increment the
  version if either index received any file, and make a new tag.
- TestPyPI upload permits already-existing filenames only to recover a partial
  workflow rerun. The following verification job still requires every filename
  and digest to match, so stale or different files cannot reach PyPI.
- Production PyPI upload fails loudly on duplicates. Never delete and recreate a
  public version, move a published tag, or overwrite release files.
- If only a downstream verification job failed, use GitHub's “re-run failed
  jobs” action; do not re-run the complete workflow unnecessarily.
- A partially completed PyPI upload needs maintainer review before recovery.
  Upload only the missing files from the retained `release-dist` artifact and
  re-run hash verification; never rebuild them.

## API-token fallback

The preferred path is Trusted Publishing. If an explicitly approved release
must be done without GitHub OIDC, use separate project-scoped TestPyPI/PyPI API
tokens, `TWINE_USERNAME=__token__`, and a temporary `TWINE_PASSWORD` environment
variable or secure prompt. Do not commit `.pypirc`, tokens, shell history, or CI
secrets. Download the consolidated `release-dist` artifact and run the same
`twine check`, archive inspection, TestPyPI upload, hash check, clean install,
and then PyPI upload. Never publish the single-platform `dist/` produced by an
ordinary developer machine as a full release.

No workflow, script, or checklist in this repository uploads merely by being
run locally. Actual TestPyPI/PyPI writes require the tag, configured publisher,
protected environment approval, and the external index.
