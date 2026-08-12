"""OpenStream100: per-channel application icon resolution for the mixer display.

This module resolves small icon images that are drawn on top of each channel
column in the hardware mixer view.  Resolution order is:

    1. GTK icon theme lookup by ``application.id`` (e.g. ``org.videolan.VLC``)
    2. GTK icon theme lookup by ``application.name`` (lowercased, no spaces)
    3. Flatpak appstream catalog search for icons at any available size
    4. Emoji fallback keyed to channel kind / property pair

All public functions return either a :class:`PIL.Image.Image` or ``None``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Lazy imports — the mixer runs without GTK when used as a CLI tool
# ---------------------------------------------------------------------------

def _try_import_gtk() -> bool:
    """Return True when GTK4 + GdkPixbuf are available."""
    try:
        from gi.repository import GdkPixbuf, Gtk  # noqa: F401
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _try_import_pil() -> bool:
    """Return True when Pillow is available for icon rendering."""
    try:
        from PIL import Image as _PILImage  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Emoji fallback map — keyed by (channel_kind, property) or generic kind
# ---------------------------------------------------------------------------

_EMOJI_FALLBACKS: dict[tuple[str, str | None], str] = {
    # Speaker / output kinds
    ("speaker", "default"): "\U0001F508",   # loudspeaker 🔈
    ("speaker", "system"): "\U0001F508",
    ("speaker", None): "\U0001F508",        # generic speaker fallback

    # Microphone / input kinds
    ("microphone", "default"): "\U0001F3A4",  # microphone 🎤
    ("microphone", "system"): "\U0001F3A4",
    ("microphone", None): "\U0001F3A4",       # generic mic fallback

    # System / mixed kinds
    ("system", None): "\U0001F50A",           # bell 🔊 (generic system)

    # Application sink-inputs (no specific property match — use generic)
    ("application", "default"): "\U0001F3B6",  # musical note (generic output)
    ("application", None): "\U0001F4AC",      # desktop computer 🖥️ (generic app)
}


def _get_emoji(kind: str, prop: str | None = None) -> str | None:
    """Return an emoji string for the given kind / property pair."""
    if prop is not None:
        key = (kind, prop)
        if key in _EMOJI_FALLBACKS:
            return _EMOJI_FALLBACKS[key]
    # Try generic kind match
    if (kind, None) in _EMOJI_FALLBACKS:
        return _EMOJI_FALLBACKS[(kind, None)]
    return None


# ---------------------------------------------------------------------------
# GTK icon lookup helpers
# ---------------------------------------------------------------------------

def _load_icon_via_gtk(
    icon_name: str, size: int = 24
) -> Optional["PIL.Image.Image"]:  # noqa: F821 — forward ref
    """Attempt to load an icon from the current GTK icon theme.

    Returns a PIL Image (RGBA) or None on failure.
    """
    if not _try_import_gtk():
        return None
    try:
        import gi  # noqa: F401 — required before repository import
        gi.require_version('GdkPixbuf', '2.0')
        gi.require_version('Gtk', '4.0')
        from gi.repository import GdkPixbuf, Gtk  # noqa: F401

        if not _try_import_pil():
            return None
        from PIL import Image as PILImage
        import io

        theme = Gtk.IconTheme.get_default()
        # List available icons for debugging
        all_icons = theme.list_icons() if theme else []
        
        pixbuf = theme.load_icon(icon_name, size, 0)
        if pixbuf is None:
            # Try loading with GIcon fallback
            try:
                gicon_name = icon_name.replace("-", "-")
                from gi.repository import Gio
                gicon = Gio.ThemedIcon.new(gicon_name)
                if gicon:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_resource_at_scale(
                        f"/org/gtk/libgtk/icons/{gicon_name}.png",
                        size, size, True
                    )
            except Exception:
                pass
            if pixbuf is None:
                return None
        # Convert GdkPixbuf → PIL Image
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        n_channels = pixbuf.get_n_channels()
        data = pixbuf.get_pixels_array()
        if n_channels == 4:
            pil_img = PILImage.frombuffer(
                "RGBA", (w, h), data, "raw", "RGBA", 0, 1
            )
        else:
            pil_img = PILImage.frombuffer(
                "RGB", (w, h), data, "raw", "RGB", 0, 1
            )
        # Ensure RGBA for compositing
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        return pil_img
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Flatpak appstream icon name translation layer
# Maps well-known desktop icon names to their Flatpak appstream equivalents
# ---------------------------------------------------------------------------

_FLATPAK_ICON_NAME_MAP: dict[str, list[str]] = {
    # Chrome variants
    "google-chrome": ["com.google.Chrome", "google-chrome"],
    "chrome": ["com.google.Chrome", "chrome"],
    "chromium-browser": ["org.chromium.Chromium", "chromium-browser"],
    "chromium": ["org.chromium.Chromium", "chromium"],
    # Spotify
    "spotify": ["com.spotify.Client", "spotify"],
    # Firefox
    "firefox": ["org.mozilla.firefox", "firefox"],
    "firefox-esr": ["org.mozilla.firefox", "firefox-esr"],
    # Discord
    "discord": ["com.discordapp.Discord", "discord"],
    # Telegram
    "telegram-desktop": ["org.telegram.desktop", "telegram-desktop"],
    # Signal
    "signal": ["org.signal.Signal", "signal"],
    # Slack
    "slack": ["com.slack.Slack", "slack"],
    # Zoom
    "zoom": ["us.zoom.xos", "zoom"],
    # OBS
    "obs": ["org.obsproject.OBS", "obs"],
    "org-obss-obs": ["org.obsproject.OBS", "obs"],
    # Blender
    "blender": ["org.blender.Blender", "blender"],
    # GIMP
    "gimp": ["org.gimp.GIMP", "gimp"],
    # Inkscape
    "inkscape": ["org.inkscape.Inkscape", "inkscape"],
    # VLC
    "vlc": ["org.videolan.VLC", "vlc"],
    # Thunderbird
    "thunderbird": ["org.mozilla.Thunderbird", "thunderbird"],
    # Microsoft Teams
    "microsoft-teams": ["com.microsoft.Teams", "microsoft-teams"],
    # Microsoft Edge
    "microsoft-edge": ["com.microsoft.Edge", "microsoft-edge"],
    # Brave
    "brave-browser": ["com.brave.Browser", "brave-browser"],
    # Opera
    "opera": ["com.opera.Opera", "opera"],
    # Visual Studio Code
    "code": ["com.visualstudio.code", "code"],
    "vscodium": ["com.vscodium.codium", "vscodium"],
    # KDEnlive
    "kdenlive": ["org.kde.kdenlive", "kdenlive"],
    # Nautilus / Files
    "org-gnome-nautilus": ["org.gnome.Nautilus", "org-gnome-Nautilus"],
    "nautilus": ["org.gnome.Nautilus", "nautilus"],
    # GNOME apps
    "org-gnome-gedit": ["org.gnome.gedit", "gedit"],
    "gedit": ["org.gnome.gedit", "gedit"],
    "org-gnome-evolution": ["org.gnome.Evolution", "evolution"],
    "evolution": ["org.gnome.Evolution", "evolution"],
    # Calibre
    "calibre": ["calibre-gui", "calibre"],
}


def _get_flatpak_icon_candidates(icon_name: str) -> list[str]:
    """Return a list of candidate icon names to try in Flatpak appstream.

    Includes the original name plus any known Flatpak equivalents from the
    translation map.  The original name is always tried first to preserve
    existing behaviour for icons that already have the correct name.
    """
    lower = icon_name.lower()
    if lower in _FLATPAK_ICON_NAME_MAP:
        return _FLATPAK_ICON_NAME_MAP[lower]
    # Return just the original name if no translation exists
    return [icon_name]


# ---------------------------------------------------------------------------
# Flatpak appstream icon search helper
# ---------------------------------------------------------------------------

def _find_icon_in_flatpak_appstream(
    candidate_names: list[str], size: int = 24
) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Search flatpak appstream catalogs for icons matching any candidate name.

    Looks in both /var/lib/flatpak/appstream and /var/lib/flatpak/imports/appstream,
    finds PNG files at any available size, picks the smallest one >= requested size,
    and downscale it using Pillow.
    """
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage

    for base in (
        Path("/var/lib/flatpak/appstream"),
        Path("/var/lib/flatpak/imports/appstream"),
    ):
        if not base.exists():
            continue
        for collection in base.iterdir():
            if not collection.is_dir():
                continue
            for arch in collection.iterdir():
                if not arch.is_dir():
                    continue
                for candidate_name in candidate_names:
                    pngs = list(arch.rglob(f"{candidate_name}.png"))
                    if pngs:
                        # Pick the one with the smallest size >= requested size
                        best = None
                        best_size = float('inf')
                        for png in pngs:
                            try:
                                sz = int(png.parent.name.split("x")[0])
                                if sz >= size and sz < best_size:
                                    best = png
                                    best_size = sz
                            except (ValueError, IndexError):
                                continue
                        if best is not None:
                            img = PILImage.open(str(best)).convert("RGBA")
                            if img.size[0] != size:
                                img = img.resize((size, size), PILImage.LANCZOS)
                            return img
    return None


