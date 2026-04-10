"""
Handler for new GitHub issues.
Triages issues and creates implementation branches as needed.
"""

import logging
import subprocess
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class NewIssueHandler:
    """Handles new issue detection and triage."""

    def __init__(self, config: dict):
        self.config = config
        self.poll_interval = config.get('agent', {}).get('poll_interval_seconds', 300)

    def detect_new_issues(self, org: str, repo: str) -> list:
        """
        Query GitHub for recently opened issues.
        Returns list of issues created since last poll.
        """
        try:
            cmd = [
                'gh', 'issue', 'list',
                '--repo', f'{org}/{repo}',
                '--json', 'number,title,body,createdAt,labels,state',
                '--state', 'open',
                '--limit', '20'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to list issues: {result.stderr}")
                return []

            import json
            issues = json.loads(result.stdout)
            return issues
        except Exception as e:
            logger.error(f"Error detecting new issues: {e}")
            return []

    def add_comment(self, org: str, repo: str, issue_num: int, comment: str) -> bool:
        """Add a comment to an issue."""
        try:
            cmd = [
                'gh', 'issue', 'comment',
                '--repo', f'{org}/{repo}',
                str(issue_num),
                '--body', comment
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to add comment: {result.stderr}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            return False

    def add_label(self, org: str, repo: str, issue_num: int, label: str) -> bool:
        """Add a label to an issue."""
        try:
            cmd = [
                'gh', 'issue', 'edit',
                '--repo', f'{org}/{repo}',
                str(issue_num),
                '--add-label', label
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to add label: {result.stderr}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error adding label: {e}")
            return False
