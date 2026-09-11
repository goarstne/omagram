import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "goarstne.omagram"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string launcherPath: decodeURIComponent(
    String(Qt.resolvedUrl("run-omagram")).replace(/^file:\/\//, ""))

  Process {
    id: launcher
    command: ["omarchy-launch-or-focus-tui", "--app-id=org.omarchy.omagram", root.launcherPath]
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰗊"
    tooltipText: "Omagram · Telegram TUI"
    slotSize: Style.bar.statusSlot
    onClicked: launcher.running = true
  }
}