# ---------------------------------------------------------------------------
# Application metadata (fallback when GTK is unavailable)
# ---------------------------------------------------------------------------

def _find_app_icon_via_desktop_entry(
    app_id: str, size: int = 24
) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Look up an icon via desktop-file-utils / freedesktop icon spec."""
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage

    # Extract the base name (e.g., "firefox" from "firefox.desktop")
    base_name = Path(app_id).stem
    
    candidates = [
        Path(f"/usr/share/icons/hicolor/{size}x{size}/apps/{base_name}.png"),
        Path(f"/usr/share/icons/hicolor/{size}x{size}*/apps/{base_name}.png"),
        Path(f"/usr/share/icons/hicolor/scalable/apps/{base_name}.svg"),
        Path(f"/usr/share/applications/icons/{base_name}.png"),
        Path(f"/usr/share/pixmaps/{base_name}.png"),
        Path(f"/usr/share/pixmaps/{base_name}.svg"),
        Path(f"/var/lib/flatpak/app/{app_id}/current/icon/{size}x{size}.png"),
    ]
    
    # Also search all hicolor sizes
    for icon_size in [16, 24, 32, 48, 64, 96, 128, 256]:
        candidates.append(
            Path(f"/usr/share/icons/hicolor/{icon_size}x{icon_size}/apps/{base_name}.png")
        )
        candidates.append(
            Path(f"/usr/share/icons/hicolor/{icon_size}x{icon_size}*/apps/{base_name}.png")
        )
    for candidate in candidates:
        # Handle glob patterns
        if "*" in str(candidate):
            matches = list(candidate.parent.glob(candidate.name))
            if matches:
                candidate = matches[0]
        if candidate.exists():
            try:
                return PILImage.open(str(candidate)).convert("RGBA")
            except Exception:
                continue
    # Try desktop file icon key
    try:
        result = subprocess.run(
            ["desktop-file-read", f"/usr/share/applications/{app_id}.desktop"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Icon="):
                    icon_name = line[5:].strip()
                    candidate = Path(
                        f"/usr/share/icons/hicolor/{size}x{size}/apps/{icon_name}.png"
                    )
                    if candidate.exists():
                        return PILImage.open(str(candidate)).convert("RGBA")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Search all desktop files for one whose Name matches the app_id stem
    stem = Path(app_id).stem
    try:
        for desktop_file in Path("/usr/share/applications").glob("*.desktop"):
            result = subprocess.run(
                ["desktop-file-read", str(desktop_file)],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                name_key = None
                icon_key = None
                for line in result.stdout.splitlines():
                    if line.startswith("Name=") and name_key is None:
                        name_key = line[5:].strip()
                    elif line.startswith("Icon="):
                        icon_key = line[5:].strip()
                # Match against both the app_id stem and common variations
                if name_key and (
                    name_key.lower() == stem.lower()
                    or stem.lower().replace("-", "").replace(".", "") in name_key.lower().replace("-", "").replace(".", "")
                ):
                    if icon_key:
                        candidate = Path(
                            f"/usr/share/icons/hicolor/{size}x{size}/apps/{icon_key}.png"
                        )
                        if candidate.exists():
                            return PILImage.open(str(candidate)).convert("RGBA")
    except (FileNotFoundError, OSError):
        pass

    # Search flatpak appstream catalog for icons at any available size,
    # downscaling to the requested size if necessary.
    try:
        candidates_for_appstream = [app_id]
        stem = Path(app_id).stem
        candidates_for_appstream.extend([
            stem,  # e.g., "Chromium" from "org.chromium.Chromium"
            stem.lower(),  # e.g., "chromium"
            app_id.replace(".", "-"),  # e.g., "org-chromium-Chromium"
            app_id.replace(".", "_"),  # e.g., "org_chromium_Chromium"
        ])
        # For Chrome/Chromium specifically, add known icon names
        if stem.lower() in ("chromium", "chrome"):
            candidates_for_appstream.extend(["com.google.Chrome", "google-chrome", "chromium-browser"])
        # For Spotify
        if stem.lower() == "spotify":
            candidates_for_appstream.append("com.spotify.Client")

        img = _find_icon_in_flatpak_appstream(candidates_for_appstream, size)
        if img is not None:
            return img
    except (FileNotFoundError, OSError):
        pass

    return None


def _find_png_in_hicolor_apps(icon_name: str, size: int = 24) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Search all hicolor icon sizes for a PNG icon in the 'apps' directory."""
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage

    for icon_size in [size, 16, 24, 32, 48, 64, 96, 128, 256, 512]:
        candidate = Path(f"/usr/share/icons/hicolor/{icon_size}x{icon_size}/apps/{icon_name}.png")
        if candidate.exists():
            try:
                return PILImage.open(str(candidate)).convert("RGBA")
            except Exception:
                continue
    return None


