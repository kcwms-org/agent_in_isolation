"""
Handler for CI failure events.
Detects failed workflow runs and invokes Claude to fix them.
"""

import logging
import subprocess
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class CIFailureHandler:
    """Handles CI failure detection and repair."""

    def __init__(self, config: dict):
        self.config = config
        self.max_attempts = config.get('agent', {}).get('max_fix_attempts', 3)

    def detect_failures(self, org: str, repo: str) -> list:
        """
        Query GitHub for recent CI failures.
        Returns list of failed workflow run info.
        """
        try:
            cmd = [
                'gh', 'run', 'list',
                '--repo', f'{org}/{repo}',
                '--json', 'number,conclusion,headBranch,title,createdAt',
                '--limit', '20'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to list runs: {result.stderr}")
                return []

            import json
            runs = json.loads(result.stdout)
            failed = [r for r in runs if r.get('conclusion') == 'failure']
            return failed
        except Exception as e:
            logger.error(f"Error detecting CI failures: {e}")
            return []

    def get_failure_log(self, org: str, repo: str, run_id: int) -> str:
        """Fetch detailed failure log for a run."""
        try:
            cmd = [
                'gh', 'run', 'view',
                '--repo', f'{org}/{repo}',
                str(run_id),
                '--json', 'conclusion,text'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout[:2000]  # Truncate to 2000 chars
            return "Unable to fetch failure log"
        except Exception as e:
            logger.error(f"Error fetching failure log: {e}")
            return "Error fetching failure log"
