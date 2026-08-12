Name:           hercules-stream100
Version:        0.15.9
Release:        1%{?dist}
Summary:        OpenStream100 PipeWire controller for Hercules Stream 100 hardware

License:        MIT
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  desktop-file-utils
BuildRequires:  gcc
BuildRequires:  libappstream-glib
BuildRequires:  pkgconfig(libusb-1.0)
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

Requires:       bash
Requires:       fontconfig
Requires:       gtk4
Requires:       hicolor-icon-theme
Requires:       pipewire-utils
Requires:       playerctl
Requires:       pulseaudio-utils
Requires:       python3
Requires:       python3-gobject
Requires:       python3-pillow
Requires:       python3-pyusb
Requires:       systemd
Requires:       wireplumber

%description
OpenStream100 provides up to eight pages of four per-application PipeWire volume controls for the
Hercules Stream 100, four soft-mute buttons, programmable action buttons with
LEDs, saved assignments, channel colours, mixer backgrounds, and a separate
full-screen image mode. Optional native meters keep a white marker at the
current volume while independent left/right coloured bars follow live PipeWire
audio activity.
The controller screen brightness can be adjusted live and saved independently.
Per-channel application icons and on-screen button labels provide clear visual
feedback. It includes a GTK4 configuration panel and drives the
full 480x272 display on Fedora Linux.

%prep
%autosetup -n %{name}-%{version}

%build
%set_build_flags
gcc $CFLAGS -std=c11 -Wall -Wextra stream100-display-helper.c \
    -o stream100-display-helper \
    $(pkg-config --cflags --libs libusb-1.0) $LDFLAGS

%install
cd %{_builddir}/%{name}-%{version}
mkdir -p %{buildroot}%{_libexecdir}/%{name}
install -pm0755 run-stream100-control.sh \
    %{buildroot}%{_libexecdir}/%{name}/run-stream100-control.sh
install -pm0755 run-stream100-mixer.sh \
    %{buildroot}%{_libexecdir}/%{name}/run-stream100-mixer.sh
install -pm0755 stream100-control.py \
    %{buildroot}%{_libexecdir}/%{name}/stream100-control.py
install -pm0755 stream100-display-service.py \
    %{buildroot}%{_libexecdir}/%{name}/stream100-display-service.py
install -pm0755 stream100-mixer.py \
    %{buildroot}%{_libexecdir}/%{name}/stream100-mixer.py
install -pm0755 stream100-mixer-alpha.py \
    %{buildroot}%{_libexecdir}/%{name}/stream100-mixer-alpha.py
install -pm0644 stream100_channel_icons.py \
    %{buildroot}%{_libexecdir}/%{name}/stream100_channel_icons.py
install -pm0644 stream100_version.py \
    %{buildroot}%{_libexecdir}/%{name}/stream100_version.py
install -pm0644 button_labels_overlay_boxes.png \
    %{buildroot}%{_libexecdir}/%{name}/button_labels_overlay_boxes.png
install -pm0644 button_labels_overlay_basic.png \
    %{buildroot}%{_libexecdir}/%{name}/button_labels_overlay_basic.png
install -pm0644 button_labels_overlay_glass.png \
    %{buildroot}%{_libexecdir}/%{name}/button_labels_overlay_glass.png
install -pm0644 button_labels_overlay_template.png \
    %{buildroot}%{_libexecdir}/%{name}/button_labels_overlay_template.png
install -pm0755 stream100-display-helper \
    %{buildroot}%{_libexecdir}/%{name}/stream100-display-helper
install -pm0644 stream100-display-replay.bin \
    %{buildroot}%{_libexecdir}/%{name}/stream100-display-replay.bin
install -pm0644 openstream100-startup.png \
    %{buildroot}%{_libexecdir}/%{name}/openstream100-startup.png

install -Dpm0755 packaging/hercules-stream100 \
     %{buildroot}%{_bindir}/hercules-stream100
install -Dpm0644 packaging/hercules-stream100.service \
     %{buildroot}%{_userunitdir}/hercules-stream100.service
install -Dpm0644 packaging/hercules-stream100-display.service \
      %{buildroot}%{_userunitdir}/hercules-stream100-display.service
# Install the mixer service from the source directory if it exists,
# otherwise fall back to the packaged copy in packaging/.
install -Dpm0644 hercules-stream100-mixer.service \
      %{buildroot}%{_userunitdir}/hercules-stream100-mixer.service
install -Dpm0644 70-hercules-stream100.rules \
      %{buildroot}%{_udevrulesdir}/70-hercules-stream100.rules
install -Dpm0644 com.hercules.Stream100.svg \
     %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/com.hercules.Stream100.svg
install -Dpm0644 packaging/com.hercules.Stream100.metainfo.xml \
     %{buildroot}%{_metainfodir}/com.hercules.Stream100.metainfo.xml
