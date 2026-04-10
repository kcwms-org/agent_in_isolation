"""
Handler for pull request monitoring.
Watches open PRs and merges when CI is green.
"""

import logging
import subprocess
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PRMonitorHandler:
    """Handles PR monitoring and merging."""

    def __init__(self, config: dict):
        self.config = config
        self.protected_branches = config.get('limits', {}).get('never_push_to', [])

    def get_open_prs(self, org: str, repo: str) -> list:
        """
        Query GitHub for open pull requests.
        Returns list of PR info.
        """
        try:
            cmd = [
                'gh', 'pr', 'list',
                '--repo', f'{org}/{repo}',
                '--json', 'number,title,baseRefName,state,statusCheckRollup,author,reviews',
                '--state', 'open',
                '--limit', '20'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to list PRs: {result.stderr}")
                return []

            import json
            prs = json.loads(result.stdout)
            return prs
        except Exception as e:
            logger.error(f"Error getting open PRs: {e}")
            return []

    def check_ci_status(self, org: str, repo: str, pr_num: int) -> Dict[str, Any]:
        """Get detailed CI status for a PR."""
        try:
            cmd = [
                'gh', 'pr', 'view',
                '--repo', f'{org}/{repo}',
                str(pr_num),
                '--json', 'statusCheckRollup,commits'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to check PR status: {result.stderr}")
                return {'status': 'error'}

            import json
            return json.loads(result.stdout)
        except Exception as e:
            logger.error(f"Error checking CI status: {e}")
            return {'status': 'error'}

    def merge_pr(self, org: str, repo: str, pr_num: int, strategy: str = 'squash') -> bool:
        """Merge a PR."""
        try:
            cmd = [
                'gh', 'pr', 'merge',
                '--repo', f'{org}/{repo}',
                str(pr_num),
                '--' + strategy
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to merge PR: {result.stderr}")
                return False
            logger.info(f"Successfully merged PR #{pr_num}")
            return True
        except Exception as e:
            logger.error(f"Error merging PR: {e}")
            return False

    def add_comment(self, org: str, repo: str, pr_num: int, comment: str) -> bool:
        """Add a comment to a PR."""
        try:
            cmd = [
                'gh', 'pr', 'comment',
                '--repo', f'{org}/{repo}',
                str(pr_num),
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
