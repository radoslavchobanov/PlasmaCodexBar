#!/usr/bin/env python3
"""
CursorCollector - Collects Cursor AI usage data for PlasmaCodexBar.

Authentication is cookie-based. Two methods are supported (in priority order):

1. Manual session token in ~/.config/plasmacodexbar/cursor_session.json
   Format: {"session_token": "your-token-here"}
   Obtain the token from your browser cookies at cursor.com.

2. Automatic extraction from Cursor's local storage at
   ~/.config/Cursor/User/globalStorage/storage.json
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


CONFIG_DIR = Path.home() / ".config" / "plasmacodexbar"
CURSOR_CONFIG_DIR = Path.home() / ".config" / "Cursor"

CURSOR_USAGE_API_URL = "https://www.cursor.com/api/usage-summary"

# Cookie names that Cursor uses for session authentication, tried in order.
COOKIE_NAMES = [
    "WorkosCursorSessionToken",
    "__Secure-next-auth.session-token",
    "authjs.session-token",
]


class CursorCollector:
    """Collects Cursor usage data via the cursor.com usage-summary API."""

    def __init__(self):
        self.session_file = CONFIG_DIR / "cursor_session.json"
        self.cursor_storage = CURSOR_CONFIG_DIR / "User" / "globalStorage" / "storage.json"
        self.cache_file = CONFIG_DIR / "cursor_usage_cache.json"

    def collect(self) -> Dict[str, Any]:
        result = {
            "provider_id": "cursor",
            "provider_name": "Cursor",
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

        # Resolve session token
        token, cookie_name = self._get_session_token()
        if not token:
            result["error_message"] = (
                "No Cursor session token found. "
                "Create ~/.config/plasmacodexbar/cursor_session.json with "
                '{\"session_token\": \"<your-token>\"} '
                "(obtain it from your browser cookies at cursor.com)."
            )
            return result

        # Fetch usage from API
        try:
            data = self._fetch_usage(token, cookie_name)
            if data is None:
                cached = self._cached_result_with_error(
                    "Failed to fetch usage. Session token may be expired."
                )
                if cached:
                    return cached
                result["error_message"] = (
                    "Failed to fetch usage data. Your session token may be "
                    "expired. Update ~/.config/plasmacodexbar/cursor_session.json "
                    "with a fresh token from your browser."
                )
                return result

            self._apply_usage_data(result, data)

        except urllib.error.HTTPError as e:
            if e.code == 401 or e.code == 403:
                cached = self._cached_result_with_error(
                    "Session expired. Showing cached data."
                )
                if cached:
                    return cached
                result["error_message"] = (
                    "Session token expired or invalid. Update "
                    "~/.config/plasmacodexbar/cursor_session.json with a fresh token."
                )
            elif e.code == 429:
                cached = self._cached_result_with_error(
                    "Rate limited. Showing cached data."
                )
                if cached:
                    return cached
                result["error_message"] = "Rate limited. Try again in a few minutes."
            else:
                cached = self._cached_result_with_error(
                    "API error. Showing cached data."
                )
                if cached:
                    return cached
                result["error_message"] = f"API error: {e.code}"
        except Exception as e:
            cached = self._cached_result_with_error(
                "Failed to refresh. Showing cached data."
            )
            if cached:
                return cached
            result["error_message"] = f"Failed to fetch: {e}"

        if result.get("is_connected"):
            self._save_cached_result(result)
        return result

    # -- Authentication -------------------------------------------------------

    def _get_session_token(self) -> tuple:
        """Return (token, cookie_name) or (None, None) if unavailable.

        Priority:
        1. Manual config file (~/.config/plasmacodexbar/cursor_session.json)
        2. Cursor local storage (~/.config/Cursor/User/globalStorage/storage.json)
        """
        # 1. Manual config
        token = self._read_manual_token()
        if token:
            return token, COOKIE_NAMES[0]

        # 2. Cursor local storage
        token, cookie_name = self._read_cursor_storage()
        if token:
            return token, cookie_name

        return None, None

    def _read_manual_token(self) -> Optional[str]:
        """Read session token from manual config file."""
        if not self.session_file.exists():
            return None
        try:
            with open(self.session_file, "r") as f:
                data = json.load(f)
            token = data.get("session_token", "").strip()
            return token if token else None
        except Exception:
            return None

    def _read_cursor_storage(self) -> tuple:
        """Try to extract a session token from Cursor's local storage.

        Looks for known cookie key patterns in storage.json.
        Returns (token, cookie_name) or (None, None).
        """
        if not self.cursor_storage.exists():
            return None, None
        try:
            with open(self.cursor_storage, "r") as f:
                data = json.load(f)
        except Exception:
            return None, None

        # Look for cookie values stored under various key patterns
        for cookie_name in COOKIE_NAMES:
            for key in (cookie_name, f"cursor.{cookie_name}", f"auth.{cookie_name}"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), cookie_name

        # Some versions store a cursorAuth or accessToken field
        for key in ("cursorAuth", "accessToken", "token"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), COOKIE_NAMES[0]

        return None, None

    # -- API ------------------------------------------------------------------

    def _fetch_usage(
        self, token: str, cookie_name: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch usage data from the Cursor usage-summary API.

        Tries the given cookie_name first, then falls back to the other
        known cookie names if the first attempt returns 401/403.
        """
        names_to_try = [cookie_name] + [n for n in COOKIE_NAMES if n != cookie_name]

        last_error = None
        for name in names_to_try:
            cookie_header = f"{name}={token}"
            req = urllib.request.Request(
                CURSOR_USAGE_API_URL,
                headers={
                    "Cookie": cookie_header,
                    "Accept": "application/json",
                    "User-Agent": "plasmacodexbar/1.0",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    if response.status == 200:
                        return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code in (401, 403):
                    continue  # try next cookie name
                raise
            except Exception:
                raise

        # All cookie names exhausted with auth errors — re-raise last
        if last_error is not None:
            raise last_error
        return None

    # -- Data mapping ---------------------------------------------------------

    def _apply_usage_data(self, result: Dict[str, Any], data: Dict[str, Any]):
        """Map the Cursor usage-summary response into the standard result dict."""
        result["is_connected"] = True

        # -- Included usage (session) --
        member = data.get("memberUsage", {})
        included = member.get("included", {})
        used = included.get("used", 0)
        limit = included.get("limit", 0)

        if limit > 0:
            result["session_used_pct"] = round((used / limit) * 100, 1)
        elif used > 0:
            result["session_used_pct"] = 100.0

        # -- Billing period -> reset times --
        billing = data.get("billingPeriod", {})
        period_end = billing.get("end", "")
        if period_end:
            result["session_reset_time"] = period_end
            result["weekly_reset_time"] = period_end

        # -- Weekly used pct mirrors session for Cursor (single billing window) --
        result["weekly_used_pct"] = result["session_used_pct"]

        # -- Model usage percentages --
        auto_pct = data.get("autoModelUsagePercent")
        api_pct = data.get("apiUsagePercent")
        if auto_pct is not None:
            result["model_usage"]["Auto"] = round(auto_pct * 100, 1)
        if api_pct is not None:
            result["model_usage"]["API"] = round(api_pct * 100, 1)

        # -- On-demand / extra usage --
        on_demand = member.get("onDemand", {})
        od_used = on_demand.get("used", 0)
        od_limit = on_demand.get("limit", 0)

        if od_limit > 0:
            result["extra_usage_enabled"] = True
            result["extra_usage_current"] = od_used
            result["extra_usage_limit"] = od_limit
            result["extra_usage_pct"] = round((od_used / od_limit) * 100, 1)
        elif od_used > 0:
            result["extra_usage_enabled"] = True
            result["extra_usage_current"] = od_used

        # -- Pace calculation --
        if period_end:
            self._calculate_pace(result, billing)

    def _calculate_pace(self, result: Dict[str, Any], billing: Dict[str, Any]):
        """Calculate pace status relative to billing period progress."""
        try:
            period_start_str = billing.get("start", "")
            period_end_str = billing.get("end", "")
            if not period_start_str or not period_end_str:
                return

            start = datetime.fromisoformat(
                period_start_str.replace("Z", "+00:00")
            )
            end = datetime.fromisoformat(
                period_end_str.replace("Z", "+00:00")
            )
            now = datetime.now(timezone.utc)

            total_secs = (end - start).total_seconds()
            if total_secs <= 0:
                return

            elapsed_secs = (now - start).total_seconds()
            expected = (elapsed_secs / total_secs) * 100
            pace = result["session_used_pct"] - expected

            if pace < 0:
                result["pace_status"] = f"Behind ({pace:.0f}%)"
            elif pace > 0:
                result["pace_status"] = f"Ahead (+{pace:.0f}%)"
            else:
                result["pace_status"] = "On track"
        except Exception:
            pass

    # -- Cache ----------------------------------------------------------------

    def _load_cached_result(self) -> Optional[Dict[str, Any]]:
        """Load the last successful Cursor usage result from cache."""
        if not self.cache_file.exists():
            return None
        try:
            with open(self.cache_file, "r") as f:
                data = json.load(f)
            cached = data.get("result")
            return cached if isinstance(cached, dict) else None
        except Exception:
            return None

    def _save_cached_result(self, result: Dict[str, Any]):
        """Persist the last successful Cursor usage result."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump(
                    {
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "result": result,
                    },
                    f,
                )
        except Exception:
            pass

    def _cached_result_with_error(self, message: str) -> Optional[Dict[str, Any]]:
        """Return cached result annotated with an error message, or None."""
        cached = self._load_cached_result()
        if not cached:
            return None
        cached = dict(cached)
        cached["error_message"] = message
        cached["is_connected"] = True
        return cached
