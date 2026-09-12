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
    // Absolute path, not a bare command name: Quickshell's own PATH is
    // outside this plugin's control, so resolving by name here would let
    // anything earlier on that PATH shadow Omarchy's launcher script.
    command: ["/usr/bin/omarchy-launch-or-focus-tui", "--app-id=org.omarchy.omagram", root.launcherPath]
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
