/*
 * StatsView - Overview and per-model stats for a provider
 */

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root

    property var providerData: null
    property color accentColor: "#E8965A"

    property string activeTab: "overview"

    spacing: Kirigami.Units.largeSpacing

    // -------------------------------------------------------------------------
    // Helper functions
    // -------------------------------------------------------------------------

    function formatTokens(t) {
        if (!t || t <= 0) return "0"
        if (t >= 1000000000) return (t / 1000000000).toFixed(1) + "B"
        if (t >= 1000000) return (t / 1000000).toFixed(1) + "m"
        if (t >= 1000) return (t / 1000).toFixed(1) + "k"
        return t.toString()
    }

    function formatDuration(seconds) {
        if (!seconds || seconds <= 0) return "0m"
        var d = Math.floor(seconds / 86400)
        var h = Math.floor((seconds % 86400) / 3600)
        var m = Math.floor((seconds % 3600) / 60)
        if (d > 0) return d + "d " + h + "h " + m + "m"
        if (h > 0) return h + "h " + m + "m"
        return m + "m"
    }

    function formatDate(isoDate) {
        if (!isoDate) return "—"
        var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        try {
            var d = new Date(isoDate)
            return months[d.getUTCMonth()] + " " + d.getUTCDate()
        } catch(e) { return isoDate }
    }

    function hasStats() {
        if (!providerData) return false
        return (providerData.stats_total_tokens || 0) > 0
            || (providerData.stats_active_days || 0) > 0
            || (providerData.stats_session_count || 0) > 0
    }

    // -------------------------------------------------------------------------
    // No data state
    // -------------------------------------------------------------------------

    PlasmaComponents.Label {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: !root.hasStats()
        text: "No stats data available yet."
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
        opacity: 0.7
    }

    // -------------------------------------------------------------------------
    // Subtab pills
    // -------------------------------------------------------------------------

    RowLayout {
        Layout.alignment: Qt.AlignHCenter
        spacing: 6
        visible: root.hasStats()

        Repeater {
            model: [
                { label: "Overview", value: "overview" },
                { label: "Models",   value: "models"   }
            ]

            Rectangle {
                required property var modelData

                property bool active: root.activeTab === modelData.value

                width: pillLabel.implicitWidth + 24
                height: 28
                radius: 14
                color: active ? Kirigami.Theme.highlightColor : Qt.rgba(0, 0, 0, 0.1)

                PlasmaComponents.Label {
                    id: pillLabel
                    anchors.centerIn: parent
                    text: modelData.label
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    color: active ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.activeTab = modelData.value
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Overview tab
    // -------------------------------------------------------------------------

    PlasmaComponents.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: root.hasStats() && root.activeTab === "overview"

        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: Kirigami.Units.largeSpacing

            // Activity heatmap
            ActivityHeatmap {
                Layout.fillWidth: true
                activityData: providerData ? (providerData.stats_daily_activity || {}) : ({})
                accentColor: root.accentColor
            }

            Kirigami.Separator { Layout.fillWidth: true }

            // Stats grid — 2 columns, 4 rows
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 16
                rowSpacing: 8

                // Row 1 — col 1
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Favorite model"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: (providerData && providerData.stats_favorite_model) ? providerData.stats_favorite_model : "—"
                        font.bold: true
                        color: root.accentColor
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }

                // Row 1 — col 2
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Total tokens"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: root.formatTokens(providerData ? providerData.stats_total_tokens : 0)
                        font.bold: true
                        color: root.accentColor
                    }
                }

                // Row 2 — col 1
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Sessions"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: providerData ? (providerData.stats_session_count || 0).toString() : "0"
                        font.bold: true
                        color: root.accentColor
                    }
                }

                // Row 2 — col 2
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Longest session"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: root.formatDuration(providerData ? providerData.stats_longest_session_seconds : 0)
                        font.bold: true
                        color: Kirigami.Theme.textColor
                    }
                }

                // Row 3 — col 1
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Active days"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: {
                            if (!providerData) return "0/0"
                            return (providerData.stats_active_days || 0) + "/" + (providerData.stats_total_days_tracked || 0)
                        }
                        font.bold: true
                        color: Kirigami.Theme.textColor
                    }
                }

                // Row 3 — col 2
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Longest streak"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: {
                            if (!providerData) return "0 days"
                            var n = providerData.stats_longest_streak_days || 0
                            return n + (n === 1 ? " day" : " days")
                        }
                        font.bold: true
                        color: Kirigami.Theme.textColor
                    }
                }

                // Row 4 — col 1
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Most active day"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: root.formatDate(providerData ? providerData.stats_most_active_day : null)
                        font.bold: true
                        color: Kirigami.Theme.textColor
                    }
                }

                // Row 4 — col 2
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    PlasmaComponents.Label {
                        text: "Current streak"
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        opacity: 0.6
                    }
                    PlasmaComponents.Label {
                        text: {
                            if (!providerData) return "0 days"
                            var n = providerData.stats_current_streak_days || 0
                            return n + (n === 1 ? " day" : " days")
                        }
                        font.bold: true
                        color: Kirigami.Theme.textColor
                    }
                }
            }

            Item { Layout.preferredHeight: Kirigami.Units.largeSpacing }
        }
    }

    // -------------------------------------------------------------------------
    // Models tab
    // -------------------------------------------------------------------------

    PlasmaComponents.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: root.hasStats() && root.activeTab === "models"
        contentWidth: availableWidth

        ModelBarChart {
            width: parent.width
            tokensByModelByDay: providerData ? (providerData.stats_tokens_by_model_by_day || {}) : ({})
        }
    }
}
