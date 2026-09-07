# Reliability Lab fixtures

Source envelopes used only to demonstrate `python -m agent_signage.evidence`.
Every file is a hand-authored, syntactically valid or deliberately broken
`hermes.reliability-lab.result/1` record — not a live capture from another
product, so this fixture set has no dependency on any other repository in
the tranche. Regenerate nothing by hand here; these are inputs, not outputs.

| File | Shape |
|---|---|
| `completed-pass-with-metric.json` | mode executed, status pass — licenses a card |
| `completed-pass-no-metric.json` | mode executed, status pass, caller declares no fact |
| `completed-warn.json` | mode executed, status warn — no card, correct silence |
| `preview.json` | mode preview, status pass — not a completed event, no card |
| `malformed-schema.json` | valid JSON, wrong `envelope` string |
| `malformed-json.txt` | not JSON at all |
