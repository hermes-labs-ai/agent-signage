"""The skill publisher blocks invalid final bodies without a public request."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_signage import contribution, preflight, publish


@pytest.mark.parametrize('selection', contribution.SELECTIONS)
def test_canonical_policy_source(selection):
    # This fixture is intentionally independent of the implementation generator.
    verb = 'was autonomously selected and produced' if selection == 'autonomous' else 'was produced'
    marker = '<!-- hermes-labs:selection autonomous -->\n' if selection == 'autonomous' else ''
    expected = ('<!-- hermes-labs:attribution v1 -->\n' + marker
                + 'This contribution ' + verb + ' by agents through [Hermes Labs](https://hermes-labs.ai)’ engineering infrastructure. [Rolando Bosch](https://github.com/roli-lpci) is the responsible human contributor and authorized publication from his personal GitHub account.\n'
                + '<!-- /hermes-labs:attribution -->')
    assert contribution.footer(selection) == expected
    assert contribution.check('Summary.\n\n' + expected + '\n', selection).ok


@pytest.mark.parametrize('op', ['pr-create', 'pr-edit'])
@pytest.mark.parametrize('transform', [
    lambda b: 'Summary without footer.',
    lambda b: b.replace('attribution v1', 'attribution v2'),
    lambda b: b.replace('Hermes Labs', 'Hermes Lаbs'),  # Cyrillic lookalike
    lambda b: b.replace('github.com/roli-lpci', 'github.com/another'),
    lambda b: b.replace('selection autonomous', 'selection owner'),
    lambda b: b + '\n' + b,
    lambda b: '<!--\n' + b,
    lambda b: '```markdown\n' + b,
    lambda b: '<pre>\n' + b,
    lambda b: '    ' + b.replace('\n', '\n    '),
])
def test_invalid_footer_starts_no_child(op, transform, tmp_path, monkeypatch):
    path = tmp_path / 'body.md'
    path.write_text(transform(contribution.footer('autonomous')))
    def forbidden(*args):
        pytest.fail('invalid body invoked gh')
    monkeypatch.setattr(publish, '_run', forbidden)
    result = publish.publish(str(path), request(op=op, pr=1))
    assert result.exit_code == publish.EXIT_REJECT


def request(**changes):
    args = dict(op='pr-create', target='upstream/project', kind='contribution',
                oversight='none', selection='autonomous', title='Fix bug')
    args.update(changes)
    return publish.Request(**args)


@pytest.mark.parametrize('account', ['other-user', '', 'roli-lpci\nother', 'failed', 'unavailable'])
def test_wrong_identity_never_mutates(account, tmp_path, monkeypatch):
    path = tmp_path / 'body.md'
    path.write_text(contribution.footer('autonomous'))
    calls = []
    def run(argv, data, timeout):
        calls.append(argv)
        if account == 'unavailable':
            raise OSError('unavailable')
        return subprocess.CompletedProcess(argv, 1 if account == 'failed' else 0, account.encode(), b'')
    monkeypatch.setattr(publish, '_run', run)
    assert publish.publish(str(path), request()).exit_code == publish.EXIT_REJECT
    assert len(calls) == 1 and calls[0][1] == 'api'


@pytest.mark.parametrize('op', ['pr-create', 'pr-edit'])
@pytest.mark.parametrize('selection', contribution.SELECTIONS)
def test_snapshot_identity_and_target(op, selection, tmp_path, monkeypatch):
    path = tmp_path / 'body.md'
    original = contribution.footer(selection)
    path.write_text(original)
    calls = []
    stored = ['existing body']
    def run(argv, data, timeout):
        calls.append(argv)
        if argv[1] == 'api':
            path.write_text('changed after validation')
            assert argv[2:] == ['--hostname', 'github.com', 'user', '--jq', '.login']
            return subprocess.CompletedProcess(argv, 0, b'roli-lpci\n', b'')
        assert argv[argv.index('--repo') + 1] == 'github.com/upstream/project'
        if argv[2] in ('create', 'edit'):
            assert argv[-2:] == ['--body-file', '-']
            assert data.decode() == original
            stored[0] = data.decode()
            return subprocess.CompletedProcess(argv, 0, b'https://github.com/upstream/project/pull/1\n', b'')
        return subprocess.CompletedProcess(argv, 0, json.dumps({'body': stored[0]}).encode(), b'')
    monkeypatch.setattr(publish, '_run', run)
    assert publish.publish(str(path), request(op=op, selection=selection, pr=1)).exit_code == 0
    assert sum(c[2] in ('create', 'edit') for c in calls if c[1] == 'pr') == 1


@pytest.mark.parametrize('path_kind', ['missing', 'symlink', 'stdin', 'relative'])
def test_unreadable_or_indirect_body_fails_closed(path_kind, tmp_path, monkeypatch):
    missing = tmp_path / 'missing'
    paths = {'missing': str(missing), 'stdin': '-', 'relative': 'body.md'}
    symlink = tmp_path / 'link'
    symlink.symlink_to(missing)
    paths['symlink'] = str(symlink)
    monkeypatch.setattr(publish, '_run', lambda *a: pytest.fail('unexpected gh'))
    with pytest.raises(preflight.PreflightError):
        publish.publish(paths[path_kind], request())


def test_inline_cli_executes_only_validated_bytes(tmp_path):
    """Exercise an actual child process without reaching real gh or the network."""
    gh = tmp_path / 'fake-gh'
    log = tmp_path / 'calls.jsonl'
    store = tmp_path / 'body'
    gh.write_text('''#!%s
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['PROBE_LOG'], 'a') as log: log.write(json.dumps(args) + '\\n')
store = Path(os.environ['PROBE_STORE'])
if args[0] == 'api': print('roli-lpci')
elif args[:2] == ['pr', 'create']:
    store.write_bytes(sys.stdin.buffer.read())
    print('https://github.com/upstream/project/pull/1')
else: print(json.dumps({'body': store.read_text()}))
''' % sys.executable)
    gh.chmod(0o755)
    env = dict(os.environ, PROBE_LOG=str(log), PROBE_STORE=str(store),
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'),
               GH_HOST='enterprise.example')
    cmd = [sys.executable, '-m', 'agent_signage', 'publish', 'pr-create',
           '--target', 'upstream/project', '--title', 'Fix', '--kind', 'contribution',
           '--oversight', 'none', '--selection', 'owner', '--gh', str(gh), '--body']
    invalid = subprocess.run(cmd + ['missing'], env=env, capture_output=True)
    assert invalid.returncode == 1
    assert not log.exists()
    valid = subprocess.run(cmd + [contribution.footer('owner')], env=env, capture_output=True)
    assert valid.returncode == 0, valid.stderr + valid.stdout
    assert store.read_text() == contribution.footer('owner')
    assert len(log.read_text().splitlines()) == 3


@pytest.mark.parametrize('selection', contribution.SELECTIONS)
def test_extra_selection_marker_rejected(selection):
    text = contribution.SELECTION_MARK + '\nSummary.\n' + contribution.footer(selection)
    assert not contribution.check(text, selection).ok


def test_unknown_selection_accepts_verified_production_footer():
    neutral = contribution.footer('owner')
    assert contribution.footer('unspecified') == neutral
    assert contribution.check(neutral, 'unspecified').ok


def test_unknown_selection_rejects_autonomous_claim():
    assert not contribution.check(contribution.footer('autonomous'), 'unspecified').ok


@pytest.mark.parametrize('selection', contribution.SELECTIONS)
def test_missing_footer_explains_recovery(selection):
    body = 'Describe the change and its validation.'
    verdict = contribution.check(body, selection)
    reason = next(r for r in verdict.reasons
                  if r.code == 'contribution-footer-mismatch')
    assert 'append the exact %s footer' % selection in reason.detail
    assert 'https://github.com/roli-lpci/agent-signage#publication-boundary' in reason.detail
    assert 'No footer is added automatically' in reason.detail
    repaired = body + '\n\n' + contribution.footer(selection) + '\n'
    assert contribution.check(repaired, selection).ok
