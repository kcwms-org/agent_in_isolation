#!/usr/bin/env python3
"""
Autonomous GitHub CI/Issue Agent
Monitors repos for CI failures and new issues, invokes Claude CLI to fix autonomously.
"""

import os
import sys
import yaml
import time
import json
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('agent.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.audit_log = []

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path) as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            sys.exit(1)

    def _log_action(self, repo: str, event_type: str, outcome: str, details: str = ""):
        """Log an action to audit log."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "repo": repo,
            "event_type": event_type,
            "outcome": outcome,
            "details": details
        }
        self.audit_log.append(entry)
        logger.info(f"[{repo}] {event_type}: {outcome} {details}")

    def _run_command(self, cmd: list, cwd: Optional[str] = None, timeout: int = 30) -> tuple:
        """Execute a shell command, return (returncode, stdout, stderr)."""
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)

    def _expand_path(self, path_str: str) -> str:
        """Expand ~ and environment variables."""
        return os.path.expanduser(os.path.expandvars(path_str))

    def _get_repo_config(self, repo_name: str) -> Optional[dict]:
        """Get config for a specific repo."""
        for repo in self.config.get('repos', []):
            if repo['name'] == repo_name:
                return repo
        return None

    def check_ci_failures(self, repo_config: dict) -> list:
        """
        Check for CI failures in the repo using gh CLI.
        Returns list of failed workflow runs.
        """
        if not repo_config.get('watch', {}).get('ci_failures'):
            return []

        repo_name = repo_config['name']
        try:
            # Get recent workflow runs
            cmd = [
                'gh', 'run', 'list',
                '--repo', f"{repo_config['org']}/{repo_name}",
                '--json', 'number,conclusion,headBranch,displayTitle',
                '--limit', '10'
            ]
            returncode, stdout, stderr = self._run_command(cmd)

            if returncode != 0:
                logger.warning(f"Failed to fetch runs for {repo_name}: {stderr}")
                return []

            runs = json.loads(stdout) if stdout else []
            failed_runs = [r for r in runs if r.get('conclusion') == 'failure']
            return failed_runs
        except Exception as e:
            logger.error(f"Error checking CI for {repo_name}: {e}")
            return []

    def check_new_issues(self, repo_config: dict) -> list:
        """
        Check for new issues in the repo.
        Returns list of issues created in last poll interval.
        """
        if not repo_config.get('watch', {}).get('new_issues'):
            return []

        repo_name = repo_config['name']
        try:
            # Get recent open issues
            cmd = [
                'gh', 'issue', 'list',
                '--repo', f"{repo_config['org']}/{repo_name}",
                '--json', 'number,title,body,createdAt,state',
                '--state', 'open',
                '--limit', '10'
            ]
            returncode, stdout, stderr = self._run_command(cmd)

            if returncode != 0:
                logger.warning(f"Failed to fetch issues for {repo_name}: {stderr}")
                return []

            issues = json.loads(stdout) if stdout else []
            return issues
        except Exception as e:
            logger.error(f"Error checking issues for {repo_name}: {e}")
            return []

    def check_pr_status(self, repo_config: dict) -> list:
        """
        Check for open PRs with green CI.
        Returns list of PRs ready to merge.
        """
        if not repo_config.get('watch', {}).get('pr_monitor'):
            return []

        repo_name = repo_config['name']
        try:
            cmd = [
                'gh', 'pr', 'list',
                '--repo', f"{repo_config['org']}/{repo_name}",
                '--json', 'number,title,state,statusCheckRollup,baseRefName',
                '--state', 'open',
                '--limit', '10'
            ]
            returncode, stdout, stderr = self._run_command(cmd)

            if returncode != 0:
                logger.warning(f"Failed to fetch PRs for {repo_name}: {stderr}")
                return []

            prs = json.loads(stdout) if stdout else []
            return prs
        except Exception as e:
            logger.error(f"Error checking PRs for {repo_name}: {e}")
            return []

    def invoke_claude(self, repo_config: dict, handler_type: str, context: dict, attempt: int = 1) -> bool:
        """
        Invoke Claude CLI with appropriate prompt template.
        Returns True if successful, False otherwise.
        """
        repo_path = self._expand_path(repo_config['local_path'])
        repo_name = repo_config['name']

        # Load and render prompt template
        prompt_file = Path(__file__).parent / 'prompts' / f'{handler_type}.md'
        if not prompt_file.exists():
            logger.error(f"Prompt template not found: {prompt_file}")
            return False

        try:
            with open(prompt_file) as f:
                prompt = f.read()

            # Inject context variables
            for key, value in context.items():
                prompt = prompt.replace(f'{{{{{key}}}}}', str(value))

            # Invoke claude
            max_timeout = self.config.get('agent', {}).get('claude_timeout_seconds', 300)
            cmd = [
                'claude',
                '--dangerously-skip-permissions',
                '-p', prompt
            ]

            returncode, stdout, stderr = self._run_command(
                cmd,
                cwd=repo_path,
                timeout=max_timeout
            )

            if returncode == 0:
                self._log_action(repo_name, handler_type, 'success', f'Attempt {attempt}')
                return True
            else:
                self._log_action(repo_name, handler_type, 'failed', f'Attempt {attempt}: {stderr[:100]}')
                return False
        except Exception as e:
            logger.error(f"Error invoking Claude for {repo_name}: {e}")
            self._log_action(repo_name, handler_type, 'error', str(e)[:100])
            return False

    def handle_ci_failure(self, repo_config: dict, run_info: dict) -> None:
        """Handle a CI failure event."""
        repo_name = repo_config['name']
        branch = run_info.get('headBranch', 'unknown')

        # Check attempt count
        attempt_key = f"{repo_name}:ci_failure:{run_info.get('number')}"
        # In a real implementation, this would be persisted to a database/file
        attempt = 1

        max_attempts = self.config.get('agent', {}).get('max_fix_attempts', 3)
        if attempt > max_attempts:
            logger.warning(f"Max attempts reached for CI failure in {repo_name} on {branch}")
            # In a real implementation, would label the issue
            self._log_action(repo_name, 'ci_failure', 'max_attempts_reached', branch)
            return

        logger.info(f"Handling CI failure in {repo_name} on {branch}")

        context = {
            'repo': f"{repo_config['org']}/{repo_name}",
            'branch': branch,
            'run_number': run_info.get('number'),
            'log': f"CI run {run_info.get('number')} failed"
        }

        success = self.invoke_claude(repo_config, 'fix_ci_failure', context, attempt)
        if not success and attempt < max_attempts:
            logger.info(f"Will retry CI fix on next poll cycle")

    def handle_new_issue(self, repo_config: dict, issue_info: dict) -> None:
        """Handle a new issue event."""
        repo_name = repo_config['name']
        issue_num = issue_info.get('number')

        logger.info(f"Handling new issue #{issue_num} in {repo_name}")

        context = {
            'repo': f"{repo_config['org']}/{repo_name}",
            'issue_number': issue_num,
            'title': issue_info.get('title'),
            'body': issue_info.get('body', '')
        }

        self.invoke_claude(repo_config, 'triage_issue', context)

    def handle_pr_monitor(self, repo_config: dict, pr_info: dict) -> None:
        """Handle PR monitoring and potential merge."""
        repo_name = repo_config['name']
        pr_num = pr_info.get('number')
        base_branch = pr_info.get('baseRefName', 'main')

        # Check if we should merge
        protected_branches = self.config.get('limits', {}).get('never_push_to', [])
        if base_branch in protected_branches:
            logger.warning(f"PR #{pr_num} targets protected branch {base_branch}, skipping")
            return

        # Check CI status
        status_checks = pr_info.get('statusCheckRollup', [])
        all_green = all(check.get('conclusion') == 'success' for check in status_checks)

        if not all_green:
            logger.info(f"PR #{pr_num} CI not fully green, skipping merge")
            return

        logger.info(f"PR #{pr_num} ready to merge in {repo_name}")

        context = {
            'repo': f"{repo_config['org']}/{repo_name}",
            'pr_number': pr_num,
            'base_branch': base_branch
        }

        self.invoke_claude(repo_config, 'monitor_pr', context)

    def poll(self) -> None:
        """Main polling loop."""
        logger.info("Agent started, entering polling loop")

        while True:
            try:
                for repo_config in self.config.get('repos', []):
                    repo_name = repo_config['name']
                    logger.debug(f"Polling {repo_name}")

                    # Check CI failures
                    failed_runs = self.check_ci_failures(repo_config)
                    for run in failed_runs:
                        self.handle_ci_failure(repo_config, run)

                    # Check new issues
                    issues = self.check_new_issues(repo_config)
                    for issue in issues:
                        self.handle_new_issue(repo_config, issue)

                    # Check PR status
                    prs = self.check_pr_status(repo_config)
                    for pr in prs:
                        self.handle_pr_monitor(repo_config, pr)

                # Sleep before next poll
                poll_interval = self.config.get('agent', {}).get('poll_interval_seconds', 300)
                logger.debug(f"Sleeping for {poll_interval}s before next poll")
                time.sleep(poll_interval)
            except KeyboardInterrupt:
                logger.info("Agent shutting down")
                break
            except Exception as e:
                logger.error(f"Unexpected error in poll loop: {e}", exc_info=True)
                time.sleep(30)  # Back off on error


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Autonomous GitHub CI/Issue Agent')
    parser.add_argument('--config', default='config.yaml', help='Path to config file')
    args = parser.parse_args()

    agent = Agent(args.config)
    agent.poll()


if __name__ == '__main__':
    main()
