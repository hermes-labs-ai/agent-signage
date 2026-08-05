# Releasing

Three commands. No web UI at any point.

Publishing uses a PyPI API token stored as a repository secret, the same way the other Hermes
Labs packages ship. Trusted publishing (OIDC) is deliberately not used: it is more secure, but
it can only be configured through PyPI's website — there is no API for it and `twine` has no
account-management surface — which would make releases un-scriptable.

## One-time setup

```bash
# 1. create the public repo and push
gh repo create hermes-labs-ai/agent-signage \
  --public --source=. --push \
  --description "Road signs for coding agents: one measured fact at the moment of action, silence otherwise"

# 2. copy the existing PyPI token in from ~/.pypirc
python3 -c "import configparser,os;c=configparser.ConfigParser();c.read(os.path.expanduser('~/.pypirc'));print(c['pypi']['password'])" \
  | gh secret set PYPI_API_TOKEN --repo hermes-labs-ai/agent-signage
```

The token in `~/.pypirc` is **user-scoped**, not project-scoped — verified by decoding its
macaroon caveats offline: it carries a single `RequestUser` caveat and no `ProjectName` or
`ProjectID` caveat. A project-scoped token would carry one of those and would fail on a project
that does not exist yet. This one will not.

## Every release

```bash
# tag and cut the release; the workflow builds, validates and uploads
gh release create v0.1.0 --title "agent-signage 0.1.0" --notes-file RELEASE-0.1.0.md
```

`.github/workflows/publish.yml` triggers on `release: published`, builds sdist and wheel, runs
`twine check`, then uploads.

## Verifying before you cut

Everything the workflow does, run locally first:

```bash
python -m build                 # sdist + wheel
python -m twine check dist/*    # exactly what PyPI validates on upload
pytest && ruff check src tests && agent-signage selftest
```

For a genuine end-to-end rehearsal without burning the version number, upload to TestPyPI —
a separate index with its own account and token:

```bash
python -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ agent-signage
```

## What cannot be undone

**A version number on PyPI is permanent.** You cannot re-upload `0.1.0` after deleting or
yanking it — the filename is burned for good. If a release is wrong, the fix is `0.1.1`, never
a re-upload.

The project name is also claimed permanently on first upload.

## Order matters

Push the GitHub repo **before** publishing to PyPI. The README badges and the primary install
command both point at `github.com/hermes-labs-ai/agent-signage`; publishing first means the
PyPI project page ships with links to a 404.

## Version bump checklist

`__version__` in `src/agent_signage/__init__.py` and `version` in `pyproject.toml` must match.

```bash
python3 -c "
import re,pathlib
i=re.search(r'__version__ = \"([^\"]+)\"',pathlib.Path('src/agent_signage/__init__.py').read_text()).group(1)
p=re.search(r'^version = \"([^\"]+)\"',pathlib.Path('pyproject.toml').read_text(),re.M).group(1)
print(f'init={i} pyproject={p}', 'OK' if i==p else 'MISMATCH')"
```
