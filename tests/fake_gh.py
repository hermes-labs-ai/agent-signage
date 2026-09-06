"""A fake `gh` that records exactly what it was given.

Real `gh` is never invoked by the test suite -- it would need credentials, a
network, and a repository someone owns. What the tests need to observe is
narrower and entirely local: the argv the publisher built, the bytes it put on
the child's stdin, and the order the child ran in relative to the sign.

Driven by the environment so one script covers every case:

  FAKE_GH_LOG        append one JSON record per invocation (required)
  FAKE_GH_STORE      file holding the "published" body between create and view
  FAKE_GH_FAIL       exit with this status from `pr create` / `pr edit`
  FAKE_GH_NO_URL     `pr create` succeeds but prints no URL
  FAKE_GH_VIEW_BODY  `pr view` returns this body instead of the stored one
  FAKE_GH_VIEW_FAIL  `pr view` exits non-zero
  FAKE_GH_MUTATE_PATH rewrite this path when the publishing child starts
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

URL = "https://github.com/hermes-labs-ai/agent-signage/pull/99"


def main() -> int:
    argv = sys.argv[1:]
    payload = b""
    if not sys.stdin.isatty():
        try:
            payload = sys.stdin.buffer.read()
        except (OSError, ValueError):
            payload = b""

    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({
            "argv": argv,
            "stdin_len": len(payload),
            "stdin_sha256": hashlib.sha256(payload).hexdigest(),
            "stdin_text": payload.decode("utf-8", "replace"),
        }) + "\n")

    store = os.environ.get("FAKE_GH_STORE")
    if argv[:2] in (["pr", "create"], ["pr", "edit"]):
        mutate = os.environ.get("FAKE_GH_MUTATE_PATH")
        if mutate:
            with open(mutate, "w", encoding="utf-8") as fh:
                fh.write("MUTATED by the child after preflight; no attribution.\n")
        failure = os.environ.get("FAKE_GH_FAIL")
        if failure:
            sys.stderr.write("fake gh: refusing to publish (simulated)\n")
            return int(failure)
        if store:
            with open(store, "wb") as fh:
                fh.write(payload)
        if not os.environ.get("FAKE_GH_NO_URL"):
            sys.stdout.write(URL + "\n")
        return 0

    if argv[:2] == ["pr", "view"]:
        if os.environ.get("FAKE_GH_VIEW_FAIL"):
            sys.stderr.write("fake gh: could not read the pull request\n")
            return 1
        body = os.environ.get("FAKE_GH_VIEW_BODY")
        if body is None and store and os.path.exists(store):
            with open(store, "rb") as fh:
                body = fh.read().decode("utf-8", "replace")
        sys.stdout.write(json.dumps({"body": body or ""}) + "\n")
        return 0

    sys.stderr.write("fake gh: unexpected command %r\n" % (argv,))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
