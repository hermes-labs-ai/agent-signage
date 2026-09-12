"""A fake `gh` that records exactly what it was given.

Real `gh` is never invoked by the test suite -- it would need credentials, a
network, and a repository someone owns. What the tests need to observe is
narrower and entirely local: the argv the publisher built, the bytes it put on
the child's stdin, and the order the child ran in relative to the sign.

Driven by the environment so one script covers every case:

  FAKE_GH_LOG        append one JSON record per invocation (required)
  FAKE_GH_STORE      file holding the "published" body between create and view
  FAKE_GH_FAIL       exit with this status from `pr create` / `pr edit` / an api write
  FAKE_GH_NO_URL     `pr create` prints no URL; an api comment write prints no JSON
  FAKE_GH_VIEW_BODY  `pr view` / an api comment read returns this body instead
  FAKE_GH_VIEW_FAIL  `pr view` / an api comment read exits non-zero
  FAKE_GH_MUTATE_PATH rewrite this path when the publishing child starts
  FAKE_GH_LOGIN      login printed by `api user` (default roli-lpci)
  FAKE_GH_COMMENT_LOGIN      author login of issue comments (default roli-lpci)
  FAKE_GH_COMMENT_ISSUE_URL  issue_url of a read comment instead of the stored one

`api` issue comments live in FAKE_GH_STORE (body) and FAKE_GH_STORE.issue (the
issue_url recorded at creation; a comment never created reads as issue 7).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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

    if argv[:1] == ["api"]:
        return api(argv[1:], payload, store)

    sys.stderr.write("fake gh: unexpected command %r\n" % (argv,))
    return 2


def api(args, payload, store):
    method, endpoint, index = "GET", None, 0
    while index < len(args):
        if args[index] in ("--hostname", "--method", "-X", "-F", "--field", "--jq", "-q"):
            if args[index] in ("--method", "-X"):
                method = args[index + 1]
            index += 2
            continue
        endpoint = args[index]
        index += 1
    if endpoint == "user":
        sys.stdout.write(os.environ.get("FAKE_GH_LOGIN", "roli-lpci") + "\n")
        return 0

    create = re.fullmatch(r"repos/([^/]+/[^/]+)/issues/(\d+)/comments", endpoint or "")
    single = re.fullmatch(r"repos/([^/]+/[^/]+)/issues/comments/(\d+)", endpoint or "")
    meta = (store or "") + ".issue"
    if (method == "POST" and create) or (method == "PATCH" and single):
        mutate = os.environ.get("FAKE_GH_MUTATE_PATH")
        if mutate:
            with open(mutate, "w", encoding="utf-8") as fh:
                fh.write("MUTATED by the child after preflight; no attribution.\n")
        failure = os.environ.get("FAKE_GH_FAIL")
        if failure:
            sys.stderr.write("fake gh: refusing to publish (simulated)\n")
            return int(failure)
        repo = (create or single).group(1)
        if store:
            with open(store, "wb") as fh:
                fh.write(payload)
            if create:
                with open(meta, "w", encoding="utf-8") as fh:
                    fh.write("https://api.github.com/repos/%s/issues/%s" % (repo, create.group(2)))
        if os.environ.get("FAKE_GH_NO_URL"):
            return 0
        comment_id = 4242 if create else int(single.group(2))
        sys.stdout.write(json.dumps(comment(repo, comment_id, payload.decode("utf-8"), meta)) + "\n")
        return 0
    if method == "GET" and single:
        if os.environ.get("FAKE_GH_VIEW_FAIL"):
            sys.stderr.write("fake gh: could not read the comment\n")
            return 1
        body = os.environ.get("FAKE_GH_VIEW_BODY")
        if body is None and store and os.path.exists(store):
            with open(store, "rb") as fh:
                body = fh.read().decode("utf-8", "replace")
        record = comment(single.group(1), int(single.group(2)), body or "", meta)
        sys.stdout.write(json.dumps(record) + "\n")
        return 0
    sys.stderr.write("fake gh: unexpected api call %r\n" % (args,))
    return 2


def comment(repo, comment_id, body, meta):
    issue_url = os.environ.get("FAKE_GH_COMMENT_ISSUE_URL")
    if issue_url is None and os.path.exists(meta):
        with open(meta, encoding="utf-8") as fh:
            issue_url = fh.read()
    issue_url = issue_url or "https://api.github.com/repos/%s/issues/7" % repo
    return {
        "id": comment_id,
        "body": body,
        "issue_url": issue_url,
        "html_url": "https://github.com/%s/issues/%s#issuecomment-%d"
                    % (repo, issue_url.rsplit("/", 1)[-1], comment_id),
        "user": {"login": os.environ.get("FAKE_GH_COMMENT_LOGIN", "roli-lpci")},
    }


if __name__ == "__main__":
    raise SystemExit(main())
