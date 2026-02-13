#!/usr/bin/env python3
"""
AI Usage Monitor - A Linux system tray application for monitoring AI usage limits
Inspired by CodexBar for macOS

Version 3.1 - Polished tabbed UI with theme support:
  - Dark and Light themes with settings dialog
  - Professional UI/UX with proper contrast
  - Claude: via Anthropic OAuth API
  - Codex/ChatGPT: via OpenAI OAuth API (same as CodexBar)
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')

try:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
    HAS_APPINDICATOR = True
except:
    HAS_APPINDICATOR = False

from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import json
import os
import sys
import tempfile
import webbrowser
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
import cairo
import subprocess
import math

# ============================================================================
# Constants
# ============================================================================

APP_NAME = "AI Usage Monitor"
APP_ID = "ai-usage-monitor"
VERSION = "3.1.0"

CONFIG_DIR = Path.home() / ".config" / "ai-usage-monitor"
CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"

ICON_DIR = Path(tempfile.gettempdir()) / "ai-usage-monitor-icons"

# Claude OAuth API
CLAUDE_OAUTH_API_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA_HEADER = "oauth-2025-04-20"

# Codex OAuth API
CODEX_OAUTH_API_URL = "https://chatgpt.com/backend-api/wham/usage"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class ProviderStats:
    """Statistics for a single AI provider"""
    provider_id: str = ""
    provider_name: str = ""
    is_connected: bool = False
    last_update: Optional[datetime] = None
    error_message: str = ""

    # Plan info
    plan_name: str = "Unknown"

    # Session (5-hour window)
    session_used_pct: float = 0.0
    session_reset_time: Optional[datetime] = None

    # Weekly (7-day window)
    weekly_used_pct: float = 0.0
    weekly_reset_time: Optional[datetime] = None

    # Pace calculation
    pace_percentage: float = 0.0
    pace_status: str = "On track"

    # Model-specific (Sonnet/Opus quotas)
    model_usage: Dict[str, float] = field(default_factory=dict)

    # Extra usage / overage
    extra_usage_enabled: bool = False
    extra_usage_current: float = 0.0
    extra_usage_limit: float = 0.0
    extra_usage_pct: float = 0.0

    # Cost tracking (from local stats)
    cost_today: float = 0.0
    cost_today_tokens: int = 0
    cost_30_days: float = 0.0
    cost_30_days_tokens: int = 0

    # Raw stats
    total_messages: int = 0
    total_sessions: int = 0


@dataclass
class AppConfig:
    """Application configuration"""
    refresh_interval: int = 60
    enabled_providers: List[str] = field(default_factory=lambda: ["claude", "codex"])


# ============================================================================
# Claude OAuth Usage Fetcher
# ============================================================================

class ClaudeOAuthFetcher:
    """Fetches real subscription usage from Claude OAuth API"""

    def __init__(self):
        self.credentials_file = CLAUDE_DIR / ".credentials.json"
        self.stats_file = CLAUDE_DIR / "stats-cache.json"

    def _load_credentials(self) -> Optional[dict]:
        """Load OAuth credentials from Claude Code"""
        if not self.credentials_file.exists():
            return None
        try:
            with open(self.credentials_file, 'r') as f:
                data = json.load(f)
                return data.get("claudeAiOauth", {})
        except Exception:
            return None

    def _fetch_usage_api(self, access_token: str) -> Optional[dict]:
        """Fetch usage data from Claude OAuth API"""
        try:
            req = urllib.request.Request(
                CLAUDE_OAUTH_API_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": CLAUDE_OAUTH_BETA_HEADER,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "AIUsageMonitor/2.1"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            print(f"Claude API HTTP error: {e.code}")
        except Exception as e:
            print(f"Claude API error: {e}")
        return None

    def _load_local_cost_stats(self) -> dict:
        """Load cost/token stats from local Claude Code cache"""
        stats = {
            "cost_today": 0.0,
            "cost_today_tokens": 0,
            "cost_30_days": 0.0,
            "cost_30_days_tokens": 0,
            "total_messages": 0,
            "total_sessions": 0,
        }

        if not self.stats_file.exists():
            return stats

        try:
            with open(self.stats_file, 'r') as f:
                data = json.load(f)

            # Pricing per 1M tokens (rough average)
            PRICING = {
                "claude-opus-4-5-20251101": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
                "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
            }
            DEFAULT_PRICING = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}

            today = datetime.now().date()
            thirty_days_ago = today - timedelta(days=30)

            # Calculate daily tokens
            for entry in data.get("dailyModelTokens", []):
                try:
                    entry_date = datetime.strptime(entry["date"], "%Y-%m-%d").date()
                    tokens = sum(entry.get("tokensByModel", {}).values())
                    if entry_date == today:
                        stats["cost_today_tokens"] = tokens
                    if entry_date >= thirty_days_ago:
                        stats["cost_30_days_tokens"] += tokens
                except:
                    continue

            # Calculate costs from model usage
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

            stats["cost_30_days"] = total_cost
            stats["cost_today"] = (stats["cost_today_tokens"] / 1_000_000) * 5  # Rough estimate

            stats["total_messages"] = data.get("totalMessages", 0)
            stats["total_sessions"] = data.get("totalSessions", 0)

        except Exception as e:
            print(f"Error loading local stats: {e}")

        return stats

    def collect(self) -> ProviderStats:
        """Collect Claude usage from OAuth API"""
        stats = ProviderStats(
            provider_id="claude",
            provider_name="Claude",
        )

        # Load credentials
        creds = self._load_credentials()
        if not creds:
            stats.error_message = "Not logged in. Run 'claude' to authenticate."
            return stats

        access_token = creds.get("accessToken")
        if not access_token:
            stats.error_message = "No access token found. Run 'claude' to authenticate."
            return stats

        # Check if token is expired
        expires_at = creds.get("expiresAt", 0)
        if expires_at and datetime.fromtimestamp(expires_at / 1000) < datetime.now():
            stats.error_message = "Token expired. Run 'claude' to refresh."
            return stats

        # Determine plan from credentials
        sub_type = creds.get("subscriptionType", "free")
        rate_tier = creds.get("rateLimitTier", "")
        if "max" in rate_tier.lower() or "max" in sub_type.lower():
            stats.plan_name = "Max"
        elif "pro" in sub_type.lower():
            stats.plan_name = "Pro"
        elif "team" in sub_type.lower():
            stats.plan_name = "Team"
        else:
            stats.plan_name = sub_type.title()

        # Fetch from OAuth API
        usage_data = self._fetch_usage_api(access_token)
        if not usage_data:
            stats.error_message = "Failed to fetch usage data from API."
            stats.is_connected = True  # We have credentials, just API failed
            return stats

        stats.is_connected = True
        stats.last_update = datetime.now()

        # Parse 5-hour session window
        five_hour = usage_data.get("five_hour", {})
        if five_hour:
            stats.session_used_pct = five_hour.get("utilization", 0.0)
            resets_at = five_hour.get("resets_at")
            if resets_at:
                try:
                    stats.session_reset_time = datetime.fromisoformat(resets_at.replace('Z', '+00:00'))
                except:
                    pass

        # Parse 7-day window
        seven_day = usage_data.get("seven_day", {})
        if seven_day:
            stats.weekly_used_pct = seven_day.get("utilization", 0.0)
            resets_at = seven_day.get("resets_at")
            if resets_at:
                try:
                    stats.weekly_reset_time = datetime.fromisoformat(resets_at.replace('Z', '+00:00'))
                except:
                    pass

        # Parse model-specific quotas (Sonnet/Opus)
        for key in ["seven_day_sonnet", "seven_day_opus"]:
            model_data = usage_data.get(key, {})
            if model_data and model_data.get("utilization") is not None:
                model_name = "Sonnet" if "sonnet" in key else "Opus"
                stats.model_usage[model_name] = model_data.get("utilization", 0.0)

        # Parse extra usage
        extra = usage_data.get("extra_usage", {})
        if extra:
            stats.extra_usage_enabled = extra.get("is_enabled", False)
            if stats.extra_usage_enabled:
                # Values are in cents, convert to dollars
                used = extra.get("used_credits", 0) or 0
                limit = extra.get("monthly_limit", 0) or 0
                stats.extra_usage_current = used / 100.0
                stats.extra_usage_limit = limit / 100.0
                stats.extra_usage_pct = extra.get("utilization", 0.0)

        # Calculate pace
        now = datetime.now(timezone.utc)
        if stats.weekly_reset_time:
            # Calculate expected usage based on time through the week
            week_start = stats.weekly_reset_time - timedelta(days=7)
            total_seconds = (stats.weekly_reset_time - week_start).total_seconds()
            elapsed_seconds = (now - week_start).total_seconds()
            expected_pct = (elapsed_seconds / total_seconds) * 100
            stats.pace_percentage = stats.weekly_used_pct - expected_pct

            if stats.pace_percentage < 0:
                stats.pace_status = f"Behind ({stats.pace_percentage:.0f}%)"
            elif stats.pace_percentage > 0:
                stats.pace_status = f"Ahead (+{stats.pace_percentage:.0f}%)"
            else:
                stats.pace_status = "On track"

        # Load local cost stats
        local_stats = self._load_local_cost_stats()
        stats.cost_today = local_stats["cost_today"]
        stats.cost_today_tokens = local_stats["cost_today_tokens"]
        stats.cost_30_days = local_stats["cost_30_days"]
        stats.cost_30_days_tokens = local_stats["cost_30_days_tokens"]
        stats.total_messages = local_stats["total_messages"]
        stats.total_sessions = local_stats["total_sessions"]

        return stats


# ============================================================================
# Codex (OpenAI) OAuth Data Collector
# ============================================================================

class CodexOAuthFetcher:
    """Fetches real subscription usage from Codex/ChatGPT OAuth API"""

    # OAuth endpoints
    REFRESH_ENDPOINT = "https://auth.openai.com/oauth/token"
    CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"

    # Plan type mappings
    PLAN_DISPLAY_NAMES = {
        "guest": "Guest",
        "free": "Free",
        "go": "Go",
        "plus": "Plus",
        "pro": "Pro",
        "free_workspace": "Free Workspace",
        "team": "Team",
        "business": "Business",
        "education": "Education",
        "quorum": "Quorum",
        "k12": "K12",
        "enterprise": "Enterprise",
        "edu": "Edu",
    }

    def __init__(self):
        self.auth_file = CODEX_DIR / "auth.json"
        self.config_file = CODEX_DIR / "config.toml"
        self.sessions_dir = CODEX_DIR / "sessions"

    def _load_credentials(self) -> Optional[dict]:
        """Load OAuth credentials from ~/.codex/auth.json"""
        if not self.auth_file.exists():
            return None
        try:
            with open(self.auth_file, 'r') as f:
                data = json.load(f)

            # Check for API key auth
            api_key = data.get("OPENAI_API_KEY") or ""
            if isinstance(api_key, str):
                api_key = api_key.strip()
            if api_key:
                return {
                    "access_token": api_key,
                    "refresh_token": "",
                    "account_id": None,
                    "last_refresh": None,
                }

            # OAuth tokens
            tokens = data.get("tokens", {})
            access_token = (tokens.get("access_token") or "").strip()
            refresh_token = (tokens.get("refresh_token") or "").strip()

            if not access_token:
                return None

            # Parse last_refresh timestamp
            last_refresh = None
            last_refresh_str = data.get("last_refresh", "")
            if last_refresh_str:
                try:
                    last_refresh = datetime.fromisoformat(last_refresh_str.replace('Z', '+00:00'))
                except:
                    pass

            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": tokens.get("id_token"),
                "account_id": tokens.get("account_id"),
                "last_refresh": last_refresh,
            }
        except Exception as e:
            print(f"Error loading Codex credentials: {e}")
            return None

    def _needs_refresh(self, creds: dict) -> bool:
        """Check if token needs refresh (8-day threshold like CodexBar)"""
        if not creds.get("refresh_token"):
            return False
        last_refresh = creds.get("last_refresh")
        if not last_refresh:
            return True
        eight_days = timedelta(days=8)
        if isinstance(last_refresh, datetime):
            return datetime.now(timezone.utc) - last_refresh.replace(tzinfo=timezone.utc) > eight_days
        return True

    def _refresh_token(self, creds: dict) -> Optional[dict]:
        """Refresh the OAuth token"""
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            return creds

        try:
            body = json.dumps({
                "client_id": self.CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "openid profile email",
            }).encode('utf-8')

            req = urllib.request.Request(
                self.REFRESH_ENDPOINT,
                data=body,
                headers={
                    "Content-Type": "application/json",
                },
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    new_creds = {
                        "access_token": data.get("access_token", creds["access_token"]),
                        "refresh_token": data.get("refresh_token", creds["refresh_token"]),
                        "id_token": data.get("id_token", creds.get("id_token")),
                        "account_id": creds.get("account_id"),
                        "last_refresh": datetime.now(timezone.utc),
                    }
                    self._save_credentials(new_creds)
                    return new_creds

        except urllib.error.HTTPError as e:
            print(f"Codex token refresh HTTP error: {e.code}")
        except Exception as e:
            print(f"Codex token refresh error: {e}")

        return None

    def _save_credentials(self, creds: dict):
        """Save refreshed credentials back to auth.json"""
        try:
            existing = {}
            if self.auth_file.exists():
                with open(self.auth_file, 'r') as f:
                    existing = json.load(f)

            tokens = {
                "access_token": creds["access_token"],
                "refresh_token": creds["refresh_token"],
            }
            if creds.get("id_token"):
                tokens["id_token"] = creds["id_token"]
            if creds.get("account_id"):
                tokens["account_id"] = creds["account_id"]

            existing["tokens"] = tokens
            existing["last_refresh"] = datetime.now(timezone.utc).isoformat()

            self.auth_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.auth_file, 'w') as f:
                json.dump(existing, f, indent=2, sort_keys=True)
        except Exception as e:
            print(f"Error saving Codex credentials: {e}")

    def _resolve_base_url(self) -> str:
        """Resolve the ChatGPT base URL from config or use default"""
        base_url = self.DEFAULT_BASE_URL

        # Try to read from config.toml
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    for line in f:
                        line = line.split('#')[0].strip()
                        if not line or '=' not in line:
                            continue
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key == "chatgpt_base_url" and value:
                            base_url = value
                            break
            except:
                pass

        # Normalize URL
        base_url = base_url.rstrip('/')
        if base_url.startswith("https://chatgpt.com") or base_url.startswith("https://chat.openai.com"):
            if "/backend-api" not in base_url:
                base_url += "/backend-api"

        return base_url

    def _fetch_usage_api(self, access_token: str, account_id: Optional[str]) -> Optional[dict]:
        """Fetch usage data from Codex/ChatGPT OAuth API"""
        base_url = self._resolve_base_url()
        usage_path = "/wham/usage" if "/backend-api" in base_url else "/api/codex/usage"
        usage_url = base_url + usage_path

        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "AIUsageMonitor/2.1",
                "Accept": "application/json",
            }
            if account_id:
                headers["ChatGPT-Account-Id"] = account_id

            req = urllib.request.Request(usage_url, headers=headers)

            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    return json.loads(response.read().decode('utf-8'))

        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print(f"Codex API unauthorized: {e.code}")
            else:
                print(f"Codex API HTTP error: {e.code}")
        except Exception as e:
            print(f"Codex API error: {e}")

        return None

    def collect(self) -> ProviderStats:
        """Collect Codex usage from OAuth API"""
        stats = ProviderStats(
            provider_id="codex",
            provider_name="Codex",
        )

        # Load credentials
        creds = self._load_credentials()
        if not creds:
            stats.error_message = "Not logged in. Run 'codex' to authenticate."
            return stats

        access_token = creds.get("access_token")
        if not access_token:
            stats.error_message = "No access token found. Run 'codex' to authenticate."
            return stats

        # Refresh token if needed
        if self._needs_refresh(creds):
            refreshed = self._refresh_token(creds)
            if refreshed:
                creds = refreshed
                access_token = creds["access_token"]

        # Fetch usage data
        usage_data = self._fetch_usage_api(access_token, creds.get("account_id"))
        if not usage_data:
            stats.error_message = "Failed to fetch usage data from API."
            stats.is_connected = True  # We have credentials, just API failed
            return stats

        stats.is_connected = True
        stats.last_update = datetime.now()

        # Parse plan type
        plan_type = usage_data.get("plan_type", "unknown")
        stats.plan_name = self.PLAN_DISPLAY_NAMES.get(plan_type, plan_type.replace("_", " ").title())

        # Parse rate limits
        rate_limit = usage_data.get("rate_limit", {})

        # Primary window (typically shorter, like 3-hour session)
        primary = rate_limit.get("primary_window", {})
        if primary:
            stats.session_used_pct = float(primary.get("used_percent", 0))
            reset_at = primary.get("reset_at")
            if reset_at:
                try:
                    stats.session_reset_time = datetime.fromtimestamp(reset_at, tz=timezone.utc)
                except:
                    pass

        # Secondary window (typically longer, like daily/weekly)
        secondary = rate_limit.get("secondary_window", {})
        if secondary:
            stats.weekly_used_pct = float(secondary.get("used_percent", 0))
            reset_at = secondary.get("reset_at")
            if reset_at:
                try:
                    stats.weekly_reset_time = datetime.fromtimestamp(reset_at, tz=timezone.utc)
                except:
                    pass

        # Parse credits
        credits_data = usage_data.get("credits", {})
        if credits_data:
            has_credits = credits_data.get("has_credits", False)
            unlimited = credits_data.get("unlimited", False)
            balance = credits_data.get("balance")

            if unlimited:
                stats.extra_usage_enabled = True
                stats.extra_usage_limit = float('inf')
            elif has_credits and balance is not None:
                stats.extra_usage_enabled = True
                try:
                    stats.extra_usage_current = float(balance) if isinstance(balance, (int, float, str)) else 0
                except:
                    stats.extra_usage_current = 0

        # Calculate pace for secondary window
        if stats.weekly_reset_time and stats.weekly_used_pct > 0:
            now = datetime.now(timezone.utc)
            # Assume 7-day window if we have secondary window data
            secondary_window_seconds = secondary.get("limit_window_seconds", 7 * 24 * 3600)
            window_duration = timedelta(seconds=secondary_window_seconds)
            window_start = stats.weekly_reset_time - window_duration
            total_seconds = window_duration.total_seconds()
            elapsed_seconds = (now - window_start).total_seconds()
            expected_pct = (elapsed_seconds / total_seconds) * 100

            stats.pace_percentage = stats.weekly_used_pct - expected_pct
            if stats.pace_percentage < 0:
                stats.pace_status = f"Behind ({stats.pace_percentage:.0f}%)"
            elif stats.pace_percentage > 0:
                stats.pace_status = f"Ahead (+{stats.pace_percentage:.0f}%)"
            else:
                stats.pace_status = "On track"

        # Load token stats from local session files
        local_stats = self._load_local_cost_stats()
        stats.cost_today_tokens = local_stats["cost_today_tokens"]
        stats.cost_30_days_tokens = local_stats["cost_30_days_tokens"]

        return stats

    def _load_local_cost_stats(self) -> dict:
        """Load token stats from Codex CLI session files.

        Session files at ~/.codex/sessions/YYYY/MM/DD/*.jsonl contain
        token_count events with cumulative total_token_usage per session.
        """
        result = {
            "cost_today": 0.0,
            "cost_today_tokens": 0,
            "cost_30_days": 0.0,
            "cost_30_days_tokens": 0,
        }

        if not self.sessions_dir.exists():
            return result

        try:
            today = datetime.now().date()
            thirty_days_ago = today - timedelta(days=30)

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
                            tokens = self._get_session_tokens(session_file)
                            if dir_date == today:
                                result["cost_today_tokens"] += tokens
                            result["cost_30_days_tokens"] += tokens
        except Exception:
            pass

        return result

    def _get_session_tokens(self, session_file) -> int:
        """Extract output tokens from the last token_count event in a session file.

        Uses output_tokens only to match Claude's metric (model-generated tokens).
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


# ============================================================================
# Icon Generator
# ============================================================================

# Path to bundled SVG icons
APP_ICON_SVG = Path(__file__).parent / "icons" / "app-icon.svg"
TRAY_ICON_SVG = Path(__file__).parent / "icons" / "tray-icon.svg"


class IconGenerator:
    """Generates modern application icons for AI Usage Monitor"""

    def __init__(self):
        ICON_DIR.mkdir(parents=True, exist_ok=True)

    def _rounded_rect(self, ctx, x, y, width, height, radius):
        """Draw a rounded rectangle path"""
        ctx.new_path()
        ctx.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
        ctx.arc(x + width - radius, y + radius, radius, 1.5 * math.pi, 2 * math.pi)
        ctx.arc(x + width - radius, y + height - radius, radius, 0, 0.5 * math.pi)
        ctx.arc(x + radius, y + height - radius, radius, 0.5 * math.pi, math.pi)
        ctx.close_path()

    def create_app_icon(self, size: int = 64) -> str:
        """Create modern app icon - uses bundled SVG or generates PNG fallback"""
        # Try to use the bundled SVG icon first
        if APP_ICON_SVG.exists():
            try:
                # Convert SVG to PNG at requested size using GdkPixbuf
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(APP_ICON_SVG), size, size, True
                )
                icon_path = str(ICON_DIR / f"app-icon-{size}.png")
                pixbuf.savev(icon_path, "png", [], [])
                return icon_path
            except Exception as e:
                print(f"Could not load SVG icon: {e}")

        # Fallback: Generate PNG with Cairo
        return self._generate_app_icon_cairo(size)

    def _generate_app_icon_cairo(self, size: int = 64) -> str:
        """Generate KDE-style monochrome app icon using Cairo (fallback) - Robot face"""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surface)

        ctx.set_source_rgba(1, 1, 1, 0.95)
        line_width = size * 0.06

        # Antenna
        ctx.set_line_width(line_width)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.move_to(size / 2, size * 0.03)
        ctx.line_to(size / 2, size * 0.19)
        ctx.stroke()
        ctx.arc(size / 2, size * 0.03, size * 0.045, 0, 2 * math.pi)
        ctx.fill()

        # Head (rounded rectangle)
        head_x, head_y = size * 0.125, size * 0.22
        head_w, head_h = size * 0.75, size * 0.69
        head_r = size * 0.16
        self._rounded_rect(ctx, head_x, head_y, head_w, head_h, head_r)
        ctx.set_line_width(line_width)
        ctx.stroke()

        # Eyes
        eye_w, eye_h = size * 0.19, size * 0.16
        eye_r = size * 0.05
        self._rounded_rect(ctx, size * 0.23, size * 0.375, eye_w, eye_h, eye_r)
        ctx.fill()
        self._rounded_rect(ctx, size * 0.58, size * 0.375, eye_w, eye_h, eye_r)
        ctx.fill()

        # Mouth
        ctx.set_line_width(line_width)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.move_to(size * 0.31, size * 0.72)
        ctx.line_to(size * 0.69, size * 0.72)
        ctx.stroke()

        icon_path = str(ICON_DIR / f"app-icon-{size}.png")
        surface.write_to_png(icon_path)
        return icon_path

    def create_tray_icon(self, size: int = 22) -> str:
        """Create static KDE-style monochrome tray icon - Robot face"""
        icon_path = str(ICON_DIR / "tray-robot.png")

        # Try to use bundled SVG first
        if TRAY_ICON_SVG.exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(TRAY_ICON_SVG), size, size, True
                )
                pixbuf.savev(icon_path, "png", [], [])
                print(f"Loaded tray icon from SVG: {TRAY_ICON_SVG}")
                return icon_path
            except Exception as e:
                print(f"Failed to load SVG tray icon: {e}")

        # Fallback: Generate with Cairo - Robot face
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surface)

        ctx.set_source_rgba(1, 1, 1, 0.95)
        line_width = size * 0.07

        # Antenna
        ctx.set_line_width(line_width)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.move_to(size / 2, size * 0.045)
        ctx.line_to(size / 2, size * 0.18)
        ctx.stroke()
        ctx.arc(size / 2, size * 0.045, size * 0.045, 0, 2 * math.pi)
        ctx.fill()

        # Head
        head_x, head_y = size * 0.14, size * 0.23
        head_w, head_h = size * 0.72, size * 0.64
        head_r = size * 0.14
        self._rounded_rect(ctx, head_x, head_y, head_w, head_h, head_r)
        ctx.set_line_width(line_width)
        ctx.stroke()

        # Eyes
        eye_w, eye_h = size * 0.18, size * 0.14
        eye_r = size * 0.045
        self._rounded_rect(ctx, size * 0.25, size * 0.36, eye_w, eye_h, eye_r)
        ctx.fill()
        self._rounded_rect(ctx, size * 0.57, size * 0.36, eye_w, eye_h, eye_r)
        ctx.fill()

        # Mouth
        ctx.move_to(size * 0.32, size * 0.68)
        ctx.line_to(size * 0.68, size * 0.68)
        ctx.stroke()

        surface.write_to_png(icon_path)
        return icon_path


# ============================================================================
# Theme System
# ============================================================================

class Theme:
    """Color theme for the UI"""

    LIGHT = {
        "name": "light",
        "bg": "#f5f5f7",
        "bg_secondary": "#e8e8ec",
        "bg_hover": "#dcdce0",
        "fg": "#1d1d1f",
        "fg_secondary": "#6e6e73",
        "fg_muted": "#86868b",
        "accent": "#0071e3",
        "success": "#34c759",
        "warning": "#ff9500",
        "error": "#ff3b30",
        "border": "#d2d2d7",
        "progress_bg": "#d2d2d7",
        "progress_green": "#34c759",
        "progress_yellow": "#ff9500",
        "progress_red": "#ff3b30",
        "tab_active_bg": "rgba(0, 113, 227, 0.9)",
        "tab_active_fg": "#ffffff",
        "tab_inactive_fg": "#6e6e73",
    }

    DARK = {
        "name": "dark",
        "bg": "#1e1e1e",
        "bg_secondary": "#2d2d2d",
        "bg_hover": "#3d3d3d",
        "fg": "#ffffff",
        "fg_secondary": "#b3b3b3",
        "fg_muted": "#808080",
        "accent": "#0a84ff",
        "success": "#30d158",
        "warning": "#ff9f0a",
        "error": "#ff453a",
        "border": "#3d3d3d",
        "progress_bg": "#3d3d3d",
        "progress_green": "#30d158",
        "progress_yellow": "#ff9f0a",
        "progress_red": "#ff453a",
        "tab_active_bg": "rgba(10, 132, 255, 0.9)",
        "tab_active_fg": "#ffffff",
        "tab_inactive_fg": "#808080",
    }

    @classmethod
    def get(cls, theme_name: str) -> dict:
        return cls.DARK if theme_name == "dark" else cls.LIGHT


class ThemeManager:
    """Manages theme preferences"""

    def __init__(self):
        self.config_file = CONFIG_DIR / "settings.json"
        self._theme = "dark"  # Default to dark
        self._load()

    def _load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                    self._theme = data.get("theme", "dark")
        except:
            pass

    def save(self):
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump({"theme": self._theme}, f)
        except:
            pass

    @property
    def theme(self) -> str:
        return self._theme

    @theme.setter
    def theme(self, value: str):
        self._theme = value
        self.save()

    @property
    def colors(self) -> dict:
        return Theme.get(self._theme)


# Global theme manager
theme_manager = ThemeManager()


class SettingsDialog(Gtk.Dialog):
    """Settings dialog for theme selection"""

    def __init__(self, parent):
        super().__init__(title="Settings", transient_for=parent, modal=True)
        self.set_default_size(300, 150)

        colors = theme_manager.colors

        # Apply theme to dialog
        self.get_style_context().add_class("settings-dialog")

        content = self.get_content_area()
        content.set_spacing(16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(20)
        content.set_margin_end(20)

        # Theme section
        theme_label = Gtk.Label(label="Theme")
        theme_label.set_halign(Gtk.Align.START)
        theme_label.get_style_context().add_class("settings-label")
        content.pack_start(theme_label, False, False, 0)

        theme_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        self.light_btn = Gtk.RadioButton.new_with_label(None, "Light")
        self.dark_btn = Gtk.RadioButton.new_with_label_from_widget(self.light_btn, "Dark")

        if theme_manager.theme == "dark":
            self.dark_btn.set_active(True)
        else:
            self.light_btn.set_active(True)

        theme_box.pack_start(self.light_btn, False, False, 0)
        theme_box.pack_start(self.dark_btn, False, False, 0)
        content.pack_start(theme_box, False, False, 0)

        # Buttons
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Apply", Gtk.ResponseType.APPLY)

        self.show_all()

    def get_selected_theme(self) -> str:
        return "dark" if self.dark_btn.get_active() else "light"


class AIUsageMonitorWindow(Gtk.Window):
    """Main application window with tabbed interface and theme support"""

    def __init__(self, providers: Dict[str, ProviderStats], on_refresh: callable, on_quit: callable,
                 on_theme_changed: callable = None):
        super().__init__(title=APP_NAME)
        self.providers = providers
        self.on_refresh = on_refresh
        self.on_quit = on_quit
        self.on_theme_changed = on_theme_changed
        self.current_provider = "claude"

        self.set_default_size(360, 580)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        self._apply_css()
        self._create_content()
        self._position_window()

        self.connect("draw", self._on_draw)
        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-out-event", self._on_focus_out)

    def _on_focus_out(self, widget, event):
        """Hide and destroy window on focus loss"""
        self.hide()
        self.destroy()
        return False

    def _get_css(self) -> bytes:
        """Generate CSS based on current theme"""
        c = theme_manager.colors
        return f"""
        * {{
            font-family: "Noto Sans", "Segoe UI", "Ubuntu", sans-serif;
        }}

        .main-window {{
            background-color: transparent;
        }}

        .tab-bar {{
            padding: 10px 12px 6px 12px;
        }}

        .tab-button {{
            background: transparent;
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            margin: 0 3px;
            color: {c["fg_secondary"]};
            font-size: 11px;
            font-weight: 500;
        }}
        .tab-button:hover {{
            background-color: {c["bg_hover"]};
            color: {c["fg"]};
        }}
        .tab-button.active {{
            background-color: {c["tab_active_bg"]};
            color: {c["tab_active_fg"]};
        }}

        .content-area {{
            padding: 12px 18px;
        }}

        .provider-header {{
            font-size: 20px;
            font-weight: 600;
            color: {c["fg"]};
        }}
        .update-time {{
            font-size: 12px;
            color: {c["fg_muted"]};
        }}
        .plan-badge {{
            font-size: 12px;
            color: {c["fg_secondary"]};
            font-weight: 500;
            background-color: {c["bg_secondary"]};
            padding: 4px 10px;
            border-radius: 12px;
        }}

        .section-title {{
            font-size: 13px;
            font-weight: 600;
            color: {c["fg"]};
            margin-top: 14px;
        }}

        .stat-label {{
            font-size: 12px;
            color: {c["fg_muted"]};
        }}
        .stat-value {{
            font-size: 12px;
            color: {c["fg_secondary"]};
            font-weight: 500;
        }}

        .pace-text {{
            font-size: 11px;
            color: {c["fg_muted"]};
            margin-top: 2px;
        }}

        .separator {{
            background-color: {c["border"]};
            min-height: 1px;
            margin: 14px 0;
        }}

        .cost-title {{
            font-size: 13px;
            font-weight: 600;
            color: {c["fg"]};
        }}
        .cost-detail {{
            font-size: 12px;
            color: {c["fg_secondary"]};
            margin: 3px 0;
        }}

        .menu-item {{
            padding: 10px 6px;
            border-radius: 8px;
            background: transparent;
            border: none;
        }}
        .menu-item:hover {{
            background-color: {c["bg_hover"]};
        }}
        .menu-item-text {{
            font-size: 13px;
            color: {c["fg"]};
        }}

        .menu-separator {{
            background-color: {c["border"]};
            min-height: 1px;
            margin: 10px 0;
        }}

        .footer-item {{
            padding: 8px 6px;
            background: transparent;
            border: none;
            border-radius: 6px;
        }}
        .footer-item:hover {{
            background-color: {c["bg_hover"]};
        }}
        .footer-text {{
            font-size: 12px;
            color: {c["fg_muted"]};
        }}

        progressbar trough {{
            background-color: {c["progress_bg"]};
            border-radius: 3px;
            min-height: 6px;
        }}
        progressbar progress {{
            border-radius: 3px;
            min-height: 6px;
            background-color: {c["progress_green"]};
        }}
        progressbar.warning progress {{
            background-color: {c["progress_yellow"]};
        }}
        progressbar.error progress {{
            background-color: {c["progress_red"]};
        }}

        .error-text {{
            color: {c["fg_muted"]};
            font-size: 13px;
        }}

        .settings-dialog {{
            background-color: {c["bg"]};
        }}
        .settings-label {{
            font-size: 13px;
            font-weight: 500;
            color: {c["fg"]};
        }}
        """.encode('utf-8')

    def _apply_css(self):
        self.style_provider = Gtk.CssProvider()
        self.style_provider.load_from_data(self._get_css())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self.style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _on_draw(self, widget, ctx):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        c = theme_manager.colors

        # Draw rounded rectangle with shadow effect
        radius = 12

        # Shadow (subtle)
        ctx.set_source_rgba(0, 0, 0, 0.3)
        ctx.new_path()
        ctx.arc(radius + 2, radius + 2, radius, math.pi, 1.5 * math.pi)
        ctx.arc(width - radius + 2, radius + 2, radius, 1.5 * math.pi, 2 * math.pi)
        ctx.arc(width - radius + 2, height - radius + 2, radius, 0, 0.5 * math.pi)
        ctx.arc(radius + 2, height - radius + 2, radius, 0.5 * math.pi, math.pi)
        ctx.close_path()
        ctx.fill()

        # Main background
        ctx.new_path()
        ctx.arc(radius, radius, radius, math.pi, 1.5 * math.pi)
        ctx.arc(width - radius, radius, radius, 1.5 * math.pi, 2 * math.pi)
        ctx.arc(width - radius, height - radius, radius, 0, 0.5 * math.pi)
        ctx.arc(radius, height - radius, radius, 0.5 * math.pi, math.pi)
        ctx.close_path()
        ctx.clip()

        # Parse background color
        bg = c["bg"].lstrip('#')
        r, g, b = int(bg[0:2], 16)/255, int(bg[2:4], 16)/255, int(bg[4:6], 16)/255
        ctx.set_source_rgba(r, g, b, 0.98)
        ctx.paint()

        # Subtle border
        border = c["border"].lstrip('#')
        br, bg_c, bb = int(border[0:2], 16)/255, int(border[2:4], 16)/255, int(border[4:6], 16)/255
        ctx.set_source_rgba(br, bg_c, bb, 0.5)
        ctx.set_line_width(1)
        ctx.new_path()
        ctx.arc(radius, radius, radius, math.pi, 1.5 * math.pi)
        ctx.arc(width - radius, radius, radius, 1.5 * math.pi, 2 * math.pi)
        ctx.arc(width - radius, height - radius, radius, 0, 0.5 * math.pi)
        ctx.arc(radius, height - radius, radius, 0.5 * math.pi, math.pi)
        ctx.close_path()
        ctx.stroke()

        return False

    def _create_content(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.get_style_context().add_class("main-window")

        main_box.pack_start(self._create_tab_bar(), False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(400)

        self.content_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._populate_content()
        scroll.add(self.content_container)
        main_box.pack_start(scroll, True, True, 0)

        main_box.pack_start(self._create_footer(), False, False, 0)

        self.add(main_box)

    def _create_tab_bar(self) -> Gtk.Box:
        tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tab_bar.get_style_context().add_class("tab-bar")
        tab_bar.set_halign(Gtk.Align.CENTER)

        provider_icons = {
            "claude": ("◐", "Claude"),
            "codex": ("◑", "Codex"),
        }

        self.tab_buttons = {}

        for provider_id, (icon, name) in provider_icons.items():
            tab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("tab-button")

            btn_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            icon_label = Gtk.Label(label=icon)
            btn_content.pack_start(icon_label, False, False, 0)

            name_label = Gtk.Label(label=name)
            btn_content.pack_start(name_label, False, False, 0)

            btn.add(btn_content)

            if provider_id == self.current_provider:
                btn.get_style_context().add_class("active")

            btn.connect("clicked", self._on_tab_clicked, provider_id)
            self.tab_buttons[provider_id] = btn

            tab_box.pack_start(btn, False, False, 0)

            # Status indicator line
            indicator = Gtk.DrawingArea()
            indicator.set_size_request(60, 3)

            if provider_id in self.providers and self.providers[provider_id].is_connected:
                pct = self.providers[provider_id].session_used_pct
                c = theme_manager.colors
                if pct < 50:
                    color = self._parse_color(c["progress_green"])
                elif pct < 80:
                    color = self._parse_color(c["progress_yellow"])
                else:
                    color = self._parse_color(c["progress_red"])
            else:
                color = self._parse_color(theme_manager.colors["fg_muted"])

            indicator.connect("draw", lambda w, ctx, col=color: self._draw_indicator(w, ctx, col))
            tab_box.pack_start(indicator, False, False, 0)

            tab_bar.pack_start(tab_box, False, False, 0)

        return tab_bar

    def _parse_color(self, color_str: str) -> tuple:
        """Parse hex color to RGB tuple"""
        color = color_str.lstrip('#')
        return (int(color[0:2], 16)/255, int(color[2:4], 16)/255, int(color[4:6], 16)/255)

    def _draw_indicator(self, widget, ctx, color):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        ctx.set_source_rgb(*color)
        # Rounded indicator
        radius = height / 2
        ctx.arc(radius, radius, radius, 0.5 * math.pi, 1.5 * math.pi)
        ctx.arc(width - radius, radius, radius, 1.5 * math.pi, 0.5 * math.pi)
        ctx.close_path()
        ctx.fill()

    def _on_tab_clicked(self, button, provider_id):
        for pid, btn in self.tab_buttons.items():
            ctx = btn.get_style_context()
            if pid == provider_id:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

        self.current_provider = provider_id
        self._populate_content()

    def _populate_content(self):
        for child in self.content_container.get_children():
            self.content_container.remove(child)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.get_style_context().add_class("content-area")

        stats = self.providers.get(self.current_provider)

        if stats is None or not stats.is_connected:
            content.pack_start(self._create_not_connected(stats), False, False, 0)
        else:
            content.pack_start(self._create_provider_header(stats), False, False, 0)

            sep = Gtk.Separator()
            sep.get_style_context().add_class("separator")
            content.pack_start(sep, False, False, 0)

            # Session usage
            content.pack_start(self._create_usage_section(
                "Session",
                stats.session_used_pct,
                stats.session_reset_time,
            ), False, False, 0)

            # Weekly usage
            content.pack_start(self._create_usage_section(
                "Weekly",
                stats.weekly_used_pct,
                stats.weekly_reset_time,
                pace_text=f"Pace: {stats.pace_status}" if stats.pace_status != "On track" else None
            ), False, False, 0)

            # Model-specific quotas
            for model_name, usage_pct in stats.model_usage.items():
                content.pack_start(self._create_model_section(model_name, usage_pct), False, False, 0)

            # Extra usage
            if stats.extra_usage_enabled:
                content.pack_start(self._create_extra_usage_section(stats), False, False, 0)

            # Cost tracking
            if stats.cost_30_days > 0 or stats.cost_today > 0:
                content.pack_start(self._create_cost_section(stats), False, False, 0)

        sep2 = Gtk.Separator()
        sep2.get_style_context().add_class("menu-separator")
        content.pack_start(sep2, False, False, 8)

        content.pack_start(self._create_menu_items(), False, False, 0)

        self.content_container.pack_start(content, False, False, 0)
        self.content_container.show_all()

    def _create_not_connected(self, stats: Optional[ProviderStats]) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(30)
        box.set_margin_bottom(30)

        title = Gtk.Label()
        c = theme_manager.colors
        title.set_markup(f"<span size='large' weight='bold' foreground='{c['fg']}'>{self.current_provider.title()}</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 0)

        error_msg = stats.error_message if stats else f"{self.current_provider.title()} not configured"
        msg = Gtk.Label(label=error_msg)
        msg.get_style_context().add_class("error-text")
        msg.set_halign(Gtk.Align.START)
        msg.set_line_wrap(True)
        box.pack_start(msg, False, False, 8)

        return box

    def _create_provider_header(self, stats: ProviderStats) -> Gtk.Box:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        name = Gtk.Label()
        name.set_markup(f"<span size='large' weight='bold'>{stats.provider_name}</span>")
        name.get_style_context().add_class("provider-header")
        name.set_halign(Gtk.Align.START)
        left.pack_start(name, False, False, 0)

        if stats.last_update:
            delta = datetime.now() - stats.last_update
            if delta.seconds < 60:
                update_text = "Updated just now"
            elif delta.seconds < 3600:
                update_text = f"Updated {delta.seconds // 60}m ago"
            else:
                update_text = f"Updated {delta.seconds // 3600}h ago"
        else:
            update_text = "Not updated"

        update = Gtk.Label(label=update_text)
        update.get_style_context().add_class("update-time")
        update.set_halign(Gtk.Align.START)
        left.pack_start(update, False, False, 0)

        header.pack_start(left, True, True, 0)

        plan = Gtk.Label(label=stats.plan_name)
        plan.get_style_context().add_class("plan-badge")
        plan.set_valign(Gtk.Align.START)
        header.pack_end(plan, False, False, 0)

        return header

    def _create_usage_section(self, title: str, percentage: float,
                              reset_time: Optional[datetime], pace_text: str = None) -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        section.set_margin_top(14)

        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class("section-title")
        title_label.set_halign(Gtk.Align.START)
        section.pack_start(title_label, False, False, 0)

        progress = Gtk.ProgressBar()
        progress.set_fraction(min(1.0, percentage / 100))
        if percentage >= 80:
            progress.get_style_context().add_class("error")
        elif percentage >= 50:
            progress.get_style_context().add_class("warning")
        section.pack_start(progress, False, False, 4)

        stats_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        used_label = Gtk.Label(label=f"{percentage:.0f}% used")
        used_label.get_style_context().add_class("stat-label")
        stats_row.pack_start(used_label, True, True, 0)

        if reset_time:
            now = datetime.now(timezone.utc)
            if reset_time.tzinfo is None:
                reset_time = reset_time.replace(tzinfo=timezone.utc)
            delta = reset_time - now
            if delta.days > 0:
                reset_text = f"Resets in {delta.days}d {delta.seconds // 3600}h"
            elif delta.seconds >= 3600:
                reset_text = f"Resets in {delta.seconds // 3600}h {(delta.seconds % 3600) // 60}m"
            else:
                reset_text = f"Resets in {max(0, delta.seconds // 60)}m"

            reset_label = Gtk.Label(label=reset_text)
            reset_label.get_style_context().add_class("stat-value")
            stats_row.pack_end(reset_label, False, False, 0)

        section.pack_start(stats_row, False, False, 0)

        if pace_text:
            pace = Gtk.Label(label=pace_text)
            pace.get_style_context().add_class("pace-text")
            pace.set_halign(Gtk.Align.START)
            section.pack_start(pace, False, False, 0)

        return section

    def _create_model_section(self, model_name: str, usage_pct: float) -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        section.set_margin_top(14)

        title = Gtk.Label(label=model_name)
        title.get_style_context().add_class("section-title")
        title.set_halign(Gtk.Align.START)
        section.pack_start(title, False, False, 0)

        progress = Gtk.ProgressBar()
        progress.set_fraction(min(1.0, usage_pct / 100))
        if usage_pct >= 80:
            progress.get_style_context().add_class("error")
        elif usage_pct >= 50:
            progress.get_style_context().add_class("warning")
        section.pack_start(progress, False, False, 4)

        used = Gtk.Label(label=f"{usage_pct:.0f}% used")
        used.get_style_context().add_class("stat-label")
        used.set_halign(Gtk.Align.START)
        section.pack_start(used, False, False, 0)

        return section

    def _create_extra_usage_section(self, stats: ProviderStats) -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        section.set_margin_top(18)

        sep = Gtk.Separator()
        sep.get_style_context().add_class("separator")
        section.pack_start(sep, False, False, 0)

        title = Gtk.Label(label="Extra Usage")
        title.get_style_context().add_class("section-title")
        title.set_halign(Gtk.Align.START)
        title.set_margin_top(10)
        section.pack_start(title, False, False, 0)

        progress = Gtk.ProgressBar()
        progress.set_fraction(min(1.0, stats.extra_usage_pct / 100))
        section.pack_start(progress, False, False, 4)

        stats_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cost_text = f"${stats.extra_usage_current:.2f} / ${stats.extra_usage_limit:.2f}"
        cost_label = Gtk.Label(label=cost_text)
        cost_label.get_style_context().add_class("stat-label")
        stats_row.pack_start(cost_label, True, True, 0)

        pct_label = Gtk.Label(label=f"{stats.extra_usage_pct:.0f}% used")
        pct_label.get_style_context().add_class("stat-value")
        stats_row.pack_end(pct_label, False, False, 0)

        section.pack_start(stats_row, False, False, 0)
        return section

    def _create_cost_section(self, stats: ProviderStats) -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        section.set_margin_top(18)

        sep = Gtk.Separator()
        sep.get_style_context().add_class("separator")
        section.pack_start(sep, False, False, 0)

        title = Gtk.Label(label="Cost Tracking")
        title.get_style_context().add_class("cost-title")
        title.set_halign(Gtk.Align.START)
        title.set_margin_top(10)
        section.pack_start(title, False, False, 0)

        today_text = f"Today: ${stats.cost_today:.2f} · {self._format_tokens(stats.cost_today_tokens)} tokens"
        today = Gtk.Label(label=today_text)
        today.get_style_context().add_class("cost-detail")
        today.set_halign(Gtk.Align.START)
        section.pack_start(today, False, False, 0)

        month_text = f"30 days: ${stats.cost_30_days:.2f} · {self._format_tokens(stats.cost_30_days_tokens)} tokens"
        month = Gtk.Label(label=month_text)
        month.get_style_context().add_class("cost-detail")
        month.set_halign(Gtk.Align.START)
        section.pack_start(month, False, False, 0)

        return section

    def _create_menu_items(self) -> Gtk.Box:
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        items = [
            ("📊", "Usage Dashboard", self._on_usage_dashboard),
            ("⚡", "Status Page", self._on_status_page),
        ]

        for icon, label, callback in items:
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("menu-item")

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            icon_lbl = Gtk.Label(label=icon)
            box.pack_start(icon_lbl, False, False, 0)

            text_lbl = Gtk.Label(label=label)
            text_lbl.get_style_context().add_class("menu-item-text")
            text_lbl.set_halign(Gtk.Align.START)
            box.pack_start(text_lbl, True, True, 0)

            btn.add(box)
            btn.connect("clicked", callback)
            menu.pack_start(btn, False, False, 0)

        return menu

    def _create_footer(self) -> Gtk.Box:
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        footer.set_margin_start(18)
        footer.set_margin_end(18)
        footer.set_margin_bottom(14)

        sep = Gtk.Separator()
        sep.get_style_context().add_class("menu-separator")
        footer.pack_start(sep, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        items = [
            ("⚙ Settings", self._on_settings),
            ("Quit", lambda b: self.on_quit()),
        ]

        for label, callback in items:
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("footer-item")

            text = Gtk.Label(label=label)
            text.get_style_context().add_class("footer-text")
            btn.add(text)
            btn.connect("clicked", callback)
            btn_row.pack_start(btn, False, False, 0)

        footer.pack_start(btn_row, False, False, 0)

        return footer

    def _format_tokens(self, tokens: int) -> str:
        if tokens >= 1_000_000_000:
            return f"{tokens / 1_000_000_000:.1f}B"
        elif tokens >= 1_000_000:
            return f"{tokens / 1_000_000:.1f}M"
        elif tokens >= 1_000:
            return f"{tokens / 1_000:.0f}K"
        return str(tokens)

    def _position_window(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor()
        if monitor:
            geometry = monitor.get_geometry()
            scale = monitor.get_scale_factor()
            # Position near bottom-right, above the panel
            x = geometry.x + geometry.width - 380
            y = geometry.y + geometry.height - 620
            self.move(x, y)

    def _on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            self.destroy()
            return True
        return False

    def _on_settings(self, btn):
        dialog = SettingsDialog(self)
        response = dialog.run()

        if response == Gtk.ResponseType.APPLY:
            new_theme = dialog.get_selected_theme()
            if new_theme != theme_manager.theme:
                theme_manager.theme = new_theme
                if self.on_theme_changed:
                    self.on_theme_changed()

        dialog.destroy()

    def _on_usage_dashboard(self, btn):
        if self.current_provider == "claude":
            webbrowser.open("https://claude.ai/settings/usage")
        elif self.current_provider == "codex":
            webbrowser.open("https://platform.openai.com/usage")

    def _on_status_page(self, btn):
        if self.current_provider == "claude":
            webbrowser.open("https://status.anthropic.com/")
        elif self.current_provider == "codex":
            webbrowser.open("https://status.openai.com/")

    def update_providers(self, providers: Dict[str, ProviderStats]):
        self.providers = providers
        self._populate_content()


# ============================================================================
# Main Application
# ============================================================================

class AIUsageMonitorApp:
    def __init__(self):
        self.config = AppConfig()
        self.icon_gen = IconGenerator()
        self.collectors = {
            "claude": ClaudeOAuthFetcher(),
            "codex": CodexOAuthFetcher(),
        }
        self.providers: Dict[str, ProviderStats] = {}
        self.indicator = None
        self.window = None
        self.refresh_timeout_id = None

    def run(self):
        tray_icon_path = self.icon_gen.create_tray_icon()
        self.refresh_stats()

        if HAS_APPINDICATOR:
            self._create_indicator(tray_icon_path)
        else:
            self._create_status_icon(tray_icon_path)

        self._start_refresh_timer()
        Gtk.main()

    def _create_indicator(self, icon_path: str):
        self.indicator = AppIndicator3.Indicator.new(
            APP_ID, icon_path,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title(APP_NAME)
        self._update_indicator_menu()

    def _update_indicator_menu(self):
        menu = Gtk.Menu()

        for pid, stats in self.providers.items():
            if stats.is_connected:
                header = Gtk.MenuItem(label=f"━━ {stats.provider_name} ({stats.plan_name}) ━━")
                header.set_sensitive(False)
                menu.append(header)

                session = Gtk.MenuItem(label=f"  Session: {stats.session_used_pct:.0f}% used")
                session.set_sensitive(False)
                menu.append(session)

                weekly = Gtk.MenuItem(label=f"  Weekly: {stats.weekly_used_pct:.0f}% used")
                weekly.set_sensitive(False)
                menu.append(weekly)

                menu.append(Gtk.SeparatorMenuItem())

        show_item = Gtk.MenuItem(label="📊 Show Details...")
        show_item.connect("activate", lambda i: self._show_window())
        menu.append(show_item)

        refresh_item = Gtk.MenuItem(label="🔄 Refresh")
        refresh_item.connect("activate", lambda i: self.refresh_stats())
        menu.append(refresh_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda i: self._quit())
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

    def _create_status_icon(self, icon_path: str):
        self.status_icon = Gtk.StatusIcon()
        self.status_icon.set_from_file(icon_path)
        self.status_icon.set_tooltip_text(APP_NAME)
        self.status_icon.connect("activate", lambda i: self._show_window())
        self.status_icon.set_visible(True)

    def _show_window(self):
        # Always create a fresh window (old one is destroyed on focus-out)
        if self.window is not None:
            try:
                self.window.destroy()
            except:
                pass
            self.window = None

        self.window = AIUsageMonitorWindow(
            self.providers, self.refresh_stats, self._quit,
            on_theme_changed=self._on_theme_changed
        )
        self.window.connect("destroy", self._on_window_destroy)
        self.window.show_all()
        self.window.present()

    def _on_theme_changed(self):
        """Recreate window with new theme"""
        if self.window is not None:
            try:
                self.window.destroy()
            except:
                pass
            self.window = None
        self._show_window()

    def _on_window_destroy(self, widget):
        """Clear window reference when destroyed"""
        self.window = None

    def _start_refresh_timer(self):
        if self.refresh_timeout_id:
            GLib.source_remove(self.refresh_timeout_id)

        if self.config.refresh_interval > 0:
            self.refresh_timeout_id = GLib.timeout_add_seconds(
                self.config.refresh_interval,
                self._on_refresh_timeout
            )

    def _on_refresh_timeout(self) -> bool:
        self.refresh_stats()
        return True

    def refresh_stats(self):
        for provider_id, collector in self.collectors.items():
            self.providers[provider_id] = collector.collect()

        if self.indicator:
            self._update_indicator_menu()

        if self.window is not None:
            try:
                if self.window.is_visible():
                    self.window.update_providers(self.providers)
            except:
                self.window = None

    def _quit(self):
        if self.refresh_timeout_id:
            GLib.source_remove(self.refresh_timeout_id)
        Gtk.main_quit()


# ============================================================================
# CLI
# ============================================================================

def print_cli_status():
    collectors = {
        "claude": ClaudeOAuthFetcher(),
        "codex": CodexOAuthFetcher(),
    }

    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    DIM = "\033[2m"

    def get_bar(pct: float, width: int = 25) -> str:
        filled = int((pct / 100) * width)
        color = GREEN if pct < 50 else (YELLOW if pct < 80 else RED)
        return f"{color}{'█' * filled}{'░' * (width - filled)}{RESET}"

    def fmt_tokens(t: int) -> str:
        if t >= 1_000_000:
            return f"{t / 1_000_000:.0f}M"
        if t >= 1_000:
            return f"{t / 1_000:.0f}K"
        return str(t)

    print()
    print(f"{BOLD}{CYAN}╭{'─' * 50}╮{RESET}")
    print(f"{BOLD}{CYAN}│{'AI Usage Monitor v3.1 - Theme Support':^50}│{RESET}")
    print(f"{BOLD}{CYAN}╰{'─' * 50}╯{RESET}")

    for pid, collector in collectors.items():
        stats = collector.collect()
        print()
        print(f"{BOLD}━━━ {stats.provider_name} ━━━{RESET}")

        if not stats.is_connected:
            print(f"{DIM}  {stats.error_message}{RESET}")
            continue

        print(f"{DIM}  Plan: {stats.plan_name}  •  Updated: {stats.last_update.strftime('%H:%M') if stats.last_update else 'N/A'}{RESET}")
        print()
        print(f"  Session   {get_bar(stats.session_used_pct)} {stats.session_used_pct:5.1f}%")

        if stats.session_reset_time:
            now = datetime.now(timezone.utc)
            if stats.session_reset_time.tzinfo is None:
                stats.session_reset_time = stats.session_reset_time.replace(tzinfo=timezone.utc)
            delta = stats.session_reset_time - now
            hours = delta.seconds // 3600
            mins = (delta.seconds % 3600) // 60
            print(f"{DIM}            Resets in {hours}h {mins}m{RESET}")

        print(f"  Weekly    {get_bar(stats.weekly_used_pct)} {stats.weekly_used_pct:5.1f}%")

        if stats.weekly_reset_time:
            now = datetime.now(timezone.utc)
            if stats.weekly_reset_time.tzinfo is None:
                stats.weekly_reset_time = stats.weekly_reset_time.replace(tzinfo=timezone.utc)
            delta = stats.weekly_reset_time - now
            print(f"{DIM}            Resets in {delta.days}d {delta.seconds // 3600}h{RESET}")

        print(f"  {DIM}Pace: {stats.pace_status}{RESET}")
        print()
        print(f"  {CYAN}Cost (local){RESET}")
        print(f"  {DIM}Today: ${stats.cost_today:.2f} · {fmt_tokens(stats.cost_today_tokens)} tokens{RESET}")
        print(f"  {DIM}30 days: ${stats.cost_30_days:.2f} · {fmt_tokens(stats.cost_30_days_tokens)} tokens{RESET}")

    print()


def main():
    import argparse

    parser = argparse.ArgumentParser(description=f"{APP_NAME} - Track AI usage limits")
    parser.add_argument('-s', '--status', action='store_true', help='Print status to terminal')
    parser.add_argument('-v', '--version', action='version', version=f'{APP_NAME} {VERSION}')

    args = parser.parse_args()

    if args.status:
        print_cli_status()
        return 0

    app = AIUsageMonitorApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
