"""`state.clear()` is the only way to wipe stamps by hand, so its `repo`
argument has to actually scope the wipe. Before this test existed, `clear()`
accepted `repo` and silently ignored it -- passing a specific repo still wiped
every session-dedupe, acknowledgement, and fetch-cooldown stamp in the state
dir, for every repo, with no error. This asserts the parameter does what its
signature promises.
"""

from __future__ import annotations

import os

import pytest

from agent_signage import state

pytestmark = pytest.mark.usefixtures("isolated_state")


def _stamp_all(repo: str) -> None:
    state.mark_signed("sess-1", repo, "stale_checkout", "token-a")
    state.acknowledge(repo, "stale_checkout", "token-a")
    state.mark_fetch_attempt(repo)


def _all_stamped(repo: str) -> bool:
    return (
        state.already_signed("sess-1", repo, "stale_checkout", "token-a")
        and state.is_acknowledged(repo, "stale_checkout", "token-a")
        and state.fetch_attempted_recently(repo)
    )


def test_clear_with_no_repo_removes_everything():
    _stamp_all("/repo/a")
    _stamp_all("/repo/b")

    removed = state.clear()

    assert removed == 6  # 3 stamp kinds x 2 repos
    assert not _all_stamped("/repo/a")
    assert not _all_stamped("/repo/b")


def test_clear_scoped_to_repo_leaves_other_repos_untouched():
    """Regression: `clear(repo=...)` must not be a global wipe in disguise."""
    _stamp_all("/repo/a")
    _stamp_all("/repo/b")
    assert _all_stamped("/repo/a") and _all_stamped("/repo/b")

    removed = state.clear(repo="/repo/a")

    assert removed == 3
    assert not _all_stamped("/repo/a")
    assert _all_stamped("/repo/b"), "clear(repo=a) must not remove repo b's stamps"


def test_clear_scoped_to_unknown_repo_removes_nothing():
    _stamp_all("/repo/a")

    removed = state.clear(repo="/repo/never-stamped")

    assert removed == 0
    assert _all_stamped("/repo/a")


def test_clear_ignores_files_it_did_not_write():
    foreign = os.path.join(state.state_dir(), "not-a-stamp")
    open(foreign, "w").close()

    removed = state.clear()

    assert removed == 0
    assert os.path.exists(foreign)


def test_clear_with_no_repo_removes_pre_scoping_stamps():
    """A full wipe must still collect stamps written before repo scoping.

    Those filenames carry no repo segment, so upgrading would otherwise
    strand every stamp the previously installed version wrote.
    """
    legacy = [
        os.path.join(state.state_dir(), "sess-deadbeefcafe1234"),
        os.path.join(state.state_dir(), "ack-deadbeefcafe1234"),
    ]
    for p in legacy:
        open(p, "w").close()

    removed = state.clear()

    assert removed == 2
    assert not any(os.path.exists(p) for p in legacy)


def test_clear_scoped_to_repo_leaves_pre_scoping_stamps():
    """A repo-scoped wipe cannot claim an unlabelled stamp as that repo's."""
    legacy = os.path.join(state.state_dir(), "sess-deadbeefcafe1234")
    open(legacy, "w").close()

    assert state.clear(repo="/repo/a") == 0
    assert os.path.exists(legacy)