def _find_svg_in_hicolor_apps(icon_name: str, size: int = 24) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Search all hicolor icon sizes for an SVG icon in the 'apps' directory."""
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage

    for icon_size in [size, 16, 24, 32, 48, 64, 96, 128, 256, 512]:
        svg_candidate = Path(f"/usr/share/icons/hicolor/{icon_size}x{icon_size}/apps/{icon_name}.svg")
        if svg_candidate.exists():
            try:
                return PILImage.open(str(svg_candidate)).convert("RGBA")
            except Exception:
                continue
    return None


def _find_app_icon_via_icon_name(icon_name: str, size: int = 24) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Look up an icon by its Icon= name from a .desktop file."""
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage

    # First try PNG via dedicated helper
    img = _find_png_in_hicolor_apps(icon_name, size)
    if img is not None:
        return img
    
    # Then try SVG via dedicated helper
    img = _find_svg_in_hicolor_apps(icon_name, size)
    if img is not None:
        return img
    
    # Try pixmaps
    for candidate_path in [
        Path(f"/usr/share/pixmaps/{icon_name}.png"),
        Path(f"/usr/share/pixmaps/{icon_name}.svg"),
        Path(f"/usr/share/applications/icons/{icon_name}.png"),
    ]:
        if candidate_path.exists():
            try:
                return PILImage.open(str(candidate_path)).convert("RGBA")
            except Exception:
                continue
    
    # Try flatpak appstream (with icon name translation)
    try:
        candidates = _get_flatpak_icon_candidates(icon_name)
        img = _find_icon_in_flatpak_appstream(candidates, size)
        if img is not None:
            return img
    except (FileNotFoundError, OSError):
        pass

    return None


