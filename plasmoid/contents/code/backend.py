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

# Configuration paths
CONFIG_DIR = Path.home() / ".config" / "plasmacodexbar"
CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"

# API endpoints
CLAUDE_OAUTH_API_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
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
        """Load cost/token stats from Claude session files.

        Token counts and costs come from parsing session JSONL files.
        Prices are fetched live from OpenRouter (cached 24h).
        """
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)
        pricing = PricingCache()

        projects_dir = CLAUDE_DIR / "projects"
        if not projects_dir.exists():
            return

        try:
            for jsonl in projects_dir.glob("**/*.jsonl"):
                try:
                    mtime = datetime.fromtimestamp(jsonl.stat().st_mtime).date()
                except OSError:
                    continue
                # Skip files that are definitely too old (performance optimisation only)
                if mtime < thirty_days_ago:
                    continue

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

                                # Use per-message timestamp for accurate date classification
                                ts = entry.get("timestamp", "")
                                if ts:
                                    try:
                                        msg_date = datetime.fromisoformat(ts.replace('Z', '+00:00')).date()
                                    except Exception:
                                        msg_date = mtime
                                else:
                                    msg_date = mtime

                                if msg_date < thirty_days_ago:
                                    continue

                                inp = usage.get("input_tokens", 0)
                                out = usage.get("output_tokens", 0)
                                model = msg.get("model", "")
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
                                    display = _format_model_name(model)
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
                except OSError:
                    continue
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
