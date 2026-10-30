import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import qs.Commons
import qs.Ui as Ui

ColumnLayout {
  id: root
  property var service: null
  property var contact: null
  property var pendingContacts: []
  property color vipColor: Color.foreground
  signal selectContact(var contact)
  signal openContactsList()
  signal openOmagram()
  signal dismiss()

  readonly property Item focusTarget: messagesList
  spacing: Style.spacing.sm
  Keys.onEscapePressed: root.dismiss()

  // Header
  RowLayout {
    Layout.fillWidth: true
    spacing: Style.spacing.sm

    Rectangle {
      width: Style.space(32)
      height: width
      radius: width / 2
      color: Util.alpha(root.vipColor, 0.2)
      border.color: root.vipColor
      border.width: 1

      Text {
        anchors.centerIn: parent
        text: root.contact ? (Array.from(root.contact.name.trim().toLocaleUpperCase())[0] || "?") : "?"
        color: root.vipColor
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }
    }

    ColumnLayout {
      Layout.fillWidth: true
      spacing: 0

      Text {
        Layout.fillWidth: true
        text: root.contact ? root.contact.name : "VIP message"
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.heading
        font.bold: true
        elide: Text.ElideRight
      }

      Text {
        Layout.fillWidth: true
        text: {
          if (!root.contact) return ""
          var info = root.contact.username ? "@" + root.contact.username : ""
          var unread = root.contact.unread > 0
            ? (root.contact.unread === 1 ? "1 unread message" : root.contact.unread + " unread messages")
            : "No unread messages"
          return info ? info + " · " + unread : unread
        }
        color: Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }
    }

    Ui.PanelActionButton {
      visible: root.contact && root.contact.unread > 0
      iconText: "󰄲"
      tooltipText: "Mark as read"
      focusable: true
      onClicked: if (root.service && root.contact) root.service.markRead(root.contact.id)
    }

    Ui.PanelActionButton {
      iconText: "󰒓"
      tooltipText: "Manage VIP list"
      focusable: true
      onClicked: root.openContactsList()
    }

    Ui.PanelActionButton {
      iconText: "󰅖"
      tooltipText: "Close"
      focusable: true
      onClicked: root.dismiss()
    }
  }

  // Multi-VIP switcher if more than 1 contact has unread messages
  RowLayout {
    Layout.fillWidth: true
    visible: root.pendingContacts.length > 1
    spacing: Style.spacing.xs

    Repeater {
      model: root.pendingContacts
      delegate: Rectangle {
        required property var modelData
        readonly property bool isSelected: root.contact && root.contact.id === modelData.id
        readonly property color chipColor: Qt.hsla(
          (0.58 + (root.service && root.service.status.selected ? root.service.status.selected.indexOf(modelData.id) : 0) * 0.61803398875) % 1,
          0.7, Color.background.hslLightness < 0.5 ? 0.7 : 0.35, 1)

        height: Style.space(26)
        implicitWidth: chipRow.implicitWidth + Style.spacing.sm * 2
        radius: Style.cornerRadius
        color: isSelected ? Util.alpha(chipColor, 0.25) : Util.alpha(Color.foreground, 0.06)
        border.color: isSelected ? chipColor : Color.popups.border
        border.width: 1

        RowLayout {
          id: chipRow
          anchors.centerIn: parent
          spacing: Style.spacing.xs

          Text {
            text: Array.from(modelData.name.trim().toLocaleUpperCase())[0] || "?"
            color: chipColor
            font.bold: true
            font.pixelSize: Style.font.caption
          }

          Text {
            text: modelData.name + (modelData.unread > 1 ? " (" + modelData.unread + ")" : "")
            color: isSelected ? Color.foreground : Color.muted
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }
        }

        MouseArea {
          anchors.fill: parent
          cursorShape: Qt.PointingHandCursor
          onClicked: root.selectContact(modelData)
        }
      }
    }
  }

  // Messages List
  ListView {
    id: messagesList
    objectName: "vipMessagesList"
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: Style.spacing.sm
    model: root.contact && root.contact.messages && root.contact.messages.length > 0 ? root.contact.messages : []
    Controls.ScrollBar.vertical: Controls.ScrollBar {}

    delegate: Rectangle {
      required property var modelData
      width: ListView.view.width
      implicitHeight: messageColumn.implicitHeight + Style.spacing.sm * 2
      radius: Style.cornerRadius
      color: modelData.out ? Util.alpha(Color.accent, 0.15) : Util.alpha(Color.foreground, 0.06)
      border.color: modelData.out ? Util.alpha(Color.accent, 0.4) : Color.popups.border
      border.width: 1

      ColumnLayout {
        id: messageColumn
        anchors.fill: parent
        anchors.margins: Style.spacing.sm
        spacing: Style.spacing.xs

        RowLayout {
          Layout.fillWidth: true
          visible: modelData.date && modelData.date.length > 0

          Item { Layout.fillWidth: true }

          Text {
            text: modelData.date || ""
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }

        Text {
          Layout.fillWidth: true
          text: modelData.text || ""
          color: Color.foreground
          wrapMode: Text.WordWrap
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          textFormat: Text.PlainText
        }
      }
    }

    Text {
      anchors.centerIn: parent
      width: parent.width - Style.spacing.md * 2
      visible: messagesList.count === 0
      text: root.contact && root.contact.unread > 0
        ? "Loading message …"
        : (root.contact ? "No unread messages from " + root.contact.name + "." : "No messages.")
      color: Color.muted
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.WordWrap
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }
  }

  // Footer Buttons
  RowLayout {
    Layout.fillWidth: true
    spacing: Style.spacing.sm

    Ui.Button {
      Layout.fillWidth: true
      text: "Open in Omagram"
      focusable: true
      onClicked: root.openOmagram()
    }

    Ui.Button {
      visible: root.contact && root.contact.unread > 0
      text: "Mark as read"
      focusable: true
      onClicked: if (root.service && root.contact) root.service.markRead(root.contact.id)
    }
  }
}
