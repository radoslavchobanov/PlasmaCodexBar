"""
CopilotCollector - GitHub Copilot usage data collector for PlasmaCodexBar.

Fetches quota and usage information from the GitHub Copilot internal API
using tokens discovered from environment variables or local config files.
"""

import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional


COPILOT_API_URL = "https://api.github.com/copilot_internal/user"
USER_AGENT = "plasmacodexbar/1.0"

# Token source paths (checked in order after environment variable)
_COPILOT_HOSTS_PATHS = [
    Path.home() / ".config" / "github-copilot" / "hosts.json",
    Path.home() / ".config" / "Code" / "User" / "globalStorage" / "github.copilot" / "hosts.json",
]

PLAN_NAMES = {
    "free": "Free",
    "professional": "Professional",
    "business": "Business",
    "enterprise": "Enterprise",
}


class CopilotCollector:
    """Collects GitHub Copilot usage data."""

    def _find_token(self) -> Optional[str]:
        """Locate a Copilot OAuth token from known sources.

        Checks in order:
          1. COPILOT_API_TOKEN environment variable
          2. ~/.config/github-copilot/hosts.json
          3. VS Code globalStorage hosts.json
        """
        # 1. Environment variable
        env_token = os.environ.get("COPILOT_API_TOKEN", "").strip()
        if env_token:
            return env_token

        # 2-3. hosts.json files
        for hosts_path in _COPILOT_HOSTS_PATHS:
            token = self._read_hosts_token(hosts_path)
            if token:
                return token

        return None

    @staticmethod
    def _read_hosts_token(path: Path) -> Optional[str]:
        """Extract oauth_token from a GitHub Copilot hosts.json file."""
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # The file maps host names to credential objects.
            # Look for github.com first, then fall back to any entry.
            for key in ("github.com", "github.com:443"):
                entry = data.get(key)
                if isinstance(entry, dict):
                    token = entry.get("oauth_token", "").strip()
                    if token:
                        return token
            # Fallback: try any entry that has an oauth_token
            for entry in data.values():
                if isinstance(entry, dict):
                    token = entry.get("oauth_token", "").strip()
                    if token:
                        return token
        except Exception:
            pass
        return None

    def _fetch_user(self, token: str) -> Dict[str, Any]:
        """Call the Copilot internal user API and return parsed JSON.

        Raises urllib.error.HTTPError on HTTP failures.
        Raises Exception on network / parse errors.
        """
        req = urllib.request.Request(
            COPILOT_API_URL,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def collect(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "provider_id": "copilot",
            "provider_name": "Copilot",
            "is_connected": False,
            "error_message": "",
            "plan_name": "Unknown",
            "session_used_pct": 0,
            "session_reset_time": "",
            "weekly_used_pct": 0,
            "weekly_reset_time": "",
            "pace_status": "On track",
            "model_usage": {},
            "extra_usage_enabled": False,
            "extra_usage_current": 0,
            "extra_usage_limit": 0,
            "extra_usage_pct": 0,
            # Cost tracking (Copilot does not expose per-token costs)
            "cost_today": 0,
            "cost_today_tokens": 0,
            "cost_today_input_tokens": 0,
            "cost_today_output_tokens": 0,
            "cost_today_by_model": {},
            "cost_30_days": 0,
            "cost_30_days_tokens": 0,
            "cost_30_days_input_tokens": 0,
            "cost_30_days_output_tokens": 0,
            "cost_by_model": {},
            "cost_approximate": False,
        }

        # Resolve token
        token = self._find_token()
        if not token:
            result["error_message"] = (
                "No Copilot token found. Set COPILOT_API_TOKEN or sign in "
                "via GitHub Copilot in VS Code."
            )
            return result

        # Fetch usage from API
        try:
            data = self._fetch_user(token)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                result["error_message"] = (
                    "Token expired or invalid. Re-authenticate in VS Code "
                    "or update COPILOT_API_TOKEN."
                )
            elif e.code == 403:
                result["error_message"] = (
                    "Access denied. Your GitHub account may not have an "
                    "active Copilot subscription."
                )
            else:
                result["error_message"] = f"API error: {e.code}"
            return result
        except Exception as e:
            result["error_message"] = f"Failed to fetch: {e}"
            return result

        # Successful response
        result["is_connected"] = True

        # Plan
        plan_type = data.get("copilot_plan_type", "").lower()
        result["plan_name"] = PLAN_NAMES.get(plan_type, plan_type.title() or "Unknown")

        # Premium interactions -> session usage
        premium = data.get("premium_interactions")
        if isinstance(premium, dict):
            pct_remaining = premium.get("percent_remaining")
            if pct_remaining is not None:
                result["session_used_pct"] = round(100.0 - float(pct_remaining), 2)
            entitlement = premium.get("entitlement", 0)
            remaining = premium.get("remaining", 0)
            if entitlement:
                result["model_usage"]["Premium"] = round(
                    100.0 - (float(remaining) / float(entitlement) * 100.0), 2
                )

        # Chat interactions -> weekly usage
        chat = data.get("chat")
        if isinstance(chat, dict):
            pct_remaining = chat.get("percent_remaining")
            if pct_remaining is not None:
                result["weekly_used_pct"] = round(100.0 - float(pct_remaining), 2)
            entitlement = chat.get("entitlement", 0)
            remaining = chat.get("remaining", 0)
            if entitlement:
                result["model_usage"]["Chat"] = round(
                    100.0 - (float(remaining) / float(entitlement) * 100.0), 2
                )

        return result
