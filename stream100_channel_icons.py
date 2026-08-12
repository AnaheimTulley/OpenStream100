"""OpenStream100: per-channel application icon resolution for the mixer display.

This module resolves small icon images that are drawn on top of each channel
column in the hardware mixer view.  Resolution order is:

    1. GTK icon theme lookup by ``application.id`` (e.g. ``org.videolan.VLC``)
    2. GTK icon theme lookup by ``application.name`` (lowercased, no spaces)
    3. Emoji fallback keyed to channel kind / property pair

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
    ("speaker", "default"): "\U0001F508",   # loudspeaker 📈
    ("speaker", "system"): "\U0001F508",
    ("application", "default"): "\U0001F3B6",  # musical note (generic output)

    # Microphone / input kinds
    ("microphone", "default"): "\U0001F3A4",  # microphone 🎤
    ("microphone", "system"): "\U0001F3A4",

    # Application sink-inputs (no specific property match — use generic)
    ("application", None): "\U0001F4BB",  # computer mouse 🖱️ (generic app)
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
        from gi.repository import GdkPixbuf, Gtk  # noqa: F401

        if not _try_import_pil():
            return None
        from PIL import Image as PILImage
        import io

        theme = Gtk.IconTheme.get_default()
        pixbuf = theme.load_icon(icon_name, size, 0)
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
# Application metadata (fallback when GTK is unavailable)
# ---------------------------------------------------------------------------

def _find_app_icon_via_desktop_entry(
    app_id: str, size: int = 24
) -> Optional["PIL.Image.Image"]:  # noqa: F821
    """Look up an icon via desktop-file-utils / freedesktop icon spec."""
    if not _try_import_pil():
        return None
    from PIL import Image as PILImage

    candidates = [
        Path(f"/usr/share/icons/hicolor/{size}x{size}/apps/{app_id}.png"),
        Path(f"/usr/share/icons/hicolor/{size}x{size}*/apps/{app_id}.png"),
        Path(f"/usr/share/applications/icons/{app_id}.png"),
        Path(f"/var/lib/flatpak/app/{app_id}/current/icon/{size}x{size}.png"),
    ]
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
    app_id = (
        str(channel.get("application_id", "")).lower()
        if channel.get("kind") == "application"
        else ""
    )

    # Try GTK icon theme first (fastest path when available)
    if _try_import_gtk():
        if app_id:
            # Strip .desktop extension if present
            base = Path(app_id).stem
            img = _load_icon_via_gtk(base, icon_size)
            if img is not None:
                return img

    # Try desktop-file lookup as a GTK-independent fallback
    if app_id:
        img = _find_app_icon_via_desktop_entry(app_id, icon_size)
        if img is not None:
            return img

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
