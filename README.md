# agent_in_isolation

Autonomous GitHub CI/Issue Agent — a standalone daemon that monitors GitHub repositories for CI failures and new issues, invokes Claude CLI to fix problems autonomously, and re-submits commits without human intervention.

## Overview

This agent runs in an isolated environment (VM or container) with restricted network access and:

- **Polls GitHub** every 5 minutes for CI failures, new issues, and open PRs
- **Invokes Claude CLI** with scoped prompts to autonomously fix problems
- **Commits & pushes** fixes to feature branches (never to main/master)
- **Merges PRs** when CI is green
- **Maintains audit logs** of all actions

## Architecture

```
agent_in_isolation/
├── agent.py                   # Main polling service
├── config.yaml               # Repo list, intervals, constraints
├── handlers/
│   ├── ci_failure.py        # CI failure detection
│   ├── new_issue.py         # Issue triage
│   └── pr_monitor.py        # PR merging
├── prompts/
│   ├── fix_ci_failure.md    # Claude prompt for CI fixes
│   ├── triage_issue.md      # Claude prompt for issue triage
│   └── monitor_pr.md        # Claude prompt for PR merging
├── network/
│   └── iptables.sh          # Network isolation setup
└── systemd/
    └── agent.service        # Systemd unit (optional)
```

## Setup

### Requirements

- Python 3.10+
- `gh` CLI authenticated with a scoped GitHub token (repo r/w)
- `claude` CLI installed and authenticated

### Installation

```bash
# Clone and install dependencies
git clone <repo> agent_in_isolation
cd agent_in_isolation
pip install -r requirements.txt

# Configure repos to monitor
cp config.yaml config.yaml.local
# Edit config.yaml.local with your repos and settings
```

### Configuration

Edit `config.yaml` to specify:

```yaml
repos:
  - org: your-org
    name: your-repo
    local_path: ~/path/to/local/repo
    watch:
      ci_failures: true
      new_issues: true
      pr_monitor: true

agent:
  poll_interval_seconds: 300     # Poll every 5 minutes
  max_fix_attempts: 3             # Give up after 3 failed fixes
  claude_timeout_seconds: 300     # 5 minute timeout per Claude call

limits:
  never_push_to: [main, master]   # Protected branches
  never_delete_branches: true
  never_merge_without_green_ci: true
```

## Running

### Development

```bash
# Make agent executable
chmod +x agent.py

# Run agent
python3 agent.py --config config.yaml

# View logs in real-time
tail -f agent.log
```

### Production (Systemd)

```bash
# Copy to system location
sudo cp -r . /opt/agent_in_isolation
sudo chown -R agent:agent /opt/agent_in_isolation
sudo cp config.yaml /etc/agent_in_isolation/config.yaml

# Install systemd unit
sudo cp systemd/agent.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable agent.service
sudo systemctl start agent.service

# Monitor
sudo journalctl -u agent.service -f
```

## Network Isolation

To restrict the agent to GitHub and Anthropic APIs only:

```bash
# Setup iptables rules (requires root)
sudo ./network/iptables.sh

# Verify isolation
curl https://google.com      # Should fail
gh api /user                 # Should succeed
```

To restore original iptables:

```bash
sudo iptables-restore < /tmp/iptables-backup.txt
```

## How It Works

### 1. CI Failure Handling

- Detects failed workflow runs via `gh run list`
- Fetches failure logs and branch info
- Invokes Claude with `prompts/fix_ci_failure.md`
- Claude edits files, commits, and pushes to the branch
- Agent monitors for re-run success

### 2. Issue Triage

- Detects new issues via `gh issue list`
- Invokes Claude with `prompts/triage_issue.md`
- Claude analyzes issue, creates a branch if needed, adds comments
- Can label issues or request clarification

### 3. PR Monitoring

- Detects open PRs via `gh pr list`
- Checks CI status for each PR
- Invokes Claude with `prompts/monitor_pr.md` when CI is green
- Claude merges PR or adds comments if issues remain

## Safety Features

- **Protected branches**: Never pushes to main/master
- **Attempt limits**: Gives up after 3 failed fix attempts, labels issue `needs-human`
- **Timeouts**: 5-minute timeout on each Claude invocation
- **Audit logging**: All actions logged with timestamps and outcomes
- **Permission scoping**: Non-root user, read/write to configured repos only
- **Network isolation**: iptables restricts to GitHub + Anthropic APIs

## Audit Log

Every action is logged to `agent.log`:

```
2026-04-09 15:23:45 - INFO - [kvcdr-carb-calculator-client] Polling kvcdr-carb-calculator-client
2026-04-09 15:24:12 - INFO - [kvcdr-carb-calculator-client] ci_failure: success Attempt 1
2026-04-09 15:24:45 - INFO - [kvcdr-carb-calculator-client] new_issue: success Issue #42
```

## Troubleshooting

### Agent not detecting issues/failures

```bash
# Check GitHub token is valid
gh auth status

# Test gh CLI directly
gh run list --repo your-org/your-repo
gh issue list --repo your-org/your-repo
```

### Claude invocation failing

```bash
# Check Claude CLI
claude --version

# Test with manual prompt
echo "Hello" | claude -p "Repeat: {{input}}"

# Check ANTHROPIC_API_KEY is set
echo $ANTHROPIC_API_KEY
```

### Network isolation blocking requests

```bash
# Check current iptables rules
sudo iptables -L OUTPUT -n -v

# Restore original rules and check if issue persists
sudo iptables-restore < /tmp/iptables-backup.txt
```

## Development Notes

### Adding a new event handler

1. Create handler in `handlers/new_handler.py`
2. Add prompt template in `prompts/new_handler.md`
3. Add detection method to `Agent` class in `agent.py`
4. Add handler invocation in polling loop

### Testing

```bash
# Trigger CI failure manually
git checkout -b test/break-ci
# Introduce a lint error or failing test
git push -u origin test/break-ci

# Monitor agent response
tail -f agent.log

# Agent should detect failure, invoke Claude, and push fix
```

## Monitoring and Alerting

In production, consider:

- Monitoring `agent.log` for `error` or `max_attempts_reached` lines
- Setting up alerts if agent hasn't polled in 10 minutes
- Tracking `needs-human` labels in GitHub
- Monitoring agent process uptime

## License

Proprietary — for internal use only.
