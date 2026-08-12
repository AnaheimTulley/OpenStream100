# OpenStream100 — Working Context (updated 2026-08-11)

## Current Version & State
- **Package version:** 0.15.9-1.fc44.x86_64 (installed and hardware-validated)
- **Source tree:** `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.15.9/`
- **RPM build kit:** `work/package-v102-base/hercules-stream100-rpm-build-kit/`
- **Installed files:** `/usr/libexec/hercules-stream100/`

## What Was Done (2026-08-11)

### 1. Custom Button-Label Overlays — Roadmap #12 Extension
- Added **Custom** alongside the built-in Boxes, Basic, and Glass overlay styles
- Added **Import custom overlay…** to the GTK control panel
- Imports require a valid PNG at exactly 480x80 pixels and are converted to RGBA
- The app keeps its own durable copy at `~/.config/hercules-stream100/button-overlay-custom.png`
- A successful import automatically selects Custom; users apply the change with **Apply changes**
- Removing the custom overlay automatically returns the selection to Boxes
- The mixer detects overlay file changes and safely falls back to Boxes if custom artwork is missing or invalid

### 2. User Design Template
- Packaged the supplied `template button overlay.png` as `button_labels_overlay_template.png`
- Added **Save template…** so users can export an editable copy from the control panel
- Verified the packaged and supplied templates are byte-identical 480x80 RGBA PNGs

### 3. Version, Packaging, and Installation
- Version bumped from 0.14.8 to 0.14.9
- RPM spec, build script, source archive, AppStream metadata, man page, and README updated
- `hercules-stream100-0.14.9-1.fc44.x86_64.rpm` built and installed successfully
- Display and mixer services restarted and confirmed active with a complete hardware startup sequence
- Custom import/export, validation, persistence, cache refresh, and fallback behavior tested successfully

### 4. Dual-Channel Stereo Visualisers — Roadmap #13
- Version 0.15.0 now requests two interleaved float channels from each PipeWire-Pulse monitor
- Calculates separate left and right 40 ms peaks for every monitored stream or output device
- Aggregates and smooths each side independently for all four visible mixer channels
- Added the `S1C3` 32-byte metadata revision, carrying eight 4-bit activity levels without increasing the frame size
- Retains decoder compatibility with older `S1C2` mono frames by mirroring their value to left and right
- The display helper now sends distinct left/right values in the hardware's native `0x40` meter slots
- The independent white `0x41` volume marker, button LEDs, mute handling, and page behavior are preserved
- Python capture/smoothing/packing tests, C `S1C3`/`S1C2` decoder tests, a live stereo `parec` probe, RPM checks, and service startup all passed
- Live logs confirmed genuinely different channel values (for example `53/76` and `8/26`), proving independent capture and transport
- User confirmed the independent stereo hardware columns work perfectly; roadmap item #13 is complete

### 5. Selectable Mono or Stereo Activity Monitoring — Roadmap #13 Extension
- Added a saved **Activity monitoring** selector under **Screen content** with Stereo and Mono choices
- Stereo remains the backward-compatible default and continues to capture independent left/right channels
- Mono requests one mixed channel from PipeWire-Pulse and mirrors its peak across both native activity bars
- The selector is available in Mixer mode and takes effect when the user selects **Apply changes**
- Preference persistence, invalid-value fallback, mono/stereo sample decoding, and capture arguments passed automated tests
- Built and installed `hercules-stream100-0.15.1-1.fc44.x86_64.rpm`
- Live runtime testing confirmed `--channels=1` and mirrored mono-level reporting in Mono mode, then `--channels=2` and distinct L/R values after restoring Stereo
- The user's original configuration was restored exactly after the mode test; both display and mixer services are active
- User confirmed the Mono/Stereo selector works correctly on the hardware

