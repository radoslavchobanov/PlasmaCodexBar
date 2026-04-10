#!/usr/bin/env python3
"""
OpenRouter Collector - Fetches API usage and credit data from OpenRouter.

API key is resolved in order:
  1. OPENROUTER_API_KEY environment variable
  2. ~/.config/plasmacodexbar/openrouter.json  {"api_key": "sk-or-xxx"}
"""

import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional


CONFIG_DIR = Path.home() / ".config" / "plasmacodexbar"

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/auth/credits"


class OpenRouterCollector:
    """Collects OpenRouter API usage and credit data."""

    def __init__(self):
        self.config_file = CONFIG_DIR / "openrouter.json"

    def _resolve_api_key(self) -> Optional[str]:
        """Return the API key from env var or config file, or None."""
        env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if env_key:
            return env_key

        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                key = data.get("api_key", "").strip()
                if key:
                    return key
            except Exception:
                pass

        return None

    def _fetch_json(self, url: str, api_key: str) -> Optional[Dict[str, Any]]:
        """GET a JSON endpoint with Bearer auth. Returns parsed body or None."""
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "plasmacodexbar/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError:
            return None
        except Exception:
            return None

    def collect(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "provider_id": "openrouter",
            "provider_name": "OpenRouter",
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
            # Cost tracking
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

        # Resolve API key
        api_key = self._resolve_api_key()
        if not api_key:
            result["error_message"] = (
                "No API key. Set OPENROUTER_API_KEY or create "
                "~/.config/plasmacodexbar/openrouter.json with {\"api_key\": \"sk-or-xxx\"}."
            )
            return result

        # Fetch key info
        key_resp = self._fetch_json(OPENROUTER_KEY_URL, api_key)
        if key_resp is None:
            result["error_message"] = "Failed to fetch key info from OpenRouter API."
            return result

        key_data = key_resp.get("data", {})
        result["is_connected"] = True

        is_free = key_data.get("is_free_tier", False)
        usage = key_data.get("usage", 0) or 0  # cumulative USD spend
        limit = key_data.get("limit")           # USD cap, or null

        # Plan name
        result["plan_name"] = "Free" if is_free else "Paid"

        # Rate limit info stored for reference
        rate_limit = key_data.get("rate_limit", {})
        if rate_limit:
            result["model_usage"]["Rate limit"] = (
                f"{rate_limit.get('requests', '?')} req / {rate_limit.get('interval', '?')}"
            )

        # If a spending limit is configured, compute session utilisation
        if limit is not None and limit > 0:
            result["session_used_pct"] = min((usage / limit) * 100, 100)
            result["model_usage"]["Limit"] = f"${limit:.2f}"
            result["model_usage"]["Used"] = f"${usage:.2f}"
            remaining = max(limit - usage, 0)
            result["model_usage"]["Remaining"] = f"${remaining:.2f}"

        # Fetch credits info
        credits_resp = self._fetch_json(OPENROUTER_CREDITS_URL, api_key)
        if credits_resp is not None:
            credits_data = credits_resp.get("data", {})
            total_credits = credits_data.get("total_credits", 0) or 0
            total_usage = credits_data.get("total_usage", 0) or 0
            balance = max(total_credits - total_usage, 0)

            result["extra_usage_enabled"] = total_credits > 0
            result["extra_usage_current"] = total_usage
            result["extra_usage_limit"] = total_credits
            if total_credits > 0:
                result["extra_usage_pct"] = min((total_usage / total_credits) * 100, 100)

            # Total usage is already in USD -- use as 30-day cost
            result["cost_30_days"] = total_usage

            # Enrich plan name with balance
            if total_credits > 0:
                result["plan_name"] += f" (${balance:.2f} balance)"

            result["model_usage"]["Credits"] = f"${total_credits:.2f}"
            result["model_usage"]["Spent"] = f"${total_usage:.2f}"
            result["model_usage"]["Balance"] = f"${balance:.2f}"

            # If no hard limit was set, derive session pct from credits
            if limit is None and total_credits > 0:
                result["session_used_pct"] = min(
                    (total_usage / total_credits) * 100, 100
                )

        # Label from key metadata
        label = key_data.get("label", "")
        if label:
            result["model_usage"]["Key"] = label

        return result
