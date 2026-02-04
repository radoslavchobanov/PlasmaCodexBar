# PlasmaCodexBar


[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-green)](https://www.python.org/)
[![KDE Plasma 6](https://img.shields.io/badge/KDE_Plasma-6-blue)](https://kde.org/plasma-desktop/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

A native KDE Plasma 6 system tray widget for monitoring AI subscription usage. Inspired by [CodexBar](https://github.com/steipete/CodexBar) for macOS.

## Quick Install

```bash
git clone https://github.com/radoslavchobanov/plasmacodexbar.git
cd plasmacodexbar
./install-plasmoid.sh
```

That's it! The widget will appear in your system tray.

## Features

- **Native Plasma 6 Widget**: Seamless system tray integration
- **Multi-Provider Support**: Monitor Claude and Codex/ChatGPT usage
- **Real-time Tracking**:
  - Session usage (5-hour window)
  - Weekly usage with reset countdown
  - Per-model quotas (Sonnet, Opus, etc.)
  - Extra usage and cost estimation
- **Visual Progress Bars**: Color-coded (green → yellow → red)

## Requirements

- KDE Plasma 6
- Python 3.8+
- [Claude Code CLI](https://claude.ai/code) authenticated (for Claude monitoring)
- [Codex CLI](https://github.com/openai/codex) authenticated (for Codex monitoring)

## Alternative Installation

### From KDE Store

1. Right-click desktop → "Add Widgets" → "Get New Widgets" → "Download New Plasma Widgets"
2. Search for "PlasmaCodexBar"
3. Click "Install"

### From .plasmoid file

```bash
kpackagetool6 -t Plasma/Applet -i plasmacodexbar-1.0.0.plasmoid
```

## Usage

Click the robot icon in your system tray to view:
- Provider tabs (Claude / Codex) with logos
- Session and weekly usage bars
- Models section with per-model breakdown
- Extra usage and estimated costs
- Quick links to provider dashboards

## Configuration

If the widget doesn't appear automatically:

1. Right-click on the system tray
2. Select "Configure System Tray..."
3. Go to "Entries" tab
4. Find "PlasmaCodexBar" → set to "Always shown"

## Troubleshooting

**"Not connected" for Claude**
- Ensure Claude Code CLI is authenticated: run `claude` and complete login

**"Not connected" for Codex**
- Ensure Codex CLI is authenticated: check `~/.codex/auth.json` exists

**Widget not showing**
- Restart plasmashell: `kquitapp6 plasmashell && kstart plasmashell`

## Uninstall

```bash
kpackagetool6 -t Plasma/Applet -r org.kde.plasma.plasmacodexbar
```

## Credits

- Inspired by [CodexBar](https://github.com/steipete/CodexBar) by Peter Steinberger
- Uses Anthropic and OpenAI OAuth APIs

## License

MIT License - see [LICENSE](LICENSE) file for details.
