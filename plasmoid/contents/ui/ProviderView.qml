/*
 * ProviderView - Displays usage data for a single AI provider
 */

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root

    property var providerData: null
    property string providerName: ""
    property string providerIcon: ""
    property string dashboardUrl: ""
    property string statusUrl: ""

    spacing: Kirigami.Units.largeSpacing

    function hasLocalUsageData() {
        if (!providerData) return false
        return ((providerData.cost_today_output_tokens || 0) > 0)
            || ((providerData.cost_30_days_output_tokens || 0) > 0)
            || ((providerData.cost_30_days || 0) > 0)
    }

    // Not connected state
    ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: !providerData || (!providerData.is_connected && !root.hasLocalUsageData())
        spacing: Kirigami.Units.largeSpacing

        Item { Layout.fillHeight: true }

        Kirigami.Icon {
            source: "dialog-warning"
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: Kirigami.Units.iconSizes.large
            implicitHeight: Kirigami.Units.iconSizes.large
            opacity: 0.5
        }

        PlasmaComponents.Label {
            text: providerData && providerData.error_message ? providerData.error_message : "Not configured"
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            opacity: 0.7
        }

        Item { Layout.fillHeight: true }
    }

    // Connected state - scrollable
    PlasmaComponents.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: providerData && (providerData.is_connected || root.hasLocalUsageData())

        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: Kirigami.Units.largeSpacing

            PlasmaComponents.Label {
                Layout.fillWidth: true
                visible: providerData && providerData.error_message
                text: providerData ? providerData.error_message : ""
                wrapMode: Text.WordWrap
                color: Kirigami.Theme.negativeTextColor
                opacity: 0.8
            }

            // Plan badge
            RowLayout {
                Layout.fillWidth: true
                visible: providerData && providerData.plan_name

                PlasmaComponents.Label {
                    text: "Plan:"
                    opacity: 0.7
                }

                PlasmaComponents.Label {
                    text: providerData ? providerData.plan_name : ""
                    font.bold: true
                }

                Item { Layout.fillWidth: true }
            }

            // Session usage
            UsageBar {
                Layout.fillWidth: true
                visible: providerData && providerData.is_connected
                title: "Session"
                percentage: providerData ? providerData.session_used_pct : 0
                subtitle: formatResetTime(providerData ? providerData.session_reset_time : "")
            }

            // Weekly usage
            UsageBar {
                Layout.fillWidth: true
                visible: providerData && providerData.is_connected
                title: "Weekly"
                percentage: providerData ? providerData.weekly_used_pct : 0
                subtitle: formatResetTime(providerData ? providerData.weekly_reset_time : "")
                extraInfo: providerData ? providerData.pace_status : ""
            }

            // Models section
            ColumnLayout {
                Layout.fillWidth: true
                visible: providerData && providerData.is_connected && providerData.model_usage && Object.keys(providerData.model_usage).length > 0
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Separator { Layout.fillWidth: true }

                PlasmaExtras.Heading {
                    level: 4
                    text: "Models"
                }

                Repeater {
                    model: providerData && providerData.model_usage ? Object.keys(providerData.model_usage) : []

                    UsageBar {
                        Layout.fillWidth: true
                        required property string modelData
                        title: modelData
                        percentage: providerData.model_usage[modelData]
                        compact: true
                    }
                }
            }

            // Extra usage section
            ColumnLayout {
                Layout.fillWidth: true
                visible: providerData && providerData.is_connected && providerData.extra_usage_enabled
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Separator { Layout.fillWidth: true }

                PlasmaExtras.Heading {
                    level: 4
                    text: "Extra usage"
                }

                PlasmaComponents.ProgressBar {
                    Layout.fillWidth: true
                    from: 0
                    to: 100
                    value: providerData ? providerData.extra_usage_pct : 0
                }

                RowLayout {
                    Layout.fillWidth: true

                    PlasmaComponents.Label {
                        text: providerData ? "This month: $ " + providerData.extra_usage_current.toFixed(2) + " / $ " + providerData.extra_usage_limit.toFixed(2) : ""
                        opacity: 0.8
                    }

                    Item { Layout.fillWidth: true }

                    PlasmaComponents.Label {
                        text: providerData ? Math.round(providerData.extra_usage_pct) + "% used" : ""
                        opacity: 0.6
                    }
                }
            }

            // Usage section
            ColumnLayout {
                Layout.fillWidth: true
                visible: providerData && root.hasLocalUsageData()
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Separator { Layout.fillWidth: true }

                PlasmaExtras.Heading {
                    level: 4
                    text: "Usage"
                }

                // Today
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    visible: providerData && ((providerData.cost_today_input_tokens || 0) > 0 || (providerData.cost_today_output_tokens || 0) > 0)

                    PlasmaComponents.Label {
                        text: "Today"
                        opacity: 0.5
                    }

                    Repeater {
                        model: providerData && providerData.cost_today_by_model ? Object.keys(providerData.cost_today_by_model) : []
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            required property string modelData
                            leftPadding: Kirigami.Units.largeSpacing
                            text: {
                                var m = providerData.cost_today_by_model[modelData]
                                if (!m) return ""
                                var base = modelData + ":  " + formatTokens(m.input) + " in  /  " + formatTokens(m.output) + " out"
                                return base + costSuffix(m.cost)
                            }
                            opacity: 0.8
                        }
                    }

                    // Fallback when no per-model data
                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        visible: !providerData || !providerData.cost_today_by_model || Object.keys(providerData.cost_today_by_model).length === 0
                        leftPadding: Kirigami.Units.largeSpacing
                        text: {
                            if (!providerData) return ""
                            var inp = providerData.cost_today_input_tokens || 0
                            var out = providerData.cost_today_output_tokens || 0
                            var base = formatTokens(inp) + " in  /  " + formatTokens(out) + " out"
                            return base + costSuffix(providerData.cost_today || 0)
                        }
                        opacity: 0.8
                    }
                }

                // 30 days
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1

                    PlasmaComponents.Label {
                        text: "30 days"
                        opacity: 0.5
                    }

                    Repeater {
                        model: providerData && providerData.cost_by_model ? Object.keys(providerData.cost_by_model) : []
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            required property string modelData
                            leftPadding: Kirigami.Units.largeSpacing
                            text: {
                                var m = providerData.cost_by_model[modelData]
                                if (!m) return ""
                                var base = modelData + ":  " + formatTokens(m.input) + " in  /  " + formatTokens(m.output) + " out"
                                return base + costSuffix(m.cost)
                            }
                            opacity: 0.8
                        }
                    }

                    // Total line when multiple models
                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        visible: providerData && providerData.cost_by_model && Object.keys(providerData.cost_by_model).length > 1
                        leftPadding: Kirigami.Units.largeSpacing
                        font.italic: true
                        text: {
                            if (!providerData) return ""
                            var inp = providerData.cost_30_days_input_tokens || 0
                            var out = providerData.cost_30_days_output_tokens || 0
                            var base = "Total:  " + formatTokens(inp) + " in  /  " + formatTokens(out) + " out"
                            return base + costSuffix(providerData.cost_30_days || 0)
                        }
                        opacity: 0.6
                    }

                    // Fallback when no per-model data
                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        visible: !providerData || !providerData.cost_by_model || Object.keys(providerData.cost_by_model).length === 0
                        leftPadding: Kirigami.Units.largeSpacing
                        text: {
                            if (!providerData) return ""
                            var inp = providerData.cost_30_days_input_tokens || 0
                            var out = providerData.cost_30_days_output_tokens || 0
                            var base = formatTokens(inp) + " in  /  " + formatTokens(out) + " out"
                            return base + costSuffix(providerData.cost_30_days || 0)
                        }
                        opacity: 0.8
                    }
                }
            }

            Item { Layout.preferredHeight: Kirigami.Units.largeSpacing }

            // Action buttons
            Kirigami.Separator { Layout.fillWidth: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                PlasmaComponents.ToolButton {
                    text: "Dashboard"
                    icon.name: "document-preview"
                    onClicked: Qt.openUrlExternally(root.dashboardUrl)
                }

                PlasmaComponents.ToolButton {
                    text: "Status"
                    icon.name: "network-connect"
                    onClicked: Qt.openUrlExternally(root.statusUrl)
                }
            }
        }
    }

    function costSuffix(cost) {
        if (!cost || cost <= 0) return ""
        var prefix = providerData && providerData.cost_approximate ? "~$" : "$"
        return "  ·  " + prefix + cost.toFixed(2)
    }

    function formatResetTime(isoTime) {
        if (!isoTime) return ""
        try {
            var reset = new Date(isoTime)
            var now = new Date()
            var diff = reset - now
            if (diff <= 0) return "Resetting..."

            var days = Math.floor(diff / (1000 * 60 * 60 * 24))
            var hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60))
            var mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))

            if (days > 0) return "Resets in " + days + "d " + hours + "h"
            if (hours > 0) return "Resets in " + hours + "h " + mins + "m"
            return "Resets in " + mins + "m"
        } catch (e) {
            return ""
        }
    }

    function formatTokens(tokens) {
        if (!tokens) return "0"
        if (tokens >= 1000000000) return (tokens / 1000000000).toFixed(1) + "B"
        if (tokens >= 1000000) return (tokens / 1000000).toFixed(0) + "M"
        if (tokens >= 1000) return (tokens / 1000).toFixed(0) + "K"
        return tokens.toString()
    }
}