install -Dpm0644 packaging/hercules-stream100.1 \
     %{buildroot}%{_mandir}/man1/hercules-stream100.1

desktop-file-install \
     --dir=%{buildroot}%{_datadir}/applications \
     packaging/com.hercules.Stream100.desktop

%check
cd %{_builddir}/%{name}-%{version}
python3 -m py_compile \
     stream100-control.py \
     stream100-display-service.py \
     stream100-mixer.py \
     stream100-mixer-alpha.py \
     stream100_channel_icons.py \
     stream100_version.py
desktop-file-validate \
     %{buildroot}%{_datadir}/applications/com.hercules.Stream100.desktop
appstream-util validate-relax --nonet \
     %{buildroot}%{_metainfodir}/com.hercules.Stream100.metainfo.xml
gcc $CFLAGS -std=c11 -Wall -Wextra stream100-test-native-meters.c \
    -o stream100-test-native-meters \
    $(pkg-config --cflags --libs libusb-1.0) $LDFLAGS
./stream100-test-native-meters

%post
%systemd_user_post hercules-stream100-display.service hercules-stream100-mixer.service hercules-stream100.service

%preun
%systemd_user_preun hercules-stream100-display.service hercules-stream100-mixer.service hercules-stream100.service

%postun
%systemd_user_postun_with_restart hercules-stream100-display.service hercules-stream100-mixer.service hercules-stream100.service

%files
%license LICENSE
%doc README-STREAM100.md
%{_bindir}/hercules-stream100
%dir %{_libexecdir}/%{name}
%{_libexecdir}/%{name}/run-stream100-control.sh
%{_libexecdir}/%{name}/run-stream100-mixer.sh
%{_libexecdir}/%{name}/stream100-control.py
%{_libexecdir}/%{name}/stream100-display-service.py
%{_libexecdir}/%{name}/stream100-display-helper
%{_libexecdir}/%{name}/stream100-display-replay.bin
%{_libexecdir}/%{name}/openstream100-startup.png
%{_libexecdir}/%{name}/stream100-mixer.py
%{_libexecdir}/%{name}/stream100-mixer-alpha.py
%{_libexecdir}/%{name}/stream100_channel_icons.py
%{_libexecdir}/%{name}/stream100_version.py
%{_libexecdir}/%{name}/button_labels_overlay_boxes.png
%{_libexecdir}/%{name}/button_labels_overlay_basic.png
%{_libexecdir}/%{name}/button_labels_overlay_glass.png
%{_libexecdir}/%{name}/button_labels_overlay_template.png
%{_userunitdir}/hercules-stream100-display.service
%{_userunitdir}/hercules-stream100-mixer.service
%{_userunitdir}/hercules-stream100.service
%{_udevrulesdir}/70-hercules-stream100.rules
%{_datadir}/applications/com.hercules.Stream100.desktop
%{_datadir}/icons/hicolor/scalable/apps/com.hercules.Stream100.svg
%{_metainfodir}/com.hercules.Stream100.metainfo.xml
%{_mandir}/man1/hercules-stream100.1*

%changelog
* Tue Aug 11 2026 OpenStream100 contributors - 0.15.9-1
- Apply the E053 companion mode required by the native Segmented style
- Preserve independent left and right activity in the alternative geometry
- Add exact-record regression coverage for each style and companion mode

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.8-1
- Select the four firmware-owned meter geometries through native 0x32 records
- Restore Classic, Segmented, Rounded, and Slim in the settings panel
- Preserve live stereo activity, volume markers, and percentage badges

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.7-1
- Restore the full-height native Classic meters and separate percentage badges
- Remove rejected compact and static alternative styles from the control panel
- Migrate saved experimental style selections safely back to Classic

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.6-1
- Disable the fixed Classic meter surfaces whenever an alternative style is active
- Animate distinct Segmented, Rounded, and Slim stereo widgets through compact native objects
- Combine live activity, numeric volume, and a white volume marker without framebuffer traffic

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.5-1
- Remove the unsafe continuous indexed-plane animation introduced in 0.15.4
- Attempt static custom tracks around the fixed native layer, superseded in 0.15.6
- Preserve the proven Classic stereo activity and white volume objects

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.4-1
- Pair custom-meter palette changes with their indexed framebuffer planes
- Attempt custom live repainting, later removed after hardware corruption feedback
- Preserve the firmware-composited Classic path and Mono/Stereo monitoring

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.3-1
- Replace the incorrect colour-word style mapping with genuine custom silhouettes
- Add distinct Segmented, Rounded, and Slim stereo meter silhouettes
- Keep Classic on the proven firmware compositor and preserve Mono/Stereo monitoring

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.2-1
- Add the saved Classic, Segmented, Rounded, and Slim selector
- Add the initial native VU mapping later corrected in 0.15.3
- Preserve Mono/Stereo monitoring, white volume markers, and older metadata

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.1-1
- Add a saved Mono or Stereo activity-monitoring selector
- Capture a single mixed PipeWire-Pulse channel in Mono mode
- Mirror the mono activity level across both native visualiser bars

