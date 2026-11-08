"""System tray icon: StatusNotifierItem + com.canonical.dbusmenu over Gio D-Bus.

Why hand-rolled instead of a library: ``pystray`` drags in an X11/GTK3
dependency and does not speak SNI on Wayland, and ``libappindicator`` would add
a C dependency the app otherwise doesn't need. This speaks the two protocols
directly, so the icon appears in Noctalia, Waybar, KDE Plasma and any GNOME
setup with the AppIndicator extension — and nowhere else needs any glue.

Both interfaces are small enough to read in one sitting:

* ``org.kde.StatusNotifierItem`` — properties (icon, tooltip, status) plus
  Activate / SecondaryActivate / ContextMenu.
* ``com.canonical.dbusmenu`` — a tree of items the host renders, with click
  events coming back as ``Event``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import APP_NAME, icons  # noqa: E402

SNI_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

SNI_XML = f"""
<node>
  <interface name="{SNI_IFACE}">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
  </interface>
</node>
"""

MENU_XML = f"""
<node>
  <interface name="{MENU_IFACE}">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{{sv}}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{{sv}})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{{sv}})"/>
      <arg name="removedProps" type="a(as)"/>
    </signal>
  </interface>
</node>
"""


@dataclass
class MenuItem:
    """One row of the tray menu."""

    id: int
    label: str = ""
    kind: str = "standard"  # standard | separator | submenu
    enabled: bool = True
    visible: bool = True
    action: str = ""
    children: list["MenuItem"] = field(default_factory=list)

    def props(self) -> dict:
        """DBusMenu properties. Only what the host actually needs is sent."""
        props: dict = {"visible": GLib.Variant("b", self.visible)}
        if self.kind == "separator":
            props["type"] = GLib.Variant("s", "separator")
            return props
        if self.kind == "submenu":
            props["children-display"] = GLib.Variant("s", "submenu")
        if self.label:
            props["label"] = GLib.Variant("s", self.label)
        props["enabled"] = GLib.Variant("b", self.enabled)
        return props

    def variant(self) -> GLib.Variant:
        """This item as ``(ia{sv}av)`` — for use inside an ``av`` of children."""
        return GLib.Variant("(ia{sv}av)", self.tree())

    def tree(self):
        """The item as a plain Python tuple.

        Two PyGObject traps live here, both verified against the real libglib:

        * A ready-made ``GLib.Variant`` cannot be nested inside an outer tuple
          (``GLib.Variant('(u(ia{sv}av))', (rev, built))`` raises "Expected
          GLib.Variant, but got tuple"), so the outer struct is assembled from
          plain tuples.
        * For the same reason the child list must be a *plain list of variants*,
          not a pre-built ``av`` variant, even though ``av`` built on its own
          works fine.
        """
        return (self.id, self.props(), [child.variant() for child in self.children])

    def walk(self, bucket: dict[int, "MenuItem"]) -> None:
        bucket[self.id] = self
        for child in self.children:
            child.walk(bucket)


class Tray:
    """Owns the bus name, the two exported objects, and the menu contents.

    The host calls nothing until it has registered us, so construction is cheap
    and safe even where no StatusNotifierWatcher exists (tray simply absent).
    """

    def __init__(
        self,
        app_id: str,
        title: str = APP_NAME,
        on_activate: Optional[Callable[[], None]] = None,
        on_secondary: Optional[Callable[[], None]] = None,
    ):
        self.app_id = app_id
        self.title = title
        self._on_activate = on_activate
        self._on_secondary = on_secondary

        self._conn: Optional[Gio.DBusConnection] = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._sni_ids: list[int] = []
        self._menu_ids: list[int] = []
        self._actions: dict[str, Callable[[], None]] = {}
        self._items: dict[int, MenuItem] = {}

        self.state = "none"
        self.tooltip = ""
        self.available = False

    # ── lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        Gio.bus_own_name(
            Gio.BusType.SESSION,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            self._on_name_lost,
        )

    def _on_bus_acquired(self, connection: Gio.DBusConnection, name: str) -> None:
        self._conn = connection
        try:
            self._sni_ids.append(self._register_sni(connection))
            self._menu_ids.append(self._register_menu(connection))
        except GLib.Error as exc:
            print(f"krbctl: tray registration failed: {exc}", flush=True)
            return
        self._register_with_watcher(connection)

    def _on_name_lost(self, *_args) -> None:
        self.available = False

    def _register_with_watcher(self, connection: Gio.DBusConnection) -> None:
        def done(conn, result, _data):
            try:
                conn.call_finish(result)
                self.available = True
            except GLib.Error as exc:
                # No tray host running: the app still works, it just has no icon.
                print(f"krbctl: no status notifier host ({exc.message})", flush=True)

        connection.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None,
            Gio.DBusCallFlags.NONE,
            4000,
            None,
            done,
            None,
        )

    # ── state updates ─────────────────────────────────────────

    def set_state(self, state: str, tooltip: str) -> None:
        changed_icon = state != self.state
        self.state = state
        self.tooltip = tooltip
        if self._conn is None:
            return
        if changed_icon:
            self._emit(SNI_IFACE, "NewIcon", None)
            self._emit(SNI_IFACE, "NewToolTip", None)
            self._emit_props_changed(
                SNI_IFACE,
                {"IconPixmap": self._prop_value("IconPixmap"), "ToolTip": self._prop_value("ToolTip")},
            )

    def set_menu(self, items: list[MenuItem], actions: dict[str, Callable[[], None]]) -> None:
        """Replace the menu wholesale; revisions let the host refresh."""
        self._items = {}
        for item in items:
            item.walk(self._items)
        self._actions = actions
        self._revision = getattr(self, "_revision", 1) + 1
        self._emit(MENU_IFACE, "LayoutUpdated", GLib.Variant("(ui)", (self._revision, 0)))

    def _emit(self, iface: str, signal: str, params: Optional[GLib.Variant]) -> None:
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(None, SNI_PATH if iface == SNI_IFACE else MENU_PATH, iface, signal, params)
        except GLib.Error:
            pass

    def _emit_props_changed(self, iface: str, props: dict) -> None:
        self._emit(
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (iface, props, [])),
        )

    # ── SNI ───────────────────────────────────────────────────

    def _prop_value(self, name: str):
        """The value for a SNI property, optionally wrapped as a variant."""
        pixmap = icons.sni_pixmap(self.state, 22)
        table = {
            "Category": ("s", "ApplicationStatus"),
            "Id": ("s", "krbctl"),
            "Title": ("s", self.title),
            "Status": ("s", "Active"),
            "IconName": ("s", "dialog-password-symbolic"),
            "IconPixmap": ("a(iiay)", pixmap),
            "OverlayIconName": ("s", ""),
            "AttentionIconName": ("s", ""),
            "AttentionIconPixmap": ("a(iiay)", []),
            "ToolTip": ("(sa(iiay)ss)", ("", pixmap, self.title, self.tooltip)),
            "ItemIsMenu": ("b", False),
            "Menu": ("o", MENU_PATH),
        }
        sig, value = table.get(name, ("s", ""))
        return GLib.Variant(sig, value)

    def _register_sni(self, connection: Gio.DBusConnection) -> int:
        node = Gio.DBusNodeInfo.new_for_xml(SNI_XML)

        def on_method(conn, sender, path, iface, method, params, invocation):
            if method == "Activate":
                if self._on_activate:
                    self._on_activate()
            elif method == "SecondaryActivate":
                if self._on_secondary:
                    self._on_secondary()
            # ContextMenu is left to the host: the Menu property already points
            # at our dbusmenu object, which is what every modern host uses.
            invocation.return_value(None)

        def on_get(conn, sender, path, iface, prop, *_extra):
            # PyGObject passes user_data to method_call but not to the property
            # getter, so accept both shapes rather than guess.
            return self._prop_value(prop)

        return connection.register_object(
            SNI_PATH, node.interfaces[0], on_method, on_get, None
        )

    # ── DBusMenu ──────────────────────────────────────────────

    def _register_menu(self, connection: Gio.DBusConnection) -> int:
        node = Gio.DBusNodeInfo.new_for_xml(MENU_XML)

        def on_method(conn, sender, path, iface, method, params, invocation):
            if method == "GetLayout":
                parent_id = params.unpack()[0]
                self._revision = getattr(self, "_revision", 1)
                root = self._items.get(parent_id) or MenuItem(0)
                invocation.return_value(
                    GLib.Variant("(u(ia{sv}av))", (self._revision, root.tree()))
                )
            elif method == "GetGroupProperties":
                ids = params.unpack()[0]
                out = []
                for item_id in ids:
                    item = self._items.get(item_id)
                    if item is not None:
                        out.append((item_id, item.props()))
                invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
            elif method == "GetProperty":
                item_id, name = params.unpack()
                item = self._items.get(item_id)
                value = item.props().get(name) if item else None
                invocation.return_value(GLib.Variant("(v)", (value or GLib.Variant("s", ""),)))
            elif method == "Event":
                item_id, event_id, _data, _ts = params.unpack()
                if event_id == "clicked":
                    item = self._items.get(item_id)
                    action = self._actions.get(item.action) if item else None
                    if action:
                        action()
                invocation.return_value(None)
            elif method == "EventGroup":
                events = params.unpack()[0]
                for item_id, event_id, _data, _ts in events:
                    if event_id == "clicked":
                        item = self._items.get(item_id)
                        action = self._actions.get(item.action) if item else None
                        if action:
                            action()
                invocation.return_value(GLib.Variant("(ai)", ([],)))
            elif method == "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (False,)))
            elif method == "AboutToShowGroup":
                invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            else:
                invocation.return_value(None)

        def on_get(conn, sender, path, iface, prop, *_extra):
            table = {
                "Version": ("u", 3),
                "TextDirection": ("s", "ltr"),
                "Status": ("s", "normal"),
                "IconThemePath": ("as", []),
            }
            sig, value = table.get(prop, ("s", ""))
            return GLib.Variant(sig, value)

        return connection.register_object(
            MENU_PATH, node.interfaces[0], on_method, on_get, None
        )
