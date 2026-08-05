# agent-signage 0.1.1

A packaging fix. No behaviour changes, no new dependencies, the six signs are unchanged.

## What was wrong

**The source distribution shipped files that were never meant to leave the working tree** — a
directory of internal integration-strategy notes, the release process document, and two sets of
release notes. The cause was the default sdist rule of "include everything git doesn't ignore."
The wheel was unaffected, so `pip install` never delivered any of it; only a direct sdist
download did.

The sdist is now built from an **explicit allow-list**. A stray file can no longer ride along by
default, which is the actual fix — excluding the one directory I happened to notice would have
left the same failure mode in place. Auditing the built artifact turned up two more offenders
than the one that prompted this.

**`py.typed` was missing.** `pyproject.toml` has declared the `Typing :: Typed` classifier since
0.0.8. That classifier is a promise to type checkers, and without the marker file in the
installed wheel, mypy and pyright silently resolved every import from this package to `Any`.
Verified fixed by installing the wheel into a clean environment and type-checking against it:
`agent_signage.hook.run` now reveals a concrete signature rather than `Any`.

Both changes are the same category — the artifact not matching its own declared metadata.

## Verification

68 tests, ruff clean, `twine check` passes on both artifacts, installs into a clean virtualenv
with zero dependencies. Both the sdist and the wheel were listed file-by-file and confirmed to
contain nothing unintended.