* Tue Aug 11 2026 OpenStream100 contributors - 0.15.0-1
- Capture independent left and right peaks from PipeWire-Pulse monitor streams
- Transport eight stereo activity levels in backward-compatible 32-byte metadata
- Drive the controller's native left and right visualiser columns independently

* Tue Aug 11 2026 OpenStream100 contributors - 0.14.9-1
- Add validated 480x80 PNG custom button overlay imports
- Add Custom to the button overlay selector with safe Boxes fallback
- Package an exportable button overlay design template

* Mon Aug 10 2026 OpenStream100 contributors - 0.14.8-1
- Rename button overlay styles: thick -> boxes, thin -> basic
- Add new 'glass' button overlay style option
- Update overlay images to button_labels_overlay_boxes.png, button_labels_overlay_basic.png, button_labels_overlay_glass.png
* Mon Aug 10 2026 OpenStream100 contributors - 0.14.6-1
- Add robust icon name variant generator for Flatpak and native app IDs
- Preserve original case in app IDs to support Flatpak naming conventions
- Generate multiple icon name patterns (dots, hyphens, case variations, etc.)
- Fix SVG icon rendering via GdkPixbuf fallback (get_pixels + frombytes)
* Mon Aug 10 2026 OpenStream100 contributors - 0.14.5-1
- Add SVG icon support via GdkPixbuf fallback when PIL cannot render icon files
- Ensure gi.require_version for Gdk and GdkPixbuf in GTK4 icon lookup
- Fix icon resolution for Flatpak-provided SVG icons (e.g. com.spotify.Client)
* Mon Aug 10 2026 OpenStream100 contributors - 0.14.4-1
- Fix GTK4 icon lookup: replace deprecated Gtk.IconTheme.get_default() with get_for_display()
- Fix icon name resolution: try multiple name variants (full ID, last component, etc.)
- Add stream-based icon name lookup as fallback
- Add gi.require_version("Gdk", "4.0") for GTK4 compatibility
* Mon Aug 10 2026 OpenStream100 contributors - 0.14.3-1
- Fix emoji icon rendering: validate fonts produce visible pixels before accepting
- Prioritize NotoEmoji-Regular.ttf over COLR fonts that fail Pillow rasterization
- Add gi.require_version calls to suppress PyGI version warnings
- Replace deprecated Image.Image.getdata() with get_flattened_data() for Pillow 14 compat
* Sun Aug 09 2026 OpenStream100 contributors - 0.14.2-1
- Add stream100_version.py as single source of truth for version number
- Add on-screen button labels overlay (Roadmap #12)
- Add button overlay style selector (boxes/basic/glass) in GTK control panel
- Include button_labels_overlay_boxes.png, button_labels_overlay_basic.png, and button_labels_overlay_glass.png

* Sat Aug 08 2026 OpenStream100 contributors - 0.13.7-1
- Fix NameError: replace undefined app_id with app_name in _find_app_icon_by_name Phase 6

* Sat Aug 08 2026 OpenStream100 contributors - 0.13.6-1
- Expand Chrome/Chromium icon fallback with exhaustive hicolor name variation search
- Add dedicated _find_png_in_hicolor_apps and _find_svg_in_hicolor_icon helpers
- Add normalized keys to _WELL_KNOWN_APP_MAP for cross-matching app names

* Sat Aug 08 2026 OpenStream100 contributors - 0.13.5-1
- Fix icon resolution: add missing `import gi` required for GTK icon theme lookup
- Add explicit well-known app mappings (Firefox, Chrome, Spotify, Discord, VLC, etc.)
- Reduce false positives in desktop file Name= matching (raise stem threshold to 5)
- Split desktop file matching into exact + substring passes to prevent cross-contamination

* Fri Aug 07 2026 OpenStream100 contributors - 0.13.2-1
- Add per-channel application icon badges on the mixer display (Roadmap #11)
- Add stream100_channel_icons module for icon/theme/emoji resolution
- Wire show_channel_icons toggle into GTK control panel and save to config
- Resolve icons by matching PipeWire stream properties to assigned channels
- Detect icon state changes (apps open/close) and force full display rebuilds

* Thu Aug 06 2026 OpenStream100 contributors - 0.12.0-1
- Add a saved 10% to 100% hardware screen-brightness slider
- Apply brightness changes live without redrawing or blanking the display
- Preserve the proven independent startup-logo brightness
