/*
 * PlasmaCodexBar - KDE Plasma 6 System Tray Applet
 */

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property var claudeData: null
    property var codexData: null
    property string currentTab: "claude"
    property bool loading: true
    property string lastError: ""
    property int defaultRefreshMs: 300000
    property int rateLimitedRefreshMs: 1800000
    property int refreshMs: defaultRefreshMs

    Plasmoid.icon: Qt.resolvedUrl("../icons/ai-robot.svg")
    toolTipMainText: "PlasmaCodexBar"
    toolTipSubText: getTooltipText()

    function getTooltipText() {
        if (loading) return "Loading..."
        var parts = []
        if (claudeData && claudeData.is_connected) {
            parts.push("Claude: " + Math.round(claudeData.session_used_pct) + "%")
        }
        if (codexData && codexData.is_connected) {
            parts.push("Codex: " + Math.round(codexData.session_used_pct) + "%")
        }
        return parts.length > 0 ? parts.join(" | ") : "Click to configure"
    }

    // Backend data source
    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []

        onNewData: (source, data) => {
            var stdout = data["stdout"]
            var stderr = data["stderr"]
            var exitCode = data["exit code"]

            if (exitCode === 0 && stdout) {
                try {
                    var result = JSON.parse(stdout)
                    if (result.providers) {
                        for (var i = 0; i < result.providers.length; i++) {
                            var p = result.providers[i]
                            if (p.provider_id === "claude") {
                                root.claudeData = p
                            } else if (p.provider_id === "codex") {
                                root.codexData = p
                            }
                        }
                    }
                    var claudeRateLimited = root.claudeData
                        && root.claudeData.error_message
                        && root.claudeData.error_message.indexOf("Rate limited") !== -1
                    root.refreshMs = claudeRateLimited ? root.rateLimitedRefreshMs : root.defaultRefreshMs
                    root.lastError = ""
                } catch (e) {
                    root.lastError = "Parse error"
                }
            } else {
                root.lastError = stderr || "Backend error"
            }
            root.loading = false
            disconnectSource(source)
        }
    }

    // Path to embedded backend script
    readonly property string backendPath: Qt.resolvedUrl("../code/backend.py").toString().replace("file://", "")

    function refresh() {
        root.loading = true
        executable.connectSource("python3 " + backendPath + " --json")
    }

    // Auto refresh
    Timer {
        interval: root.refreshMs
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: refresh()
    }

    // Compact representation (tray icon)
    compactRepresentation: Kirigami.Icon {
        source: Plasmoid.icon
        active: mouseArea.containsMouse

        MouseArea {
            id: mouseArea
            anchors.fill: parent
            hoverEnabled: true
            onClicked: root.expanded = !root.expanded
        }
    }

    // Full popup
    fullRepresentation: PlasmaExtras.Representation {
        id: popup

        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.minimumHeight: Kirigami.Units.gridUnit * 16
        Layout.preferredWidth: Kirigami.Units.gridUnit * 20
        Layout.preferredHeight: contentColumn.implicitHeight + Kirigami.Units.largeSpacing * 2

        header: PlasmaExtras.PlasmoidHeading {
            RowLayout {
                anchors.fill: parent

                PlasmaExtras.Heading {
                    level: 1
                    text: "PlasmaCodexBar"
                    Layout.fillWidth: true
                }

                PlasmaComponents.ToolButton {
                    icon.name: "view-refresh"
                    enabled: !root.loading
                    onClicked: root.refresh()
                    PlasmaComponents.ToolTip { text: "Refresh" }
                }
            }
        }

        ColumnLayout {
            id: contentColumn
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.largeSpacing

            // Compact tab buttons with icon above text
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: Kirigami.Units.smallSpacing

                // Codex tab
                Rectangle {
                    width: 60
                    height: 50
                    radius: 6
                    color: root.currentTab === "codex" ? Kirigami.Theme.highlightColor : "transparent"

                    MouseArea {
                        anchors.fill: parent
                        onClicked: root.currentTab = "codex"
                    }

                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 2

                        Kirigami.Icon {
                            source: Qt.resolvedUrl("../icons/openai-logo.svg")
                            implicitWidth: 20
                            implicitHeight: 20
                            Layout.alignment: Qt.AlignHCenter
                        }

                        PlasmaComponents.Label {
                            text: "Codex"
                            font.pointSize: Kirigami.Theme.smallFont.pointSize
                            Layout.alignment: Qt.AlignHCenter
                            color: root.currentTab === "codex" ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                        }
                    }
                }

                // Claude tab
                Rectangle {
                    width: 60
                    height: 50
                    radius: 6
                    color: root.currentTab === "claude" ? Kirigami.Theme.highlightColor : "transparent"

                    MouseArea {
                        anchors.fill: parent
                        onClicked: root.currentTab = "claude"
                    }

                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 2

                        Kirigami.Icon {
                            source: Qt.resolvedUrl("../icons/claude-logo.svg")
                            implicitWidth: 20
                            implicitHeight: 20
                            Layout.alignment: Qt.AlignHCenter
                        }

                        PlasmaComponents.Label {
                            text: "Claude"
                            font.pointSize: Kirigami.Theme.smallFont.pointSize
                            Layout.alignment: Qt.AlignHCenter
                            color: root.currentTab === "claude" ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                        }
                    }
                }
            }

            // Loading indicator
            PlasmaComponents.BusyIndicator {
                Layout.alignment: Qt.AlignCenter
                running: root.loading
                visible: root.loading
            }

            // Error message
            PlasmaComponents.Label {
                text: root.lastError
                visible: root.lastError !== "" && !root.loading
                color: Kirigami.Theme.negativeTextColor
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
            }

            // Provider content
            ProviderView {
                id: providerView
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !root.loading

                providerData: root.currentTab === "claude" ? root.claudeData : root.codexData
                providerName: root.currentTab === "claude" ? "Claude" : "Codex"
                providerIcon: root.currentTab === "claude" ? Qt.resolvedUrl("../icons/claude-logo.svg") : Qt.resolvedUrl("../icons/openai-logo.svg")
                dashboardUrl: root.currentTab === "claude" ? "https://claude.ai/settings/usage" : "https://platform.openai.com/usage"
                statusUrl: root.currentTab === "claude" ? "https://status.anthropic.com/" : "https://status.openai.com/"
            }
        }
    }
}