def _find_app_icon_by_name(app_name: str, size: int = 24) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Search all .desktop files for one whose Name matches *app_name*, then
    return the icon from its Icon= key if it exists on disk.

    Prioritises exact stem matches over partial matches to avoid false positives
    (e.g. "Chromium" matching before "Chrome").  Well-known apps get explicit
    fallback names so the correct brand icon is always picked first.
    """
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage

    stem = app_name.lower().replace("-", "").replace(".", "").replace(" ", "")

    # Well-known apps with explicit desktop file stems and icon names — tried
    # before the generic Name= scan so the correct brand icon is always picked.
    # Keys exist in both spaced and non-spaced forms to match app_name after normalization.
    _WELL_KNOWN_APP_MAP: dict[str, list[tuple[str, str]]] = {
        "firefox": [("firefox", "firefox"), ("firefox-esr", "firefox-esr")],
        "firefoxdeveloperedition": [
            ("firefox-developer-edition", "firefox-developer-edition")
        ],
        "googlechrome": [("google-chrome", "google-chrome")],
        "googlechromealternate": [("google-chrome", "google-chrome")],
        "chromium": [
            ("chromium-browser", "chromium-browser"),
            ("chromium", "chromium-browser"),
            ("org.chromium.Chromium", "chromium-browser"),
        ],
        "chromiumbrowser": [("chromium-browser", "chromium-browser")],
        # Also add spaced variants for backward compatibility
        "google chrome": [("google-chrome", "google-chrome")],
        "chrome": [("google-chrome", "google-chrome")],
        "spotify": [("spotify", "spotify"), ("com.spotify.Client", "spotify")],
        "discord": [("discord", "discord")],
        "visual studio code": [("code", "vscodium"), ("codium", "vscodium")],
        "thunderbird": [("thunderbird", "thunderbird")],
        "vlc": [("vlc", "vlc")],
        "gimp": [("gimp", "gimp")],
        "nautilus": [("org.gnome.Nautilus", "org-gnome-Nautilus"), ("files", "folder-mine")],
        "firefox developer edition": [("firefox-developer-edition", "firefox-developer-edition")],
        "microsoft edge": [("microsoft-edge", "microsoft-edge")],
        "opera": [("opera", "opera")],
        "brave": [("brave-browser", "brave-browser")],
        "signal": [("org.signal.Signal", "signal"), ("signal", "signal")],
        "telegram": [("telegram-desktop", "telegram-desktop")],
        "slack": [("slack", "slack")],
        "zoom": [("us.zoom.xos", "zoom")],
        "teams": [("com.microsoft.Teams", "microsoft-teams"), ("teams", "microsoft-teams")],
        "obs": [("org.obss.OBS", "obs"), ("obs", "obs")],
        "kdenlive": [("org.kde.kdenlive", "kdenlive")],
        "gimp": [("gimp", "gimp")],
        "inkscape": [("inkscape", "inkscape")],
        "blender": [("org.blender.Blender", "blender")],
        "gedit": [("org.gnome.gedit", "gedit")],
        "evolution": [("org.gnome.Evolution", "evolution")],
        "calibre": [("calibre-gui", "calibre")],
        "zoom": [("us.zoom.xos", "zoom")],
    }

    # --- Phase 0: well-known explicit fallbacks (highest priority) ---
    if stem in _WELL_KNOWN_APP_MAP:
        for desktop_stem, icon_name in _WELL_KNOWN_APP_MAP[stem]:
            img = _find_app_icon_via_icon_name(icon_name, size)
            if img is not None:
                return img

    # Chrome/Chromium have well-known desktop file stems and icon names; list
    # them explicitly too (may overlap with _WELL_KNOWN_APP_MAP but kept for
    # backward compatibility).
    chrome_fallbacks: list[tuple[str, str]] | None = None
    if stem in ("chrome", "chromium", "googlechrome", "googlechromium"):
        chrome_fallbacks = [
            ("google-chrome", "google-chrome"),
            ("google-chrome-wrapper", "google-chrome"),
            ("chromium-browser", "chromium-browser"),
            ("chromium-browser-wrapper", "chromium-browser"),
            ("chromium", "chromium-browser"),
            ("org.chromium.Chromium", "chromium-browser"),
            ("Chromium", "chromium-browser"),
        ]

    # --- Phase 1: exact stem match on desktop file name (highest priority) ---
    try:
        for desktop_file in Path("/usr/share/applications").glob("*.desktop"):
            result = subprocess.run(
                ["desktop-file-read", str(desktop_file)],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                continue
            name_key = None
            icon_key = None
            for line in result.stdout.splitlines():
                if line.startswith("Name=") and name_key is None:
                    name_key = line[5:].strip()
                elif line.startswith("Icon="):
                    icon_key = line[5:].strip()
            if not name_key or not icon_key:
                continue
            name_clean = name_key.lower().replace("-", "").replace(".", "").replace(" ", "")
            desktop_stem = desktop_file.stem.lower().replace("-", "").replace(".", "")

            # Exact stem match on either side
            if stem == name_clean or stem == desktop_stem:
                img = _find_app_icon_via_icon_name(icon_key, size)
                if img is not None:
                    return img
    except (FileNotFoundError, OSError):
        pass

    # --- Phase 2: Chrome/Chromium explicit fallbacks ---
    if chrome_fallbacks:
        for desktop_stem, icon_name in chrome_fallbacks:
            img = _find_app_icon_via_icon_name(icon_name, size)
            if img is not None:
                return img

    # --- Phase 3: search all .desktop files for Name= match using strict ---
    # matching.  Prefer *exact* clean-name matches first (highest precision),
    # then fall back to stem-substring matches only when the stem is long
    # enough (>= 5 chars) to avoid false positives like "Chrome" matching
    # before "Chromium".
    try:
        desktop_files = sorted(
            Path("/usr/share/applications").glob("*.desktop"),
            key=lambda f: len(f.stem),  # shortest stems first (more specific)
        )
        # First pass: exact Name= match (cleaned)
        for desktop_file in desktop_files:
            result = subprocess.run(
                ["desktop-file-read", str(desktop_file)],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                continue
            name_key = None
            icon_key = None
            for line in result.stdout.splitlines():
                if line.startswith("Name=") and name_key is None:
                    name_key = line[5:].strip()
                elif line.startswith("Icon="):
                    icon_key = line[5:].strip()
            if not name_key or not icon_key:
                continue
            name_clean = name_key.lower().replace("-", "").replace(".", "").replace(" ", "")
            if stem == name_clean:
                img = _find_app_icon_via_icon_name(icon_key, size)
                if img is not None:
                    return img
    except (FileNotFoundError, OSError):
        pass

    # Second pass: substring match only for stems >= 5 chars (reduces false positives)
    try:
        desktop_files = sorted(
            Path("/usr/share/applications").glob("*.desktop"),
            key=lambda f: len(f.stem),
        )
        for desktop_file in desktop_files:
            result = subprocess.run(
                ["desktop-file-read", str(desktop_file)],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                continue
            name_key = None
            icon_key = None
            for line in result.stdout.splitlines():
                if line.startswith("Name=") and name_key is None:
                    name_key = line[5:].strip()
                elif line.startswith("Icon="):
                    icon_key = line[5:].strip()
            if not name_key or not icon_key:
                continue
            name_clean = name_key.lower().replace("-", "").replace(".", "").replace(" ", "")
            # Only match if stem is >= 5 chars AND it's a substring match
            if len(stem) >= 5 and (stem in name_clean):
                img = _find_app_icon_via_icon_name(icon_key, size)
                if img is not None:
                    return img
    except (FileNotFoundError, OSError):
        pass

    # --- Phase 4: search Flatpak appstream by application name ---
    try:
        candidate_names = [stem]
        # Try common variations
        candidate_names.extend([
            app_name.replace(" ", "-"),
            app_name.replace(" ", "_"),
            app_name.lower(),
        ])
        if stem in ("chrome", "chromium", "googlechrome"):
            candidate_names.extend(["google-chrome", "chromium-browser"])
        if stem == "spotify":
            candidate_names.append("com.spotify.Client")

        img = _find_icon_in_flatpak_appstream(candidate_names, size)
        if img is not None:
            return img
    except (FileNotFoundError, OSError):
        pass

    # --- Phase 5: Chrome/Chromium — try every known icon name variation via GTK ---
    if stem in ("chrome", "chromium", "googlechrome"):
        chrome_icon_variations = [
            "google-chrome", "google-chrome-wrapper", "googichrome",
            "chromium-browser", "chromium", "Chromium", "Chromium-icon",
            "ChromiumDesktop", "chrome", "browser-chrome",
        ]
        for icon_name in chrome_icon_variations:
            img = _find_png_in_hicolor_apps(icon_name, size)
            if img is not None:
                return img
            # Also try scalable SVG
            img = _find_svg_in_hicolor_apps(icon_name, size)
            if img is not None:
                return img

    # --- Phase 6: search /usr/share/pixmaps as final fallback ---
    if app_name:
        base = Path(app_name).stem
        for candidate_path in [
            Path(f"/usr/share/pixmaps/{base}.png"),
            Path(f"/usr/share/pixmaps/{base}.svg"),
            Path(f"/usr/share/pixmaps/{base}-icon.png"),
        ]:
            if candidate_path.exists():
                try:
                    return PILImage.open(str(candidate_path)).convert("RGBA")
                except Exception:
                    continue

    return None


# ---------------------------------------------------------------------------
# Emoji fallback rendering — draws emoji onto a PIL Image
# ---------------------------------------------------------------------------

def _render_emoji_icon(
    emoji: str, size: int = 24
) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Render an emoji character onto a small RGBA PIL Image."""
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage, ImageDraw, ImageFont

    img = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        # Try several emoji-friendly fonts
        font_paths = [
            "/usr/share/fonts/google-noto-emoji-fonts/NotoEmoji-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf",
            "/usr/share/fonts/google-noto-color-emoji/noto-color-emoji.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        font = None
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, size - 4)
                break
            except (IOError, OSError):
                continue
        if font is None:
            font = ImageFont.load_default()
    except (IOError, OSError):
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), emoji, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # Center the emoji in the square: ignore baseline offsets for proper centering.
    tx = (size - tw) // 2
    ty = (size - th) // 2
    draw.text((tx, ty), emoji, font=font, fill=(255, 255, 255, 220))
    return img