### 6. Visualiser Style Experiments and Safe Rollback — Roadmap #13 Extension
- v0.15.2 added a saved **Visualiser style** selector under **Screen content**
- The experiment offered Classic, Segmented, Rounded, and Slim
- Classic retains the current solid twin-bar appearance and is the backward-compatible default
- Segmented uses separated stacked blocks; Rounded uses softer medium-width columns; Slim uses narrow minimal columns
- The setting works independently of the Mono/Stereo monitoring choice and follows every mixer page
- Encoded the zero-based style in the unused high nibble of metadata byte 28; the low nibble continues to carry the mixer-page index
- Classic metadata remains byte-identical to v0.15.1; older `S1C3` and `S1C2` frames decode as Classic
- Hardware feedback proved the v0.15.2 geometry mapping was incorrect: changing `0x34` byte 2 only shifted RGB565 colour `0x2104` toward blue, so every option retained the same shape
- SDK decompilation confirmed Stream 100 command `0x34` contains only meter colours; the separate `HSM02_SetVuMeterStyle` request belongs to another control path and is not a Stream 100 `0x34` shape byte
- v0.15.3 restores the captured `0x2104` colour word and keeps Classic on the proven native `0x34`/`0x40`/`0x41` compositor path
- Segmented, Rounded, and Slim now use genuinely different static indexed-framebuffer decorations around the native live visualisers
- The native compositor retains per-channel colour, independent stereo activity, a white saved-volume marker, mute/offline handling, Mono mirroring, imported backgrounds, percentage badges, and button overlays
- Hardware feedback confirmed the v0.15.3 shapes but showed that its standalone `0x33` writes did not repaint committed indexed pixels, leaving all custom styles static
- v0.15.4 attempted to animate those pixels by recommitting 32 indexed framebuffer planes on every live update. On real startup this immediately garbled the display, made the controls unresponsive, and latched the USB display endpoint; that transport is unsafe and has been removed
- v0.15.5 keeps each alternative silhouette as static decorative artwork and delegates every live activity (`0x40`) and volume-marker (`0x41`) update to the proven native compositor
- Hardware feedback on v0.15.5 showed the fixed Classic bars composited over the static alternative decoration, producing an unacceptable hybrid rather than a genuinely different live meter
- v0.15.6 tested the only editable live bitmap layer (`0x35`), but the firmware fixes it at 32x32 in the percentage-badge position; the alternatives were therefore far too small and displaced the volume numbers
- The separately decompiled `HSM02_SetVuMeterStyle` request is not a Stream 100 solution: its DLL targets USB products `06f8:e054/e055`, while Stream 100 is `06f8:e053`
- v0.15.7 removes the rejected alternatives from the settings UI, treats every old custom-style value as Classic, restores the native full-height meters, and keeps the percentage badges separate
- Strict C/Python compilation, saved-style migration, desktop/AppStream, RPM `%check`, package-integrity, and connected-hardware startup tests pass for v0.15.7
- Built and installed `hercules-stream100-0.15.7-1.fc44.x86_64.rpm`; Classic + Stereo is active, both services are running with zero restarts, logs confirm full-size meters, all four percentage badges, white volume markers, and paired activity updates, and the user confirmed the mixer is working normally with Classic meters

### 7. Native Firmware Visualiser Styles — v0.15.8–v0.15.9
- Further decompilation followed the real Stream 100 E053 path instead of the unrelated E054/E055 bulk request
- The native shape selector is the low style value in the first byte of each six-byte channel record in command `0x32`; the captured Classic record is `81 01 07 07 f9 64`
- Official Windows resources map Classic, Segmented, Rounded, and Slim to native identifiers `1`, `2`, `4`, and `3`, producing active record bytes `0x81`, `0x82`, `0x84`, and `0x83`
- v0.15.8 restores the four-choice selector and changes only those native record bytes; it does not draw custom silhouettes or continuously recommit framebuffer planes
- Native `0x40` stereo activity, `0x41` volume markers, `0x34` colours, separate `0x35` percentages, Mono/Stereo selection, icons, and button overlays are preserved
- Exact 27-byte record tests cover all four choices plus invalid-value fallback to Classic; strict C/Python, AppStream, RPM `%check`, and package-integrity validation pass
- The v0.15.8 RPM was installed with Segmented selected; both services remained healthy, the E053 stayed enumerated, and live stereo values continued flowing
- An initial test appeared mono, but the user later identified that the test audio itself was mono, so it did not demonstrate a firmware or application defect
- The official E053 builder explains the missing detail: when its captured enabled-state flag is present, native style 2 requires companion mode byte `2`, while styles 1, 3, and 4 use mode byte `1`
- v0.15.9 therefore changes Segmented records from the incomplete `82 01 07 07 f9 64` to the Windows-matched `82 02 07 07 f9 64`; all other style records remain unchanged
- The exact-record test now covers both the native style identifier and its companion mode, preventing regression from the complete official record mapping
- v0.15.9 is installed with Segmented + Stereo active; the complete startup passed with healthy USB, zero service restarts, four percentage badges, four white volume markers, and paired live activity updates
- The user confirmed the corrected Segmented style works, including stereo visualization; the native alternative-meter feature is hardware-validated

