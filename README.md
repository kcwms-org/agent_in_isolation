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
│   ├── iptables.sh          # Network isolation setup
│   └── iptables-restore.sh  # Restore rules (handles missing backup)
└── systemd/
    └── agent.service        # Systemd unit (optional)
```

## Server Requirements

Recommended host (e.g. a DigitalOcean Droplet):

| Resource | Minimum | Recommended |
|---|---|---|
| vCPU | 1 | 2 |
| RAM | 2 GB (+2 GB swap) | 4 GB |
| Disk | 25 GB | 50–80 GB |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

Recommended: Basic Droplet, 2 vCPU / 4 GB RAM / 80 GB SSD.

The agent process itself is lightweight. Sizing is driven by the `claude` CLI
(~300–500 MB per invocation) and by builds/tests that Claude runs in the
monitored repos (can spike to 1–2 GB). Move up to 4 vCPU / 8 GB if you monitor
several repos or repos with heavy builds.

Networking: outbound HTTPS only. Open no inbound ports except SSH, and restrict
SSH to your IP with a cloud firewall.

## FIRST STEPS

One-time server configuration, in order. Run as a sudo-capable user unless noted.

### 1. Update the system and create the service user

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ca-certificates build-essential python3 python3-pip python3-venv iptables logrotate

# Non-root user that runs the agent
sudo adduser --disabled-password --gecos "" agent
```

Optional but recommended on a 2 GB droplet — add swap:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 2. Harden SSH and enable a firewall

Use key-only SSH and allow only SSH inbound (also add a cloud firewall restricted to your IP):

```bash
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
sudo ufw allow OpenSSH && sudo ufw enable
```

### 3. Install the GitHub CLI

```bash
(type -p wget >/dev/null || (sudo apt update && sudo apt install wget -y)) \
	&& sudo mkdir -p -m 755 /etc/apt/keyrings \
	&& out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg \
	&& cat $out | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
	&& sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
	&& sudo mkdir -p -m 755 /etc/apt/sources.list.d \
	&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
	&& sudo apt update \
	&& sudo apt install gh -y
```

### 4. Install Node.js and the Claude CLI

The Claude CLI requires Node.js. Node is also commonly needed by the repos being monitored.

```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
sudo curl -fsSL https://claude.ai/install.sh | bash
claude --version
```

Also install any other toolchain your monitored repos need (e.g. .NET SDK, Java, Docker)
so Claude can build and run their tests.

### 5. Authenticate as the `agent` user

```bash
sudo -iu agent

# GitHub: use a fine-grained token scoped to only the monitored repos
# (see "GitHub token permissions" below)
gh auth login        # or: echo "<token>" | gh auth login --with-token
gh auth status

# Anthropic
export ANTHROPIC_API_KEY=<your key>   # persist via the systemd unit / env file, not .bashrc on shared hosts
claude --version
```

#### GitHub token permissions

Create the token at Settings → Developer settings → Personal access tokens →
Fine-grained tokens. Set **Resource owner** to the org that owns the repos, choose
**Only select repositories** (the monitored repos), and set a sensible expiration —
the agent will start failing on every poll once the token expires.

Repository permissions (add the non-default ones via **Add permissions**):

| Permission | Access | Used for |
|---|---|---|
| Contents | Read and write | Pushing fix commits and branches |
| Issues | Read and write | Triage comments, labels (`needs-human`) |
| Pull requests | Read and write | Listing/viewing PRs, merging, PR comments |
| Actions | Read-only | `gh run list` / `gh run view` failure logs |
| Commit statuses | Read-only | CI status on PRs |
| Workflows | Read and write | Pushing fixes to `.github/workflows/*` files |
| Metadata | Read-only | Required; added automatically |

Notes:

- There is no "Checks" permission for fine-grained tokens (it is GitHub Apps only), and the
  agent's `gh` calls should not need it. **However, it may still be a cause of failure:** the
  PR monitor reads CI status via `gh pr view`, which can include check runs. If PRs appear to
  have no CI status (or `never_merge_without_green_ci` blocks PRs whose CI is actually green),
  suspect the missing Checks access. The fallback is a classic token with the `repo` (and
  `workflow`) scopes, which can read check runs.
- Without **Workflows**, GitHub rejects any push that touches `.github/workflows/`. Drop it
  if you don't want the agent editing CI definitions — those failures will then end in `needs-human`.
- Branch protection / rulesets on `main` (require PR checks) are the way to enforce the
  merge rules on GitHub's side; the token cannot.

Also configure git identity for commits made by the agent:

