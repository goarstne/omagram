import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import qs.Commons
import qs.Ui as Ui

ColumnLayout {
  id: root
  property var service: null
  readonly property var status: service ? service.status : ({connected: false, contacts: [], selected: [], unread: 0, error: "Loading Omagram …"})
  readonly property Item focusTarget: search
  readonly property var visibleContacts: status.contacts.filter(function(c) {
    var query = search.text.trim().toLocaleLowerCase()
    return (!onlySelected.checked || root.status.selected.indexOf(c.id) >= 0)
      && (!query || (c.name + " @" + c.username).toLocaleLowerCase().indexOf(query) >= 0)
  })
  property bool hasPendingMessages: false
  signal openMessages()
  signal openOmagram()
  signal dismiss()
  spacing: Style.spacing.sm
  Keys.onEscapePressed: root.dismiss()

  RowLayout {
    Layout.fillWidth: true
    Text {
      text: "VIPgram"
      color: Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.heading
      font.bold: true
      Layout.fillWidth: true
    }
    Ui.PanelActionButton {
      visible: root.hasPendingMessages
      iconText: "󰍡"
      tooltipText: "Show unread VIP messages"
      focusable: true
      onClicked: root.openMessages()
    }
    Ui.PanelActionButton {
      iconText: "󰑐"
      tooltipText: "Refresh contacts / reconnect"
      focusable: true
      onClicked: if (root.service) root.service.refresh()
    }
    Ui.PanelActionButton {
      iconText: "󰅖"
      tooltipText: "Close"
      focusable: true
      onClicked: root.dismiss()
    }
  }

  Text {
    Layout.fillWidth: true
    textFormat: Text.PlainText
    text: root.status.error || (root.status.selected.length + " selected · " + root.status.unread + " unread")
    color: root.status.error ? Color.urgent : Color.muted
    wrapMode: Text.WordWrap
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  Ui.TextField {
    id: search
    objectName: "contactSearch"
    Layout.fillWidth: true
    placeholderText: "Search name or @username …"
    Accessible.name: "Search contacts"
    Keys.onDownPressed: { list.forceActiveFocus(); list.currentIndex = 0 }
  }

  Ui.Toggle {
    id: onlySelected
    objectName: "selectedFilter"
    Layout.fillWidth: true
    label: "Selected contacts only"
    checked: false
    onClicked: checked = !checked
  }

  ListView {
    id: list
    objectName: "contactList"
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: Style.spacing.xs
    model: root.visibleContacts
    currentIndex: -1
    keyNavigationEnabled: true
    Controls.ScrollBar.vertical: Controls.ScrollBar {}
    Keys.onSpacePressed: if (currentItem && root.service) root.service.select(currentItem.modelData.id)
    Keys.onReturnPressed: if (currentItem && root.service) root.service.select(currentItem.modelData.id)
    delegate: Ui.Toggle {
      required property var modelData
      width: ListView.view.width
      label: modelData.name
      description: (modelData.username ? "@" + modelData.username + " · " : "")
        + (modelData.unread > 0 ? modelData.unread + " unread" : "No unread messages")
      checked: root.status.selected.indexOf(modelData.id) >= 0
      hasCursor: ListView.isCurrentItem && list.activeFocus
      Accessible.role: Accessible.CheckBox
      Accessible.name: label
      Accessible.checked: checked
      onClicked: if (root.service) root.service.select(modelData.id)
    }

    Text {
      anchors.centerIn: parent
      width: parent.width
      visible: list.count === 0
      text: root.status.connected ? "No matching contacts."
        : (root.status.error.indexOf("Loading") >= 0 ? "Loading contacts …" : "Not connected to Telegram.")
      color: Color.muted
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.WordWrap
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }
  }

  Text {
    Layout.fillWidth: true
    text: "Pick the people whose direct messages you want to see here. Your selection is saved automatically."
    color: Color.muted
    wrapMode: Text.WordWrap
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  Ui.Button {
    Layout.fillWidth: true
    text: "Open Omagram"
    focusable: true
    onClicked: root.openOmagram()
  }
}
