/*
 * PlasmaCodexBar config categories
 */

import QtQuick
import org.kde.plasma.configuration 2.0

ConfigModel {
    ConfigCategory {
        name: i18nc("@title", "General")
        icon: "configure"
        source: "ConfigGeneral.qml"
    }
}
