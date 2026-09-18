# Using agent-signage from a non-Claude harness

`agent-signage` is a subprocess with no framework dependency: it reads one JSON
object on stdin and writes one JSON object (or nothing) to stdout, then exits
0. Any harness that can shell out before a file operation and forward the
result can use it, provided it can deliver a text field to the model
somewhere near the tool result.

## The contract

**Input** (stdin, one JSON object):

```json
{
  "session_id": "any-string-identifying-this-agent-run",
  "tool_name": "Read",
  "tool_input": {"file_path": "/absolute/or/relative/path/being/touched"}
}
```

- `tool_input.file_path` is checked for `Read`/`Edit`/`Write`/`NotebookEdit`-style
  tools. `tool_input.path` and `tool_input.notebook_path` are also accepted, so a
  `Grep`/`Glob`-style tool works without translation.
- `session_id` scopes de-duplication: the same sign is emitted at most once per
  `(session_id, repo)` pair. If your harness has no session concept, reuse a
  stable string for the run.
- Anything else in the payload is ignored.

**Output** (stdout): either nothing (exit 0, empty stdout — the common case),
or one JSON object:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "STALE CHECKOUT - ..."
  }
}
```

`additionalContext` is a single line of plain text. There is no `decision` key
and no block/deny mechanism anywhere in the output — `agent-signage` cannot
stop a tool call, only annotate one. A non-Claude harness should take
`hookSpecificOutput.additionalContext` and surface it to the model however it
surfaces tool metadata (a system note, a prepended line in the tool result,
etc.). Exit code is always `0`, whether or not anything was said.

## A real, runnable example

This builds a throwaway git repo two commits behind a throwaway "origin," then
runs the hook against it exactly as a harness would, so the input and output
below are not illustrative — they are what the commands actually produce.

```bash
pip install agent-signage

WORK=$(mktemp -d)
git init -q -b main "$WORK/origin"
git -C "$WORK/origin" config user.email t@t.t
git -C "$WORK/origin" config user.name t
echo x > "$WORK/origin/a.txt"
git -C "$WORK/origin" add a.txt
git -C "$WORK/origin" commit -qm "add a.txt"

git clone -q "$WORK/origin" "$WORK/clone"
git -C "$WORK/clone" config user.email t@t.t
git -C "$WORK/clone" config user.name t

echo y > "$WORK/origin/b.txt" && git -C "$WORK/origin" add b.txt && git -C "$WORK/origin" commit -qm "add b.txt"
echo z > "$WORK/origin/c.txt" && git -C "$WORK/origin" add c.txt && git -C "$WORK/origin" commit -qm "add c.txt"
git -C "$WORK/clone" fetch -q

PAYLOAD=$(printf '{"session_id":"demo","tool_name":"Read","tool_input":{"file_path":"%s/clone/a.txt"}}' "$WORK")
echo "$PAYLOAD" | python3 -m agent_signage
```

Output (pretty-printed here for readability; the real output is one line with
no trailing newline):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "STALE CHECKOUT - clone is 2 commit(s) behind origin/main (upstream tip 10 seconds ago). This working copy may not be what is deployed; confirm which source is authoritative before treating work here as fixing the live system. Inspect: git -C /tmp/.../clone log --oneline HEAD..@{u}"
  }
}
```

Run the same `echo ... | python3 -m agent_signage` a second time in the same
session and it prints nothing — the sign already fired once for this
`(session_id, repo)` pair. Run `git -C "$WORK/clone" merge -q origin/main`
first (making the clone current) and it prints nothing from the start: the
fact it would report is no longer true.

## Wiring it into a harness loop

The general shape, independent of language:

```
before each file-touching tool call:
    payload = {"session_id": <run id>, "tool_name": <tool>, "tool_input": {"file_path": <path>}}
    result  = run(["python3", "-m", "agent_signage"], stdin=json.dumps(payload), timeout=8)
    if result.stdout is non-empty:
        text = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        attach text to the model's view of this tool call
    proceed with the tool call regardless of result — agent-signage never blocks
```

A `timeout` on your side is good practice even though the hook enforces its
own internal deadline (`hook.DEADLINE_S`, 3 seconds) and fails open on every
error path.
