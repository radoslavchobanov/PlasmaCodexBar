/*
 * ConfigGeneral - Provider visibility settings
 */

import QtQuick 2.15
import QtQuick.Controls 2.5 as QQC2
import QtQuick.Layouts 1.15
import org.kde.kirigami 2.19 as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: configPage

    property string cfg_enabledProviders
    property int cfg_refreshInterval

    // Return true if the given provider id is in cfg_enabledProviders
    function isEnabled(providerId) {
        var raw = configPage.cfg_enabledProviders || ""
        var parts = raw.split(",")
        for (var i = 0; i < parts.length; i++) {
            if (parts[i].trim() === providerId) return true
        }
        return false
    }

    // Add or remove a provider id from cfg_enabledProviders while
    // preserving the existing order of the other entries.
    function setEnabled(providerId, enabled) {
        var raw = configPage.cfg_enabledProviders || ""
        var parts = raw.split(",").map(function(x) { return x.trim() }).filter(function(x) { return x.length > 0 })
        var idx = parts.indexOf(providerId)
        if (enabled && idx === -1) {
            parts.push(providerId)
        } else if (!enabled && idx !== -1) {
            parts.splice(idx, 1)
        }
        configPage.cfg_enabledProviders = parts.join(",")
    }

    Kirigami.FormLayout {
        anchors.left: parent.left
        anchors.right: parent.right

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18n("Visible providers")
        }

        QQC2.CheckBox {
            id: codexCheck
            Kirigami.FormData.label: i18n("Codex:")
            text: i18n("OpenAI Codex / ChatGPT CLI")
            checked: (("," + (configPage.cfg_enabledProviders || "") + ",").indexOf(",codex,") !== -1)
            onToggled: configPage.setEnabled("codex", checked)
        }

        QQC2.CheckBox {
            id: claudeCheck
            Kirigami.FormData.label: i18n("Claude:")
            text: i18n("Anthropic Claude Code CLI")
            checked: (("," + (configPage.cfg_enabledProviders || "") + ",").indexOf(",claude,") !== -1)
            onToggled: configPage.setEnabled("claude", checked)
        }

        QQC2.CheckBox {
            id: geminiCheck
            Kirigami.FormData.label: i18n("Gemini:")
            text: i18n("Google Gemini CLI")
            checked: (("," + (configPage.cfg_enabledProviders || "") + ",").indexOf(",gemini,") !== -1)
            onToggled: configPage.setEnabled("gemini", checked)
        }

        QQC2.CheckBox {
            id: copilotCheck
            Kirigami.FormData.label: i18n("Copilot:")
            text: i18n("GitHub Copilot")
            checked: (("," + (configPage.cfg_enabledProviders || "") + ",").indexOf(",copilot,") !== -1)
            onToggled: configPage.setEnabled("copilot", checked)
        }

        QQC2.CheckBox {
            id: cursorCheck
            Kirigami.FormData.label: i18n("Cursor:")
            text: i18n("Cursor AI Editor")
            checked: (("," + (configPage.cfg_enabledProviders || "") + ",").indexOf(",cursor,") !== -1)
            onToggled: configPage.setEnabled("cursor", checked)
        }

        QQC2.CheckBox {
            id: openrouterCheck
            Kirigami.FormData.label: i18n("OpenRouter:")
            text: i18n("OpenRouter API")
            checked: (("," + (configPage.cfg_enabledProviders || "") + ",").indexOf(",openrouter,") !== -1)
            onToggled: configPage.setEnabled("openrouter", checked)
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18n("Refresh")
        }

        QQC2.SpinBox {
            Kirigami.FormData.label: i18n("Interval (seconds):")
            from: 30
            to: 3600
            stepSize: 30
            value: configPage.cfg_refreshInterval
            onValueModified: configPage.cfg_refreshInterval = value
        }
    }
}
