import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root
  property var status: ({connected: false, contacts: [], selected: [], unread: 0, error: "Connecting …"})
  readonly property string pluginPath: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, ""))

  function select(id) {
    if (!worker.running) return
    worker.write(JSON.stringify({toggle: id}) + "\n")
  }

  function refresh() {
    if (worker.running) worker.write('{"refresh":true}\n')
    else worker.running = true
  }

  function markRead(id) {
    if (!worker.running) return
    worker.write(JSON.stringify({read: id}) + "\n")
  }

  Process {
    id: worker
    command: [root.pluginPath + "run-widget"]
    stdinEnabled: true
    running: true
    stdout: SplitParser {
      onRead: function(line) {
        try { root.status = JSON.parse(line) }
        catch (error) { console.warn("Omagram: invalid status response") }
      }
    }
    onExited: {
      root.status = Object.assign({}, root.status, {connected: false, error: "Omagram widget stopped. Reconnecting …"})
      restart.restart()
    }
  }

  Timer {
    id: restart
    interval: 60000
    onTriggered: if (!worker.running) worker.running = true
  }

  IpcHandler {
    target: "omagram-widget"
    function refresh(): void { root.refresh() }
    function markRead(id: string): void { root.markRead(id) }
    function status(): string {
      return JSON.stringify({connected: root.status.connected, contacts: root.status.contacts.length,
        selected: root.status.selected.length, unread: root.status.unread, error: root.status.error})
    }
  }
}
