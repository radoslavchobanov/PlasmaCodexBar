#!/usr/bin/env python3
"""
PlasmaCodexBar - Backend Service
Exposes AI usage data for the KDE Plasma applet
"""

import json
import sys
import os
import re
import glob
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import urllib.request
import urllib.error

# Add adapters directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapters"))

# Configuration paths
CONFIG_DIR = Path.home() / ".config" / "plasmacodexbar"
CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"
STATS_CACHE_FILE = CONFIG_DIR / "claude_stats_cache.json"

# API endpoints
CLAUDE_MESSAGES_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_OAUTH_API_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
CLAUDE_QUOTA_CHECK_MODEL = "claude-sonnet-4-6"
CODEX_OAUTH_API_URL = "https://chatgpt.com/backend-api/wham/usage"


def _format_model_name(model_id: str) -> str:
    """Convert raw model ID to a short display name.

    Examples:
      claude-sonnet-4-5-20250929 → Sonnet 4.5
      claude-opus-4-6            → Opus 4.6
      gpt-5.3-codex              → GPT-5.3 Codex
    """
    name = re.sub(r'-\d{8}$', '', model_id)  # strip date suffix
    m = re.match(r'claude-(\w+)-(\d+)-(\d+)$', name, re.IGNORECASE)
    if m:
        return f"{m.group(1).title()} {m.group(2)}.{m.group(3)}"
    m = re.match(r'gpt-([\d.]+)-([\w]+?)(-max)?$', name, re.IGNORECASE)
    if m:
        suffix = " Max" if m.group(3) else ""
        return f"GPT-{m.group(1)} {m.group(2).title()}{suffix}"
    return name


