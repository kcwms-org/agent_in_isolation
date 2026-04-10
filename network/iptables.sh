#!/bin/bash
# Network isolation setup for agent_in_isolation
# Restricts outbound connections to GitHub API and Anthropic API only
# Requires root privileges

set -e

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

echo "Setting up network isolation for agent_in_isolation..."

# Save current rules for recovery if needed
iptables-save > /tmp/iptables-backup.txt || true

# Allow established connections
echo "Allowing established and related connections..."
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow GitHub API
echo "Allowing GitHub API..."
iptables -A OUTPUT -d api.github.com -p tcp --dport 443 -j ACCEPT
iptables -A OUTPUT -d github.com -p tcp --dport 443 -j ACCEPT

# Allow Anthropic API (needed for claude CLI)
echo "Allowing Anthropic API..."
iptables -A OUTPUT -d api.anthropic.com -p tcp --dport 443 -j ACCEPT

# Allow localhost
echo "Allowing localhost..."
iptables -A OUTPUT -o lo -j ACCEPT

# Block everything else
echo "Blocking all other outbound traffic..."
iptables -A OUTPUT -j DROP

echo "Network isolation setup complete!"
echo "Current rules:"
iptables -L OUTPUT -n -v

echo ""
echo "To restore original rules, run:"
echo "  iptables-restore < /tmp/iptables-backup.txt"
