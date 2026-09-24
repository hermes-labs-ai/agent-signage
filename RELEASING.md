# Releasing

Releases are published from the existing
[`roli-lpci/agent-signage`](https://github.com/roli-lpci/agent-signage) repository.
Publishing a GitHub release triggers `.github/workflows/publish.yml`, which builds
the source distribution and wheel, checks them with `twine`, and uploads to PyPI
using trusted publishing (OIDC). The workflow does not use a long-lived PyPI token.

## Verify the publisher configuration

Before the next release, verify that the `agent-signage` project on PyPI trusts
this repository and workflow, especially after an ownership transfer:

- GitHub owner: `roli-lpci`
- Repository: `agent-signage`
- Workflow filename: `publish.yml`
- GitHub environment: `pypi`

These values describe the checked-in workflow; they do not prove that the PyPI
account has the corresponding publisher registration. A project maintainer must
verify that registration on PyPI before publishing. See
[PyPI's trusted-publisher setup guide](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
Do not recreate the repository or copy a token from a developer's machine.

## Prepare and check a release

Choose a new package version, update `version` in `pyproject.toml` and
`__version__` in `src/agent_signage/__init__.py` together, and prepare release notes
for that version. From a development environment with the build and test tools:

```bash
pytest && ruff check src tests && agent-signage selftest
release_version=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
python -m build --outdir "dist/$release_version"
python -m twine check "dist/$release_version"/*
```

The version lookup uses Python 3.11 or newer; the publishing workflow uses 3.12.
Inspect both archives and verify a regular wheel installation before publishing.
Use a clean build directory so an older distribution cannot be uploaded by mistake.

## Publish and verify

After the release checks and publisher registration are verified, create the
release from a clean checkout of the verified release commit and its
version-specific notes:

```bash
release_commit=$(git rev-parse HEAD)
gh release create "v$release_version" \
  --repo roli-lpci/agent-signage --target "$release_commit" \
  --title "agent-signage $release_version" \
  --notes-file "RELEASE-$release_version.md"
```

The release targets the checked-out commit. This command publishes a GitHub release and starts the PyPI workflow; it is not a dry run.
Check the workflow result and install that exact version from PyPI in a fresh
environment before declaring the release successful. A published GitHub release
alone does not establish a successful package upload.

PyPI distribution filenames cannot be reused. If an uploaded release needs a
fix, publish a new version rather than deleting and re-uploading the same files.
