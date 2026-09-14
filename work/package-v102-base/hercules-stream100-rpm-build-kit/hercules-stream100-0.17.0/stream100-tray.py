#!/usr/bin/python3
"""GTK 3 AppIndicator companion for the GTK 4 OpenStream100 control panel."""

from __future__ import annotations

from pathlib import Path
import signal
import subprocess
import sys


APP_ID = "com.hercules.Stream100.Tray"
APP_DIR = Path(__file__).resolve().parent
CONTROL_RUNNER = APP_DIR / "run-stream100-control.sh"
SERVICE_NAME = "hercules-stream100.service"


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
    quit_item = Gtk.MenuItem(label="Quit tray icon")
    menu.append(quit_item)

    def refresh_status() -> bool:
        running = mixer_running()
        status_item.set_label("Mixer running" if running else "Mixer stopped")
        power_item.set_label("Stop mixer" if running else "Start mixer")
        restart_item.set_sensitive(running)
        return True

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
