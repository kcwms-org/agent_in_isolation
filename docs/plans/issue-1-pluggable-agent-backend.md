# Plan: Pluggable Agent Backend — Issue #1

**Status:** Done

## Context

Currently `agent_in_isolation` hardcodes the Claude CLI as the sole AI backend. The GitHub issue requests the ability to plug in other agents (e.g., OpenAI, local models, custom CLIs). The hardcoded invocation at `agent.py:197-201` constructs a `claude`-specific command; the binary name, flags, and prompt-passing convention are all literals.

## Goal

Make the AI backend fully configurable via `config.yaml` so that any CLI-based agent can be swapped in without touching Python code.

## Approach

### 1. Update `config.yaml` — add a `backend` block

```yaml
agent:
  backend:
    name: claude                         # Identifier (for logging)
    cli_path: claude                     # Binary name or absolute path
    cli_flags:
      - --dangerously-skip-permissions   # Zero or more flags prepended to every call
    prompt_arg: -p                       # Flag used to pass the prompt (null = use stdin)
  poll_interval_seconds: 300
  max_fix_attempts: 3
  timeout_seconds: 300                   # Renamed from claude_timeout_seconds
```

- `prompt_arg: null` means pipe the prompt on stdin instead of as a CLI argument — needed for CLIs that don't accept a `-p` flag.
- Keep `claude_timeout_seconds` as a fallback alias so existing configs don't break immediately; log a deprecation warning.

### 2. Rename and refactor `invoke_claude` → `invoke_agent` in `agent.py`

Replace the hardcoded block:

```python
cmd = [
    'claude',
    '--dangerously-skip-permissions',
    '-p', prompt
]
```

With config-driven construction:

```python
backend = self.config.get('agent', {}).get('backend', {})
cli = backend.get('cli_path', 'claude')
flags = backend.get('cli_flags', ['--dangerously-skip-permissions'])
prompt_arg = backend.get('prompt_arg', '-p')

cmd = [cli] + flags
if prompt_arg:
    cmd += [prompt_arg, prompt]
```

If `prompt_arg` is `None`/absent, pass prompt via `stdin` to `_run_command`.

### 3. Update `_run_command` to accept optional stdin

```python
def _run_command(self, cmd, cwd=None, timeout=30, stdin_text=None):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        input=stdin_text,         # None = no stdin
        capture_output=True,
        text=True,
        timeout=timeout
    )
```

### 4. Update timeout config key

Read `config.agent.timeout_seconds` first; fall back to `config.agent.claude_timeout_seconds` for backward compat.

## Files to Change

| File | Change |
|------|--------|
| `config.yaml` | Add `agent.backend` block; rename timeout key |
| `agent.py` | Rename `invoke_claude` → `invoke_agent`; build cmd from config; update `_run_command` for stdin |

## Files NOT changing

- `handlers/` — no changes needed
- `prompts/` — templates are already backend-agnostic text; swap them per-deployment if needed
- `systemd/agent.service` — no changes needed

## Verification

1. Run agent with existing `claude` backend — behaviour unchanged
2. Add a second backend entry (e.g., `cli_path: echo`, `prompt_arg: null`) and confirm prompt is piped on stdin
3. Confirm `agent.log` includes the backend `name` on each invocation line
4. Python: `python3 -m py_compile agent.py` passes with no errors
