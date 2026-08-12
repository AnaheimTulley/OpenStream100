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

    puts("native meter panel records: PASS");
    return 0;
}
