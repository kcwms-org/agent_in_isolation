# Plan: Fix DNS Blocking in Network Isolation — Issue #2

**Status:** In Progress

## Context

`network/iptables.sh` sets up outbound network isolation for the agent. The OUTPUT chain allows ESTABLISHED/RELATED, specific HTTPS destinations, and loopback — then DROPs everything else (`line 37`). There is no rule permitting DNS traffic (UDP/TCP port 53).

Because DNS is blocked, the hostname-based ACCEPT rules for `api.github.com`, `github.com`, and `api.anthropic.com` (lines 24-29) never fire at runtime — the kernel can't resolve those hostnames. Any process that does a DNS lookup (including `gh` and `claude`) sees `Temporary failure in name resolution`. The README's verification step (`gh api /user` should succeed) silently fails for the same reason.

The backup file at `/tmp/iptables-backup.txt` appearing "empty" is expected behaviour — on a system with no prior iptables rules, `iptables-save` produces an empty/minimal output. The restore works correctly.

## Goal

Allow DNS queries to the system's configured resolver(s) in the OUTPUT chain before the final DROP, so hostname-based connections actually work after isolation is applied.

## Approach

### 1. Add DNS rules to `network/iptables.sh`

Add a loop that reads nameserver addresses from `/etc/resolv.conf` at runtime and opens UDP/TCP port 53 to each one. Insert it **between** the loopback rule and the DROP rule.

**Replace** (lines 31–37):
```bash
# Allow localhost
echo "Allowing localhost..."
iptables -A OUTPUT -o lo -j ACCEPT

# Block everything else
echo "Blocking all other outbound traffic..."
iptables -A OUTPUT -j DROP
```

**With:**
```bash
# Allow localhost
echo "Allowing localhost..."
iptables -A OUTPUT -o lo -j ACCEPT

# Allow DNS to system resolver(s) from /etc/resolv.conf
echo "Allowing DNS (UDP+TCP port 53) to system resolver(s)..."
while IFS= read -r ns; do
    iptables -A OUTPUT -d "$ns" -p udp --dport 53 -j ACCEPT
    iptables -A OUTPUT -d "$ns" -p tcp --dport 53 -j ACCEPT
done < <(awk '/^nameserver/ { print $2 }' /etc/resolv.conf)

# Block everything else
echo "Blocking all other outbound traffic..."
iptables -A OUTPUT -j DROP
```

The `awk` one-liner extracts every `nameserver` line, handles IPv4 and IPv6, and ignores comments. Process substitution is valid bash (script already uses `[[ ]]` and has `#!/bin/bash` shebang). On Ubuntu 24.04 with systemd-resolved this emits a rule for `127.0.0.53`; on other distros it uses whatever is in `/etc/resolv.conf`.

### 2. Add `network/iptables-restore.sh` teardown script

A dedicated restore script that handles the case where the `/tmp` backup was lost on reboot.

**New file `network/iptables-restore.sh`:**
```bash
#!/bin/bash
# Restore iptables rules from backup created by iptables.sh
# NOTE: The backup is stored in /tmp and is lost on reboot.
# If /tmp/iptables-backup.txt is missing, the safest recovery is:
#   sudo iptables -F OUTPUT
#   sudo iptables -P OUTPUT ACCEPT

set -e

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root"
    exit 1
fi

BACKUP=/tmp/iptables-backup.txt

if [[ ! -f "$BACKUP" ]]; then
    echo "Backup not found at $BACKUP (lost on reboot?)"
    echo "Flushing OUTPUT chain and setting policy to ACCEPT instead."
    iptables -F OUTPUT
    iptables -P OUTPUT ACCEPT
    echo "OUTPUT chain restored to permissive default."
    exit 0
fi

echo "Restoring iptables rules from $BACKUP..."
iptables-restore < "$BACKUP"
echo "Restore complete."
echo "Current OUTPUT rules:"
iptables -L OUTPUT -n -v
```

### 3. Update `README.md`

**Network Isolation section** (lines 116–133) — document DNS allowance, fix verification examples, reference restore script:

Replace:
```markdown
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
```

With:
```markdown
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
```

**Troubleshooting section** (lines 204–212) — add DNS note:

Replace:
```markdown
### Network isolation blocking requests

```bash
# Check current iptables rules
sudo iptables -L OUTPUT -n -v

# Restore original rules and check if issue persists
sudo iptables-restore < /tmp/iptables-backup.txt
```
```

With:
```markdown
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
```

## Files to Change

| File | Change |
|------|--------|
| `network/iptables.sh` | Add DNS ACCEPT rules (UDP+TCP 53) before DROP, reading nameserver(s) from `/etc/resolv.conf` at runtime |
| `network/iptables-restore.sh` | New — teardown script that handles missing `/tmp` backup gracefully |
| `README.md` | Update Network Isolation section (DNS docs, verify examples, restore script ref); update Troubleshooting section |

## Files NOT changing

- `agent.py` — DNS fix is in the network layer only
- `config.yaml` — no changes needed
- `handlers/` — no changes needed
- `prompts/` — no changes needed
- `systemd/agent.service` — no changes needed

## Verification

1. `sudo ./network/iptables.sh` — output includes "Allowing DNS" line; `iptables -L OUTPUT -n -v` shows UDP+TCP rules for port 53
2. `dig @127.0.0.53 api.github.com` returns a valid A record
3. `gh api /user` returns GitHub user JSON (previously failed with DNS error)
4. `curl --max-time 5 https://google.com` times out (isolation is real)
5. `sudo ./network/iptables-restore.sh` with backup present — restores cleanly
6. `sudo rm /tmp/iptables-backup.txt && sudo ./network/iptables-restore.sh` without backup — flushes OUTPUT and sets ACCEPT policy