## What Was Done (2026-08-10)

### 1. Critical Icon Rendering Bug Fix — COLRv1 Font Incompatibility
- **Bug discovered:** All icons displayed as empty boxes despite successful GTK/icon resolution
- **Root cause:** `Noto-COLRv1.ttf` loads successfully with `ImageFont.truetype()` but produces 0 visible pixels when rasterized (Pillow cannot render COLR format fonts)
- **Fix applied in `_render_emoji_icon()`:**
  - Reordered `font_paths` to prioritize standard TTF/OTF emoji fonts (`NotoEmoji-Regular.ttf`)
  - Added renderability validation: tests each font by rendering a sample glyph and checking `sum(1 for p in test_pixels if p[3] > 0) > 0` before accepting
  - Loop no longer breaks on first successful load; validates actual rasterization success
- **Verification:** Emoji fallback now renders correctly (🔈=229px, 🎤=146px, 💻=303px)

### 2. SVG Icon Rendering Fix — GdkPixbuf API Correction
- **Bug discovered:** SVG icons (e.g., `com.spotify.Client`) returned `None` despite being found by GTK
- **Root cause:** `pixbuf.get_pixels_array()` method doesn't exist in PyGObject's `GdkPixbuf.Pixbuf` API
- **Fix applied in `_load_icon_via_gtk()`:**
  - Replaced `get_pixels_array()` with `get_pixels()` + `get_rowstride()`
  - Changed `PILImage.frombuffer()` to `PILImage.frombytes(bytes(raw_data), ..., stride, 1)`
  - Properly handles row padding in RGBA pixel data
- **Verification:** Spotify SVG icon now renders correctly (392 visible pixels)

### 3. Icon Name Variant Generator — Flatpak & Native App Support
- **Problem:** Lowercased app IDs broke Flatpak-style naming (`com.spotify.Client` → `com.spotify.client`)
- **Solution:** Added `_generate_icon_name_variants(app_id)` helper function that generates multiple candidate names:
  - Preserves original case for Flatpak IDs
  - Tries: full ID, lowercase, last component, domain-app patterns, dot→hyphen replacements
  - Handles special cases: Chrome→`chromium`/`google-chrome`, Firefox→`firefox`, Spotify→`spotify`/`spotify-client`
  - Deduplicates while preserving order
- **Updated `load_channel_icon()`:**
  - Removed premature `.lower()` on `app_id`
  - Now iterates through all generated variants via GTK lookup
  - Also applies variant generation to stream-derived app names
  - Desktop entry fallback uses lowercase for compatibility

### 4. Version & Package Management
- **Version bumped:** 0.14.2 → 0.14.3 → 0.14.4 → 0.14.5 → 0.14.6 → 0.14.7
- **RPM build kit updated:** Spec file, build script, and source tarball all synchronized
- **Changelog entries:** Documented all icon rendering fixes and font compatibility improvements