```bash
git config --global user.name "agent"
git config --global user.email "agent@example.com"
```

### 6. Clone the agent and the monitored repos

```bash
# still as the agent user
mkdir -p ~/repos/github && cd ~/repos/github
git clone <this-repo-url> agent_in_isolation
git clone https://github.com/<org>/<monitored-repo>.git

cd agent_in_isolation
# Ubuntu 24.04 blocks system-wide pip installs (PEP 668), so use a venv
python3 -m venv .venv                    # needs: sudo apt install -y python3-venv
.venv/bin/pip install -r requirements.txt
cp config.yaml config.yaml.local         # set org/name/local_path for each repo
```

Pre-install each monitored repo's dependencies now (e.g. `npm ci`) **before** enabling
network isolation — see the next step.

### 7. Smoke-test before isolating the network

```bash
gh run list --repo <org>/<monitored-repo>
echo "Say hello" | claude -p
.venv/bin/python agent.py --config config.yaml.local   # watch agent.log, Ctrl-C when satisfied
```

### 8. Enable network isolation

```bash
sudo ./network/iptables.sh
```

Isolation only allows `github.com`, `api.github.com`, `api.anthropic.com` and DNS.
Package registries (npm, PyPI, NuGet, etc.) will be **blocked**, so any repo
dependencies must be installed in step 6. If a monitored repo's CI fix needs new
dependencies, either allow the registry in `network/iptables.sh` or re-run
without isolation temporarily. Verify with the commands in
[Verify isolation](#verify-isolation).

Note: iptables rules are not persistent across reboot. Persist them with
`sudo apt install iptables-persistent && sudo netfilter-persistent save`, or re-run
the script on boot.

### 9. Install as a service

Follow [Production (Systemd)](#production-systemd). Then add log rotation:

```bash
sudo tee /etc/logrotate.d/agent_in_isolation <<'EOF'
/opt/agent_in_isolation/agent.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

> **Check before enabling the unit**: `systemd/agent.service` sets `ProtectHome=true`
> and `ProtectSystem=strict`. If your repos and `gh`/`claude` credentials live under
> `/home/agent`, the agent will not be able to read or write them. Either keep the
> repos elsewhere and add them to `ReadWritePaths=`, or relax `ProtectHome`. Also
> confirm how `ANTHROPIC_API_KEY` is supplied (the unit currently has
> `Environment="ANTHROPIC_API_KEY=%i"`, which is a template placeholder);
> an `EnvironmentFile=` with mode `600` is the safer option.

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
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

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
# (or: source .venv/bin/activate, then python3 agent.py ...)
.venv/bin/python agent.py --config config.yaml

# View logs in real-time
tail -f agent.log
```

### Production (Systemd)

```bash
# Copy to system location
sudo cp -r . /opt/agent_in_isolation
sudo chown -R agent:agent /opt/agent_in_isolation
# venvs are not relocatable: recreate it in the new location
sudo -u agent rm -rf /opt/agent_in_isolation/.venv
sudo -u agent python3 -m venv /opt/agent_in_isolation/.venv
sudo -u agent /opt/agent_in_isolation/.venv/bin/pip install -r /opt/agent_in_isolation/requirements.txt
sudo mkdir -p /etc/agent_in_isolation
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
```

The script allows:
- Outbound TCP 443 to `api.github.com`, `github.com`, and `api.anthropic.com`
- Outbound UDP/TCP 53 to the system's configured DNS resolver(s) (read from `/etc/resolv.conf` at runtime)
- All established/related connections
- Loopback traffic

Everything else is dropped.

### Verify isolation

```bash
# Should succeed (GitHub API over DNS + HTTPS)
gh api /user

# Should fail (blocked destination)
curl --max-time 5 https://google.com   # Connection timed out
```

### Restore original rules

```bash
sudo ./network/iptables-restore.sh
```

> **Note**: The backup is written to `/tmp/iptables-backup.txt`, which is cleared on reboot. If the backup is missing, the restore script flushes the OUTPUT chain and sets the policy to ACCEPT.

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

If you see `Temporary failure in name resolution`, DNS port 53 may be blocked
or the system resolver has changed since `iptables.sh` was run. Re-run the
script to re-detect `/etc/resolv.conf` and rebuild the rules.

```bash
# Check current iptables rules
sudo iptables -L OUTPUT -n -v

# Re-run isolation setup to rebuild rules (re-reads /etc/resolv.conf)
sudo ./network/iptables.sh

# Or restore original rules entirely
sudo ./network/iptables-restore.sh
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
