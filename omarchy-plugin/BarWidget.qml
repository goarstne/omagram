import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

BarWidget {
  id: root
  moduleName: "local.omagram"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: launcher
    command: ["omarchy-launch-or-focus-tui", "--app-id=org.omarchy.omagram", "bash", "-lc", "cd \"$HOME/Projects/omagram\" && exec uv run omagram run"]
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
