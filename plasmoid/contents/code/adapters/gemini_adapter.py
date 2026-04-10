"""
GeminiCollector - Collects Gemini CLI usage quota data.

Reads OAuth credentials from ~/.gemini/oauth_creds.json and queries the
Google Cloud Code quota API to retrieve per-model usage fractions and
reset times.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional


GEMINI_DIR = Path.home() / ".gemini"
GEMINI_QUOTA_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"


def _format_gemini_model(model_id: str) -> str:
    """Convert a raw Gemini model ID to a short display name.

    Examples:
      gemini-2.5-pro   -> Pro 2.5
      gemini-2.5-flash -> Flash 2.5
      gemini-2.0-flash-lite -> Flash Lite 2.0
    """
    name = model_id.lower().strip()
    # Strip leading "models/" prefix if present
    if name.startswith("models/"):
        name = name[len("models/"):]

    # Try to extract version and tier from patterns like gemini-X.Y-<tier>
    parts = name.replace("gemini-", "").split("-", 1)
    if len(parts) == 2:
        version, tier = parts[0], parts[1]
        tier_display = tier.replace("-", " ").title()
        return f"{tier_display} {version}"
    return model_id


class GeminiCollector:
    """Collects Gemini CLI usage quota data."""

    def __init__(self):
        self.creds_file = GEMINI_DIR / "oauth_creds.json"
        self.settings_file = GEMINI_DIR / "settings.json"

    def collect(self) -> Dict[str, Any]:
        result = {
            "provider_id": "gemini",
            "provider_name": "Gemini",
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
            # Cost tracking (Gemini CLI is free-tier, so these stay at 0)
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

        # Load credentials
        if not self.creds_file.exists():
            result["error_message"] = "Not logged in. Run 'gemini auth login' to authenticate."
            return result

        try:
            with open(self.creds_file, "r") as f:
                creds = json.load(f)
        except Exception:
            result["error_message"] = "Failed to read credentials."
            return result

        access_token = creds.get("access_token", "").strip()
        if not access_token:
            result["error_message"] = "No access token found in credentials."
            return result

        # Check token expiry
        expiry = creds.get("expiry_date")
        if expiry is not None:
            try:
                # expiry_date can be epoch millis (number) or ISO string
                if isinstance(expiry, (int, float)):
                    expiry_dt = datetime.fromtimestamp(expiry / 1000, tz=timezone.utc)
                else:
                    expiry_dt = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))

                if expiry_dt < datetime.now(timezone.utc):
                    result["error_message"] = (
                        "Token expired. Run 'gemini auth login' to re-authenticate."
                    )
                    return result
            except Exception:
                pass  # If we can't parse the expiry, proceed anyway

        # Determine plan from settings
        result["plan_name"] = self._detect_plan()

        # Fetch quota
        try:
            quota_data = self._fetch_quota(access_token)
            if quota_data is not None:
                self._apply_quota(result, quota_data)
            else:
                result["error_message"] = "Failed to fetch quota data."
        except urllib.error.HTTPError as e:
            if e.code == 401:
                result["error_message"] = (
                    "Token expired or invalid. Run 'gemini auth login' to re-authenticate."
                )
            elif e.code == 429:
                result["error_message"] = "Rate limited. Try again in a few minutes."
            else:
                result["error_message"] = f"API error: {e.code}"
        except Exception as e:
            result["error_message"] = f"Failed to fetch: {e}"

        return result

    def _detect_plan(self) -> str:
        """Read settings.json to determine the plan/auth type."""
        if not self.settings_file.exists():
            return "Free"
        try:
            with open(self.settings_file, "r") as f:
                settings = json.load(f)
            auth_type = settings.get("auth_type", "").lower()
            if "adc" in auth_type or "service_account" in auth_type:
                return "Cloud"
            return "Free"
        except Exception:
            return "Free"

    def _fetch_quota(self, access_token: str) -> Optional[Dict[str, Any]]:
        """POST to the quota API and return the parsed response."""
        req = urllib.request.Request(
            GEMINI_QUOTA_URL,
            data=b"{}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))

    def _apply_quota(self, result: Dict[str, Any], data: Dict[str, Any]):
        """Map quota API response into the standard result dict.

        The API returns a structure like:
        {
          "quotaBuckets": [
            {
              "modelId": "gemini-2.5-pro",
              "remainingFraction": 0.85,
              "resetTime": "2026-04-10T12:00:00Z"
            },
            ...
          ]
        }
        """
        result["is_connected"] = True

        buckets = data.get("quotaBuckets", [])
        if not buckets:
            return

        lowest_remaining = 1.0
        earliest_reset = ""

        for bucket in buckets:
            model_id = bucket.get("modelId", "unknown")
            remaining = bucket.get("remainingFraction")
            reset_time = bucket.get("resetTime", "")

            if remaining is None:
                continue

            remaining = float(remaining)
            used_pct = round((1.0 - remaining) * 100, 1)

            # Store per-model usage
            display_name = _format_gemini_model(model_id)
            result["model_usage"][display_name] = used_pct

            # Track the most-consumed quota bucket for session_used_pct
            if remaining < lowest_remaining:
                lowest_remaining = remaining
                earliest_reset = reset_time

        result["session_used_pct"] = round((1.0 - lowest_remaining) * 100, 1)
        if earliest_reset:
            result["session_reset_time"] = self._normalize_timestamp(earliest_reset)

    @staticmethod
    def _normalize_timestamp(ts: str) -> str:
        """Ensure the timestamp is in ISO 8601 format with timezone info."""
        if not ts:
            return ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.isoformat()
        except Exception:
            return ts
