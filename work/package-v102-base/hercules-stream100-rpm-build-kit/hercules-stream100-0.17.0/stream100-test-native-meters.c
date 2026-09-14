#define main stream100_display_helper_entry
#include "stream100-display-helper.c"
#undef main

#include <assert.h>
#include <stdio.h>

static void assert_style(unsigned char saved_style,
                         unsigned char native_style,
                         unsigned char companion_mode) {
    unsigned char body[NATIVE_PANEL_STATE_BYTES];
    assert(build_native_panel_state(body, 0, saved_style) == LIBUSB_SUCCESS);
    assert(body[0] == 0x32);
    assert(body[1] == 0x00);
    for (int channel = 0; channel < 4; ++channel) {
        const size_t offset = 2u + (size_t)channel * 6u;
        assert(body[offset] == (unsigned char)(0x80u | native_style));
        assert(body[offset + 1] == companion_mode);
        assert(body[offset + 2] == 0x07);
        assert(body[offset + 3] == 0x07);
        assert(body[offset + 4] == 0xf9);
        assert(body[offset + 5] == 0x64);
    }
    assert(body[26] == 0x00);
}

static void assert_notepad_metadata_mode(void) {
    unsigned char frame[FRAME_INPUT_SIZE] = {0};
    unsigned char *metadata = frame + FRAME_METADATA_V2_OFFSET;
    unsigned char levels[4];
    unsigned char meter_left_levels[4];
    unsigned char meter_right_levels[4];
    unsigned char muted_mask;
    unsigned char online_mask;
    unsigned char display_mode;
    unsigned char channel_colors[4][3];
    unsigned char button_leds[4];
    unsigned char page_index;
    unsigned char page_count;
    unsigned char meter_style;
    unsigned char volume_meters;
    unsigned char badge_style;
    unsigned char transient_volume_mask;
    unsigned char display_brightness;

    memcpy(metadata, "S1C3", 4);
    metadata[10] = 5;
    assert(read_native_metadata(
        frame, levels, meter_left_levels, meter_right_levels,
        &muted_mask, &online_mask, &display_mode,
        channel_colors, button_leds, &page_index, &page_count,
        &meter_style, &volume_meters, &badge_style,
        &transient_volume_mask,
        &display_brightness) == 1);
    assert(display_mode == 5);
    assert(badge_style == BADGE_STYLE_OPENSTREAM);

    metadata[10] = 6;
    metadata[30] = 3 | 0x04 | 0xa0;
    assert(read_native_metadata(
        frame, levels, meter_left_levels, meter_right_levels,
        &muted_mask, &online_mask, &display_mode,
        channel_colors, button_leds, &page_index, &page_count,
        &meter_style, &volume_meters, &badge_style,
        &transient_volume_mask,
        &display_brightness) == 1);
    assert(display_mode == 6);
    assert(volume_meters == 3);
    assert(badge_style == BADGE_STYLE_HERCULES);
    assert(transient_volume_mask == 0x0a);
}

static void assert_clean_hercules_percentage(void) {
    const unsigned int white = 0xf0000u | native_rgb565(255, 255, 255);
    const unsigned int blue = 0xf0000u | percentage_background(
        0, 0, 1, default_channel_colors);

    /* 24% uses doubled pixels: every authored 3x5 cell occupies a stable
     * 2x2 LCD block. The rest of the object is a solid badge, not a sampled
     * approximation of the detailed framebuffer icon underneath. */
    assert(percentage_badge_pixel(
        0, 24, 0, 1, default_channel_colors, NULL, 1, 6, 11) == white);
    assert(percentage_badge_pixel(
        0, 24, 0, 1, default_channel_colors, NULL, 1, 7, 12) == white);
    assert(percentage_badge_pixel(
        0, 24, 0, 1, default_channel_colors, NULL, 1, 20, 11) == white);
    assert(percentage_badge_pixel(
        0, 24, 0, 1, default_channel_colors, NULL, 1, 24, 11) == white);
    assert(percentage_badge_pixel(
        0, 24, 0, 1, default_channel_colors, NULL, 1, 22, 11) == blue);
    assert(percentage_badge_pixel(
        0, 24, 0, 1, default_channel_colors, NULL, 1, 1, 1) == blue);
}

int main(void) {
    unsigned char clear[NATIVE_PANEL_STATE_BYTES];

    assert_style(1, 1, 1); /* Classic */
    assert_style(2, 2, 2); /* Segmented requires its paired stereo mode. */
    assert_style(3, 4, 1); /* Rounded */
    assert_style(4, 3, 1); /* Slim */
    assert_style(0, 1, 1); /* Invalid values fall back safely. */
    assert_style(5, 1, 1);

    assert(build_native_panel_state(clear, 1, 4) == LIBUSB_SUCCESS);
    assert(clear[0] == 0x32);
    for (size_t offset = 1; offset < sizeof(clear); ++offset) {
        assert(clear[offset] == 0x00);
    }

    assert_notepad_metadata_mode();
    assert_clean_hercules_percentage();

    puts("native meter panel records, Notepad, and System Monitor metadata: PASS");
    return 0;
}
