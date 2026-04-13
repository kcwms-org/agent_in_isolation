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
