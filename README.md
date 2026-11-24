# LLVM digest (DAM agent)

Watches **LLVM Discourse** and **`llvm/llvm-project` GitHub issues** for three areas and
posts concise executive summaries to Slack:

| Category | Discourse | GitHub labels |
|---|---|---|
| `[Sanitizers]` | category 12/73, keywords (ASan, MSan, TSan, UBSan, HWASan, ...) | `compiler-rt:*san`, `compiler-rt:sanitizer` |
| `[LLC Backend]` | CodeGen categories 51-59, AMDGPU, keywords (ISel, TableGen, ...) | `backend:*`, `tools:llc`, `llvm:codegen`, `llvm:globalisel`, `tablegen` |
| `[Clang Frontend]` | Clang categories 6/47/48/49/113, keywords (Sema, parser, ...) | `clang`, `clang:*`, `extension:clang` |

Issues only (pull requests are excluded). The default look-back window is 48 hours.

## How it works on DAM

```
schedule (hourly) -> precheck: llvm-digest precheck
                       exit 1 = nothing new  -> run skipped, no model call
                       exit 0 = JSON of new items appended to the prompt
                  -> agent summarises each item (CLAUDE.md format)
                  -> agent posts to its bound Slack channel (send_channel_message)
                  -> llvm-digest mark-posted <keys>
```

State (processed items) lives in SQLite at `state/llvm_digest.db`, keyed `discourse:<topic_id>` /
`github:<issue_number>`, on DAM's persistent volume.

## Setup on DAM

1. Create an agent (Claude or Bob harness) from this repository (`kit.yaml`), or clone it into `~/work`.
2. Run `pip install -e .` (the kit's install command does this).
3. Bind the agent to a Slack channel (`/dam bind`, then `/dam login` once).
4. Network access: allow `discourse.llvm.org`. Granting a GitHub connection adds `api.github.com`.
5. Keep the schedule's precheck: `llvm-digest precheck`.
6. Test with **Run now** on the schedule.

## Standalone use

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env              # fill in values locally; never commit .env
.venv/bin/llvm-digest preview     # fetch live data, print the summaries; posts nothing
.venv/bin/llvm-digest run --dry-run
WEBHOOK_URL=... .venv/bin/llvm-digest run   # post via a Slack Incoming Webhook
```

## Development

```bash
.venv/bin/pytest                  # unit tests (network is mocked)
RUN_LIVE=1 .venv/bin/pytest -m live
.venv/bin/mypy
```

## Security

* Webhook URL, GitHub token and LLM key come from the environment only and are never logged.
* Post content from the forum is treated as untrusted: Slack text is escaped and `@channel`-style
  mentions are neutralised; the LLM prompt tells the model to ignore instructions in the post.
* Egress is limited to the hosts listed above.
