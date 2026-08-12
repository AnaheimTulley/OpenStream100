Name:           hercules-stream100
Version:        0.13.0
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
current volume while paired coloured bars follow live PipeWire audio activity.
The controller screen brightness can be adjusted live and saved independently.
It includes a GTK4 configuration panel and drives the
full 480x272 display on Fedora Linux.

%prep
%autosetup

%build
%set_build_flags
gcc $CFLAGS -std=c11 -Wall -Wextra stream100-display-helper.c \
    -o stream100-display-helper \
    $(pkg-config --cflags --libs libusb-1.0) $LDFLAGS

%install
install -d %{buildroot}%{_libexecdir}/%{name}
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
install -pm0644 stream100-channel-icons.py \
    %{buildroot}%{_libexecdir}/%{name}/stream100-channel-icons.py
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
python3 -m py_compile \
    stream100-control.py stream100-display-service.py \
    stream100-mixer.py stream100-mixer-alpha.py stream100-channel-icons.py
desktop-file-validate \
    %{buildroot}%{_datadir}/applications/com.hercules.Stream100.desktop
appstream-util validate-relax --nonet \
    %{buildroot}%{_metainfodir}/com.hercules.Stream100.metainfo.xml

%post
%systemd_user_post hercules-stream100-display.service hercules-stream100.service

%preun
%systemd_user_preun hercules-stream100-display.service hercules-stream100.service

%postun
%systemd_user_postun_with_restart hercules-stream100-display.service hercules-stream100.service

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
%{_libexecdir}/%{name}/stream100-channel-icons.py
%{_userunitdir}/hercules-stream100-display.service
%{_userunitdir}/hercules-stream100.service
%{_udevrulesdir}/70-hercules-stream100.rules
%{_datadir}/applications/com.hercules.Stream100.desktop
%{_datadir}/icons/hicolor/scalable/apps/com.hercules.Stream100.svg
%{_metainfodir}/com.hercules.Stream100.metainfo.xml
%{_mandir}/man1/hercules-stream100.1*