## Architecture Notes
- `stream100-mixer.py` is a thin wrapper (10 lines) that uses `runpy.run_path()` to execute `stream100-mixer-alpha.py`
- **Icon logic in `stream100_channel_icons.py`:**
  - `_generate_icon_name_variants()` — generates 5-11 candidate names per app ID
  - `_load_icon_via_gtk()` — GTK icon theme lookup with SVG→GdkPixbuf→PIL conversion
  - `load_channel_icon()` — main resolution entry point (GTK → Desktop Entry → None)
  - `_render_emoji_icon()` — emoji fallback with validated font selection
- Fallback chain: GTK icon → Desktop entry → Emoji text → Default emoji
- Button overlay choices: Boxes → Basic → Glass → Custom user PNG
- Custom overlay: fixed 480x80 RGBA copy at `~/.config/hercules-stream100/button-overlay-custom.png`
- Custom overlay template: `/usr/libexec/hercules-stream100/button_labels_overlay_template.png`
- Meter capture: selectable stereo (two-channel float32le) or mono (one mixed float32le channel) at 8 kHz with 40 ms peaks
- Monitoring preference: `meter_channel_mode` in `config.json`; valid values are `stereo` and `mono`, defaulting to Stereo when absent or invalid
- Visualiser appearance: selectable full-height native `classic`, `segmented`, `rounded`, and `slim` styles, mapped to firmware identifiers `1`, `2`, `4`, and `3`
- Visualiser implementation: command `0x32` selects the controller-owned geometry, `0x40`/`0x41` drive live activity and volume, and the independent 32x32 `0x35` objects remain dedicated to volume percentages
- `0x34` correction: its first configuration word is RGB565 colour `0x2104`, not a style selector
- Stereo metadata: `S1C3`; left values use bytes 11/31 and right values use the unused high nibbles of bytes 24–27
- Backward compatibility: `S1C2` mono meter values are duplicated to left/right by the helper
- **Font compatibility:**
  - `Noto-COLRv1.ttf`: Loads but renders 0 pixels (COLR format incompatible with Pillow)
  - `NotoEmoji-Regular.ttf`: Loads and renders correctly (standard TTF)
  - Validation ensures only renderable fonts are selected
- Display output via shared replay buffer: `/usr/libexec/hercules-stream100/stream100-display-replay.bin`
- USB interface: `/dev/bus/usb/009/003` (checked; no conflicts)

## Key Files & Paths
| Purpose | Path |
|---------|------|
| Spec file | `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100.spec` |
| Build script | `work/package-v102-base/hercules-stream100-rpm-build-kit/build-stream100-rpm.sh` |
| Source tree (current) | `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.15.9/` |
| Icon module (source) | `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.15.9/stream100_channel_icons.py` |
| Control panel (source) | `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.15.9/stream100-control.py` |
| Mixer renderer (source) | `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.15.9/stream100-mixer-alpha.py` |
| Display helper (source) | `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.15.9/stream100-display-helper.c` |
| Overlay template (source) | `work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.15.9/button_labels_overlay_template.png` |
| Icon module (installed) | `/usr/libexec/hercules-stream100/stream100_channel_icons.py` |
| Mixer alpha (installed) | `/usr/libexec/hercules-stream100/stream100-mixer-alpha.py` |
| Mixer wrapper | `/usr/libexec/hercules-stream100/stream100-mixer.py` |
| Control app | `/usr/libexec/hercules-stream100/stream100-control.py` |
| Display service | `/usr/libexec/hercules-stream100/stream100-display-service.py` |
| Display helper (C) | `/usr/libexec/hercules-stream100/stream100-display-helper` |
| Replay buffer | `/usr/libexec/hercules-stream100/stream100-display-replay.bin` |

## Remaining Tasks

### Immediate (next session)
1. Begin roadmap item #14: remove the three bottom action-zone dividers from image-only mode

### From ROADMAP.md (items #11 and #12 complete)
- **Channel application and device icons** — ✅ COMPLETE and tested:
  - GTK icon theme resolution ✅
  - SVG icon rendering via GdkPixbuf ✅
  - Emoji fallback with validated font selection ✅
  - Flatpak app ID variant generation ✅
  - Full fallback chain working ✅
