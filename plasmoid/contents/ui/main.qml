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

    // All provider data keyed by provider_id
    property var providerDataMap: ({})
    property string currentTab: ""
    property string viewMode: "usage"
    property bool loading: true
    property string lastError: ""
    property int defaultRefreshMs: Plasmoid.configuration.refreshInterval * 1000 || 300000
    property int rateLimitedRefreshMs: 1800000
    property int refreshMs: defaultRefreshMs

    onCurrentTabChanged: viewMode = "usage"

    // Provider registry: id -> { name, icon, dashboard, status }
    readonly property var providerMeta: ({
        "claude":     { name: "Claude",     icon: "../icons/claude-logo.svg",     dashboard: "https://claude.ai/settings/usage",         status: "https://status.anthropic.com/" },
        "codex":      { name: "Codex",      icon: "../icons/openai-logo.svg",     dashboard: "https://platform.openai.com/usage",        status: "https://status.openai.com/" },
        "gemini":     { name: "Gemini",     icon: "../icons/gemini-logo.svg",     dashboard: "https://aistudio.google.com/",             status: "https://status.cloud.google.com/" },
        "copilot":    { name: "Copilot",    icon: "../icons/copilot-logo.svg",    dashboard: "https://github.com/settings/copilot",      status: "https://www.githubstatus.com/" },
        "cursor":     { name: "Cursor",     icon: "../icons/cursor-logo.svg",     dashboard: "https://www.cursor.com/settings",          status: "https://status.cursor.com/" },
        "openrouter": { name: "OpenRouter", icon: "../icons/openrouter-logo.svg", dashboard: "https://openrouter.ai/activity",           status: "https://status.openrouter.ai/" }
    })

    // Which providers to show (from config)
    property var enabledList: {
        var raw = Plasmoid.configuration.enabledProviders || "claude,codex"
        var parts = raw.split(",")
        var result = []
        for (var i = 0; i < parts.length; i++) {
            var id = parts[i].trim()
            if (id.length > 0 && providerMeta[id]) result.push(id)
        }
        return result
    }

    Plasmoid.icon: Qt.resolvedUrl("../icons/ai-robot.svg")
    toolTipMainText: "PlasmaCodexBar"
    toolTipSubText: getTooltipText()

    function getTooltipText() {
        if (loading) return "Loading..."
        var parts = []
        for (var i = 0; i < enabledList.length; i++) {
            var id = enabledList[i]
            var d = providerDataMap[id]
            if (d && d.is_connected) {
                var meta = providerMeta[id]
                parts.push(meta.name + ": " + Math.round(d.session_used_pct) + "%")
            }
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
                        var newMap = {}
                        // Preserve existing data first
                        var keys = Object.keys(root.providerDataMap)
                        for (var k = 0; k < keys.length; k++) {
                            newMap[keys[k]] = root.providerDataMap[keys[k]]
                        }
                        // Update with new data
                        for (var i = 0; i < result.providers.length; i++) {
                            var p = result.providers[i]
                            newMap[p.provider_id] = p
                        }
                        root.providerDataMap = newMap
                    }

                    // Check if any provider is rate limited
                    var anyRateLimited = false
                    var eKeys = Object.keys(root.providerDataMap)
                    for (var j = 0; j < eKeys.length; j++) {
                        var pd = root.providerDataMap[eKeys[j]]
                        if (pd && pd.error_message && pd.error_message.indexOf("Rate limited") !== -1) {
                            anyRateLimited = true
                            break
                        }
                    }
                    root.refreshMs = anyRateLimited ? root.rateLimitedRefreshMs : root.defaultRefreshMs
                    root.lastError = ""
                } catch (e) {
                    root.lastError = "Parse error"
                }
            } else {
                root.lastError = stderr || "Backend error"
            }
            root.loading = false

            // Set initial tab if not set
            if (root.currentTab === "" && root.enabledList.length > 0) {
                root.currentTab = root.enabledList[0]
            }

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

            // Dynamic tab bar
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: Kirigami.Units.smallSpacing

                Repeater {
                    model: root.enabledList

                    Rectangle {
                        required property string modelData

                        width: 60
                        height: 50
                        radius: 6
                        color: root.currentTab === modelData ? Kirigami.Theme.highlightColor : "transparent"

                        MouseArea {
                            anchors.fill: parent
                            onClicked: root.currentTab = modelData
                        }

                        ColumnLayout {
                            anchors.centerIn: parent
                            spacing: 2

                            Kirigami.Icon {
                                source: {
                                    var meta = root.providerMeta[modelData]
                                    return meta ? Qt.resolvedUrl(meta.icon) : ""
                                }
                                implicitWidth: 20
                                implicitHeight: 20
                                Layout.alignment: Qt.AlignHCenter
                                isMask: true
                                // Fallback to generic icon if provider icon missing
                                fallback: "application-x-executable"
                            }

                            PlasmaComponents.Label {
                                text: {
                                    var meta = root.providerMeta[modelData]
                                    return meta ? meta.name : modelData
                                }
                                font.pointSize: Kirigami.Theme.smallFont.pointSize
                                Layout.alignment: Qt.AlignHCenter
                                color: root.currentTab === modelData ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                            }
                        }
                    }
                }
            }

            // Usage / Stats toggle pills (Claude only)
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                visible: root.currentTab === "claude" && !root.loading && root.enabledList.length > 0
                spacing: 4

                Repeater {
                    model: [
                        { label: "Usage", value: "usage" },
                        { label: "Stats", value: "stats" }
                    ]

                    Rectangle {
                        required property var modelData
                        width: 60; height: 24; radius: 12
                        color: root.viewMode === modelData.value ? Kirigami.Theme.highlightColor : Qt.rgba(0.5, 0.5, 0.5, 0.15)

                        PlasmaComponents.Label {
                            anchors.centerIn: parent
                            text: modelData.label
                            font.pointSize: Kirigami.Theme.smallFont.pointSize
                            color: root.viewMode === modelData.value ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: root.viewMode = modelData.value
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

            // No providers configured hint
            PlasmaComponents.Label {
                text: "No providers enabled.\nRight-click the widget and select 'Configure...' to choose providers."
                visible: root.enabledList.length === 0 && !root.loading
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                opacity: 0.7
            }

            // Provider content
            ProviderView {
                id: providerView
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !root.loading && root.enabledList.length > 0 && (root.viewMode === "usage" || root.currentTab !== "claude")

                providerData: root.providerDataMap[root.currentTab] || null
                providerName: {
                    var meta = root.providerMeta[root.currentTab]
                    return meta ? meta.name : ""
                }
                providerIcon: {
                    var meta = root.providerMeta[root.currentTab]
                    return meta ? Qt.resolvedUrl(meta.icon) : ""
                }
                dashboardUrl: {
                    var meta = root.providerMeta[root.currentTab]
                    return meta ? meta.dashboard : ""
                }
                statusUrl: {
                    var meta = root.providerMeta[root.currentTab]
                    return meta ? meta.status : ""
                }
            }

            // Stats view (Claude only)
            StatsView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !root.loading && root.enabledList.length > 0 && root.viewMode === "stats" && root.currentTab === "claude"
                providerData: root.providerDataMap[root.currentTab] || null
            }
        }
    }
}
