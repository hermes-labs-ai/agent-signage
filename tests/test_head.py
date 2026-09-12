"""Cross-fork head selectors remain one validated argv value."""
import pytest

from agent_signage import preflight, publish


def request(head, **changes):
    values = dict(op='pr-create', target='upstream/project', kind='contribution',
                  oversight='none', title='Fix', head=head)
    values.update(changes)
    return publish.Request(**values)


@pytest.mark.parametrize('head', ['fix/little-canary-degraded-verdict',
    'roli-lpci:fix/little-canary-degraded-verdict'])
def test_plain_and_cross_fork_head_preserved(head):
    req = request(head)
    publish.validate_request(req)
    argv = publish.publish_argv(req)
    assert argv[argv.index('--head') + 1] == head


@pytest.mark.parametrize('head', [':branch', 'owner:', 'owner:one:two',
    '-owner:branch', 'owner-:branch', 'bad_owner:branch', 'bad--owner:branch',
    'a' * 40 + ':branch', 'owner:--help', 'owner:foo//bar', 'owner:/foo',
    'owner:foo/', 'owner:.hidden', 'owner:foo/../bar', 'owner:foo..bar',
    'owner:foo.lock', 'owner:foo.lock/bar', 'owner:foo.',
    'owner:foo$(id)', 'owner:foo;bar', 'owner:foo bar', 'owner:foo\nbar'])
def test_invalid_head_blocks_before_children(head, tmp_path, monkeypatch):
    path = tmp_path / 'body.md'
    path.write_text('No public effect can be reached.')
    monkeypatch.setattr(publish, '_run', lambda *args: pytest.fail('unexpected child'))
    with pytest.raises(preflight.PreflightError, match='--head'):
        publish.publish(str(path), request(head))


def test_cross_fork_syntax_does_not_expand_base_or_target():
    with pytest.raises(preflight.PreflightError, match='--base'):
        publish.validate_request(request('owner:branch', base='owner:main'))
    with pytest.raises(preflight.PreflightError, match='--target'):
        publish.validate_request(request('owner:branch', target='owner:repo'))
