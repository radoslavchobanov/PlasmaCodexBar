/*
 * ActivityHeatmap - GitHub-style contribution heatmap for daily token activity
 */

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root

    property var activityData: ({})
    property color accentColor: "#E8965A"

    spacing: Kirigami.Units.smallSpacing

    // Layout constants
    readonly property int cellSize: 12
    readonly property int cellGap: 2
    readonly property int cellStep: cellSize + cellGap  // 14px per col/row
    readonly property int numWeeks: 25
    readonly property int numDays: 7
    readonly property int dayLabelWidth: 28
    readonly property int monthLabelHeight: 16

    // Computed date range and intensity data - recalculated whenever activityData changes
    property var _computed: ({})

    onActivityDataChanged: _recompute()
    Component.onCompleted: _recompute()

    function _recompute() {
        var today = new Date()
        today.setHours(0, 0, 0, 0)

        // Anchor the last column to the Monday of the current week so today
        // is always visible. Without this, the Monday-snap on the start date
        // can push today past the end of the grid by up to 6 days.
        var dow = today.getDay()
        var daysToMonday = (dow === 0) ? -6 : (1 - dow)
        var thisMonday = new Date(today)
        thisMonday.setDate(thisMonday.getDate() + daysToMonday)
        var startDate = new Date(thisMonday)
        startDate.setDate(startDate.getDate() - (numWeeks - 1) * 7)

        // Build flat list of token counts for non-zero days to compute percentiles
        var nonZero = []
        for (var c = 0; c < numWeeks; c++) {
            for (var r = 0; r < numDays; r++) {
                var d = new Date(startDate)
                d.setDate(d.getDate() + c * numDays + r)
                if (d > today) continue
                var key = _dateKey(d)
                var tokens = (activityData && activityData[key]) ? activityData[key] : 0
                if (tokens > 0) nonZero.push(tokens)
            }
        }

        nonZero.sort(function(a, b) { return a - b })
        var p25 = nonZero.length > 0 ? nonZero[Math.floor(nonZero.length * 0.25)] : 0
        var p50 = nonZero.length > 0 ? nonZero[Math.floor(nonZero.length * 0.50)] : 0
        var p75 = nonZero.length > 0 ? nonZero[Math.floor(nonZero.length * 0.75)] : 0

        _computed = {
            startDate: startDate,
            today: today,
            p25: p25,
            p50: p50,
            p75: p75
        }
        heatmapCanvas.requestPaint()
    }

    function _dateKey(d) {
        var y = d.getFullYear()
        var m = String(d.getMonth() + 1).padStart(2, "0")
        var day = String(d.getDate()).padStart(2, "0")
        return y + "-" + m + "-" + day
    }

    function _intensityLevel(tokens) {
        if (!tokens || tokens <= 0) return 0
        var c = _computed
        if (tokens > c.p75) return 4
        if (tokens > c.p50) return 3
        if (tokens > c.p25) return 2
        return 1
    }

    function _cellColor(level) {
        if (level === 0) return Qt.rgba(1, 1, 1, 0.07)
        var r = accentColor.r
        var g = accentColor.g
        var b = accentColor.b
        var alphas = [0, 0.25, 0.50, 0.75, 1.0]
        return Qt.rgba(r, g, b, alphas[level])
    }

    function formatTokens(t) {
        if (!t || t <= 0) return "0"
        if (t >= 1000000000) return (t / 1000000000).toFixed(1) + "B"
        if (t >= 1000000) return (t / 1000000).toFixed(1) + "m"
        if (t >= 1000) return (t / 1000).toFixed(1) + "k"
        return Math.round(t).toString()
    }

    // Resolved once at QML level so Canvas onPaint gets a plain color value
    readonly property color _textColor: Kirigami.Theme.textColor

    // Month names for labels
    readonly property var _monthNames: ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    // Canvas item for the heatmap grid + month labels
    Canvas {
        id: heatmapCanvas

        Layout.preferredWidth: dayLabelWidth + root.numWeeks * root.cellStep
        Layout.preferredHeight: root.monthLabelHeight + root.numDays * root.cellStep

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var c = root._computed
            if (!c || !c.startDate) return

            var startDate = c.startDate
            var today = c.today
            var offsetX = root.dayLabelWidth
            var offsetY = root.monthLabelHeight

            // --- Month labels ---
            ctx.font = "10px sans-serif"
            ctx.fillStyle = "rgba(255,255,255,0.6)"

            var lastLabelMonth = -1
            for (var col = 0; col < root.numWeeks; col++) {
                var weekStart = new Date(startDate)
                weekStart.setDate(weekStart.getDate() + col * 7)
                if (weekStart > today) break
                var mo = weekStart.getMonth()
                if (mo !== lastLabelMonth) {
                    var lx = offsetX + col * root.cellStep
                    ctx.fillText(root._monthNames[mo], lx, root.monthLabelHeight - 3)
                    lastLabelMonth = mo
                }
            }

            // --- Day-of-week labels (Mon, Wed, Fri) ---
            ctx.font = "9px sans-serif"
            ctx.fillStyle = "rgba(255,255,255,0.5)"
            var dayLabels = ["Mon", "", "Wed", "", "Fri", "", ""]
            for (var row = 0; row < root.numDays; row++) {
                if (dayLabels[row] !== "") {
                    var ly = offsetY + row * root.cellStep + root.cellSize - 1
                    ctx.fillText(dayLabels[row], 0, ly)
                }
            }

            // --- Grid cells ---
            for (var col2 = 0; col2 < root.numWeeks; col2++) {
                for (var row2 = 0; row2 < root.numDays; row2++) {
                    var cellDate = new Date(startDate)
                    cellDate.setDate(cellDate.getDate() + col2 * 7 + row2)
                    if (cellDate > today) continue

                    var key = root._dateKey(cellDate)
                    var tokens = (root.activityData && root.activityData[key]) ? root.activityData[key] : 0
                    var level = root._intensityLevel(tokens)
                    var color = root._cellColor(level)

                    var cx = offsetX + col2 * root.cellStep
                    var cy = offsetY + row2 * root.cellStep

                    ctx.fillStyle = color
                    _roundRect(ctx, cx, cy, root.cellSize, root.cellSize, 2)
                }
            }
        }

        function _roundRect(ctx, x, y, w, h, r) {
            ctx.beginPath()
            ctx.moveTo(x + r, y)
            ctx.lineTo(x + w - r, y)
            ctx.quadraticCurveTo(x + w, y, x + w, y + r)
            ctx.lineTo(x + w, y + h - r)
            ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
            ctx.lineTo(x + r, y + h)
            ctx.quadraticCurveTo(x, y + h, x, y + h - r)
            ctx.lineTo(x, y + r)
            ctx.quadraticCurveTo(x, y, x + r, y)
            ctx.closePath()
            ctx.fill()
        }

        // Hover tooltip logic
        property int hoveredCol: -1
        property int hoveredRow: -1
        property string hoveredDate: ""
        property int hoveredTokens: -1

        MouseArea {
            id: heatmapMouseArea
            anchors.fill: parent
            hoverEnabled: true

            onExited: {
                heatmapCanvas.hoveredCol = -1
                heatmapCanvas.hoveredRow = -1
                tooltipPopup.visible = false
            }

            onPositionChanged: (mouse) => {
                var c = root._computed
                if (!c || !c.startDate) {
                    tooltipPopup.visible = false
                    return
                }

                var mx = mouse.x - root.dayLabelWidth
                var my = mouse.y - root.monthLabelHeight

                if (mx < 0 || my < 0) {
                    tooltipPopup.visible = false
                    return
                }

                var col = Math.floor(mx / root.cellStep)
                var row = Math.floor(my / root.cellStep)

                // Check that we are actually over the cell (not the gap)
                var localX = mx - col * root.cellStep
                var localY = my - row * root.cellStep

                if (col < 0 || col >= root.numWeeks || row < 0 || row >= root.numDays
                        || localX >= root.cellSize || localY >= root.cellSize) {
                    tooltipPopup.visible = false
                    return
                }

                var cellDate = new Date(c.startDate)
                cellDate.setDate(cellDate.getDate() + col * 7 + row)
                if (cellDate > c.today) {
                    tooltipPopup.visible = false
                    return
                }

                heatmapCanvas.hoveredCol = col
                heatmapCanvas.hoveredRow = row
                heatmapCanvas.hoveredDate = root._dateKey(cellDate)
                var tokens = (root.activityData && root.activityData[heatmapCanvas.hoveredDate])
                    ? root.activityData[heatmapCanvas.hoveredDate] : 0
                heatmapCanvas.hoveredTokens = tokens

                // Position tooltip near mouse, keep inside canvas
                var tx = mouse.x + 8
                var ty = mouse.y - tooltipPopup.height - 4
                if (tx + tooltipPopup.width > heatmapCanvas.width) {
                    tx = mouse.x - tooltipPopup.width - 4
                }
                if (ty < 0) {
                    ty = mouse.y + 16
                }
                tooltipPopup.x = tx
                tooltipPopup.y = ty
                tooltipPopup.visible = true
            }

            onPressed: tooltipPopup.visible = false
        }

        // Inline tooltip rectangle
        Rectangle {
            id: tooltipPopup
            visible: false
            z: 100
            width: tooltipText.implicitWidth + 12
            height: tooltipText.implicitHeight + 8
            radius: 4
            color: Kirigami.Theme.backgroundColor
            border.color: Qt.rgba(
                Kirigami.Theme.textColor.r,
                Kirigami.Theme.textColor.g,
                Kirigami.Theme.textColor.b,
                0.25
            )
            border.width: 1

            // Drop shadow via layer effect is not available without Qt Quick Effects in all
            // Plasma builds; a simple semi-transparent border is sufficient.

            PlasmaComponents.Label {
                id: tooltipText
                anchors.centerIn: parent
                font.pointSize: Kirigami.Theme.smallFont.pointSize
                text: {
                    var d = heatmapCanvas.hoveredDate
                    var t = heatmapCanvas.hoveredTokens
                    if (!d) return ""
                    var tokenStr = t > 0
                        ? root.formatTokens(t) + " tokens"
                        : "No activity"
                    return d + "\n" + tokenStr
                }
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    // Legend row: Less [0][1][2][3][4] More
    RowLayout {
        Layout.alignment: Qt.AlignRight
        spacing: 4

        PlasmaComponents.Label {
            text: "Less"
            font.pointSize: Kirigami.Theme.smallFont.pointSize
            opacity: 0.6
        }

        Repeater {
            model: 5
            Rectangle {
                required property int index
                width: 10
                height: 10
                radius: 2
                color: root._cellColor(index)
                border.color: Qt.rgba(
                    Kirigami.Theme.textColor.r,
                    Kirigami.Theme.textColor.g,
                    Kirigami.Theme.textColor.b,
                    index === 0 ? 0.15 : 0
                )
                border.width: 1
            }
        }

        PlasmaComponents.Label {
            text: "More"
            font.pointSize: Kirigami.Theme.smallFont.pointSize
            opacity: 0.6
        }
    }
}
