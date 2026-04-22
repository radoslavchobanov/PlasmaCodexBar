/*
 * ModelBarChart - Stacked bar chart showing tokens per day by model
 */

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root

    // "YYYY-MM-DD" -> { "Model Name": { "input": int, "output": int, "total": int } }
    property var tokensByModelByDay: ({})

    // "all" | "30d" | "7d"
    property string timeRange: "all"

    spacing: Kirigami.Units.smallSpacing

    // Internal computed chart data
    property var _chartData: null

    // -------------------------------------------------------------------------
    // Helper functions
    // -------------------------------------------------------------------------

    function formatTokens(t) {
        if (!t) return "0"
        if (t >= 1000000000) return (t / 1000000000).toFixed(1) + "B"
        if (t >= 1000000) return (t / 1000000).toFixed(1) + "m"
        if (t >= 1000) return (t / 1000).toFixed(1) + "k"
        return t.toString()
    }

    function modelColor(modelName, allModels) {
        var palette = ["#6B8DD6", "#8E6BBE", "#E8965A", "#6BBE8E", "#BE6B6B", "#6BBEBE"]
        var sorted = allModels.slice().sort()
        var idx = sorted.indexOf(modelName)
        return palette[idx % palette.length]
    }

    // -------------------------------------------------------------------------
    // Data computation
    // -------------------------------------------------------------------------

    function _computeChartData() {
        var data = tokensByModelByDay
        if (!data) {
            _chartData = null
            canvas.requestPaint()
            return
        }

        var allDates = Object.keys(data).sort()

        // Filter by time range
        var filteredDates = []
        var now = new Date()
        now.setHours(23, 59, 59, 999)

        if (timeRange === "7d") {
            var cutoff7 = new Date(now)
            cutoff7.setDate(cutoff7.getDate() - 6)
            cutoff7.setUTCHours(0, 0, 0, 0)
            for (var i = 0; i < allDates.length; i++) {
                var d7 = new Date(allDates[i])
                if (d7 >= cutoff7) filteredDates.push(allDates[i])
            }
        } else if (timeRange === "30d") {
            var cutoff30 = new Date(now)
            cutoff30.setDate(cutoff30.getDate() - 29)
            cutoff30.setUTCHours(0, 0, 0, 0)
            for (var j = 0; j < allDates.length; j++) {
                var d30 = new Date(allDates[j])
                if (d30 >= cutoff30) filteredDates.push(allDates[j])
            }
        } else {
            filteredDates = allDates.slice()
        }

        filteredDates.sort()

        // Collect all unique model names across filtered dates
        var modelSet = {}
        for (var k = 0; k < filteredDates.length; k++) {
            var dayData = data[filteredDates[k]]
            if (!dayData) continue
            var models = Object.keys(dayData)
            for (var m = 0; m < models.length; m++) {
                modelSet[models[m]] = true
            }
        }
        var allModels = Object.keys(modelSet).sort()

        // For each date, compute per-model and total tokens
        var dateEntries = []
        var maxTotal = 0
        for (var n = 0; n < filteredDates.length; n++) {
            var date = filteredDates[n]
            var dayData2 = data[date] || {}
            var modelTokens = {}
            var dayTotal = 0
            for (var p = 0; p < allModels.length; p++) {
                var mn = allModels[p]
                var md = dayData2[mn]
                var total = md ? ((md.total !== undefined && md.total !== null) ? md.total : (md.input || 0) + (md.output || 0)) : 0
                modelTokens[mn] = total
                dayTotal += total
            }
            if (dayTotal > maxTotal) maxTotal = dayTotal
            dateEntries.push({ date: date, modelTokens: modelTokens, total: dayTotal })
        }

        // Compute aggregate per-model breakdown (for the list below chart)
        var modelBreakdown = {}
        var grandTotal = 0
        for (var q = 0; q < filteredDates.length; q++) {
            var dd = data[filteredDates[q]]
            if (!dd) continue
            for (var r = 0; r < allModels.length; r++) {
                var mn2 = allModels[r]
                var md2 = dd[mn2]
                if (!md2) continue
                if (!modelBreakdown[mn2]) {
                    modelBreakdown[mn2] = { input: 0, output: 0, total: 0 }
                }
                var inp = md2.input || 0
                var out = md2.output || 0
                var tot = (md2.total !== undefined && md2.total !== null) ? md2.total : (inp + out)
                modelBreakdown[mn2].input += inp
                modelBreakdown[mn2].output += out
                modelBreakdown[mn2].total += tot
                grandTotal += tot
            }
        }

        // Add percentage
        for (var s = 0; s < allModels.length; s++) {
            var mn3 = allModels[s]
            if (modelBreakdown[mn3]) {
                modelBreakdown[mn3].percentage = grandTotal > 0
                    ? (modelBreakdown[mn3].total / grandTotal * 100)
                    : 0
            }
        }

        _chartData = {
            dates: filteredDates,
            entries: dateEntries,
            allModels: allModels,
            maxTotal: maxTotal,
            modelBreakdown: modelBreakdown,
            grandTotal: grandTotal
        }

        canvas.requestPaint()
    }

    onTokensByModelByDayChanged: _computeChartData()
    onTimeRangeChanged: _computeChartData()
    Component.onCompleted: _computeChartData()

    // -------------------------------------------------------------------------
    // Time range selector pills
    // -------------------------------------------------------------------------

    RowLayout {
        Layout.alignment: Qt.AlignHCenter
        spacing: 4

        Repeater {
            model: [
                { label: "All time", value: "all" },
                { label: "Last 30 days", value: "30d" },
                { label: "Last 7 days", value: "7d" }
            ]

            Rectangle {
                required property var modelData
                property bool active: root.timeRange === modelData.value

                height: 22
                width: pillLabel.implicitWidth + 16
                radius: height / 2
                color: active ? Kirigami.Theme.highlightColor : "transparent"
                border.color: active
                    ? Kirigami.Theme.highlightColor
                    : Qt.rgba(
                        Kirigami.Theme.textColor.r,
                        Kirigami.Theme.textColor.g,
                        Kirigami.Theme.textColor.b,
                        0.3
                    )
                border.width: 1

                PlasmaComponents.Label {
                    id: pillLabel
                    anchors.centerIn: parent
                    text: modelData.label
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    color: active ? "white" : Kirigami.Theme.textColor
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.timeRange = modelData.value
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Canvas bar chart
    // -------------------------------------------------------------------------

    Canvas {
        id: canvas
        Layout.fillWidth: true
        Layout.preferredHeight: 180

        // Chart margins
        readonly property int marginLeft: 36
        readonly property int marginBottom: 24
        readonly property int marginTop: 8
        readonly property int marginRight: 8

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var cd = root._chartData
            var tr = Kirigami.Theme.textColor

            var plotW = width - canvas.marginLeft - canvas.marginRight
            var plotH = height - canvas.marginTop - canvas.marginBottom

            // Draw axes
            ctx.strokeStyle = Qt.rgba(tr.r, tr.g, tr.b, 0.3)
            ctx.lineWidth = 1
            ctx.beginPath()
            // Y axis
            ctx.moveTo(canvas.marginLeft, canvas.marginTop)
            ctx.lineTo(canvas.marginLeft, canvas.marginTop + plotH)
            // X axis
            ctx.lineTo(canvas.marginLeft + plotW, canvas.marginTop + plotH)
            ctx.stroke()

            if (!cd || cd.dates.length === 0) {
                // Empty state: just show axes
                ctx.font = "10px sans-serif"
                ctx.fillStyle = Qt.rgba(tr.r, tr.g, tr.b, 0.4)
                ctx.textAlign = "center"
                ctx.fillText("No data", canvas.marginLeft + plotW / 2, canvas.marginTop + plotH / 2)
                ctx.textAlign = "left"
                return
            }

            var maxTotal = cd.maxTotal
            if (maxTotal === 0) maxTotal = 1

            // Y-axis labels and grid lines (4-5 labels)
            var numYLabels = 4
            ctx.font = "10px sans-serif"
            ctx.textAlign = "right"

            for (var yi = 0; yi <= numYLabels; yi++) {
                var yVal = maxTotal * yi / numYLabels
                var yPos = canvas.marginTop + plotH - (plotH * yi / numYLabels)

                // Grid line
                ctx.strokeStyle = Qt.rgba(tr.r, tr.g, tr.b, 0.15)
                ctx.lineWidth = 1
                ctx.setLineDash([3, 3])
                ctx.beginPath()
                ctx.moveTo(canvas.marginLeft + 1, yPos)
                ctx.lineTo(canvas.marginLeft + plotW, yPos)
                ctx.stroke()
                ctx.setLineDash([])

                // Label
                ctx.fillStyle = Qt.rgba(tr.r, tr.g, tr.b, 0.6)
                ctx.fillText(root.formatTokens(Math.round(yVal)), canvas.marginLeft - 4, yPos + 3)
            }

            // Bars
            var numDates = cd.dates.length
            var allModels = cd.allModels

            // Max bar width depends on time range
            var maxBarWidth = 12
            if (root.timeRange === "30d") maxBarWidth = 7
            else if (root.timeRange === "all") maxBarWidth = 5

            var barSpacing = plotW / Math.max(numDates, 1)
            var barWidth = Math.min(maxBarWidth, barSpacing * 0.7)

            // X-axis label strategy
            // For 7d: show all 7 dates
            // For 30d: show ~6 evenly spaced
            // For all: show monthly labels
            var xLabelIndices = {}
            if (root.timeRange === "7d") {
                for (var xi = 0; xi < numDates; xi++) xLabelIndices[xi] = true
            } else if (root.timeRange === "30d") {
                var step30 = Math.max(1, Math.floor(numDates / 6))
                for (var xi2 = 0; xi2 < numDates; xi2 += step30) xLabelIndices[xi2] = true
                xLabelIndices[numDates - 1] = true
            } else {
                // Monthly labels for "all" - show first date of each month
                var lastMonth = -1
                for (var xi3 = 0; xi3 < numDates; xi3++) {
                    var dParts = cd.dates[xi3].split("-")
                    var mo = parseInt(dParts[1])
                    if (mo !== lastMonth) {
                        xLabelIndices[xi3] = true
                        lastMonth = mo
                    }
                }
            }

            var monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

            ctx.textAlign = "center"
            ctx.font = "9px sans-serif"
            ctx.fillStyle = Qt.rgba(tr.r, tr.g, tr.b, 0.6)

            for (var bi = 0; bi < numDates; bi++) {
                var entry = cd.entries[bi]
                var barCenterX = canvas.marginLeft + (bi + 0.5) * barSpacing
                var barLeft = barCenterX - barWidth / 2

                // Draw stacked bar segments
                var stackY = canvas.marginTop + plotH  // start from bottom

                for (var mi = 0; mi < allModels.length; mi++) {
                    var mn = allModels[mi]
                    var segTokens = entry.modelTokens[mn] || 0
                    if (segTokens <= 0) continue

                    var segH = plotH * segTokens / maxTotal
                    var segY = stackY - segH

                    ctx.fillStyle = root.modelColor(mn, allModels)
                    ctx.fillRect(barLeft, segY, barWidth, segH)

                    stackY = segY
                }

                // X-axis label
                if (xLabelIndices[bi] === true) {
                    var labelY = canvas.marginTop + plotH + 14
                    var labelText = ""
                    var dateParts = cd.dates[bi].split("-")

                    if (root.timeRange === "7d") {
                        // Show MM/DD
                        labelText = dateParts[1] + "/" + dateParts[2]
                    } else if (root.timeRange === "30d") {
                        labelText = dateParts[1] + "/" + dateParts[2]
                    } else {
                        // Monthly: show "Jan '25" style
                        var moIdx = parseInt(dateParts[1]) - 1
                        labelText = monthNames[moIdx]
                        if (moIdx === 0) labelText += " '" + dateParts[0].slice(2)
                    }

                    ctx.fillStyle = Qt.rgba(tr.r, tr.g, tr.b, 0.6)
                    ctx.fillText(labelText, barCenterX, labelY)
                }
            }

            ctx.textAlign = "left"
        }
    }

    // -------------------------------------------------------------------------
    // Model breakdown list
    // -------------------------------------------------------------------------

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 2
        visible: _chartData !== null && _chartData.allModels && _chartData.allModels.length > 0

        Kirigami.Separator {
            Layout.fillWidth: true
        }

        Repeater {
            model: _chartData ? _chartData.allModels : []

            delegate: ColumnLayout {
                required property string modelData
                required property int index

                Layout.fillWidth: true
                spacing: 0

                property var breakdown: _chartData ? (_chartData.modelBreakdown[modelData] || null) : null
                property string mColor: _chartData ? root.modelColor(modelData, _chartData.allModels) : "#888888"

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    // Colored circle
                    Rectangle {
                        width: 10
                        height: 10
                        radius: 5
                        color: mColor
                        Layout.alignment: Qt.AlignVCenter
                    }

                    // Model name + percentage
                    PlasmaComponents.Label {
                        font.bold: true
                        text: {
                            if (!breakdown) return modelData
                            var pct = breakdown.percentage.toFixed(1)
                            return modelData + " (" + pct + "%)"
                        }
                        Layout.fillWidth: true
                    }
                }

                // In/Out line
                PlasmaComponents.Label {
                    leftPadding: 16
                    text: {
                        if (!breakdown) return ""
                        return "In: " + root.formatTokens(breakdown.input) + "  ·  Out: " + root.formatTokens(breakdown.output)
                    }
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    opacity: 0.7
                }
            }
        }
    }
}