class PricingCache:
    """Fetches and caches AI model pricing from OpenRouter (24h TTL)."""

    CACHE_FILE = CONFIG_DIR / "pricing_cache.json"
    OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
    TTL_HOURS = 24

    # Map normalized local model IDs → OpenRouter model IDs.
    # Entries marked with "~" use a proxy (different version) since the exact
    # model isn't on OpenRouter — costs are approximate.
    MODEL_MAP = {
        "claude-opus-4-6": "anthropic/claude-opus-4.6",
        "claude-opus-4-5": "anthropic/claude-opus-4.5",
        "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
        "claude-sonnet-4-5": "anthropic/claude-sonnet-4.5",
        "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
        "gpt-5-3-codex": "openai/gpt-5.2-codex",  # proxy: 5.3 not on OpenRouter
        "gpt-5-2-codex": "openai/gpt-5.2-codex",
        "gpt-5-1-codex": "openai/gpt-5.1-codex",
        "gpt-5-1-codex-max": "openai/gpt-5.1-codex",
    }

    # Models that use a proxy (approximate pricing)
    PROXY_MODELS = {"gpt-5-3-codex"}

    def __init__(self):
        self._prices: Optional[Dict[str, Any]] = None

    def get_price(self, model_id: str) -> Optional[Dict[str, float]]:
        """Return {"input": float, "output": float} per-token prices in USD, or None."""
        prices = self._get_prices()
        if not prices:
            return None
        normalized = self._normalize(model_id)
        or_id = self.MODEL_MAP.get(normalized)
        if not or_id:
            return None
        return prices.get(or_id)

    def is_approximate(self, model_id: str) -> bool:
        """Return True if pricing uses a proxy model (approximate cost)."""
        return self._normalize(model_id) in self.PROXY_MODELS

    def _normalize(self, model_id: str) -> str:
        """Strip date suffix (-YYYYMMDD) and normalize dots to hyphens."""
        normalized = re.sub(r'-\d{8}$', '', model_id.lower())
        return normalized.replace('.', '-')

    def _get_prices(self) -> Optional[Dict[str, Any]]:
        if self._prices is not None:
            return self._prices
        self._prices = self._load()
        return self._prices

    def _load(self) -> Optional[Dict[str, Any]]:
        """Load from cache if fresh, otherwise fetch from OpenRouter."""
        if self.CACHE_FILE.exists():
            try:
                with open(self.CACHE_FILE, 'r') as f:
                    cached = json.load(f)
                fetched_at = datetime.fromisoformat(cached.get("fetched_at", "1970-01-01"))
                if datetime.now() - fetched_at < timedelta(hours=self.TTL_HOURS):
                    return cached.get("prices", {})
            except Exception:
                pass
        return self._fetch()

    def _fetch(self) -> Optional[Dict[str, Any]]:
        """Fetch pricing from OpenRouter and persist to cache file."""
        try:
            req = urllib.request.Request(
                self.OPENROUTER_URL,
                headers={"Accept": "application/json", "User-Agent": "plasmacodexbar/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))

            prices = {}
            for model in data.get("data", []):
                model_id = model.get("id", "")
                pricing = model.get("pricing", {})
                prompt = pricing.get("prompt")
                completion = pricing.get("completion")
                if model_id and prompt is not None and completion is not None:
                    prices[model_id] = {
                        "input": float(prompt),
                        "output": float(completion),
                    }

            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.CACHE_FILE, 'w') as f:
                json.dump({"fetched_at": datetime.now().isoformat(), "prices": prices}, f)
            return prices
        except Exception:
            return None


class ClaudeCollector:
    """Collects Claude usage data"""

    def __init__(self):
        self.credentials_file = CLAUDE_DIR / ".credentials.json"
        self.debug_log = CLAUDE_DIR / "debug" / "latest"
        self.cache_file = CONFIG_DIR / "claude_usage_cache.json"

    def _read_debug_tail(self, max_bytes: int = 65536) -> str:
        """Read the tail of the latest Claude debug log for auth diagnostics."""
        if not self.debug_log.exists():
            return ""
        try:
            with open(self.debug_log, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                return f.read().decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _expired_token_message(self) -> str:
        default = "Token expired. Run 'claude auth login' to re-authenticate."
        try:
            age = datetime.now() - datetime.fromtimestamp(self.debug_log.stat().st_mtime)
            if age > timedelta(days=2):
                return default
        except Exception:
            return default

        debug_tail = self._read_debug_tail()
        if (
            "https://platform.claude.com/v1/oauth/token,status=400" in debug_tail
            and "OAuth token has expired. Please obtain a new token or refresh your existing token." in debug_tail
        ):
            return "Token expired. Claude refresh failed; run 'claude auth logout' then 'claude auth login'."
        return default

    def _load_cached_result(self) -> Optional[Dict[str, Any]]:
        """Load the last successful Claude usage result."""
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
        """Persist the last successful Claude usage result."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump({
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                }, f)
        except Exception:
            pass

    def _cached_result_with_error(self, message: str) -> Optional[Dict[str, Any]]:
        cached = self._load_cached_result()
        if not cached:
            return None
        cached = dict(cached)
        cached["error_message"] = message
        cached["is_connected"] = True
        return cached

    @staticmethod
    def _epoch_to_iso(epoch_str: Optional[str]) -> Optional[str]:
        if not epoch_str:
            return None
        try:
            return datetime.fromtimestamp(int(epoch_str), tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None

    def _fetch_usage_from_headers(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Use Claude's OAuth-enabled inference path and read quota headers."""
        payload = json.dumps({
            "model": CLAUDE_QUOTA_CHECK_MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "quota"}],
        }).encode("utf-8")

        req = urllib.request.Request(
            CLAUDE_MESSAGES_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": CLAUDE_OAUTH_BETA_HEADER,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status != 200:
                    return None
                headers = response.headers
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 429):
                return None
            raise

        five_hour_util = headers.get("anthropic-ratelimit-unified-5h-utilization")
        seven_day_util = headers.get("anthropic-ratelimit-unified-7d-utilization")
        overage_util = headers.get("anthropic-ratelimit-unified-overage-utilization")
        overage_status = headers.get("anthropic-ratelimit-unified-overage-status")

        if (
            five_hour_util is None
            and seven_day_util is None
            and overage_util is None
            and overage_status is None
        ):
            return None

        usage_data: Dict[str, Any] = {}
        if five_hour_util is not None:
            usage_data["five_hour"] = {
                "utilization": float(five_hour_util) * 100,
                "resets_at": self._epoch_to_iso(headers.get("anthropic-ratelimit-unified-5h-reset")),
            }
        if seven_day_util is not None:
            usage_data["seven_day"] = {
                "utilization": float(seven_day_util) * 100,
                "resets_at": self._epoch_to_iso(headers.get("anthropic-ratelimit-unified-7d-reset")),
            }
        if overage_util is not None or overage_status is not None:
            usage_data["extra_usage"] = {
                "is_enabled": overage_status not in (None, "", "disabled"),
                "utilization": float(overage_util or 0) * 100,
                "used_credits": 0,
                "monthly_limit": 0,
            }
        return usage_data

    def _fetch_usage_api(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Fallback to the old OAuth usage endpoint when header-based fetch is unavailable."""
        req = urllib.request.Request(
            CLAUDE_OAUTH_API_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "anthropic-beta": CLAUDE_OAUTH_BETA_HEADER,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 429):
                return None
            raise
        return None

    def _apply_usage_data(self, result: Dict[str, Any], data: Dict[str, Any]):
        result["is_connected"] = True

        five_hour = data.get("five_hour", {})
        if five_hour:
            result["session_used_pct"] = five_hour.get("utilization", 0)
            if five_hour.get("resets_at"):
                result["session_reset_time"] = five_hour["resets_at"]

        seven_day = data.get("seven_day", {})
        if seven_day:
            result["weekly_used_pct"] = seven_day.get("utilization", 0)
            if seven_day.get("resets_at"):
                result["weekly_reset_time"] = seven_day["resets_at"]

        for key in ["seven_day_sonnet", "seven_day_opus"]:
            model_data = data.get(key, {})
            if model_data and model_data.get("utilization") is not None:
                model_name = "Sonnet" if "sonnet" in key else "Opus"
                result["model_usage"][model_name] = model_data["utilization"]

        extra = data.get("extra_usage", {})
        if extra and extra.get("is_enabled"):
            result["extra_usage_enabled"] = True
            result["extra_usage_current"] = (extra.get("used_credits", 0) or 0) / 100
            result["extra_usage_limit"] = (extra.get("monthly_limit", 0) or 0) / 100
            result["extra_usage_pct"] = extra.get("utilization", 0) or 0

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
            except Exception:
                pass

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
            "cost_today_input_tokens": 0,
            "cost_today_output_tokens": 0,
            "cost_today_by_model": {},
            "cost_30_days": 0,
            "cost_30_days_tokens": 0,
            "cost_30_days_input_tokens": 0,
            "cost_30_days_output_tokens": 0,
            "cost_by_model": {},
            "cost_approximate": False,
            # All-time stats
            "stats_daily_activity": {},
            "stats_total_tokens": 0,
            "stats_total_input_tokens": 0,
            "stats_total_output_tokens": 0,
            "stats_active_days": 0,
            "stats_total_days_tracked": 0,
            "stats_most_active_day": "",
            "stats_favorite_model": "",
            "stats_session_count": 0,
            "stats_longest_session_seconds": 0,
            "stats_longest_streak_days": 0,
            "stats_current_streak_days": 0,
            "stats_tokens_by_model_by_day": {},
        }

        # Load credentials
        if not self.credentials_file.exists():
            result["error_message"] = "Not logged in. Run 'claude auth login' to authenticate."
            return result

        try:
            with open(self.credentials_file, 'r') as f:
                creds = json.load(f).get("claudeAiOauth", {})
        except Exception:
            result["error_message"] = "Failed to read credentials."
            return result

        access_token = creds.get("accessToken")
        if not access_token:
            result["error_message"] = "No access token. Run 'claude auth login' to authenticate."
            return result

        # Check expiry
        expires_at = creds.get("expiresAt", 0)
        if expires_at and datetime.fromtimestamp(expires_at / 1000) < datetime.now():
            result["error_message"] = self._expired_token_message()
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

        try:
            usage_data = self._fetch_usage_api(access_token)
            if usage_data is None:
                usage_data = self._fetch_usage_from_headers(access_token)
            if usage_data is not None:
                self._apply_usage_data(result, usage_data)
            else:
                result["error_message"] = "Failed to fetch usage data."
        except urllib.error.HTTPError as e:
            if e.code == 429:
                cached = self._cached_result_with_error("Rate limited. Showing cached data.")
                if cached:
                    self._load_cost_stats(cached)
                    return cached
                result["error_message"] = "Rate limited. Try again in a few minutes."
            else:
                result["error_message"] = f"API error: {e.code}"
        except Exception as e:
            cached = self._cached_result_with_error("Failed to refresh. Showing cached data.")
            if cached:
                self._load_cost_stats(cached)
                return cached
            result["error_message"] = f"Failed to fetch: {e}"

        # Load cost stats from local cache
        self._load_cost_stats(result)
        if result.get("is_connected"):
            self._save_cached_result(result)
        return result

    def _load_stats_cache(self) -> Optional[Dict[str, Any]]:
        """Load the all-time stats snapshot from disk.

        Returns the cache dict when it exists and can be parsed, otherwise None.
        The caller is responsible for deciding whether the cache is still valid.
        """
        if not STATS_CACHE_FILE.exists():
            return None
        try:
            with open(STATS_CACHE_FILE, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def _save_stats_cache(
        self,
        daily_activity: Dict[str, int],
        tokens_by_model_by_day: Dict[str, Any],
        sessions: list,
        total_input: int,
        total_output: int,
    ):
        """Persist the all-time stats snapshot to disk."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "cached_at": datetime.now().date().isoformat(),
                "daily_activity": daily_activity,
                "tokens_by_model_by_day": tokens_by_model_by_day,
                "sessions": sessions,
                "total_input": total_input,
                "total_output": total_output,
            }
            with open(STATS_CACHE_FILE, "w") as f:
                json.dump(payload, f)
        except Exception:
            pass

    def _load_cost_stats(self, result: Dict[str, Any]):
        """Load cost/token stats from Claude session files.

        Token counts and costs come from parsing session JSONL files.
        Prices are fetched live from OpenRouter (cached 24h).

        All-time stats (daily activity, streaks, sessions, favorite model)
        are computed alongside the 30-day cost window.

        Performance: An incremental cache (STATS_CACHE_FILE) stores the
        all-time aggregates for data outside the 30-day rolling window.
        The cache is rebuilt once per day; on subsequent refreshes that day
        only the recent 30-day files are re-parsed.
        """
        today = datetime.now().date()
        today_iso = today.isoformat()
        thirty_days_ago = today - timedelta(days=30)
        pricing = PricingCache()

        projects_dir = CLAUDE_DIR / "projects"
        if not projects_dir.exists():
            return

        # ------------------------------------------------------------------
        # Cache decision
        # ------------------------------------------------------------------
        existing_cache = self._load_stats_cache()
        cache_valid = (
            existing_cache is not None
            and existing_cache.get("cached_at") == today_iso
        )

        # Two sets of accumulators to avoid double-counting:
        #   old_*   — files with mtime < thirty_days_ago (saved to / loaded from cache)
        #   fresh_* — files with mtime >= thirty_days_ago (always re-read)
        # merged_* = old_* + fresh_* with no overlap.
        fresh_daily_activity: Dict[str, int] = {}
        fresh_tokens_by_model_by_day: Dict[str, Any] = {}
        fresh_sessions: list = []   # session durations (seconds) from recent files
        old_sessions: list = []     # session durations (seconds) from old files (stale-cache run only)

        # ------------------------------------------------------------------
        # Parse JSONL files
        # ------------------------------------------------------------------
        try:
            for jsonl in projects_dir.glob("**/*.jsonl"):
                try:
                    mtime = datetime.fromtimestamp(jsonl.stat().st_mtime).date()
                except OSError:
                    continue

                # When the cache is valid, skip files that are clearly outside
                # the 30-day window (file mtime older than thirty_days_ago).
                # Their contribution is already baked into the cache.
                if cache_valid and mtime < thirty_days_ago:
                    continue

                try:
                    is_old_file = mtime < thirty_days_ago
                    # Track first/last timestamp per file (1 file = 1 session, matching CC CLI)
                    file_first_ts = None
                    file_last_ts = None
                    # A file is a subagent sidechain if it has no isSidechain:false entry.
                    # We track whether we've seen at least one non-sidechain message so we
                    # can exclude pure subagent spawn files from the session count.
                    file_has_human_turn = False

                    with open(jsonl, 'r') as f:
                        for line in f:
                            try:
                                entry = json.loads(line)

                                # Detect human/main-session turns
                                if not file_has_human_turn and entry.get("isSidechain") is False:
                                    file_has_human_turn = True

                                msg = entry.get("message")
                                if not isinstance(msg, dict):
                                    continue
                                usage = msg.get("usage")
                                if not isinstance(usage, dict):
                                    continue

                                # Parse message timestamp
                                ts = entry.get("timestamp", "")
                                msg_dt_aware = None
                                if ts:
                                    try:
                                        msg_dt_aware = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                                        msg_date = msg_dt_aware.date()
                                    except Exception:
                                        msg_date = mtime
                                else:
                                    msg_date = mtime

                                inp = usage.get("input_tokens", 0)
                                out = usage.get("output_tokens", 0)
                                total = inp + out
                                model = msg.get("model", "")
                                display = _format_model_name(model) if model else ""

                                # --- All-time stats (no date filter) ---
                                if total > 0:
                                    date_key = msg_date.isoformat()

                                    # Daily activity heatmap
                                    fresh_daily_activity[date_key] = (
                                        fresh_daily_activity.get(date_key, 0) + total
                                    )

                                    # Tokens by model by day
                                    day_models = fresh_tokens_by_model_by_day.setdefault(date_key, {})
                                    if display:
                                        m_entry = day_models.setdefault(display, {"input": 0, "output": 0, "total": 0})
                                        m_entry["input"] += inp
                                        m_entry["output"] += out
                                        m_entry["total"] += total

                                    # Track file-level timestamps for session duration
                                    if msg_dt_aware is not None:
                                        if file_first_ts is None:
                                            file_first_ts = msg_dt_aware
                                        file_last_ts = msg_dt_aware

                                # --- 30-day cost stats (date-gated) ---
                                if msg_date < thirty_days_ago:
                                    continue

                                price = pricing.get_price(model) if model else None
                                msg_cost = (inp * price["input"] + out * price["output"]) if price else 0.0
                                is_today = (msg_date == today)

                                result["cost_30_days_tokens"] += inp + out
                                result["cost_30_days_input_tokens"] += inp
                                result["cost_30_days_output_tokens"] += out
                                result["cost_30_days"] += msg_cost
                                if is_today:
                                    result["cost_today_tokens"] += inp + out
                                    result["cost_today_input_tokens"] += inp
                                    result["cost_today_output_tokens"] += out
                                    result["cost_today"] += msg_cost

                                if model and (inp + out) > 0:
                                    b = result["cost_by_model"].setdefault(display, {"input": 0, "output": 0, "cost": 0.0})
                                    b["input"] += inp
                                    b["output"] += out
                                    b["cost"] += msg_cost
                                    if is_today:
                                        bt = result["cost_today_by_model"].setdefault(display, {"input": 0, "output": 0, "cost": 0.0})
                                        bt["input"] += inp
                                        bt["output"] += out
                                        bt["cost"] += msg_cost
                            except (json.JSONDecodeError, TypeError):
                                continue

                    # Record this file as one session; bucket by age to avoid double-count
                    # Skip pure subagent sidechain files (all entries have isSidechain=true)
                    if file_has_human_turn and file_first_ts is not None and file_last_ts is not None:
                        duration_s = (file_last_ts - file_first_ts).total_seconds()
                        if is_old_file:
                            old_sessions.append(duration_s)
                        else:
                            fresh_sessions.append(duration_s)

                except OSError:
                    continue
        except Exception:
            pass

        # ------------------------------------------------------------------
        # Merge cache + fresh data into the all-time accumulators
        # ------------------------------------------------------------------
        if cache_valid:
            # Cache holds only data from old files (mtime < thirty_days_ago).
            # fresh_* holds only data from recent files. No overlap → no double-count.
            cached_daily = existing_cache.get("daily_activity", {})
            merged_daily = dict(cached_daily)
            merged_daily.update(fresh_daily_activity)  # fresh wins on any boundary overlap

            cached_tbmd = existing_cache.get("tokens_by_model_by_day", {})
            merged_tbmd = dict(cached_tbmd)
            merged_tbmd.update(fresh_tokens_by_model_by_day)  # fresh wins

            # Sessions: cache has old-file sessions, fresh has recent-file sessions
            merged_sessions = list(existing_cache.get("sessions", [])) + fresh_sessions
        else:
            # Stale cache: full scan was done; old_* and fresh_* together cover everything.
            # Save only old_sessions to cache so future runs don't double-count.
            merged_daily = fresh_daily_activity  # fresh covered ALL files in this run
            merged_tbmd = fresh_tokens_by_model_by_day
            merged_sessions = old_sessions + fresh_sessions

            self._save_stats_cache(
                daily_activity={k: v for k, v in merged_daily.items()
                                if k < thirty_days_ago.isoformat()},
                tokens_by_model_by_day={k: v for k, v in merged_tbmd.items()
                                        if k < thirty_days_ago.isoformat()},
                sessions=old_sessions,
                total_input=0,   # no longer used; totals derived from tbmd
                total_output=0,
            )

        # Derive totals from merged_tbmd (immune to accumulator double-counting)
        total_in = total_out = 0
        for day_data in merged_tbmd.values():
            for m_data in day_data.values():
                total_in += m_data.get("input", 0)
                total_out += m_data.get("output", 0)

        # Populate all-time fields in result
        result["stats_daily_activity"] = merged_daily
        result["stats_tokens_by_model_by_day"] = merged_tbmd
        result["stats_total_input_tokens"] = total_in
        result["stats_total_output_tokens"] = total_out
        result["stats_total_tokens"] = total_in + total_out

        # --- Derived all-time stats ---
        daily_activity = result["stats_daily_activity"]

        # Active days and most active day
        if daily_activity:
            result["stats_active_days"] = len(daily_activity)
            result["stats_most_active_day"] = max(daily_activity, key=lambda d: daily_activity[d])

            # Total days tracked: from first activity date to today (inclusive)
            try:
                first_day = min(daily_activity.keys())
                first_date = datetime.fromisoformat(first_day).date()
                result["stats_total_days_tracked"] = (today - first_date).days + 1
            except Exception:
                pass

        # Favorite model: highest all-time total tokens
        tokens_by_model: Dict[str, int] = {}
        for day_models in result["stats_tokens_by_model_by_day"].values():
            for model_name, counts in day_models.items():
                tokens_by_model[model_name] = tokens_by_model.get(model_name, 0) + counts.get("total", 0)
        if tokens_by_model:
            result["stats_favorite_model"] = max(tokens_by_model, key=lambda m: tokens_by_model[m])

        # Sessions: 1 JSONL file = 1 session (matches CC CLI definition)
        if merged_sessions:
            result["stats_session_count"] = len(merged_sessions)
            result["stats_longest_session_seconds"] = int(max(merged_sessions))

        # Streak calculation using daily_activity keys
        if daily_activity:
            try:
                active_dates = sorted(
                    datetime.fromisoformat(d).date() for d in daily_activity.keys()
                )

                # Longest streak
                longest = 1
                current_run = 1
                for i in range(1, len(active_dates)):
                    if (active_dates[i] - active_dates[i - 1]).days == 1:
                        current_run += 1
                        if current_run > longest:
                            longest = current_run
                    else:
                        current_run = 1
                result["stats_longest_streak_days"] = longest

                # Current streak: consecutive days ending on today or yesterday
                streak = 0
                check = today
                # Allow streak to include yesterday if today has no activity yet
                if check not in set(active_dates):
                    check = today - timedelta(days=1)

                active_set = set(active_dates)
                while check in active_set:
                    streak += 1
                    check -= timedelta(days=1)
                result["stats_current_streak_days"] = streak
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
            "cost_today_input_tokens": 0,
            "cost_today_output_tokens": 0,
            "cost_today_by_model": {},
            "cost_30_days": 0,
            "cost_30_days_tokens": 0,
            "cost_30_days_input_tokens": 0,
            "cost_30_days_output_tokens": 0,
            "cost_by_model": {},
            "cost_approximate": False,
            # Stats tab fields
            "stats_daily_activity": {},
            "stats_tokens_by_model_by_day": {},
            "stats_session_count": 0,
            "stats_active_days": 0,
            "stats_total_days_tracked": 0,
            "stats_total_tokens": 0,
            "stats_longest_session_seconds": 0,
            "stats_most_active_day": "",
            "stats_longest_streak_days": 0,
            "stats_current_streak_days": 0,
            "stats_favorite_model": "",
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
        self._load_stats(result)
        return result

    def _load_cost_stats(self, result: Dict[str, Any]):
        """Load token/cost stats from Codex CLI session files.

        Session files at ~/.codex/sessions/YYYY/MM/DD/*.jsonl contain
        token_count events with cumulative total_token_usage per session.
        We read the last token_count from each session to get its totals.
        Prices are fetched live from OpenRouter (cached 24h).
        """
        if not self.sessions_dir.exists():
            return

        model = self._get_codex_model()
        pricing = PricingCache()
        price = pricing.get_price(model) if model else None
        approximate = pricing.is_approximate(model) if model else False
        model_display = _format_model_name(model) if model else None
        result["cost_approximate"] = approximate

        try:
            today = datetime.now().date()
            thirty_days_ago = today - timedelta(days=30)

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
                            inp, out = self._get_session_tokens(session_file)
                            cost = (inp * price["input"] + out * price["output"]) if price else 0.0

                            result["cost_30_days_tokens"] += inp + out
                            result["cost_30_days_input_tokens"] += inp
                            result["cost_30_days_output_tokens"] += out
                            result["cost_30_days"] += cost
                            if dir_date == today:
                                result["cost_today_tokens"] += inp + out
                                result["cost_today_input_tokens"] += inp
                                result["cost_today_output_tokens"] += out
                                result["cost_today"] += cost

                            if model_display:
                                b = result["cost_by_model"].setdefault(model_display, {"input": 0, "output": 0, "cost": 0.0})
                                b["input"] += inp
                                b["output"] += out
                                b["cost"] += cost
                                if dir_date == today:
                                    bt = result["cost_today_by_model"].setdefault(model_display, {"input": 0, "output": 0, "cost": 0.0})
                                    bt["input"] += inp
                                    bt["output"] += out
                                    bt["cost"] += cost

        except Exception:
            pass  # Silently fail for cost stats

    def _load_stats(self, result: Dict[str, Any]):
        """Compute all-time stats from all Codex session files.

        Scans ~/.codex/sessions/YYYY/MM/DD/*.jsonl. One file = one session.
        Token totals come from the last token_count event in each file
        (cumulative total_token_usage). Session duration from first/last timestamp.
        """
        if not self.sessions_dir.exists():
            return

        model = self._get_codex_model()
        model_display = _format_model_name(model) if model else None

        daily_activity: Dict[str, int] = {}
        tokens_by_model_by_day: Dict[str, Any] = {}
        session_count = 0
        sessions_with_duration = []  # seconds

        try:
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

                        date_key = dir_date.isoformat()

                        for session_file in day_dir.glob("*.jsonl"):
                            session_count += 1
                            try:
                                first_ts = None
                                last_ts = None
                                last_input = 0
                                last_output = 0

                                with open(session_file, 'r') as f:
                                    for line in f:
                                        try:
                                            entry = json.loads(line)
                                            ts = entry.get("timestamp")
                                            if ts:
                                                if first_ts is None:
                                                    first_ts = ts
                                                last_ts = ts
                                            payload = entry.get("payload") or {}
                                            if (entry.get("type") == "event_msg"
                                                    and payload.get("type") == "token_count"):
                                                info = payload.get("info") or {}
                                                usage = info.get("total_token_usage") or {}
                                                last_input = usage.get("input_tokens", 0)
                                                last_output = usage.get("output_tokens", 0)
                                        except (json.JSONDecodeError, TypeError):
                                            continue

                                total = last_input + last_output
                                if total > 0:
                                    daily_activity[date_key] = daily_activity.get(date_key, 0) + total
                                    if model_display:
                                        day_m = tokens_by_model_by_day.setdefault(date_key, {})
                                        m_entry = day_m.setdefault(model_display, {"input": 0, "output": 0, "total": 0})
                                        m_entry["input"] += last_input
                                        m_entry["output"] += last_output
                                        m_entry["total"] += total

                                if first_ts and last_ts and first_ts != last_ts:
                                    try:
                                        t1 = datetime.fromisoformat(first_ts.replace('Z', '+00:00'))
                                        t2 = datetime.fromisoformat(last_ts.replace('Z', '+00:00'))
                                        sessions_with_duration.append((t2 - t1).total_seconds())
                                    except Exception:
                                        pass

                            except OSError:
                                continue

        except Exception:
            pass

        result["stats_daily_activity"] = daily_activity
        result["stats_tokens_by_model_by_day"] = tokens_by_model_by_day
        result["stats_session_count"] = session_count
        result["stats_total_tokens"] = sum(daily_activity.values())
        result["stats_favorite_model"] = model_display or "—"

        if sessions_with_duration:
            result["stats_longest_session_seconds"] = max(sessions_with_duration)

        if daily_activity:
            result["stats_active_days"] = len(daily_activity)
            result["stats_most_active_day"] = max(daily_activity, key=lambda d: daily_activity[d])

            all_dates = sorted(datetime.fromisoformat(d).date() for d in daily_activity.keys())
            result["stats_total_days_tracked"] = (all_dates[-1] - all_dates[0]).days + 1

            date_set = set(all_dates)
            today = datetime.now().date()

            # Longest streak
            longest = 1
            run = 1
            for i in range(1, len(all_dates)):
                if (all_dates[i] - all_dates[i - 1]).days == 1:
                    run += 1
                    longest = max(longest, run)
                else:
                    run = 1
            result["stats_longest_streak_days"] = longest

            # Current streak (count back from today; also accept yesterday if today has no data yet)
            current = 0
            check = today
            while check in date_set:
                current += 1
                check -= timedelta(days=1)
            if current == 0:
                check = today - timedelta(days=1)
                while check in date_set:
                    current += 1
                    check -= timedelta(days=1)
            result["stats_current_streak_days"] = current

    def _get_codex_model(self) -> Optional[str]:
        """Read the model name from ~/.codex/config.toml."""
        config_file = CODEX_DIR / "config.toml"
        if not config_file.exists():
            return None
        try:
            with open(config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('model') and '=' in line:
                        parts = line.split('=', 1)
                        if len(parts) == 2:
                            return parts[1].strip().strip('"\'')
        except Exception:
            pass
        return None

    def _get_session_tokens(self, session_file: Path) -> tuple:
        """Extract (input, output) tokens from the last token_count event in a session file.

        Input = input_tokens - cached_input_tokens (non-cached input processed fresh).
        Output = output_tokens.
        """
        last_input = 0
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
                            input_tokens = usage.get("input_tokens", 0)
                            cached_tokens = usage.get("cached_input_tokens", 0)
                            last_input = input_tokens - cached_tokens
                            last_output = usage.get("output_tokens", 0)
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        except Exception:
            pass
        return last_input, last_output


def _load_optional_collectors():
    """Import and instantiate collectors from the adapters package.

    Each adapter is optional – if its file is missing or fails to import
    the provider is simply skipped.
    """
    extra = []
    adapter_specs = [
        ("gemini_adapter", "GeminiCollector"),
        ("copilot_adapter", "CopilotCollector"),
        ("cursor_adapter", "CursorCollector"),
        ("openrouter_adapter", "OpenRouterCollector"),
    ]
    for module_name, class_name in adapter_specs:
        try:
            mod = __import__(module_name)
            cls = getattr(mod, class_name)
            extra.append(cls())
        except Exception:
            pass
    return extra


def main():
    """Output usage data as JSON"""
    collectors = [ClaudeCollector(), CodexCollector()] + _load_optional_collectors()

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
