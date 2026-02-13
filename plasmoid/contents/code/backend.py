#!/usr/bin/env python3
"""
PlasmaCodexBar - Backend Service
Exposes AI usage data for the KDE Plasma applet
"""

import json
import sys
import os
import glob
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import urllib.request
import urllib.error

# Configuration paths
CONFIG_DIR = Path.home() / ".config" / "plasmacodexbar"
CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"

# API endpoints
CLAUDE_OAUTH_API_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
CODEX_OAUTH_API_URL = "https://chatgpt.com/backend-api/wham/usage"


class ClaudeCollector:
    """Collects Claude usage data"""

    def __init__(self):
        self.credentials_file = CLAUDE_DIR / ".credentials.json"

    def collect(self) -> Dict[str, Any]:
        result = {
            "provider_id": "claude",
            "provider_name": "Claude",
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
            "cost_30_days": 0,
            "cost_30_days_tokens": 0,
        }

        # Load credentials
        if not self.credentials_file.exists():
            result["error_message"] = "Not logged in. Run 'claude' to authenticate."
            return result

        try:
            with open(self.credentials_file, 'r') as f:
                creds = json.load(f).get("claudeAiOauth", {})
        except Exception:
            result["error_message"] = "Failed to read credentials."
            return result

        access_token = creds.get("accessToken")
        if not access_token:
            result["error_message"] = "No access token. Run 'claude' to authenticate."
            return result

        # Check expiry
        expires_at = creds.get("expiresAt", 0)
        if expires_at and datetime.fromtimestamp(expires_at / 1000) < datetime.now():
            result["error_message"] = "Token expired. Run 'claude' to refresh."
            return result

        # Determine plan
        sub_type = creds.get("subscriptionType", "free")
        rate_tier = creds.get("rateLimitTier", "")
        if "max" in rate_tier.lower() or "max" in sub_type.lower():
            result["plan_name"] = "Max"
        elif "pro" in sub_type.lower():
            result["plan_name"] = "Pro"
        elif "team" in sub_type.lower():
            result["plan_name"] = "Team"
        else:
            result["plan_name"] = sub_type.title()

        # Fetch from API
        try:
            req = urllib.request.Request(
                CLAUDE_OAUTH_API_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": CLAUDE_OAUTH_BETA_HEADER,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))

                    result["is_connected"] = True

                    # 5-hour session (API returns percentage directly, e.g. 30.0 = 30%)
                    five_hour = data.get("five_hour", {})
                    if five_hour:
                        result["session_used_pct"] = five_hour.get("utilization", 0)
                        if five_hour.get("resets_at"):
                            result["session_reset_time"] = five_hour["resets_at"]

                    # 7-day window
                    seven_day = data.get("seven_day", {})
                    if seven_day:
                        result["weekly_used_pct"] = seven_day.get("utilization", 0)
                        if seven_day.get("resets_at"):
                            result["weekly_reset_time"] = seven_day["resets_at"]

                    # Model quotas
                    for key in ["seven_day_sonnet", "seven_day_opus"]:
                        model_data = data.get(key, {})
                        if model_data and model_data.get("utilization") is not None:
                            model_name = "Sonnet" if "sonnet" in key else "Opus"
                            result["model_usage"][model_name] = model_data["utilization"]

                    # Extra usage
                    extra = data.get("extra_usage", {})
                    if extra and extra.get("is_enabled"):
                        result["extra_usage_enabled"] = True
                        result["extra_usage_current"] = (extra.get("used_credits", 0) or 0) / 100
                        result["extra_usage_limit"] = (extra.get("monthly_limit", 0) or 0) / 100
                        result["extra_usage_pct"] = extra.get("utilization", 0) or 0

                    # Calculate pace
                    if result["weekly_reset_time"]:
                        try:
                            reset = datetime.fromisoformat(result["weekly_reset_time"].replace('Z', '+00:00'))
                            now = datetime.now(timezone.utc)
                            week_start = reset - timedelta(days=7)
                            total_secs = (reset - week_start).total_seconds()
                            elapsed_secs = (now - week_start).total_seconds()
                            expected = (elapsed_secs / total_secs) * 100
                            pace = result["weekly_used_pct"] - expected

                            if pace < 0:
                                result["pace_status"] = f"Behind ({pace:.0f}%)"
                            elif pace > 0:
                                result["pace_status"] = f"Ahead (+{pace:.0f}%)"
                            else:
                                result["pace_status"] = "On track"
                        except:
                            pass

        except urllib.error.HTTPError as e:
            result["error_message"] = f"API error: {e.code}"
            result["is_connected"] = True
        except Exception as e:
            result["error_message"] = f"Failed to fetch: {e}"

        # Load cost stats from local cache
        self._load_cost_stats(result)
        return result

    def _load_cost_stats(self, result: Dict[str, Any]):
        """Load cost/token stats from Claude session files and stats cache.

        Token counts come from parsing session JSONL files (output_tokens per message).
        Cost estimates come from the stats-cache.json modelUsage data.
        """
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)

        # Parse session files for accurate token counts
        projects_dir = CLAUDE_DIR / "projects"
        if projects_dir.exists():
            try:
                for jsonl in projects_dir.glob("**/*.jsonl"):
                    try:
                        mtime = datetime.fromtimestamp(jsonl.stat().st_mtime).date()
                    except OSError:
                        continue
                    if mtime < thirty_days_ago:
                        continue

                    session_output = 0
                    try:
                        with open(jsonl, 'r') as f:
                            for line in f:
                                try:
                                    entry = json.loads(line)
                                    msg = entry.get("message")
                                    if not isinstance(msg, dict):
                                        continue
                                    usage = msg.get("usage")
                                    if not isinstance(usage, dict):
                                        continue
                                    session_output += usage.get("output_tokens", 0)
                                except (json.JSONDecodeError, TypeError):
                                    continue
                    except OSError:
                        continue

                    if mtime == today:
                        result["cost_today_tokens"] += session_output
                    result["cost_30_days_tokens"] += session_output
            except Exception:
                pass

        # Cost estimates from stats-cache.json
        stats_file = CLAUDE_DIR / "stats-cache.json"
        if not stats_file.exists():
            return

        try:
            with open(stats_file, 'r') as f:
                data = json.load(f)

            PRICING = {
                "claude-opus-4-5-20251101": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
                "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
            }
            DEFAULT_PRICING = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}

            model_usage = data.get("modelUsage", {})
            total_cost = 0.0

            for model_id, usage in model_usage.items():
                input_tokens = usage.get("inputTokens", 0)
                output_tokens = usage.get("outputTokens", 0)
                cache_read = usage.get("cacheReadInputTokens", 0)
                cache_write = usage.get("cacheCreationInputTokens", 0)

                pricing = PRICING.get(model_id, DEFAULT_PRICING)
                cost = (
                    (input_tokens / 1_000_000) * pricing["input"] +
                    (output_tokens / 1_000_000) * pricing["output"] +
                    (cache_read / 1_000_000) * pricing["cache_read"] +
                    (cache_write / 1_000_000) * pricing["cache_write"]
                )
                total_cost += cost

            result["cost_30_days"] = total_cost
            result["cost_today"] = (result["cost_today_tokens"] / 1_000_000) * 5  # Rough estimate

        except Exception:
            pass