%changelog
* Thu Aug 06 2026 OpenStream100 contributors - 0.13.0-1
- Add channel application and device icons (roadmap item #11)
- Resolve icons via PipeWire stream metadata, .desktop files, and theme lookup
- Draw circular icon badges at the top-right of each mixer column
- Update icons dynamically when applications open or close during runtime

* Tue Aug 04 2026 OpenStream100 contributors - 0.12.0-1
- Add a saved 10% to 100% hardware screen-brightness slider
- Apply brightness changes live without redrawing or blanking the display
- Preserve the proven independent startup-logo brightness

* Tue Aug 04 2026 OpenStream100 contributors - 0.11.5-1
- Monitor each application through its PipeWire-Pulse sink-input stream
- Calculate true 40 ms peaks from raw monitor audio instead of unreliable direct node links
- Monitor the default output through its dedicated sink monitor source

* Tue Aug 04 2026 OpenStream100 contributors - 0.11.4-1
- Refresh native activity samples continuously instead of only at quantisation boundaries
- Keep healthy per-application peak readers running while failed targets retry independently
- Report live visualizer levels once per second for straightforward hardware diagnosis

* Tue Aug 04 2026 OpenStream100 contributors - 0.11.3-1
- Keep the white native marker dedicated to the selected volume
- Drive the paired VU bars independently with compact 0x40 activity values
- Colour live meter fills from each channel assignment and simplify the GUI option

* Tue Aug 04 2026 OpenStream100 contributors - 0.11.2-1
- Keep visualizer capture streams alive across paused and unconnected targets
- Let dormant application monitors linger and reconnect without falling back
- Back off failed retries and report decoded four-channel activity levels

* Tue Aug 04 2026 OpenStream100 contributors - 0.11.1-1
- Target application monitor streams by their WirePlumber node names
- Prevent rejected targets from falling back to an unrelated capture source
- Report successful and failed visualizer connections in the user-service log

* Tue Aug 04 2026 OpenStream100 contributors - 0.11.0-1
- Add true audio-reactive native meters using PipeWire monitor peak streams
- Add a choice between live audio activity and current volume setting bars
- Preserve the established badge, page, colour, button, and startup metadata layout

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.19-1
- Replace the branded display with the supplied native 480x272 transparent artwork
- Preserve the artwork's exact centering and aspect ratio without resampling
- Composite its transparent surround over the established startup background

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.18-1
- Add a persistent per-user display broker that retains the USB display session
- Reuse the resident OpenStream100 logo and skip cold initialization on mixer restarts
- Keep direct-helper startup as a portable and diagnostic fallback

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.17-1
- Replace the late captured style-4 startup layout with the proven full-screen image layout
- Prevent initialization from reactivating native faders and white action-zone lines
- Preserve the successful graceful logo, resident-frame cache, and hidden final handoff

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.16-1
- Replace the captured active startup-surface batch with the official full reset batch
- Preserve the target sequence and USB transfer framing while repairing the message CRC
- Clear inherited native meter and action-zone overlays before the resident logo is composed

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.15-1
- Let the main mixer coordinate display-helper shutdown with KillMode=mixed
- Give the graceful resident-logo transition a bounded 15-second stop window
- Retain systemd's forced cleanup for any child that does not exit normally

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.14-1
- Transition cleanly from the saved screen to the logo on graceful shutdown
- Leave and cache that exact logo framebuffer for the next resident-matched start
- Wait for the display helper to finish its final frame before terminating it

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.13-1
- Replace the visibly converging black first pass with a resident-matched frame
- Cache the last static LCD framebuffer for exact warm-start matching
- Retain the proven hidden transitions from the matched primer to logo and Mixer

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.12-1
- Insert brightness zero at the beginning of the first captured display batch
- Gate the resident framebuffer before any layout, panel, or meter setup
- Preserve the original USB packet, sequence number, and transfer boundaries

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.11-1
- Replace all eight captured native-meter activations with validated resets
- Use each record's spare bytes for an earlier brightness-zero command
- Keep initialization message sizes, sequences, and command offsets unchanged

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.10-1
- Neutralize all three captured native-panel activation records before replay
- Preserve initialization packet boundaries and regenerate each affected CRC
- Prevent warm-start framebuffer state from appearing through the old mixer layout

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.9-1
- Preserve alpha when importing mixer backgrounds and full-screen images
- Composite transparent artwork over the dark display background before RGB565
- Remove the temporary startup colour curve now that hidden RGB is handled correctly

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.8-1
- Interleave repeated panel, meter, and object clears during first-frame startup
- Clear the secondary native object family while the compositor is active
- Apply a dark panel-response curve to the supplied startup artwork

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.7-1
- Replace captured startup surface commands with neutral per-plane latches
- Remove the duplicate post-primer renewal pass
- Present the supplied startup logo at a panel-safe brightness

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.6-1
- Replace the generated loading panel with the supplied OpenStream100 logo
- Clear inherited panel, meter, and badge layers before framebuffer priming
- Remove the redundant black-primer redraw and shorten the dark startup interval

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.5-1
- Camouflage compact badge margins with sampled pixels from the active framebuffer
- Preserve opaque single-packet badge updates on custom backgrounds
- Reduce application labels from 16px to 14px for a less crowded layout

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.4-1
- Replace unsupported mixed-alpha badge margins with stable opaque panel pixels
- Preserve the smaller 24x24 badge and reduced numeral scale
- Fix striped badge fragments observed on physical hardware

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.3-1
- Reduce visible percentage badges from 32x32 to 24x24
- Add transparent spacing between badges, application names, and native meters
- Retain the validated compact percentage and paired-meter update paths

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.2-1
- Restore the original paired volume meters below the application names
- Enable the panel state required for native meter surfaces to remain visible
- Remove the redundant TURN TO ADJUST caption and percentage-badge mini bars

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.1-1
- Render segmented volume bars inside the proven percentage badge objects
- Avoid resident meter surfaces that remain hidden in full-screen style 1
- Preserve tear-free compact updates and the clean full-screen layout

* Tue Aug 04 2026 OpenStream100 contributors - 0.10.0-1
- Add configurable native volume bars driven by each PipeWire channel level
- Clear meters for muted and unavailable assignments
- Remove the redundant VOLUME percent caption from the mixer framebuffer

* Tue Aug 04 2026 OpenStream100 contributors - 0.9.3-1
- Replace page-specific palettes without switching off the panel backlight
- Keep hidden safety gates for startup and full display-mode transitions
- Preserve full label and framebuffer redraws during mixer-page navigation

* Tue Aug 04 2026 OpenStream100 contributors - 0.9.2-1
- Keep background-derived palettes stable across mixer pages
- Switch pages without blanking the display when their accent colours match
- Retain protected palette replacement for pages that genuinely change colours

* Tue Aug 04 2026 OpenStream100 contributors - 0.9.1-1
- Redraw static application labels when changing mixer pages
- Retain compact native updates for ordinary volume and mute changes
- Safely replace differing page palettes while the panel is hidden

* Tue Aug 04 2026 OpenStream100 contributors - 0.9.0-1
- Add up to eight mixer pages with four channels and actions per page
- Add next-page and previous-page programmable-button actions
- Migrate existing single-page settings automatically into Page 1

* Tue Aug 04 2026 OpenStream100 contributors - 0.8.3-1
- Correct the remaining swapped physical inputs for Buttons 2 and 3

* Tue Aug 04 2026 OpenStream100 contributors - 0.8.2-1
- Correct the physical-to-logical order of programmable Buttons 1 through 4
- Preserve the hardware-validated native LED output order

* Tue Aug 04 2026 OpenStream100 contributors - 0.8.1-1
- Let portable test builds safely override an older installed RPM service
- Ensure programmable-button actions run through the updated mixer implementation

* Tue Aug 04 2026 OpenStream100 contributors - 0.8.0-1
- Add assignments for the four programmable hardware buttons
- Support microphone, speaker, and media-control actions
- Set any mixer channel to a saved 0 to 100 percent level
- Drive assigned-button LEDs with the native compact 0x30 command

* Tue Aug 04 2026 OpenStream100 contributors - 0.7.0-1
- Add a saved 0.5 to 4.0 percent knob-sensitivity setting
- Preserve the established one-percent response for existing configurations
- Apply sensitivity changes through the desktop control panel

* Tue Aug 04 2026 OpenStream100 contributors - 0.6.10-1
- Revert initialization brightness to zero after the v65 hardware regression
- Restore the less-visible v64 native-layer convergence behavior
- Retain the clean branded-screen and final Mixer transitions

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.9-1
- Apply one-percent brightness inside the captured initialization batch
- Let native firmware surfaces converge without waking at their previous brightness
- Retain the zero-palette primer and both proven hidden transitions

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.8-1
- Make all 256 primer palette entries black so stale firmware indices are hidden
- Detect the metadata-free all-zero primer directly in the display helper
- Replace the primer palette only during the hidden branded-screen transition

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.7-1
- Prime the first-frame compositor with an all-black framebuffer
- Settle the black primer while active at minimum brightness
- Reveal the branded screen through the proven established-redraw path

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.6-1
- Latch the one-percent startup brightness before sending warm-up pixel planes
- Prevent the dimming command from being deferred until after the redraw
- Preserve the clean full-brightness reveal and final gated handoff

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.5-1
- Wake the initial compositor at minimum brightness instead of waiting at zero
- Resend and latch the completed startup pixels while intermediate planes are dimmed
- Reapply the startup layout before the clean full-brightness reveal

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.4-1
- Extend the dark first-frame settle to the proven diagnostic range
- Shorten the visible loading hold after the completed frame is revealed
- Preserve the clean gated final handoff

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.3-1
- Let the first generated framebuffer settle while the backlight remains dark
- Show only the completed branded startup frame
- Split the existing hold into dark-settle and visible-loading intervals

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.2-1
- Hide the non-atomic startup-to-mixer framebuffer upload
- Restore brightness only after the final layout and percentage objects settle
- Retain the clean branded startup screen before the dark handoff

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.1-1
- Keep the display backlight off while the captured initialization is replayed
- Reveal the panel only after the complete OpenStream100 startup frame is ready
- Repair the modified initialization message CRC without changing its sequence

* Mon Aug 03 2026 OpenStream100 contributors - 0.6.0-1
- Add a branded OpenStream100 startup framebuffer
- Transition once into the saved Mixer or Full-screen image without changing palette
- Preserve the display lease while the startup screen is briefly visible

* Mon Aug 03 2026 OpenStream100 contributors - 0.5.0-1
- Rebrand the visible application, launcher, service description, and documentation
- Add an OpenStream100 0.5.0 footer to the control panel
- Retain existing package, service, application ID, and configuration paths for upgrades

* Mon Aug 03 2026 Stream 100 Linux contributors - 0.4.2-1
- Remove the remaining image-mode action-zone dividers with black RGB
- Preserve the working image framebuffer, cleared badges, and Mixer layout

* Mon Aug 03 2026 Stream 100 Linux contributors - 0.4.1-1
- Make the full-screen image action-zone dividers transparent
- Preserve the hardware-validated opaque Mixer action-zone configuration

* Mon Aug 03 2026 Stream 100 Linux contributors - 0.4.0-1
- Add persistent Mixer and Full-screen image display modes
- Add a separate full-screen image importer with edge-to-edge cropping
- Hide native mixer percentage objects in image mode while audio controls remain active

* Mon Aug 03 2026 Stream 100 Linux contributors - 0.3.0-1
- Add imported, cropped, and readability-adjusted custom backgrounds
- Add a 240-colour adaptive background palette with reserved native metadata
- Cache the static framebuffer so live controls remain compact and tear-free

* Mon Aug 03 2026 Stream 100 Linux contributors - 0.2.1-1
- Keep custom accents in the four hardware-validated palette slots
- Correct red and blue ordering in native percentage objects

* Mon Aug 03 2026 Stream 100 Linux contributors - 0.2.0-1
- Add saved custom colours for all four application channels
- Apply custom colours to the static mixer and fast native percentage objects

* Mon Aug 03 2026 Stream 100 Linux contributors - 0.1.0-1
- Initial Fedora RPM with the hardware-validated v48 mixer and GTK control panel