# ---------------------------------------------------------------------------
# Public API — these are the functions imported by stream100-mixer-alpha.py
# ---------------------------------------------------------------------------

def load_channel_icon(
    channel: dict[str, Any],
    streams: list[dict[str, Any]],
    icon_size: int = 24,
) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Resolve and return an icon image for the given channel.

    Returns None when no icon can be found — the caller should fall back to
    :func:`get_icon_name` + :func:`load_emoji_fallback`.
    """
    # DEBUG: stream100-icon-logger — logs resolution path to /tmp/stream100_icon_debug.log
    _icon_debug_fh = None
    try:
        _icon_debug_fh = open("/tmp/stream100_icon_debug.log", "a", encoding="utf-8")
        _icon_debug_fh.write(
            f"ICON channel={channel.get('label','')} kind={channel.get('kind','')} "
            f"app_id={channel.get('application_id','<none>')} streams_len={len(streams)}\n"
        )
        for s in streams:
            _icon_debug_fh.write(
                f"  stream label={s.get('label','')} props.app.id={s.get('props',{}).get('application.id','<none>')} "
                f"props.app.name={s.get('props',{}).get('application.name','<none>')}\n"
            )
    except Exception:
        pass

    def _write_icon_debug(msg: str) -> None:
        try:
            if _icon_debug_fh is not None:
                _icon_debug_fh.write(msg + "\n")
                _icon_debug_fh.flush()
        except Exception:
            pass


    app_id = (
        str(channel.get("application_id", "")).lower()
        if channel.get("kind") == "application"
        else ""
    )

    resolved_icon = None

    # 1. Try GTK icon theme lookup (fastest when available)
    if _try_import_gtk() and app_id:
        base = Path(app_id).stem
        _write_icon_debug(f"TRY gtk icon '{base}'")
        img = _load_icon_via_gtk(base, icon_size)
        if img is not None:
            _write_icon_debug(f"FOUND gtk icon '{base}'")
            return img
        _write_icon_debug(f"gtk icon NOT FOUND for '{base}', trying fallbacks")
        # Try common fallback names derived from the application ID
        for candidate in (base.replace("-", "_"), base.replace(".", "-"), f"application-{base}", base + "-icon"):
            _write_icon_debug(f"TRY gtk icon '{candidate}'")
            img = _load_icon_via_gtk(candidate, icon_size)
            if img is not None:
                _write_icon_debug(f"FOUND gtk icon '{candidate}'")
                return img

    # 2. Try direct desktop entry / icon name lookup by application ID
    if app_id:
        base = Path(app_id).stem
        _write_icon_debug(f"TRY desktop_entry app_id='{app_id}'")
        img = _find_app_icon_via_desktop_entry(app_id, icon_size)
        if img is not None:
            _write_icon_debug(f"FOUND desktop_entry icon for '{app_id}'")
            return img
        if "." in app_id:
            _write_icon_debug(f"TRY icon_name app_id='{app_id}'")
            img = _find_app_icon_via_icon_name(app_id, icon_size)
            if img is not None:
                _write_icon_debug(f"FOUND icon_name for '{app_id}'")
                return img

    # 3. Try resolving icons by application.name from streams
    if channel.get("kind") == "application" and streams:
        _write_icon_debug(f"TRY stream-based resolution for channel '{channel.get('label')}'")
        for s in streams:
            app_name = str(s.get("props", {}).get("application.name", "")).strip()
            app_id_stream = str(s.get("props", {}).get("application.id", "")).strip()
            
            # First try by application.id from stream (more specific)
            if app_id_stream:
                _write_icon_debug(f"  TRY stream app_id='{app_id_stream}'")
                img = _find_app_icon_via_desktop_entry(app_id_stream, icon_size)
                if img is not None:
                    _write_icon_debug(f"  FOUND desktop_entry from stream '{app_id_stream}'")
                    return img
                img = _find_app_icon_via_icon_name(app_id_stream, icon_size)
                if img is not None:
                    _write_icon_debug(f"  FOUND icon_name from stream '{app_id_stream}'")
                    return img
            
            # Then try by application.name
            if app_name:
                _write_icon_debug(f"  TRY stream app_name='{app_name}'")
                img = _find_app_icon_by_name(app_name, icon_size)
                if img is not None:
                    _write_icon_debug(f"  FOUND icon from app_name '{app_name}'")
                    return img

    # 4. Try searching all .desktop files for a Name= match against the channel label
    if channel.get("kind") == "application" and not app_id:
        label = str(channel.get("label", "")).strip()
        if label:
            _write_icon_debug(f"TRY label-based resolution for '{label}'")
            img = _find_app_icon_by_name(label, icon_size)
            if img is not None:
                _write_icon_debug(f"FOUND icon from label '{label}'")
                return img

    _write_icon_debug("NO ICON FOUND — falling back to emoji")
    return None


def get_icon_name(
    channel: dict[str, Any],
    streams: list[dict[str, Any]],
) -> str | None:
    """Return an emoji key name for the given channel.

    This is used when no icon image can be resolved — the caller should pass
    this to :func:`load_emoji_fallback`.
    """
    kind = channel.get("kind", "")
    prop = channel.get("property", None) or (
        channel.get("value", None)  # fallback: value acts as property for sink-inputs
    )
    emoji = _get_emoji(kind, prop)
    if emoji is not None:
        return emoji
    # Try to extract an application name from streams
    if kind == "application" and streams:
        for s in streams:
            app_name = str(s.get("props", {}).get("application.name", "")).lower()
            if app_name:
                return f"\U0001F4BB {app_name}"  # computer mouse + name
    return None


def load_emoji_fallback(
    icon_name: str | None,
    icon_size: int = 24,
) -> "PIL.Image.Image":  # noqa: F821 — always returns an image
    """Render and return an emoji fallback icon.

    Returns a small RGBA PIL Image even when *icon_name* is ``None`` (defaults
    to a generic placeholder).
    """
    if icon_name is None:
        emoji = "\u2699"  # gear / settings
    else:
        # Extract the first Unicode character (skip any leading spaces)
        stripped = icon_name.lstrip()
        emoji = stripped[0] if stripped else "\u2699"

    img = _render_emoji_icon(emoji, icon_size)
    if img is not None:
        return img
    # Absolute last resort — draw a simple circle
    from PIL import Image as PILImage, ImageDraw

    img = PILImage.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = icon_size // 2 - 1
    draw.ellipse(
        (1, 1, 2 * r + 1, 2 * r + 1),
        fill=(60, 72, 90, 200),
        outline=(18, 25, 34, 255),
    )
    return img


# ---------------------------------------------------------------------------
# CLI helper — list available icons for a given application ID
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    test_channel = {
        "kind": "application",
        "label": sys.argv[1] if len(sys.argv) > 1 else "Firefox",
        "value": "0",
        "property": "sink_input_id",
        "application_id": (
            sys.argv[2] if len(sys.argv) > 2 else "firefox"
        ),
    }

    icon = load_channel_icon(test_channel, [], icon_size=24)
    print(f"Image icon: {'found' if icon else 'None'}", file=sys.stderr)

    name = get_icon_name(test_channel, [])
    emoji_img = load_emoji_fallback(name, icon_size=24)
    print(f"Emoji fallback: {name!r} → image size {emoji_img.size}", file=sys.stderr)
