"""Controller-screen preview built from OpenStream100's production renderer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

from stream100_system_monitor import SystemMonitor


APP_DIR = Path(__file__).resolve().parent
DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 272
PALETTE_BYTES = 512
CHUNK_BYTES = 4080


def load_renderer() -> ModuleType:
    path = APP_DIR / "stream100-mixer-alpha.py"
    spec = importlib.util.spec_from_file_location("stream100_preview_renderer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the controller display renderer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def decode_display_frame(frame: bytes, renderer: ModuleType):
    from PIL import Image

    if len(frame) != renderer.DISPLAY_MESSAGE_BYTES:
        raise RuntimeError("Preview renderer returned an invalid frame")
    palette = []
    for offset in range(0, PALETTE_BYTES, 2):
        value = int.from_bytes(frame[offset : offset + 2], "little")
        palette.append(
            (
                ((value >> 11) & 0x1F) * 255 // 31,
                ((value >> 5) & 0x3F) * 255 // 63,
                (value & 0x1F) * 255 // 31,
            )
        )
    packed = frame[PALETTE_BYTES:]
    pixels = bytearray(DISPLAY_WIDTH * DISPLAY_HEIGHT)
    for chunk, (x_offset, y_offset) in enumerate(renderer.DISPLAY_CHUNK_OFFSETS):
        cursor = chunk * CHUNK_BYTES
        for y in range(y_offset, DISPLAY_HEIGHT, 4):
            row = y * DISPLAY_WIDTH
            for x in range(x_offset, DISPLAY_WIDTH, 8):
                pixels[row + x] = packed[cursor]
                cursor += 1
    image = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT))
    image.putdata([palette[index] for index in pixels])
    return image


def draw_native_preview(
    image,
    renderer: ModuleType,
    channels: list[dict[str, str]],
    levels: list[float],
    show_meters: bool,
    meter_style: str,
    badge_style: str = "openstream",
) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    digit_rows = (
        (0b111, 0b101, 0b101, 0b101, 0b111),
        (0b010, 0b110, 0b010, 0b010, 0b111),
        (0b111, 0b001, 0b111, 0b100, 0b111),
        (0b111, 0b001, 0b111, 0b001, 0b111),
        (0b101, 0b101, 0b111, 0b001, 0b001),
        (0b111, 0b100, 0b111, 0b001, 0b111),
        (0b111, 0b100, 0b111, 0b101, 0b111),
        (0b111, 0b001, 0b001, 0b001, 0b001),
        (0b111, 0b101, 0b111, 0b101, 0b111),
        (0b111, 0b101, 0b111, 0b001, 0b111),
    )
    meter_top = 73
    # The 80px button overlay starts at y=192, but its visible top border is
    # twelve transparent inset rows lower. The native baseline sits on that
    # visible border, not on the overlay image's bounding box.
    meter_bottom = 204

    def draw_percentage_badge(channel: int, level: float, accent) -> None:
        """Reproduce the helper's firmware-owned 32x32 percentage object."""
        object_left = channel * 120 + 44
        object_top = 10
        badge = (
            (object_left + 5, object_top + 1),
            (object_left + 26, object_top + 1),
            (object_left + 27, object_top + 2),
            (object_left + 27, object_top + 23),
            (object_left + 26, object_top + 24),
            (object_left + 5, object_top + 24),
            (object_left + 4, object_top + 23),
            (object_left + 4, object_top + 2),
        )
        draw.polygon(badge, fill=(0, 0, 0))
        draw.rectangle(
            (object_left + 5, object_top + 2, object_left + 26, object_top + 23),
            fill=accent,
        )
        percentage = max(0, min(100, round(level * 100)))
        digits = [int(character) for character in str(percentage)]
        scale = 2
        glyph_width = 3 * scale
        gap = 1
        total_width = len(digits) * glyph_width + (len(digits) - 1) * gap
        glyph_left = object_left + (32 - total_width) // 2
        glyph_top = object_top + 8
        for digit_index, digit in enumerate(digits):
            digit_left = glyph_left + digit_index * (glyph_width + gap)
            for row, bits in enumerate(digit_rows[digit]):
                for column in range(3):
                    if bits & (1 << (2 - column)):
                        x = digit_left + column * scale
                        y = glyph_top + row * scale
                        draw.rectangle((x, y, x + 1, y + 1), fill=(255, 255, 255))

    def draw_native_meter(channel: int, level: float, accent) -> None:
        """Reproduce the resident Vumeter 1/2/4/3 surface geometry."""
        left = channel * 120
        top, bottom = meter_top, meter_bottom
        dim = tuple(8 + component * 12 // 100 for component in accent)
        side_levels = (level, min(1.0, level + 0.08))

        def draw_volume_marker(columns: tuple[tuple[int, int], ...]) -> None:
            # The firmware's independent 0x41 object is a vertical white
            # level column beside the right VU rail. It grows upwards from
            # the meter baseline with the knob's current volume setting.
            marker_top = bottom - round((bottom - top) * level)
            marker_x = columns[-1][1] + 2
            draw.line(
                (marker_x, marker_top, marker_x, bottom),
                fill=(255, 255, 255),
                width=2,
            )

        if meter_style == "classic":
            # Hardware capture: Classic uses two 12px square-ended rails,
            # inset closer to the channel centre and beginning at y=79.
            top = 79
            columns = ((left + 47, left + 58), (left + 62, left + 73))
            radius = 0
        elif meter_style == "segmented":
            # A rectified hardware capture shows native style 2 as a tall cap
            # followed by fifteen compact ladder segments on each side. The
            # four large blocks in Hercules' selector artwork are only a
            # thumbnail abstraction and are not the E053 firmware geometry.
            columns = ((left + 52, left + 59), (left + 63, left + 70))
            segment_top = 95
            segment_height = 6
            segment_pitch = 7
            activity_top, activity_bottom = 78, 198
            for side, (bar_left, bar_right) in enumerate(columns):
                active_top = activity_bottom - round(
                    (activity_bottom - activity_top + 1) * side_levels[side]
                )
                cap_color = accent if (78 + 92) // 2 >= active_top else dim
                draw.rounded_rectangle(
                    (bar_left, 78, bar_right, 92),
                    radius=1,
                    fill=cap_color,
                )
                for segment in range(15):
                    block_top = segment_top + segment * segment_pitch
                    block_bottom = block_top + segment_height - 1
                    block_center = (block_top + block_bottom) // 2
                    color = accent if block_center >= active_top else dim
                    draw.rectangle(
                        (bar_left, block_top, bar_right, block_bottom),
                        fill=color,
                    )
            marker_y = activity_bottom - round(
                (activity_bottom - activity_top) * level
            )
            marker_left = columns[-1][1] + 2
            draw.line(
                (marker_left, marker_y, marker_left + 10, marker_y),
                fill=(255, 255, 255),
                width=2,
            )
            return
        elif meter_style == "rounded":
            # The third visual option is native selector 4, but its artwork is
            # the official Vumeter 3 silhouette: medium rounded rails flow
            # into an always-coloured base across the bottom of the channel.
            columns = ((left + 50, left + 58), (left + 64, left + 72))
            radius = 4
        else:  # slim: fourth visual option / native selector 3
            # Hardware capture: Slim has two 5px rails with a wider 9px gap
            # and stops just above the visible button border.
            top, bottom = 79, 201
            columns = ((left + 52, left + 56), (left + 66, left + 70))
            radius = 2

        for side, (bar_left, bar_right) in enumerate(columns):
            active_top = bottom - round((bottom - top + 1) * side_levels[side])
            center_left = bar_left + radius
            center_right = bar_right - radius
            center_top = top + radius
            center_bottom = bottom - radius
            for y in range(top, bottom + 1):
                for x in range(bar_left, bar_right + 1):
                    inside = radius == 0
                    if not inside:
                        if center_left <= x <= center_right or center_top <= y <= center_bottom:
                            inside = True
                        else:
                            center_x = center_left if x < center_left else center_right
                            center_y = center_top if y < center_top else center_bottom
                            inside = (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2
                    if inside:
                        draw.point((x, y), fill=accent if y >= active_top else dim)
        draw_volume_marker(columns)

    channel_accents = [
        renderer.channel_color(channel, index)
        for index, channel in enumerate(channels)
    ]
    for index, (channel, level) in enumerate(zip(channels, levels)):
        accent = channel_accents[index]
        if badge_style == "openstream":
            draw_percentage_badge(index, level, accent)
        if not show_meters:
            continue
        draw_native_meter(index, max(0.0, min(1.0, level)), accent)

    if show_meters and meter_style == "rounded" and channel_accents:
        # Rounded's base is one continuous native silhouette. Blend either
        # side of every channel boundary so adjacent colours meld rather than
        # ending as four hard-edged rectangles.
        blend_radius = 10
        for x in range(DISPLAY_WIDTH):
            channel = min(len(channel_accents) - 1, x // 120)
            color = channel_accents[channel]
            for boundary_channel in range(len(channel_accents) - 1):
                boundary = (boundary_channel + 1) * 120
                if boundary - blend_radius <= x <= boundary + blend_radius:
                    amount = (x - (boundary - blend_radius)) / (2 * blend_radius)
                    before = channel_accents[boundary_channel]
                    after = channel_accents[boundary_channel + 1]
                    color = tuple(
                        round(before[component] * (1.0 - amount) + after[component] * amount)
                        for component in range(3)
                    )
                    break
            draw.line(
                (x, meter_bottom - 5, x, meter_bottom),
                fill=color,
            )


class DisplayPreview:
    def __init__(self) -> None:
        self.renderer = load_renderer()
        self.system_monitor = SystemMonitor()

    def render(
        self,
        *,
        mode: str,
        channels: list[dict[str, str]],
        background_image: Path | None,
        fullscreen_image: Path | None,
        notepad_text: str,
        notepad_style: dict[str, object],
        show_volume_meters: bool,
        meter_style: str,
        system_source_ids: list[str],
        button_actions: list[str],
        button_volume_presets: list[dict[str, int]],
        display_brightness: int,
        badge_style: str = "openstream",
    ):
        from PIL import ImageEnhance

        renderer = self.renderer
        levels = [0.50, 0.62, 0.38, 0.76]
        targets = [["preview"] for _index in range(4)]
        muted = [False] * 4
        button_leds = renderer.button_led_states(button_actions)
        if mode == "image":
            frame = renderer.render_fullscreen_image_display(
                channels, targets, muted, levels, fullscreen_image,
                button_leds=button_leds, display_brightness=display_brightness,
            )
        elif mode == "notepad":
            frame = renderer.render_notepad_display(
                channels, targets, muted, levels, notepad_text, notepad_style,
                button_leds=button_leds, display_brightness=display_brightness,
            )
        elif mode == "system":
            snapshot = self.system_monitor.sample_selected(system_source_ids)
            frame = renderer.render_system_monitor_display(
                snapshot, button_leds, button_actions, button_volume_presets,
                meter_style, display_brightness,
                background_image=background_image,
            )
            levels = list(snapshot.levels)
        else:
            try:
                streams_by_channel = renderer._resolve_channel_streams(
                    channels,
                    {"streams": renderer.discover_streams()},
                )
            except (OSError, RuntimeError, ValueError):
                # Empty per-channel stream lists still let the production
                # icon resolver use each saved assignment and its fallback.
                streams_by_channel = [[] for _channel in channels]
            frame = renderer.render_mixer_display(
                channels, targets, muted, levels,
                preview_levels=levels,
                native_overlay=True,
                background_image=background_image,
                button_leds=button_leds,
                meter_style=meter_style,
                show_volume_meters=show_volume_meters,
                meter_levels=[(level, min(1.0, level + 0.08)) for level in levels],
                display_brightness=display_brightness,
                streams_by_ch=streams_by_channel,
                button_actions=button_actions,
                button_volume_presets=button_volume_presets,
                badge_style=badge_style,
            )
        image = decode_display_frame(frame, renderer)
        if mode in {"mixer", "system"}:
            draw_native_preview(
                image, renderer,
                renderer.system_monitor_channels(snapshot) if mode == "system" else channels,
                levels,
                False if mode == "system" else show_volume_meters,
                meter_style,
                badge_style if mode == "mixer" else "openstream",
            )
        brightness = max(10, min(100, display_brightness)) / 100.0
        return ImageEnhance.Brightness(image).enhance(brightness)