class CodexCollector:
    """Collects Codex/ChatGPT usage data"""

    PLAN_NAMES = {
        "free": "Free", "plus": "Plus", "pro": "Pro",
        "team": "Team", "enterprise": "Enterprise",
    }

    def __init__(self):
        self.auth_file = CODEX_DIR / "auth.json"
        self.sessions_dir = CODEX_DIR / "sessions"

    def collect(self) -> Dict[str, Any]:
        result = {
            "provider_id": "codex",
            "provider_name": "Codex",
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
            "cost_30_days": 0,
            "cost_30_days_tokens": 0,
        }

        if not self.auth_file.exists():
            result["error_message"] = "Not logged in. Run 'codex' to authenticate."
            return result

        try:
            with open(self.auth_file, 'r') as f:
                auth = json.load(f)
        except Exception:
            result["error_message"] = "Failed to read auth file."
            return result

        tokens = auth.get("tokens", {})
        access_token = tokens.get("access_token", "").strip()
        if not access_token:
            access_token = auth.get("OPENAI_API_KEY", "").strip()

        if not access_token:
            result["error_message"] = "No access token. Run 'codex' to authenticate."
            return result

        # Fetch usage
        try:
            req = urllib.request.Request(
                CODEX_OAUTH_API_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                }
            )
            if tokens.get("account_id"):
                req.add_header("ChatGPT-Account-Id", tokens["account_id"])

            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))

                    result["is_connected"] = True
                    result["plan_name"] = self.PLAN_NAMES.get(
                        data.get("plan_type", ""),
                        data.get("plan_type", "Unknown").title()
                    )

                    rate_limit = data.get("rate_limit", {})

                    # Primary window (session)
                    primary = rate_limit.get("primary_window", {})
                    if primary:
                        result["session_used_pct"] = primary.get("used_percent", 0)
                        if primary.get("reset_at"):
                            result["session_reset_time"] = datetime.fromtimestamp(
                                primary["reset_at"], tz=timezone.utc
                            ).isoformat()

                    # Secondary window (weekly)
                    secondary = rate_limit.get("secondary_window", {})
                    if secondary:
                        result["weekly_used_pct"] = secondary.get("used_percent", 0)
                        if secondary.get("reset_at"):
                            result["weekly_reset_time"] = datetime.fromtimestamp(
                                secondary["reset_at"], tz=timezone.utc
                            ).isoformat()

                    # Calculate pace for secondary window
                    if result["weekly_reset_time"]:
                        try:
                            reset = datetime.fromisoformat(result["weekly_reset_time"].replace('Z', '+00:00'))
                            now = datetime.now(timezone.utc)
                            window_seconds = secondary.get("limit_window_seconds", 7 * 24 * 3600)
                            window_start = reset - timedelta(seconds=window_seconds)
                            total_secs = float(window_seconds)
                            elapsed_secs = (now - window_start).total_seconds()
                            expected = (elapsed_secs / total_secs) * 100
                            pace = result["weekly_used_pct"] - expected

                            if pace < 0:
                                result["pace_status"] = f"Behind ({pace:.0f}%)"
                            elif pace > 0:
                                result["pace_status"] = f"Ahead (+{pace:.0f}%)"
                            else:
                                result["pace_status"] = "On track"
                        except:
                            pass

        except urllib.error.HTTPError as e:
            result["error_message"] = f"API error: {e.code}"
            result["is_connected"] = True
        except Exception as e:
            result["error_message"] = f"Failed to fetch: {e}"

        # Load token stats from local session files
        self._load_cost_stats(result)
        return result

    def _load_cost_stats(self, result: Dict[str, Any]):
        """Load token/cost stats from Codex CLI session files.

        Session files at ~/.codex/sessions/YYYY/MM/DD/*.jsonl contain
        token_count events with cumulative total_token_usage per session.
        We read the last token_count from each session to get its totals.
        """
        if not self.sessions_dir.exists():
            return

        try:
            today = datetime.now().date()
            thirty_days_ago = today - timedelta(days=30)

            today_tokens = 0
            thirty_day_tokens = 0

            # Walk date-organized directories: sessions/YYYY/MM/DD/*.jsonl
            for year_dir in sorted(self.sessions_dir.iterdir()):
                if not year_dir.is_dir() or not year_dir.name.isdigit():
                    continue
                for month_dir in sorted(year_dir.iterdir()):
                    if not month_dir.is_dir() or not month_dir.name.isdigit():
                        continue
                    for day_dir in sorted(month_dir.iterdir()):
                        if not day_dir.is_dir() or not day_dir.name.isdigit():
                            continue

                        try:
                            dir_date = datetime(
                                int(year_dir.name),
                                int(month_dir.name),
                                int(day_dir.name)
                            ).date()
                        except ValueError:
                            continue

                        if dir_date < thirty_days_ago:
                            continue

                        for session_file in day_dir.glob("*.jsonl"):
                            session_tokens = self._get_session_tokens(session_file)
                            if dir_date == today:
                                today_tokens += session_tokens
                            thirty_day_tokens += session_tokens

            result["cost_today_tokens"] = today_tokens
            result["cost_30_days_tokens"] = thirty_day_tokens

        except Exception:
            pass  # Silently fail for cost stats

    def _get_session_tokens(self, session_file: Path) -> int:
        """Extract output tokens from the last token_count event in a session file.

        Uses output_tokens only to match Claude's metric (model-generated tokens).
        This gives a fair comparison since cached/input token accounting differs
        significantly between providers.
        """
        last_output = 0
        try:
            with open(session_file, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        payload = entry.get("payload") or {}
                        if (entry.get("type") == "event_msg"
                                and payload.get("type") == "token_count"):
                            info = payload.get("info") or {}
                            usage = info.get("total_token_usage") or {}
                            last_output = usage.get("output_tokens", 0)
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        except Exception:
            pass
        return last_output


def main():
    """Output usage data as JSON"""
    collectors = [ClaudeCollector(), CodexCollector()]

    providers = []
    for collector in collectors:
        try:
            providers.append(collector.collect())
        except Exception as e:
            providers.append({
                "provider_id": collector.__class__.__name__.lower().replace("collector", ""),
                "provider_name": collector.__class__.__name__.replace("Collector", ""),
                "is_connected": False,
                "error_message": str(e),
            })

    output = {
        "providers": providers,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if "--json" in sys.argv:
        print(json.dumps(output, indent=2))
    else:
        # Human-readable output
        for p in providers:
            print(f"\n=== {p['provider_name']} ===")
            if not p.get("is_connected"):
                print(f"  {p.get('error_message', 'Not connected')}")
                continue
            print(f"  Plan: {p.get('plan_name', 'Unknown')}")
            print(f"  Session: {p.get('session_used_pct', 0):.1f}%")
            print(f"  Weekly: {p.get('weekly_used_pct', 0):.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
