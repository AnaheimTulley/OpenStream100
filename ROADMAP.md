# OpenStream100 roadmap

This is the agreed development order for the Fedora/PipeWire application.

1. Custom channel colours — complete and hardware-validated
2. Custom mixer background — complete and hardware-validated
3. Alternate display modes — complete and hardware-validated, including a clean edge-to-edge full-screen image
4. OpenStream100 rebrand — complete and hardware/UI-validated, with a visible version footer and compatible internal identifiers
5. Improved startup display — complete and hardware-validated in v94, with a persistent per-user broker retaining the clean resident logo and compositor state while the mixer stops or restarts; v95 updates the branded frame to the supplied native 480x272 transparent artwork
6. Knob sensitivity — complete and hardware-validated, with a saved 0.5% to 4.0% volume-per-turn setting
7. Programmable buttons and LEDs — complete and hardware-validated, including mute/media actions, exact 0% to 100% channel-volume presets, and native LED illumination
8. Additional mixer pages — complete and hardware-validated, with eight-page configuration, hardware-button navigation, page-correct labels, and visible page-palette replacement
9. Volume meters and visualizers — complete and hardware-validated, with an independent white volume marker and paired coloured live audio-activity bars
10. Hardware screen-brightness control — complete. implemented in the 0.12.0 test build with a saved 10% to 100% live slider.
11. Channel application and device icons — complete, show the assigned application's icon at the top-right of each mixer column, with clear representative fallbacks for speakers, microphones, system audio, and applications without usable artwork. Tested working.
12. On-screen action-button labels — complete, show a concise label for each programmable button along the bottom of the hardware display and update the labels whenever its assignments change. Includes Boxes, Basic, and Glass built-in overlays plus validated 480x80 PNG custom-overlay import, selection, removal, safe fallback, and an exportable design template. Tested working, including the custom-overlay workflow added in 0.14.9.
13. Dual channel (stereo) visualisers — complete and hardware-validated in 0.15.0, monitoring independent left and right PipeWire channels and transporting them through backward-compatible metadata to the two hardware visualiser columns. v0.15.1 adds a saved Mono/Stereo selector, confirmed working by the user. After safely rolling back the failed framebuffer experiments in v0.15.7, targeted decompilation of the actual E053 Windows driver path located the native shape selector in each channel record of command `0x32`. v0.15.8 restored Classic, Segmented, Rounded, and Slim using official native IDs `1`, `2`, `4`, and `3`. An initial apparently mono Segmented test was later traced to mono source audio rather than an application defect. The Windows builder also revealed that style 2 uniquely uses companion mode `2` rather than Classic's `1`, which v0.15.9 now matches with the complete `82 02` record. Separate percentage badges, selectable stereo/mono capture, colours, overlays, and compact live `0x40`/`0x41` updates are retained. The installed native Segmented style and stereo visualization are user-confirmed working; the alternative-meter extension is complete and hardware-validated.
14. Remove three black lines at the bottom of image only mode - there are three lines at the bottom of the screen meant as dividers for the button icons, these display in image only mode for some reason and need removing.
15. System tray icon - show presence of openstream running with an icon in the system tray which can be uses to start and stop the mixer or open the gui.
16. Digital photo frame mode - displays photos from a folder in a slide show style with definable delay before changing photo.
17. Gadget display mode — provide configurable full-screen gadgets such as a clock and live CPU, memory, temperature, and network, a notepad gadget, and an extensible foundation for additional gadgets
18. Virtual interactive mixer — provide a desktop window that mirrors the active mixer pages and supports mouse-controlled volume, mute, page navigation, and button actions, including useful operation when the hardware is disconnected
19. Plugins - for adding button functions for popular apps like spotify, discord, and obs studio.
20. Desktop window mirror - captures and displays a selected desktop window on the device display.
21. Phone app for additional virtual interactive mixers with remote control with touch controls, start with android app and assess viability of ios app.

After these items, assess a 1.0 migration that can rename the internal package,
service, application ID, and configuration directory without losing user data.