- **On-screen action-button labels** — ✅ COMPLETE and tested:
  - Dynamic page-specific labels ✅
  - Boxes, Basic, and Glass built-in overlays ✅
  - Validated user-designed Custom overlay import and selection ✅
  - Exportable 480x80 PNG design template ✅
- **Dual channel (stereo) visualisers** — ✅ COMPLETE and hardware-validated in 0.15.0:
  - Independent PipeWire left/right capture and smoothing ✅
  - Backward-compatible stereo metadata and native hardware updates ✅
  - Live runtime values confirmed independently different ✅
  - Independent hardware column movement confirmed by the user ✅
  - Saved Mono/Stereo selector added in 0.15.1 and confirmed working by the user ✅
  - v0.15.2 style selector/persistence worked, but its `0x34` geometry mapping was disproved by hardware feedback
  - v0.15.3 replaces that mapping with distinct Segmented, Rounded, and Slim silhouettes, confirmed visually by the user
  - v0.15.4's continuous framebuffer recommits corrupted the hardware display and latched its USB endpoint; that implementation is retired
  - v0.15.5 produced an unacceptable hybrid of fixed live Classic bars over static custom decoration
  - v0.15.6 proved the only editable live bitmap slot is the fixed 32x32 percentage object; using it for custom meters made them tiny and displaced the volume numbers
  - v0.15.7 restores the hardware-validated full-size Classic path, preserves the percentage badges, hides rejected alternatives, and is installed, stable, and user-confirmed
  - v0.15.8 found the safe E053-native selector in command `0x32` and restores full-size Classic, Segmented, Rounded, and Slim firmware styles without custom framebuffer animation
  - v0.15.8's initial Segmented test used mono source audio, so its apparently mono movement did not establish a defect; v0.15.9 nevertheless completes the official style-2 record by applying companion mode `2`
  - Corrected Segmented is active and user-confirmed with stereo visualization; native alternative styles are hardware-validated

### Future ROADMAP items (not yet started)
14. Remove three black lines at the bottom of image-only mode
15. System tray icon
- Then continue with roadmap items 16–21 and assess the 1.0 migration
- Then: 1.0 migration (rename package/service/app-id/config-dir without data loss)

## Build Commands (for reference)
```bash
cd ~/Documents/OpenStream\ Project\ Dir/work/package-v102-base/hercules-stream100-rpm-build-kit
./build-stream100-rpm.sh          # builds the 0.15.9 RPM from the source archive
sudo dnf upgrade *.rpm             # install the new version
systemctl --user restart hercules-stream100-display.service hercules-stream100.service
```

## Testing Commands (for reference)
```bash
# Test icon resolution directly
python3 -c "
import sys
sys.path.insert(0, '/usr/libexec/hercules-stream100')
from stream100_channel_icons import load_channel_icon, get_icon_name, load_emoji_fallback

# Test app icons
for app in ['com.spotify.Client', 'org.mozilla.firefox', 'org.chromium.Chromium']:
    ch = {'kind': 'application', 'application_id': app}
    icon = load_channel_icon(ch, [], icon_size=24)
    if icon:
        pixels = list(icon.getdata())
        non_trans = sum(1 for p in pixels if p[3] > 0)
        print(f'{app}: GTK icon ({non_trans} visible pixels)')
    else:
        emoji_name = get_icon_name(ch, [])
        emoji_img = load_emoji_fallback(emoji_name, icon_size=24)
        pixels = list(emoji_img.getdata())
        non_trans = sum(1 for p in pixels if p[3] > 0)
        print(f'{app}: emoji \"{emoji_name}\" ({non_trans} visible pixels)')
"
```

## Known Issues
- **Pillow 14 deprecation warning:** `Image.Image.getdata()` → `get_flattened_data()` (cosmetic, not functional)
- **Migration script:** Fails with "Access denied" when trying to disable units (requires polkit/sudo)
- **Chromium icon:** Returns emoji fallback if no chromium/chrome icon installed on system (expected behavior)
