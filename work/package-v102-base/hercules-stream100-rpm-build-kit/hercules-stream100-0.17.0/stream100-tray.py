#!/usr/bin/python3
"""GTK 3 AppIndicator companion for the GTK 4 OpenStream100 control panel."""

from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


APP_ID = "com.hercules.Stream100.Tray"
APP_DIR = Path(__file__).resolve().parent
CONTROL_RUNNER = APP_DIR / "run-stream100-control.sh"
SERVICE_NAME = "hercules-stream100.service"
CONFIG_PATH = Path.home() / ".config" / "hercules-stream100" / "config.json"
DISPLAY_MODES = (
    ("mixer", "Mixer"),
    ("image", "Full-screen image"),
    ("notepad", "Notepad"),
    ("system", "System monitor"),
)


def read_config() -> dict:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("The mixer configuration must be a JSON object")
    return payload


def load_display_mode() -> str:
    mode = read_config().get("display_mode", "mixer")
    return mode if mode in tuple(key for key, _label in DISPLAY_MODES) else "mixer"


def select_display_mode(mode: str) -> None:
    if mode not in dict(DISPLAY_MODES):
        raise ValueError("Unsupported display mode")
    payload = read_config()
    if payload.get("display_mode", "mixer") == mode:
        return
    payload["display_mode"] = mode
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=CONFIG_PATH.parent,
            prefix="config-tray-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, indent=2) + "\n")
        temporary.replace(CONFIG_PATH)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    # Apply the mode without starting a mixer that the user has stopped.
    result = systemctl("try-restart", SERVICE_NAME)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "Display mode saved, but the mixer could not restart"
        )


def systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def mixer_running() -> bool:
    return systemctl("is-active", "--quiet", SERVICE_NAME).returncode == 0


def main() -> int:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except ValueError:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as error:
        print(
            "OpenStream100 tray support requires GTK 3 and Ayatana AppIndicator.",
            file=sys.stderr,
        )
        print(error, file=sys.stderr)
        return 2

    icon_path = APP_DIR / "com.hercules.Stream100.svg"
    if icon_path.is_file():
        icon_name = icon_path.stem
        icon_theme_path = str(APP_DIR)
    else:
        icon_name = "com.hercules.Stream100"
        icon_theme_path = ""
    indicator = AppIndicator.Indicator.new(
        APP_ID,
        icon_name,
        AppIndicator.IndicatorCategory.HARDWARE,
    )
    if icon_theme_path:
        indicator.set_icon_theme_path(icon_theme_path)
        indicator.set_icon_full(icon_name, "OpenStream100")
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
    indicator.set_title("OpenStream100")

    menu = Gtk.Menu()
    open_item = Gtk.MenuItem(label="Open OpenStream100")
    menu.append(open_item)
    menu.append(Gtk.SeparatorMenuItem())
    status_item = Gtk.MenuItem(label="Mixer status")
    status_item.set_sensitive(False)
    menu.append(status_item)
    power_item = Gtk.MenuItem(label="Start mixer")
    menu.append(power_item)
    restart_item = Gtk.MenuItem(label="Restart mixer")
    menu.append(restart_item)
    menu.append(Gtk.SeparatorMenuItem())
    mode_item = Gtk.MenuItem(label="Display mode")
    mode_menu = Gtk.Menu()
    mode_item.set_submenu(mode_menu)
    menu.append(mode_item)
    mode_items = {}
    group = None
    for mode, label in DISPLAY_MODES:
        item = Gtk.RadioMenuItem.new_with_label(group, label)
        group = item.get_group()
        mode_menu.append(item)
        mode_items[mode] = item
    menu.append(Gtk.SeparatorMenuItem())
    quit_item = Gtk.MenuItem(label="Quit tray icon")
    menu.append(quit_item)

    refreshing_modes = False

    def refresh_status() -> bool:
        nonlocal refreshing_modes
        running = mixer_running()
        status_item.set_label("Mixer running" if running else "Mixer stopped")
        power_item.set_label("Stop mixer" if running else "Start mixer")
        restart_item.set_sensitive(running)
        refreshing_modes = True
        try:
            mode_items[load_display_mode()].set_active(True)
        except (OSError, ValueError) as error:
            print(f"Could not read display mode: {error}", file=sys.stderr)
        finally:
            refreshing_modes = False
        return True

    def change_display_mode(item, mode: str) -> None:
        if refreshing_modes or not item.get_active():
            return
        try:
            select_display_mode(mode)
        except (OSError, ValueError, RuntimeError) as error:
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="Could not change display mode",
            )
            dialog.format_secondary_text(str(error))
            dialog.run()
            dialog.destroy()
        refresh_status()

    def open_control_panel(_item) -> None:
        if not CONTROL_RUNNER.is_file():
            return
        subprocess.Popen(
            [str(CONTROL_RUNNER)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def toggle_mixer(_item) -> None:
        systemctl("stop" if mixer_running() else "start", SERVICE_NAME)
        refresh_status()

    def restart_mixer(_item) -> None:
        systemctl("restart", SERVICE_NAME)
        refresh_status()

    open_item.connect("activate", open_control_panel)
    power_item.connect("activate", toggle_mixer)
    restart_item.connect("activate", restart_mixer)
    for mode, item in mode_items.items():
        item.connect("toggled", change_display_mode, mode)
    quit_item.connect("activate", lambda _item: Gtk.main_quit())
    menu.show_all()
    indicator.set_menu(menu)
    refresh_status()
    GLib.timeout_add_seconds(2, refresh_status)
    signal.signal(signal.SIGTERM, lambda _signal, _frame: GLib.idle_add(Gtk.main_quit))
    signal.signal(signal.SIGINT, lambda _signal, _frame: GLib.idle_add(Gtk.main_quit))
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
