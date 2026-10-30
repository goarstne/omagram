import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.BarWidget {
  id: root
  moduleName: "goarstne.omagram"
  readonly property var service: bar && bar.shell ? bar.shell.serviceFor(moduleName) : null
  readonly property var status: service ? service.status : ({connected: false, contacts: [], selected: [], unread: 0, error: "Loading Omagram widget …"})
  readonly property var pendingContacts: status.connected ? status.contacts.filter(function(c) {
    return root.status.selected.indexOf(c.id) >= 0 && c.unread > 0
  }).sort(function(a, b) { return a.name.localeCompare(b.name) }) : []
  property bool panelOpen: false
  property string currentView: "contacts"
  property string activeContactId: ""
  readonly property var activeContact: {
    if (activeContactId) {
      for (var i = 0; i < status.contacts.length; i++) {
        if (status.contacts[i].id === activeContactId) return status.contacts[i]
      }
    }
    return pendingContacts.length > 0 ? pendingContacts[0] : null
  }
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string launcherPath: decodeURIComponent(
    String(Qt.resolvedUrl("run-omagram")).replace(/^file:\/\//, ""))

  function openContactMessage(contact) {
    if (root.panelOpen && root.currentView === "messages" && root.activeContactId === contact.id) {
      root.panelOpen = false
    } else {
      root.activeContactId = contact.id
      root.currentView = "messages"
      root.panelOpen = true
    }
  }

  function close() { panelOpen = false }

  Ui.WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰗊"
    labelVisible: false
    Accessible.role: Accessible.Button
    Accessible.name: tooltipText
    implicitWidth: root.vertical ? root.barSize : Math.max(12, letters.implicitWidth + scaledHorizontalMargin * 2)
    implicitHeight: root.vertical ? Math.max(12, letters.implicitHeight + scaledVerticalPadding * 2) : root.barSize
    dimmed: !root.status.connected
    tooltipText: {
      if (!root.service || !root.status.connected) return "Omagram · Telegram TUI"
      var messages = root.pendingContacts.map(function(c) { return c.name + ": " + c.unread })
      return "Omagram · " + root.status.selected.length + " VIP contacts selected\n"
        + (messages.length ? messages.join("\n") : "No unread VIP messages")
        + (root.pendingContacts.length ? "\nClick a letter: read message · Right-click: open Omagram" : "\nClick: choose contacts · Right-click: open Omagram")
    }
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.LeftButton) {
        if (!root.service) {
          launcher.running = true
        } else if (root.pendingContacts.length === 0) {
          root.currentView = "contacts"
          root.panelOpen = !root.panelOpen
        } else {
          if (root.panelOpen) {
            root.panelOpen = false
          } else {
            root.currentView = "messages"
            if (!root.activeContactId && root.pendingContacts.length > 0)
              root.activeContactId = root.pendingContacts[0].id
            root.panelOpen = true
          }
        }
      } else if (mouseButton === Qt.RightButton) {
        launcher.running = true
      } else if (mouseButton === Qt.MiddleButton && root.service) {
        root.service.refresh()
      }
    }

    Grid {
      id: letters
      anchors.centerIn: parent
      columns: root.vertical ? 1 : Math.max(1, root.pendingContacts.length)
      spacing: root.pendingContacts.length > 1 ? Style.space(2) : 0

      Text {
        visible: root.pendingContacts.length === 0
        text: button.text
        color: button.foreground
        font.family: button.fontFamily
        font.pixelSize: button.fontSize
      }

      Repeater {
        id: initials
        objectName: "contactInitials"
        model: root.pendingContacts
        delegate: Text {
          id: letterText
          required property var modelData
          text: Array.from(modelData.name.trim().toLocaleUpperCase())[0] || "?"
          textFormat: Text.PlainText
          color: Qt.hsla((0.58 + root.status.selected.indexOf(modelData.id) * 0.61803398875) % 1,
            0.7, Color.background.hslLightness < 0.5 ? 0.7 : 0.35, 1)
          font.family: button.fontFamily
          font.pixelSize: button.fontSize
          font.bold: true

          MouseArea {
            objectName: "letterMouseArea"
            anchors.fill: parent
            anchors.margins: -Style.space(2)
            cursorShape: Qt.PointingHandCursor
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            onClicked: function(mouse) {
              if (mouse.button === Qt.LeftButton) {
                root.openContactMessage(letterText.modelData)
              } else if (mouse.button === Qt.RightButton) {
                launcher.running = true
              } else if (mouse.button === Qt.MiddleButton && root.service) {
                root.service.refresh()
              }
            }
          }
        }
      }
    }
  }

  Process {
    id: launcher
    command: ["/usr/bin/omarchy-launch-or-focus-tui", "--app-id=org.omarchy.omagram", root.launcherPath]
  }

  Ui.KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.panelOpen
    focusTarget: root.currentView === "messages" ? messagesPanel.focusTarget : contactsPanel.focusTarget
    contentWidth: fittedContentWidth(Style.space(380))
    contentHeight: cappedContentHeight(Style.space(520))

    Item {
      anchors.fill: parent

      MessagesPanel {
        id: messagesPanel
        objectName: "messagesPanel"
        anchors.fill: parent
        visible: root.currentView === "messages"
        service: root.service
        contact: root.activeContact
        pendingContacts: root.pendingContacts
        vipColor: {
          if (!root.activeContact) return Color.foreground
          var idx = root.status.selected.indexOf(root.activeContact.id)
          return Qt.hsla((0.58 + (idx >= 0 ? idx : 0) * 0.61803398875) % 1,
            0.7, Color.background.hslLightness < 0.5 ? 0.7 : 0.35, 1)
        }
        onSelectContact: function(c) { root.activeContactId = c.id }
        onOpenContactsList: { root.currentView = "contacts" }
        onOpenOmagram: { root.close(); launcher.running = true }
        onDismiss: root.close()
      }

      ContactsPanel {
        id: contactsPanel
        objectName: "contactsPanel"
        anchors.fill: parent
        visible: root.currentView === "contacts"
        service: root.service
        hasPendingMessages: root.pendingContacts.length > 0
        onOpenMessages: {
          if (root.pendingContacts.length > 0) {
            root.activeContactId = root.pendingContacts[0].id
            root.currentView = "messages"
          }
        }
        onOpenOmagram: { root.close(); launcher.running = true }
        onDismiss: root.close()
      }
    }
  }

  IpcHandler {
    target: "omagram-bar-widget"
    function toggle(): void { root.panelOpen = !root.panelOpen }
  }
}
