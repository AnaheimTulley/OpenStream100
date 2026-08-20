#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <libusb-1.0/libusb.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define STREAM100_VID 0x06f8
#define STREAM100_PID 0xe053
#define DISPLAY_INTERFACE 1
#define DISPLAY_ALT_SETTING 1
#define DISPLAY_ENDPOINT 0x01

#define ISO_PACKET_SIZE 952
#define PACKETS_PER_TRANSFER 32
#define PACKETS_PER_GROUP 64
#define INIT_PACKET_COUNT 192
#define FRAME_PACKET_COUNT 320
#define FRAME_DATA_OFFSET_PACKETS 43
#define KEEPALIVE_PACKET_COUNT 64
#define KEEPALIVE_INTERVAL_MS 18
#define NATIVE_MESSAGE_GAP_MS 14
#define DEFAULT_DISPLAY_BRIGHTNESS 100
#define DEFAULT_METER_STYLE 1
#define MAX_METER_STYLE 4
#define STARTUP_LOGO_BRIGHTNESS 45
#define STARTUP_LOGO_HEARTBEATS 45
#define REPLAY_PACKET_COUNT \
    (INIT_PACKET_COUNT + FRAME_PACKET_COUNT + KEEPALIVE_PACKET_COUNT)

#define PALETTE_SIZE 512
#define FRAME_WIDTH 480
#define FRAME_HEIGHT 272
#define FRAMEBUFFER_SIZE (FRAME_WIDTH * FRAME_HEIGHT)
#define FRAME_INPUT_SIZE (PALETTE_SIZE + FRAMEBUFFER_SIZE)
#define FRAME_METADATA_V1_SIZE 16
#define FRAME_METADATA_V1_OFFSET (PALETTE_SIZE - FRAME_METADATA_V1_SIZE)
#define FRAME_METADATA_V2_SIZE 32
#define FRAME_METADATA_V2_OFFSET (PALETTE_SIZE - FRAME_METADATA_V2_SIZE)
#define CHUNK_COUNT 32
#define CHUNK_SIZE 4080
#define CHUNK_CYCLE_PACKETS 8
#define FRAME_MESSAGE_PACKETS 5

struct completion_state {
    int completed;
    int failed;
};

static void sleep_ms(long milliseconds) {
    struct timespec delay = {
        .tv_sec = milliseconds / 1000,
        .tv_nsec = (milliseconds % 1000) * 1000000L,
    };
    while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {
    }
}

static void LIBUSB_CALL transfer_complete(struct libusb_transfer *transfer) {
    struct completion_state *state = transfer->user_data;
    if (transfer->status != LIBUSB_TRANSFER_COMPLETED) {
        fprintf(stderr, "Display transfer failed: status %d\n", transfer->status);
        state->failed = 1;
    } else {
        for (int index = 0; index < transfer->num_iso_packets; ++index) {
            if (transfer->iso_packet_desc[index].status !=
                LIBUSB_TRANSFER_COMPLETED) {
                fprintf(stderr, "Display packet %d failed: status %d\n",
                        index, transfer->iso_packet_desc[index].status);
                state->failed = 1;
                break;
            }
        }
    }
    state->completed += 1;
}

static int send_packet_group(libusb_context *context,
                             libusb_device_handle *device,
                             const unsigned char *packets,
                             int packet_count) {
    const int transfer_count =
        (packet_count + PACKETS_PER_TRANSFER - 1) / PACKETS_PER_TRANSFER;
    struct libusb_transfer **transfers =
        calloc((size_t)transfer_count, sizeof(*transfers));
    unsigned char **buffers =
        calloc((size_t)transfer_count, sizeof(*buffers));
    struct completion_state state = {0, 0};
    int submitted = 0;
    int result = LIBUSB_ERROR_NO_MEM;

    if (transfers == NULL || buffers == NULL) {
        fprintf(stderr, "Not enough memory for display transfers.\n");
        goto cleanup;
    }

    for (int transfer_index = 0; transfer_index < transfer_count; ++transfer_index) {
        const int first_packet = transfer_index * PACKETS_PER_TRANSFER;
        int count = packet_count - first_packet;
        if (count > PACKETS_PER_TRANSFER) {
            count = PACKETS_PER_TRANSFER;
        }
        const int byte_count = count * ISO_PACKET_SIZE;

        buffers[transfer_index] = malloc((size_t)byte_count);
        transfers[transfer_index] = libusb_alloc_transfer(count);
        if (buffers[transfer_index] == NULL || transfers[transfer_index] == NULL) {
            fprintf(stderr, "Could not allocate an isochronous transfer.\n");
            goto cancel;
        }

        memcpy(buffers[transfer_index],
               packets + (size_t)first_packet * ISO_PACKET_SIZE,
               (size_t)byte_count);

        struct libusb_transfer *transfer = transfers[transfer_index];
        transfer->dev_handle = device;
        transfer->flags = 0;
        transfer->endpoint = DISPLAY_ENDPOINT;
        transfer->type = LIBUSB_TRANSFER_TYPE_ISOCHRONOUS;
        transfer->timeout = 5000;
        transfer->status = LIBUSB_TRANSFER_ERROR;
        transfer->length = byte_count;
        transfer->actual_length = 0;
        transfer->callback = transfer_complete;
        transfer->user_data = &state;
        transfer->buffer = buffers[transfer_index];
        transfer->num_iso_packets = count;
        libusb_set_iso_packet_lengths(transfer, ISO_PACKET_SIZE);

        result = libusb_submit_transfer(transfer);
        if (result != LIBUSB_SUCCESS) {
            fprintf(stderr, "Could not submit display transfer: %s\n",
                    libusb_error_name(result));
            goto cancel;
        }
        submitted += 1;
    }

    while (state.completed < submitted) {
        struct timeval timeout = {1, 0};
        result = libusb_handle_events_timeout(context, &timeout);
        if (result != LIBUSB_SUCCESS && result != LIBUSB_ERROR_INTERRUPTED) {
            fprintf(stderr, "USB event handling failed: %s\n",
                    libusb_error_name(result));
            state.failed = 1;
            break;
        }
    }

    if (!state.failed && state.completed == submitted) {
        result = LIBUSB_SUCCESS;
        goto cleanup;
    }
    result = LIBUSB_ERROR_IO;

cancel:
    for (int index = 0; index < submitted; ++index) {
        if (transfers[index] != NULL) {
            libusb_cancel_transfer(transfers[index]);
        }
    }
    while (state.completed < submitted) {
        struct timeval timeout = {1, 0};
        const int event_result = libusb_handle_events_timeout(context, &timeout);
        if (event_result != LIBUSB_SUCCESS &&
            event_result != LIBUSB_ERROR_INTERRUPTED) {
            break;
        }
    }

cleanup:
    if (transfers != NULL) {
        for (int index = 0; index < transfer_count; ++index) {
            if (transfers[index] != NULL) {
                libusb_free_transfer(transfers[index]);
            }
        }
    }
    if (buffers != NULL) {
        for (int index = 0; index < transfer_count; ++index) {
            free(buffers[index]);
        }
    }
    free(transfers);
    free(buffers);
    return result;
}

static unsigned char *read_file(const char *path, size_t expected) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        fprintf(stderr, "Could not open %s: %s\n", path, strerror(errno));
        return NULL;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    const long length = ftell(file);
    if (length < 0 || fseek(file, 0, SEEK_SET) != 0 ||
        (size_t)length != expected) {
        fprintf(stderr, "Display replay has %ld bytes; expected %zu.\n",
                length, expected);
        fclose(file);
        return NULL;
    }
    unsigned char *data = malloc(expected);
    if (data == NULL || fread(data, 1, expected, file) != expected) {
        fprintf(stderr, "Could not read the display replay.\n");
        free(data);
        fclose(file);
        return NULL;
    }
    fclose(file);
    return data;
}

static int read_frame(unsigned char *frame) {
    size_t received = 0;
    while (received < FRAME_INPUT_SIZE) {
        const ssize_t count =
            read(STDIN_FILENO, frame + received, FRAME_INPUT_SIZE - received);
        if (count > 0) {
            received += (size_t)count;
            continue;
        }
        if (count == 0) {
            return received == 0 ? 0 : -1;
        }
        if (errno != EINTR) {
            fprintf(stderr, "Could not read a framebuffer: %s\n", strerror(errno));
            return -1;
        }
    }
    return 1;
}

static uint16_t stream100_crc(const unsigned char *message,
                              size_t message_length) {
    uint16_t crc = 0;
    for (size_t offset = 0; offset < message_length; ++offset) {
        if (offset == 4 || offset == 5) {
            continue;
        }
        crc ^= (uint16_t)message[offset];
        for (int bit = 0; bit < 8; ++bit) {
            if ((crc & 1u) != 0) {
                crc = (uint16_t)((crc >> 1) ^ 0x8005u);
            } else {
                crc = (uint16_t)(crc >> 1);
            }
        }
    }
    return crc;
}

static int patch_initial_brightness(unsigned char *replay,
                                    unsigned char brightness) {
    static const unsigned char fullscreen_image_layout[17] = {
        0x38, 0x00, 0xff,
        0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff,
        0x01, 0x00, 0x00, 0x00, 0x00,
        0x00,
    };
    int patched = 0;
    for (int packet_index = 0;
         packet_index < INIT_PACKET_COUNT;
         ++packet_index) {
        unsigned char *message =
            replay + (size_t)packet_index * ISO_PACKET_SIZE;
        if (message[0] != 'S' || message[1] != 'M') {
            continue;
        }
        const size_t message_length =
            (size_t)message[2] | ((size_t)message[3] << 8);
        /* The captured setup batch contains the 100-percent brightness command
         * followed by a 0x38 style-4 native Mixer layout. IMG_1287.MOV proves
         * that this late layout reactivates the eight fader surfaces and white
         * action-zone lines after the earlier reset batches. Keep the message,
         * sequence, and transfer boundary intact, but gate brightness and
         * replace its layout with the proven full-screen image form. */
        if (message_length == 30 &&
            message[8] == 0x14 && message[9] == 0x31 &&
            message[10] == 0x01 && message[11] == 0x00 &&
            message[13] == 0x38 && message[14] == 0x00 &&
            message[15] == 0x04) {
            message[12] = brightness;
            memcpy(message + 13, fullscreen_image_layout,
                   sizeof(fullscreen_image_layout));
            const uint16_t crc = stream100_crc(message, message_length);
            message[4] = (unsigned char)(crc & 0xffu);
            message[5] = (unsigned char)(crc >> 8);
            patched += 1;
        }
    }
    if (patched != 1) {
        fprintf(stderr,
                "Expected one captured initialization brightness command; "
                "found %d.\n",
                patched);
        return -1;
    }
    return 0;
}

static int patch_early_initial_brightness(unsigned char *replay,
                                          unsigned char brightness) {
    /* IMG_1282.MOV shows the resident complete framebuffer is exposed when
     * initialization packet 67 begins the first captured display batch. The
     * original brightness command is not sent until packet 77. Insert the
     * same validated 0x31 command immediately after wrapper 0x14 in packet 67,
     * before its controller, layout, panel, and meter commands. Four added
     * bytes still fit in the existing single 952-byte USB packet, so outer USB
     * framing and the captured sequence number remain unchanged. */
    static const unsigned char reset_batch_prefix[6] = {
        0x14, 0x12, 0x00, 0x00, 0x00, 0x00,
    };
    int patched = 0;
    for (int packet_index = 0;
         packet_index < INIT_PACKET_COUNT;
         ++packet_index) {
        unsigned char *message =
            replay + (size_t)packet_index * ISO_PACKET_SIZE;
        if (message[0] != 'S' || message[1] != 'M') {
            continue;
        }
        const size_t message_length =
            (size_t)message[2] | ((size_t)message[3] << 8);
        if (message_length < 14 || message_length + 4u > ISO_PACKET_SIZE ||
            memcmp(message + 8,
                   reset_batch_prefix, sizeof(reset_batch_prefix)) != 0) {
            continue;
        }

        memmove(message + 13, message + 9, message_length - 9u);
        message[9] = 0x31;
        message[10] = 0x01;
        message[11] = 0x00;
        message[12] = brightness;
        const size_t patched_length = message_length + 4u;
        message[2] = (unsigned char)(patched_length & 0xffu);
        message[3] = (unsigned char)(patched_length >> 8);
        const uint16_t crc = stream100_crc(message, patched_length);
        message[4] = (unsigned char)(crc & 0xffu);
        message[5] = (unsigned char)(crc >> 8);
        patched += 1;
    }
    if (patched != 1) {
        fprintf(stderr,
                "Expected one captured initialization reset batch; found %d.\n",
                patched);
        return -1;
    }
    return 0;
}

static int patch_initial_native_panel_state(unsigned char *replay) {
    /* Three captured setup batches reactivate the official four-channel
     * panel before OpenStream100 can submit its first framebuffer. On a warm
     * start that makes the device reinterpret the resident image through the
     * old native layout, producing the brief torn mixer seen in IMG_1280.MOV.
     * Replace only the 24 state bytes with the SDK's proven all-zero form;
     * message lengths, sequences, command ordering, and USB framing remain
     * untouched. */
    static const unsigned char active_panel[26] = {
        0x32, 0x00,
        0x81, 0x01, 0x07, 0x07, 0x49, 0x64,
        0x81, 0x01, 0x07, 0x07, 0x49, 0x64,
        0x81, 0x01, 0x07, 0x07, 0x49, 0x64,
        0x81, 0x01, 0x07, 0x07, 0x49, 0x64,
    };
    int patched = 0;
    for (int packet_index = 0;
         packet_index < INIT_PACKET_COUNT;
         ++packet_index) {
        unsigned char *message =
            replay + (size_t)packet_index * ISO_PACKET_SIZE;
        if (message[0] != 'S' || message[1] != 'M') {
            continue;
        }
        const size_t message_length =
            (size_t)message[2] | ((size_t)message[3] << 8);
        if (message_length < 8u + sizeof(active_panel) ||
            message_length > ISO_PACKET_SIZE) {
            continue;
        }

        int message_patched = 0;
        for (size_t offset = 8;
             offset + sizeof(active_panel) <= message_length;
             ++offset) {
            if (memcmp(message + offset,
                       active_panel, sizeof(active_panel)) != 0) {
                continue;
            }
            memset(message + offset + 2, 0, 24);
            patched += 1;
            message_patched = 1;
            offset += sizeof(active_panel) - 1u;
        }
        if (message_patched) {
            const uint16_t crc = stream100_crc(message, message_length);
            message[4] = (unsigned char)(crc & 0xffu);
            message[5] = (unsigned char)(crc >> 8);
        }
    }
    if (patched != 3) {
        fprintf(stderr,
                "Expected three captured initialization panel activations; "
                "found %d.\n",
                patched);
        return -1;
    }
    return 0;
}

static int patch_initial_native_meter_state(unsigned char *replay) {
    /* IMG_1281.MOV proves the eight 0x34 meter surfaces remain independently
     * visible after the captured 0x32 panel records are cleared. Each active
     * meter occupies 13 bytes in initialization packet 71, while the SDK's
     * validated count-zero reset occupies nine. Fill the remaining four bytes
     * with a valid brightness-zero command so every following meter retains
     * its original command offset and the backlight is gated earlier too. */
    static const unsigned char active_configuration[7] = {
        0x04, 0x21, 0x00, 0xf8, 0xff, 0xff, 0x02,
    };
    int patched = 0;
    unsigned int surface_mask = 0;
    for (int packet_index = 0;
         packet_index < INIT_PACKET_COUNT;
         ++packet_index) {
        unsigned char *message =
            replay + (size_t)packet_index * ISO_PACKET_SIZE;
        if (message[0] != 'S' || message[1] != 'M') {
            continue;
        }
        const size_t message_length =
            (size_t)message[2] | ((size_t)message[3] << 8);
        if (message_length < 21 || message_length > ISO_PACKET_SIZE) {
            continue;
        }

        int message_patched = 0;
        for (size_t offset = 8; offset + 13u <= message_length; ++offset) {
            if (message[offset] != 0x34 ||
                memcmp(message + offset + 2,
                       active_configuration,
                       sizeof(active_configuration)) != 0) {
                continue;
            }
            const unsigned char surface = message[offset + 1];
            const unsigned char channel = (unsigned char)(surface & 0x7fu);
            if (channel > 3 || (surface & 0x7cu) != 0) {
                continue;
            }
            const unsigned int surface_bit =
                (unsigned int)channel + ((surface & 0x80u) != 0 ? 4u : 0u);
            surface_mask |= 1u << surface_bit;

            memset(message + offset + 2, 0, 7);
            message[offset + 9] = 0x31;
            message[offset + 10] = 0x01;
            message[offset + 11] = 0x00;
            message[offset + 12] = 0x00;
            patched += 1;
            message_patched = 1;
            offset += 12u;
        }
        if (message_patched) {
            const uint16_t crc = stream100_crc(message, message_length);
            message[4] = (unsigned char)(crc & 0xffu);
            message[5] = (unsigned char)(crc >> 8);
        }
    }
    if (patched != 8 || surface_mask != 0xffu) {
        fprintf(stderr,
                "Expected eight captured initialization meter activations; "
                "found %d with surface mask 0x%02x.\n",
                patched, surface_mask);
        return -1;
    }
    return 0;
}

static int replace_initial_active_surface_batch(unsigned char *replay) {
    /* IMG_1286.MOV shows the compact count-zero substitutions in captured
     * packet 71 do not prevent all eight native meter/action-zone surfaces
     * from appearing over the correct resident logo. Replace that entire
     * activation batch with the official full reset batch already present in
     * packet 67. Keep packet 71's captured sequence number and one-packet USB
     * framing, but use the reset batch's complete command boundaries. */
    unsigned char *reset_message = NULL;
    unsigned char *active_message = NULL;
    size_t reset_length = 0;
    for (int packet_index = 0;
         packet_index < INIT_PACKET_COUNT;
         ++packet_index) {
        unsigned char *message =
            replay + (size_t)packet_index * ISO_PACKET_SIZE;
        if (message[0] != 'S' || message[1] != 'M') {
            continue;
        }
        const size_t message_length =
            (size_t)message[2] | ((size_t)message[3] << 8);
        const uint16_t sequence =
            (uint16_t)message[6] | ((uint16_t)message[7] << 8);
        if (message_length > ISO_PACKET_SIZE) {
            continue;
        }
        if (sequence == 21 && message_length == 241 &&
            message[8] == 0x14 && message[9] == 0x31 &&
            message[13] == 0x12) {
            reset_message = message;
            reset_length = message_length;
        } else if (sequence == 23 && message_length == 156 &&
                   message[8] == 0x14 && message[9] == 0x32) {
            active_message = message;
        }
    }
    if (reset_message == NULL || active_message == NULL ||
        reset_length < 9 || reset_length > ISO_PACKET_SIZE) {
        fprintf(stderr,
                "Could not locate the captured reset and active surface "
                "batches.\n");
        return -1;
    }

    memset(active_message + 8, 0, ISO_PACKET_SIZE - 8u);
    memcpy(active_message + 8, reset_message + 8, reset_length - 8u);
    active_message[2] = (unsigned char)(reset_length & 0xffu);
    active_message[3] = (unsigned char)(reset_length >> 8);
    const uint16_t crc = stream100_crc(active_message, reset_length);
    active_message[4] = (unsigned char)(crc & 0xffu);
    active_message[5] = (unsigned char)(crc >> 8);
    return 0;
}

static int patch_priming_frame(unsigned char *packets,
                               const unsigned char *palette,
                               const unsigned char *framebuffer,
                               uint16_t *next_sequence) {
    for (int chunk = 0; chunk < CHUNK_COUNT; ++chunk) {
        unsigned char *cycle = packets +
            ((size_t)FRAME_DATA_OFFSET_PACKETS +
             (size_t)chunk * CHUNK_CYCLE_PACKETS) * ISO_PACKET_SIZE;
        if (cycle[0] != 'S' || cycle[1] != 'M') {
            fprintf(stderr, "Priming template chunk %d has no SM header.\n", chunk);
            return -1;
        }
        const size_t message_length =
            (size_t)cycle[2] | ((size_t)cycle[3] << 8);
        if (message_length < CHUNK_SIZE + 10 ||
            message_length > FRAME_MESSAGE_PACKETS * ISO_PACKET_SIZE) {
            fprintf(stderr, "Priming template chunk %d has invalid length.\n", chunk);
            return -1;
        }

        const size_t pixel_offset = message_length - (CHUNK_SIZE + 1);
        const size_t header_offset = pixel_offset - 9;
        unsigned char *header = cycle + header_offset;
        if (header[0] != 0x37 || header[1] != 1 || header[2] != chunk ||
            header[3] != 0 || header[4] != 0 || header[5] != 0 ||
            header[6] != 0 || header[7] != 0xf0 || header[8] != 0x0f) {
            fprintf(stderr, "Priming template chunk %d header did not match.\n",
                    chunk);
            return -1;
        }
        memcpy(cycle + pixel_offset,
               framebuffer + (size_t)chunk * CHUNK_SIZE,
               CHUNK_SIZE);

        if (chunk == 0) {
            if (cycle[8] != 0x38 || cycle[24] != 0x33) {
                fprintf(stderr, "Priming palette template did not match.\n");
                return -1;
            }
            memcpy(cycle + 31, palette, PALETTE_SIZE);
        }

        const uint16_t sequence = *next_sequence;
        *next_sequence = (uint16_t)(sequence + 1u);
        cycle[6] = (unsigned char)(sequence & 0xffu);
        cycle[7] = (unsigned char)(sequence >> 8);
        const uint16_t crc = stream100_crc(cycle, message_length);
        cycle[4] = (unsigned char)(crc & 0xffu);
        cycle[5] = (unsigned char)(crc >> 8);
        if (((uint16_t)cycle[4] | ((uint16_t)cycle[5] << 8)) !=
            stream100_crc(cycle, message_length)) {
            fprintf(stderr, "Priming message %d CRC repair failed.\n", chunk);
            return -1;
        }
    }
    return 0;
}

static int patch_clean_frame(unsigned char *packets,
                             const unsigned char *palette,
                             const unsigned char *framebuffer,
                             uint16_t *next_sequence,
                             int patch_tail_messages,
                             int include_display_setup) {
    unsigned char *first_cycle = packets +
        (size_t)FRAME_DATA_OFFSET_PACKETS * ISO_PACKET_SIZE;
    if (first_cycle[0] != 'S' || first_cycle[1] != 'M' ||
        first_cycle[8] != 0x38) {
        fprintf(stderr, "Framebuffer template has no display configuration.\n");
        return -1;
    }
    unsigned char display_configuration[16];
    memcpy(display_configuration, first_cycle + 8,
           sizeof(display_configuration));

    static const unsigned char palette_header[7] = {
        0x33, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00,
    };
    static const unsigned char pixel_header[9] = {
        0x37, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0xf0, 0x0f,
    };

    for (int chunk = 0; chunk < CHUNK_COUNT; ++chunk) {
        unsigned char *cycle = packets +
            ((size_t)FRAME_DATA_OFFSET_PACKETS +
             (size_t)chunk * CHUNK_CYCLE_PACKETS) * ISO_PACKET_SIZE;
        /* The large framebuffer message occupies five packets. Packets five
         * and seven are captured HERCULES framing packets. Clean passes use a
         * neutral message in every packet-six gap. */
        memset(cycle, 0, (size_t)FRAME_MESSAGE_PACKETS * ISO_PACKET_SIZE);
        cycle[0] = 'S';
        cycle[1] = 'M';

        size_t cursor = 8;
        /* The controller configuration and palette are global state. Send
         * them with the first committed image only; reapplying them for every
         * percentage change can visibly disturb the whole panel. */
        if (chunk == 0 && include_display_setup) {
            memcpy(cycle + cursor, display_configuration,
                   sizeof(display_configuration));
            cursor += sizeof(display_configuration);
            memcpy(cycle + cursor, palette_header, sizeof(palette_header));
            cursor += sizeof(palette_header);
            memcpy(cycle + cursor, palette, PALETTE_SIZE);
            cursor += PALETTE_SIZE;
        }

        memcpy(cycle + cursor, pixel_header, sizeof(pixel_header));
        cycle[cursor + 2] = (unsigned char)chunk;
        cursor += sizeof(pixel_header);
        memcpy(cycle + cursor,
               framebuffer + (size_t)chunk * CHUNK_SIZE,
               CHUNK_SIZE);
        cursor += CHUNK_SIZE;
        cycle[cursor++] = 0;

        const size_t message_length = cursor;
        const size_t expected_length =
            (chunk == 0 && include_display_setup) ? 4633u : 4098u;
        if (message_length != expected_length) {
            fprintf(stderr, "Framebuffer message %d has invalid clean length.\n",
                    chunk);
            return -1;
        }
        cycle[2] = (unsigned char)(message_length & 0xffu);
        cycle[3] = (unsigned char)(message_length >> 8);

        const uint16_t sequence = *next_sequence;
        *next_sequence = (uint16_t)(sequence + 1u);
        cycle[6] = (unsigned char)(sequence & 0xffu);
        cycle[7] = (unsigned char)(sequence >> 8);

        const uint16_t crc = stream100_crc(cycle, message_length);
        cycle[4] = (unsigned char)(crc & 0xffu);
        cycle[5] = (unsigned char)(crc >> 8);
        const uint16_t stored_crc =
            (uint16_t)cycle[4] | ((uint16_t)cycle[5] << 8);
        if (stored_crc != stream100_crc(cycle, message_length)) {
            fprintf(stderr, "Framebuffer message %d CRC repair failed.\n", chunk);
            return -1;
        }

        unsigned char *first_framing = cycle + 5 * ISO_PACKET_SIZE;
        unsigned char *heartbeat = cycle + 6 * ISO_PACKET_SIZE;
        unsigned char *second_framing = cycle + 7 * ISO_PACKET_SIZE;
        if (memcmp(first_framing, "HERCULES", 8) != 0 ||
            memcmp(second_framing, "HERCULES", 8) != 0) {
            fprintf(stderr, "Framebuffer cycle %d has invalid framing.\n", chunk);
            return -1;
        }

        if (patch_tail_messages) {
            memset(heartbeat, 0, ISO_PACKET_SIZE);
            heartbeat[0] = 'S';
            heartbeat[1] = 'M';
            const size_t heartbeat_length = 9u;
            heartbeat[2] = (unsigned char)heartbeat_length;
            const uint16_t heartbeat_sequence = *next_sequence;
            *next_sequence = (uint16_t)(heartbeat_sequence + 1u);
            heartbeat[6] = (unsigned char)(heartbeat_sequence & 0xffu);
            heartbeat[7] = (unsigned char)(heartbeat_sequence >> 8);
            heartbeat[8] = 0;
            const uint16_t heartbeat_crc =
                stream100_crc(heartbeat, heartbeat_length);
            heartbeat[4] = (unsigned char)(heartbeat_crc & 0xffu);
            heartbeat[5] = (unsigned char)(heartbeat_crc >> 8);
            if (((uint16_t)heartbeat[4] | ((uint16_t)heartbeat[5] << 8)) !=
                stream100_crc(heartbeat, heartbeat_length)) {
                fprintf(stderr,
                        "Framebuffer heartbeat %d CRC repair failed.\n", chunk);
                return -1;
            }
        }
    }
    return 0;
}

static int patch_frame_tail_body(unsigned char *packets,
                                 int chunk,
                                 const unsigned char *body,
                                 size_t body_length,
                                 uint16_t *next_sequence) {
    if (chunk < 0 || chunk >= CHUNK_COUNT ||
        body_length > ISO_PACKET_SIZE - 8u) {
        return -1;
    }
    unsigned char *cycle = packets +
        ((size_t)FRAME_DATA_OFFSET_PACKETS +
         (size_t)chunk * CHUNK_CYCLE_PACKETS) * ISO_PACKET_SIZE;
    unsigned char *first_framing = cycle + 5 * ISO_PACKET_SIZE;
    unsigned char *message = cycle + 6 * ISO_PACKET_SIZE;
    unsigned char *second_framing = cycle + 7 * ISO_PACKET_SIZE;
    if (memcmp(first_framing, "HERCULES", 8) != 0 ||
        memcmp(second_framing, "HERCULES", 8) != 0) {
        fprintf(stderr, "Startup tail cycle %d has invalid framing.\n", chunk);
        return -1;
    }

    memset(message, 0, ISO_PACKET_SIZE);
    message[0] = 'S';
    message[1] = 'M';
    const size_t message_length = 8u + body_length;
    message[2] = (unsigned char)(message_length & 0xffu);
    message[3] = (unsigned char)(message_length >> 8);
    const uint16_t sequence = *next_sequence;
    *next_sequence = (uint16_t)(sequence + 1u);
    message[6] = (unsigned char)(sequence & 0xffu);
    message[7] = (unsigned char)(sequence >> 8);
    memcpy(message + 8, body, body_length);
    const uint16_t crc = stream100_crc(message, message_length);
    message[4] = (unsigned char)(crc & 0xffu);
    message[5] = (unsigned char)(crc >> 8);
    if (((uint16_t)message[4] | ((uint16_t)message[5] << 8)) !=
        stream100_crc(message, message_length)) {
        fprintf(stderr, "Startup tail %d CRC repair failed.\n", chunk);
        return -1;
    }
    return 0;
}

static int patch_startup_cleanup_tails(unsigned char *packets,
                                       uint16_t *next_sequence) {
    /* Standalone clears sent before the first frame are ignored while the
     * native compositor is dormant. Interleave the same proven commands with
     * the 32 priming planes so they are repeated after the compositor wakes.
     * The second phase reasserts the layout, panel, and meter reset late in the
     * frame; the first phase also clears both compact object families. */
    static const unsigned char image_layout[17] = {
        0x38, 0x00, 0xff,
        0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff,
        0x01, 0x00, 0x00, 0x00, 0x00,
        0x00,
    };
    static const unsigned char clear_panel[27] = {
        0x32, 0x00,
    };
    int chunk = 0;

    for (int phase = 0; phase < 2; ++phase) {
        if (patch_frame_tail_body(
                packets, chunk++, image_layout, sizeof(image_layout),
                next_sequence) != 0 ||
            patch_frame_tail_body(
                packets, chunk++, clear_panel, sizeof(clear_panel),
                next_sequence) != 0) {
            return -1;
        }

        for (int channel = 0; channel < 4; ++channel) {
            for (int bank = 0; bank < 2; ++bank) {
                const unsigned char surface =
                    (unsigned char)(channel | (bank == 0 ? 0x00 : 0x80));
                unsigned char clear_meter[26] = {0};
                size_t cursor = 0;
                clear_meter[cursor++] = 0x34;
                clear_meter[cursor++] = surface;
                cursor += 7;
                clear_meter[cursor++] = 0x40;
                clear_meter[cursor++] = surface;
                cursor += 4;
                clear_meter[cursor++] = 0x41;
                clear_meter[cursor++] = surface;
                clear_meter[cursor++] = 0;
                clear_meter[cursor++] = 0xc0;
                clear_meter[cursor++] = 0;
                clear_meter[cursor++] = 0xc0;
                clear_meter[cursor++] = 0;
                if (patch_frame_tail_body(
                        packets, chunk++, clear_meter, cursor,
                        next_sequence) != 0) {
                    return -1;
                }
            }
        }

        if (phase == 0) {
            for (int channel = 0; channel < 4; ++channel) {
                unsigned char clear_badge[5 + 32 * 3 + 1] = {0};
                clear_badge[0] = 0x35;
                clear_badge[1] = 0;
                clear_badge[2] = (unsigned char)channel;
                clear_badge[3] = 32 * 3;
                if (patch_frame_tail_body(
                        packets, chunk++, clear_badge, sizeof(clear_badge),
                        next_sequence) != 0) {
                    return -1;
                }
            }
            for (int channel = 0; channel < 4; ++channel) {
                for (int bank = 0; bank < 2; ++bank) {
                    unsigned char clear_secondary[5 + 16] = {0};
                    clear_secondary[0] = 0x36;
                    clear_secondary[1] = (unsigned char)bank;
                    clear_secondary[2] = (unsigned char)channel;
                    clear_secondary[3] = 16;
                    if (patch_frame_tail_body(
                            packets, chunk++, clear_secondary,
                            sizeof(clear_secondary), next_sequence) != 0) {
                        return -1;
                    }
                }
            }
        }
    }

    if (chunk != CHUNK_COUNT) {
        fprintf(stderr, "Startup cleanup filled %d of %d tail slots.\n",
                chunk, CHUNK_COUNT);
        return -1;
    }
    return 0;
}

static int patch_single_latch_tail(unsigned char *packets,
                                   uint16_t *next_sequence) {
    const int chunk = CHUNK_COUNT - 1;
    unsigned char *cycle = packets +
        ((size_t)FRAME_DATA_OFFSET_PACKETS +
         (size_t)chunk * CHUNK_CYCLE_PACKETS) * ISO_PACKET_SIZE;
    unsigned char *first_framing = cycle + 5 * ISO_PACKET_SIZE;
    unsigned char *latch = cycle + 6 * ISO_PACKET_SIZE;
    unsigned char *second_framing = cycle + 7 * ISO_PACKET_SIZE;
    if (memcmp(first_framing, "HERCULES", 8) != 0 ||
        memcmp(second_framing, "HERCULES", 8) != 0) {
        fprintf(stderr, "Final latch cycle has invalid framing.\n");
        return -1;
    }

    memset(latch, 0, ISO_PACKET_SIZE);
    latch[0] = 'S';
    latch[1] = 'M';
    latch[2] = 10;
    const uint16_t sequence = *next_sequence;
    *next_sequence = (uint16_t)(sequence + 1u);
    latch[6] = (unsigned char)(sequence & 0xffu);
    latch[7] = (unsigned char)(sequence >> 8);
    latch[8] = 0x17;
    latch[9] = 0;
    const uint16_t crc = stream100_crc(latch, 10);
    latch[4] = (unsigned char)(crc & 0xffu);
    latch[5] = (unsigned char)(crc >> 8);
    if (((uint16_t)latch[4] | ((uint16_t)latch[5] << 8)) !=
        stream100_crc(latch, 10)) {
        fprintf(stderr, "Final latch CRC repair failed.\n");
        return -1;
    }
    return 0;
}

static int send_frame(libusb_context *context,
                      libusb_device_handle *device,
                      const unsigned char *packets,
                      int include_tail_messages) {
    /* The native Windows driver submits exactly one SM message per isochronous
     * transfer. Every transfer starts with a 952-byte HERCULES framing packet,
     * followed by only the number of 952-byte slices needed by that message.
     * Sending a frame as one large transfer shifts the controller's command
     * boundary and produces the characteristic lattice/tearing corruption. */
    for (int chunk = 0; chunk < CHUNK_COUNT; ++chunk) {
        const unsigned char *cycle = packets +
            ((size_t)FRAME_DATA_OFFSET_PACKETS +
             (size_t)chunk * CHUNK_CYCLE_PACKETS) * ISO_PACKET_SIZE;
        const unsigned char *message_framing = cycle - ISO_PACKET_SIZE;
        const size_t message_length =
            (size_t)cycle[2] | ((size_t)cycle[3] << 8);
        const int message_packets =
            (int)((message_length + ISO_PACKET_SIZE - 1u) / ISO_PACKET_SIZE);
        if (memcmp(message_framing, "HERCULES", 8) != 0 ||
            cycle[0] != 'S' || cycle[1] != 'M' ||
            message_packets < 1 || message_packets > FRAME_MESSAGE_PACKETS) {
            fprintf(stderr, "Framebuffer message %d has invalid native framing.\n",
                    chunk);
            return LIBUSB_ERROR_INVALID_PARAM;
        }

        int result = send_packet_group(
            context, device, message_framing, 1 + message_packets);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);

        if (include_tail_messages) {
            /* Startup interleaves one compositor-clear message after each
             * plane. patch_startup_cleanup_tails() must replace every captured
             * Windows tail before this path is used. */
            const unsigned char *heartbeat_framing =
                cycle + 5 * ISO_PACKET_SIZE;
            if (memcmp(heartbeat_framing, "HERCULES", 8) != 0 ||
                heartbeat_framing[ISO_PACKET_SIZE] != 'S' ||
                heartbeat_framing[ISO_PACKET_SIZE + 1] != 'M') {
                fprintf(stderr,
                        "Framebuffer heartbeat %d has invalid native framing.\n",
                        chunk);
                return LIBUSB_ERROR_INVALID_PARAM;
            }
            result = send_packet_group(
                context, device, heartbeat_framing, 2);
            if (result != LIBUSB_SUCCESS) {
                return result;
            }
            sleep_ms(NATIVE_MESSAGE_GAP_MS);
        }
    }
    return LIBUSB_SUCCESS;
}

static int send_single_latch_tail(libusb_context *context,
                                  libusb_device_handle *device,
                                  const unsigned char *packets) {
    const int chunk = CHUNK_COUNT - 1;
    const unsigned char *cycle = packets +
        ((size_t)FRAME_DATA_OFFSET_PACKETS +
         (size_t)chunk * CHUNK_CYCLE_PACKETS) * ISO_PACKET_SIZE;
    /* Native short messages are HERCULES + SM; there is no trailing framing
     * packet inside the same transfer. */
    return send_packet_group(context, device,
                             cycle + 5 * ISO_PACKET_SIZE, 2);
}

static int send_idle_heartbeat(libusb_context *context,
                               libusb_device_handle *device,
                               uint16_t *next_sequence) {
    unsigned char transfer[2 * ISO_PACKET_SIZE] = {0};
    memcpy(transfer, "HERCULES", 8);

    unsigned char *message = transfer + ISO_PACKET_SIZE;
    message[0] = 'S';
    message[1] = 'M';
    message[2] = 9;
    const uint16_t sequence = *next_sequence;
    message[6] = (unsigned char)(sequence & 0xffu);
    message[7] = (unsigned char)(sequence >> 8);
    message[8] = 0;
    const uint16_t crc = stream100_crc(message, 9);
    message[4] = (unsigned char)(crc & 0xffu);
    message[5] = (unsigned char)(crc >> 8);

    const int result = send_packet_group(context, device, transfer, 2);
    if (result == LIBUSB_SUCCESS) {
        *next_sequence = (uint16_t)(sequence + 1u);
    }
    return result;
}

static int send_short_message(libusb_context *context,
                              libusb_device_handle *device,
                              const unsigned char *body,
                              size_t body_length,
                              uint16_t *next_sequence) {
    const size_t message_length = 8u + body_length;
    if (message_length > ISO_PACKET_SIZE) {
        return LIBUSB_ERROR_INVALID_PARAM;
    }

    unsigned char transfer[2 * ISO_PACKET_SIZE] = {0};
    memcpy(transfer, "HERCULES", 8);
    unsigned char *message = transfer + ISO_PACKET_SIZE;
    message[0] = 'S';
    message[1] = 'M';
    message[2] = (unsigned char)(message_length & 0xffu);
    message[3] = (unsigned char)(message_length >> 8);
    const uint16_t sequence = *next_sequence;
    message[6] = (unsigned char)(sequence & 0xffu);
    message[7] = (unsigned char)(sequence >> 8);
    memcpy(message + 8, body, body_length);
    const uint16_t crc = stream100_crc(message, message_length);
    message[4] = (unsigned char)(crc & 0xffu);
    message[5] = (unsigned char)(crc >> 8);

    const int result = send_packet_group(context, device, transfer, 2);
    if (result == LIBUSB_SUCCESS) {
        *next_sequence = (uint16_t)(sequence + 1u);
    }
    return result;
}

static int send_native_button_leds(libusb_context *context,
                                   libusb_device_handle *device,
                                   const unsigned char states[4],
                                   int force,
                                   unsigned char previous_states[4],
                                   uint16_t *next_sequence) {
    int updated = 0;
    for (int button = 0; button < 4; ++button) {
        const unsigned char state = states[button] <= 2 ? states[button] : 0;
        if (!force && state == previous_states[button]) {
            continue;
        }
        const unsigned char body[3] = {
            0x30, (unsigned char)button, state,
        };
        const int result = send_short_message(
            context, device, body, sizeof(body), next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        previous_states[button] = state;
        updated += 1;
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
    }
    if (updated != 0) {
        fprintf(stderr,
                "Programmable button LEDs updated: %d button%s.\n",
                updated, updated == 1 ? "" : "s");
    }
    return LIBUSB_SUCCESS;
}

static int send_display_brightness(libusb_context *context,
                                   libusb_device_handle *device,
                                   unsigned char brightness,
                                   uint16_t *next_sequence) {
    const unsigned char body[4] = {
        0x31, 0x01, 0x00, brightness,
    };
    return send_short_message(
        context, device, body, sizeof(body), next_sequence);
}

static unsigned int badge_color(int channel, int white) {
    /* Native object pixels use an alpha nibble followed by RGB565. */
    static const unsigned int backgrounds[4] = {
        0xffe0u, /* yellow */
        0x07e0u, /* green */
        0x001fu, /* blue */
        0xf800u, /* red */
    };
    return 0xf0000u | (white ? 0xffffu : backgrounds[channel]);
}

static unsigned int badge_pixel(int channel, int x, int y) {
    static const unsigned char digits[4][7] = {
        {0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e},
        {0x0e, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1f},
        {0x1e, 0x01, 0x01, 0x0e, 0x01, 0x01, 0x1e},
        {0x02, 0x06, 0x0a, 0x12, 0x1f, 0x02, 0x02},
    };
    if (x == 0 || y == 0 || x == 31 || y == 31) {
        return 0xf0000u; /* opaque black border */
    }
    const int glyph_x = x - 6;
    const int glyph_y = y - 2;
    if (glyph_x >= 0 && glyph_x < 20 && glyph_y >= 0 && glyph_y < 28) {
        const int column = glyph_x / 4;
        const int row = glyph_y / 4;
        if ((digits[channel][row] & (1u << (4 - column))) != 0) {
            return badge_color(channel, 1);
        }
    }
    return badge_color(channel, 0);
}

static int send_native_object_badge(libusb_context *context,
                                    libusb_device_handle *device,
                                    int channel,
                                    uint16_t *next_sequence) {
    unsigned char compressed[32 * 32 * 3];
    size_t compressed_length = 0;
    for (int y = 0; y < 32; ++y) {
        int x = 0;
        while (x < 32) {
            const unsigned int color = badge_pixel(channel, x, y);
            int run = 1;
            while (x + run < 32 &&
                   badge_pixel(channel, x + run, y) == color) {
                run += 1;
            }
            const int reaches_row_end = x + run == 32;
            if (!reaches_row_end && run > 15) {
                run = 15;
            }
            const int encoded_run = reaches_row_end ? 0 : run;
            compressed[compressed_length++] =
                (unsigned char)((encoded_run << 4) | ((color >> 16) & 0x0fu));
            compressed[compressed_length++] = (unsigned char)(color & 0xffu);
            compressed[compressed_length++] = (unsigned char)((color >> 8) & 0xffu);
            x += run;
        }
    }

    unsigned char body[5 + sizeof(compressed) + 1];
    body[0] = 0x35;
    body[1] = 0; /* first 32x32 native object surface */
    body[2] = (unsigned char)channel;
    body[3] = (unsigned char)(compressed_length & 0xffu);
    body[4] = (unsigned char)(compressed_length >> 8);
    memcpy(body + 5, compressed, compressed_length);
    body[5 + compressed_length] = 0;
    return send_short_message(context, device, body,
                              6u + compressed_length, next_sequence);
}

static int send_native_volume_level(libusb_context *context,
                                    libusb_device_handle *device,
                                    int channel,
                                    uint16_t value,
                                    uint16_t *next_sequence) {
    unsigned char body[13] = {
        0x41, (unsigned char)channel,
        (unsigned char)(value & 0xffu), (unsigned char)(value >> 8),
        (unsigned char)(value & 0xffu), (unsigned char)(value >> 8),
        0x41, (unsigned char)(0x80 | channel),
        (unsigned char)(value & 0xffu), (unsigned char)(value >> 8),
        (unsigned char)(value & 0xffu), (unsigned char)(value >> 8),
        0,
    };
    return send_short_message(context, device, body, sizeof(body), next_sequence);
}

static int send_native_activity_level(libusb_context *context,
                                      libusb_device_handle *device,
                                      int channel,
                                      unsigned char left_value,
                                      unsigned char right_value,
                                      uint16_t *next_sequence) {
    /* The SDK uses 0x40 for the four VU samples associated with each surface
     * (current left/right plus their held peaks). Mirror each current stereo
     * peak into its matching held-peak slot and both composited surfaces. The
     * independent 0x41 volume marker is deliberately not touched here. */
    unsigned char body[13] = {
        0x40, (unsigned char)channel,
        left_value, right_value, left_value, right_value,
        0x40, (unsigned char)(0x80 | channel),
        left_value, right_value, left_value, right_value,
        0,
    };
    return send_short_message(context, device, body, sizeof(body), next_sequence);
}

static unsigned int native_rgb565(unsigned int red,
                                  unsigned int green,
                                  unsigned int blue) {
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3);
}

static int uses_custom_meter_geometry(unsigned char display_mode,
                                      unsigned char volume_meters,
                                      unsigned char meter_style) {
    /* Styles 1-4 are all firmware-owned native geometries. None requires a
     * framebuffer mask or a live bitmap layer. */
    (void)display_mode;
    (void)volume_meters;
    (void)meter_style;
    return 0;
}

#define BADGE_OBJECT_X_OFFSET 44
#define BADGE_OBJECT_Y 10
#define BADGE_BACKDROP_COLUMNS 4

static const unsigned char framebuffer_chunk_offsets[CHUNK_COUNT][2] = {
    {0, 0}, {4, 0}, {0, 2}, {4, 2},
    {2, 0}, {6, 0}, {2, 2}, {6, 2},
    {0, 1}, {2, 1}, {4, 1}, {6, 1},
    {0, 3}, {2, 3}, {4, 3}, {6, 3},
    {1, 0}, {3, 0}, {5, 0}, {7, 0},
    {1, 2}, {3, 2}, {5, 2}, {7, 2},
    {1, 1}, {3, 1}, {5, 1}, {7, 1},
    {1, 3}, {3, 3}, {5, 3}, {7, 3},
};

static uint16_t framebuffer_pixel_rgb565(const unsigned char *frame,
                                         int x,
                                         int y) {
    for (int chunk = 0; chunk < CHUNK_COUNT; ++chunk) {
        const int x_offset = framebuffer_chunk_offsets[chunk][0];
        const int y_offset = framebuffer_chunk_offsets[chunk][1];
        if ((x & 7) != x_offset || (y & 3) != y_offset) {
            continue;
        }
        const size_t packed_offset =
            PALETTE_SIZE + (size_t)chunk * CHUNK_SIZE +
            (size_t)((y - y_offset) / 4) * (FRAME_WIDTH / 8) +
            (size_t)((x - x_offset) / 8);
        const unsigned int palette_index = frame[packed_offset];
        return (uint16_t)frame[palette_index * 2u] |
            ((uint16_t)frame[palette_index * 2u + 1u] << 8);
    }
    return (uint16_t)native_rgb565(24, 31, 42);
}

static void read_native_badge_backdrops(
        const unsigned char *frame,
        uint16_t backdrops[4][32][BADGE_BACKDROP_COLUMNS]) {
    /* Mixed transparent/opaque object rows are unreliable. Reuse opaque
     * RGB565 pixels from the committed framebuffer instead. Four horizontal
     * samples per row keep the compact 0x35 message below one USB packet while
     * matching gradients and imported artwork far better than a black box. */
    for (int channel = 0; channel < 4; ++channel) {
        const int object_left = channel * 120 + BADGE_OBJECT_X_OFFSET;
        for (int y = 0; y < 32; ++y) {
            for (int column = 0; column < BADGE_BACKDROP_COLUMNS; ++column) {
                const int x = object_left + column * 8 + 4;
                backdrops[channel][y][column] = framebuffer_pixel_rgb565(
                    frame, x, BADGE_OBJECT_Y + y);
            }
        }
    }
}

static const unsigned char default_channel_colors[4][3] = {
    {48, 204, 190},
    {54, 211, 128},
    {246, 190, 64},
    {91, 130, 246},
};

static unsigned int percentage_background(
        int channel,
        int muted,
        int online,
        const unsigned char channel_colors[4][3]) {
    if (!online) {
        return native_rgb565(57, 69, 84);
    }
    if (muted) {
        return native_rgb565(245, 77, 91);
    }
    return native_rgb565(channel_colors[channel][0],
                         channel_colors[channel][1],
                         channel_colors[channel][2]);
}

static int percentage_digit_pixel(int digit, int x, int y) {
    static const unsigned char digits[10][5] = {
        {0x7, 0x5, 0x5, 0x5, 0x7},
        {0x2, 0x6, 0x2, 0x2, 0x7},
        {0x7, 0x1, 0x7, 0x4, 0x7},
        {0x7, 0x1, 0x7, 0x1, 0x7},
        {0x5, 0x5, 0x7, 0x1, 0x1},
        {0x7, 0x4, 0x7, 0x1, 0x7},
        {0x7, 0x4, 0x7, 0x5, 0x7},
        {0x7, 0x1, 0x1, 0x1, 0x1},
        {0x7, 0x5, 0x7, 0x5, 0x7},
        {0x7, 0x5, 0x7, 0x1, 0x7},
    };
    return (digits[digit][y] & (1u << (2 - x))) != 0;
}

static unsigned int percentage_badge_pixel(int channel,
                                           unsigned int level,
                                           int muted,
                                           int online,
                                           const unsigned char channel_colors[4][3],
                                           const uint16_t badge_backdrop[32][BADGE_BACKDROP_COLUMNS],
                                           int x,
                                           int y) {
    const unsigned int background =
        percentage_background(channel, muted, online, channel_colors);
    const unsigned int foreground = native_rgb565(255, 255, 255);
    const unsigned int panel_background = badge_backdrop == NULL ?
        native_rgb565(24, 31, 42) : badge_backdrop[y][x / 8];
    /* The firmware reserves a fixed 32x32 object. Draw a smaller 24x24 badge
     * at its top and camouflage the remaining pixels with samples from the
     * committed framebuffer. Mixed transparent/opaque runs render as stripes
     * on this device, while a fully opaque object remains stable. */
    const int badge_left = 4;
    const int badge_right = 27;
    const int badge_top = 1;
    const int badge_bottom = 24;
    if (x < badge_left || x > badge_right ||
            y < badge_top || y > badge_bottom) {
        return 0xf0000u | panel_background;
    }
    if ((x == badge_left || x == badge_right) &&
            (y == badge_top || y == badge_bottom)) {
        return 0xf0000u | panel_background; /* clipped corners */
    }
    if (x == badge_left || y == badge_top ||
            x == badge_right || y == badge_bottom) {
        return 0xf0000u; /* opaque black border */
    }
    if (!online) {
        if (x >= 9 && x <= 22 && y >= 11 && y <= 14) {
            return 0xf0000u | foreground;
        }
        return 0xf0000u | background;
    }

    if (level > 100u) {
        level = 100u;
    }
    int values[3] = {0, 0, 0};
    int count = 1;
    values[0] = (int)level;
    if (level >= 100u) {
        count = 3;
        values[0] = 1;
        values[1] = 0;
        values[2] = 0;
    } else if (level >= 10u) {
        count = 2;
        values[0] = (int)(level / 10u);
        values[1] = (int)(level % 10u);
    }

    const int glyph_scale = 2;
    const int glyph_width = 3 * glyph_scale;
    const int gap = 1;
    const int total_width = count * glyph_width + (count - 1) * gap;
    const int left = (32 - total_width) / 2;
    const int top = 8;
    if (y >= top && y < top + 5 * glyph_scale &&
            x >= left && x < left + total_width) {
        const int relative_x = x - left;
        const int slot = glyph_width + gap;
        const int digit_index = relative_x / slot;
        const int in_digit_x = relative_x % slot;
        if (digit_index < count && in_digit_x < glyph_width) {
            const int column = in_digit_x / glyph_scale;
            const int row = (y - top) / glyph_scale;
            if (percentage_digit_pixel(values[digit_index], column, row)) {
                return 0xf0000u | foreground;
            }
        }
    }
    return 0xf0000u | background;
}

static int send_native_percentage_badge(libusb_context *context,
                                        libusb_device_handle *device,
                                        int channel,
                                        unsigned int level,
                                        int muted,
                                        int online,
                                        const unsigned char channel_colors[4][3],
                                        const uint16_t badge_backdrop[32][BADGE_BACKDROP_COLUMNS],
                                        uint16_t *next_sequence) {
    unsigned char compressed[32 * 32 * 3];
    size_t compressed_length = 0;
    for (int y = 0; y < 32; ++y) {
        int x = 0;
        while (x < 32) {
            const unsigned int color = percentage_badge_pixel(
                channel, level, muted, online, channel_colors,
                badge_backdrop, x, y);
            int run = 1;
            while (x + run < 32 &&
                    percentage_badge_pixel(channel, level, muted, online,
                                           channel_colors, badge_backdrop,
                                           x + run, y) == color) {
                run += 1;
            }
            const int reaches_row_end = x + run == 32;
            if (!reaches_row_end && run > 15) {
                run = 15;
            }
            const int encoded_run = reaches_row_end ? 0 : run;
            compressed[compressed_length++] =
                (unsigned char)((encoded_run << 4) | ((color >> 16) & 0x0fu));
            compressed[compressed_length++] = (unsigned char)(color & 0xffu);
            compressed[compressed_length++] = (unsigned char)((color >> 8) & 0xffu);
            x += run;
        }
    }

    unsigned char body[5 + sizeof(compressed) + 1];
    body[0] = 0x35;
    body[1] = 0;
    body[2] = (unsigned char)channel;
    body[3] = (unsigned char)(compressed_length & 0xffu);
    body[4] = (unsigned char)(compressed_length >> 8);
    memcpy(body + 5, compressed, compressed_length);
    body[5 + compressed_length] = 0;
    return send_short_message(context, device, body,
                              6u + compressed_length, next_sequence);
}

static int send_native_transparent_badge(libusb_context *context,
                                         libusb_device_handle *device,
                                         int channel,
                                         uint16_t *next_sequence) {
    /* A zero-length-to-row-end run with alpha zero makes every pixel in the
     * firmware-composited 32x32 object transparent.  Retaining the four
     * objects in this state lets a full-screen framebuffer own every pixel. */
    unsigned char compressed[32 * 3];
    for (int row = 0; row < 32; ++row) {
        compressed[row * 3] = 0x00;
        compressed[row * 3 + 1] = 0x00;
        compressed[row * 3 + 2] = 0x00;
    }

    unsigned char body[5 + sizeof(compressed) + 1];
    body[0] = 0x35;
    body[1] = 0;
    body[2] = (unsigned char)channel;
    body[3] = (unsigned char)(sizeof(compressed) & 0xffu);
    body[4] = (unsigned char)(sizeof(compressed) >> 8);
    memcpy(body + 5, compressed, sizeof(compressed));
    body[5 + sizeof(compressed)] = 0;
    return send_short_message(context, device, body,
                              6u + sizeof(compressed), next_sequence);
}

static int clear_native_percentage_badges(libusb_context *context,
                                           libusb_device_handle *device,
                                           uint16_t *next_sequence) {
    for (int channel = 0; channel < 4; ++channel) {
        const int result = send_native_transparent_badge(
            context, device, channel, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
    }
    return LIBUSB_SUCCESS;
}

static unsigned char meter_nibble_to_percent(unsigned char value) {
    return (unsigned char)(((value & 0x0fu) * 100u + 7u) / 15u);
}

static int read_native_metadata(const unsigned char *frame,
                                unsigned char levels[4],
                                unsigned char meter_left_levels[4],
                                unsigned char meter_right_levels[4],
                                unsigned char *muted_mask,
                                unsigned char *online_mask,
                                unsigned char *display_mode,
                                unsigned char channel_colors[4][3],
                                unsigned char button_leds[4],
                                unsigned char *page_index,
                                unsigned char *page_count,
                                unsigned char *meter_style,
                                unsigned char *volume_meters,
                                unsigned char *display_brightness) {
    memcpy(channel_colors, default_channel_colors,
           sizeof(default_channel_colors));
    memset(button_leds, 0, 4);
    *volume_meters = 0;
    *meter_style = DEFAULT_METER_STYLE;
    *display_brightness = DEFAULT_DISPLAY_BRIGHTNESS;
    memset(meter_left_levels, 0, 4);
    memset(meter_right_levels, 0, 4);

    const unsigned char *metadata = frame + FRAME_METADATA_V2_OFFSET;
    const int stereo_metadata = memcmp(metadata, "S1C3", 4) == 0;
    if (stereo_metadata || memcmp(metadata, "S1C2", 4) == 0) {
        memcpy(levels, metadata + 4, 4);
        meter_left_levels[0] = meter_nibble_to_percent(metadata[11]);
        meter_left_levels[1] = meter_nibble_to_percent(metadata[11] >> 4);
        meter_left_levels[2] = meter_nibble_to_percent(metadata[31]);
        meter_left_levels[3] = meter_nibble_to_percent(metadata[31] >> 4);
        if (stereo_metadata) {
            for (int channel = 0; channel < 4; ++channel) {
                meter_right_levels[channel] = meter_nibble_to_percent(
                    metadata[24 + channel] >> 4);
                button_leds[channel] = metadata[24 + channel] & 0x0fu;
            }
        } else {
            memcpy(meter_right_levels, meter_left_levels, 4);
            memcpy(button_leds, metadata + 24, 4);
        }
        *muted_mask = metadata[8];
        *online_mask = metadata[9] & 0x0fu;
        *display_mode = metadata[10] == 2 ? 2 :
            (metadata[10] == 3 ? 3 :
             (metadata[10] == 4 ? 4 : (metadata[10] == 5 ? 5 : 1)));
        memcpy(channel_colors, metadata + 12, 12);
        const unsigned char encoded_meter_style = metadata[28] >> 4;
        *meter_style = encoded_meter_style < MAX_METER_STYLE
            ? (unsigned char)(encoded_meter_style + 1u)
            : DEFAULT_METER_STYLE;
        const unsigned char encoded_page_index = metadata[28] & 0x0fu;
        *page_index = encoded_page_index <= 7 ? encoded_page_index : 0;
        const unsigned char encoded_brightness = (unsigned char)(
            (metadata[9] >> 4) | (metadata[29] & 0xf0u));
        *display_brightness = encoded_brightness == 0
            ? DEFAULT_DISPLAY_BRIGHTNESS
            : (unsigned char)(encoded_brightness - 1u);
        if (*display_brightness < 10 || *display_brightness > 100) {
            *display_brightness = DEFAULT_DISPLAY_BRIGHTNESS;
        }
        const unsigned char encoded_page_count = metadata[29] & 0x0fu;
        *page_count = encoded_page_count >= 1 && encoded_page_count <= 8 ?
            encoded_page_count : 1;
        *volume_meters = metadata[30] <= 2 ? metadata[30] : 0;
        return 1;
    }

    metadata = frame + FRAME_METADATA_V1_OFFSET;
    if (memcmp(metadata, "S1LV", 4) != 0) {
        return 0;
    }
    memcpy(levels, metadata + 4, 4);
    memcpy(meter_left_levels, levels, 4);
    memcpy(meter_right_levels, levels, 4);
    *muted_mask = metadata[8];
    *online_mask = metadata[9];
    *display_mode = 1;
    *page_index = 0;
    *page_count = 1;
    return 1;
}

static int is_black_startup_primer(const unsigned char *frame) {
    /* The primer deliberately contains no S1Cx metadata because every one of
     * its 256 RGB565 palette entries must remain zero. Detect the unique all-
     * zero palette plus framebuffer directly and synthesize mode 4 in main. */
    for (size_t byte = 0; byte < FRAME_INPUT_SIZE; ++byte) {
        if (frame[byte] != 0) {
            return 0;
        }
    }
    return 1;
}

static int send_native_percentages(libusb_context *context,
                                   libusb_device_handle *device,
                                   const unsigned char levels[4],
                                   const unsigned char meter_left_levels[4],
                                   const unsigned char meter_right_levels[4],
                                   unsigned char muted_mask,
                                   unsigned char online_mask,
                                   const unsigned char channel_colors[4][3],
                                   const uint16_t badge_backdrops[4][32][BADGE_BACKDROP_COLUMNS],
                                   unsigned char volume_meters,
                                   int force,
                                   unsigned char previous_levels[4],
                                   unsigned char previous_meter_left_levels[4],
                                   unsigned char previous_meter_right_levels[4],
                                   unsigned char *previous_muted_mask,
                                   unsigned char *previous_online_mask,
                                   unsigned char *previous_meter_mode,
                                   uint16_t *next_sequence) {
    int badge_updates = 0;
    int volume_marker_updates = 0;
    int activity_updates = 0;
    for (int channel = 0; channel < 4; ++channel) {
        const unsigned char bit = (unsigned char)(1u << channel);
        const int state_changed =
            ((muted_mask ^ *previous_muted_mask) & bit) != 0 ||
            ((online_mask ^ *previous_online_mask) & bit) != 0;
        const int badge_changed =
            force || levels[channel] != previous_levels[channel] ||
            state_changed;
        const int volume_marker_changed =
            force || state_changed || volume_meters != *previous_meter_mode ||
            levels[channel] != previous_levels[channel];
        /* Mode 2 is a live VU stream. Re-emit its 0x40 sample for every
         * metadata frame even if the compact 4-bit value has not changed.
         * The firmware does not retain a steady sample like a framebuffer
         * pixel, and suppressing equal values made continuous audio appear as
         * a brief bar only when it crossed a quantisation boundary. */
        const int activity_changed =
            volume_meters == 2 || force || state_changed ||
            volume_meters != *previous_meter_mode ||
            meter_left_levels[channel] !=
                previous_meter_left_levels[channel] ||
            meter_right_levels[channel] !=
                previous_meter_right_levels[channel];
        if (badge_changed) {
            const int result = send_native_percentage_badge(
                context, device, channel, levels[channel],
                (muted_mask & bit) != 0, (online_mask & bit) != 0,
                channel_colors, badge_backdrops[channel],
                next_sequence);
            if (result != LIBUSB_SUCCESS) {
                return result;
            }
            badge_updates += 1;
        }
        if ((volume_meters == 2 && activity_changed) ||
            (volume_meters == 1 && *previous_meter_mode == 2)) {
            unsigned int left_activity_percent =
                volume_meters == 2 ? meter_left_levels[channel] : 0;
            unsigned int right_activity_percent =
                volume_meters == 2 ? meter_right_levels[channel] : 0;
            if ((muted_mask & bit) != 0 || (online_mask & bit) == 0) {
                left_activity_percent = 0;
                right_activity_percent = 0;
            }
            const unsigned char left_activity_value = (unsigned char)(
                (left_activity_percent * 255u + 50u) / 100u);
            const unsigned char right_activity_value = (unsigned char)(
                (right_activity_percent * 255u + 50u) / 100u);
            const int activity_result = send_native_activity_level(
                context, device, channel,
                left_activity_value, right_activity_value, next_sequence);
            if (activity_result != LIBUSB_SUCCESS) {
                return activity_result;
            }
            activity_updates += 1;
        }
        if (volume_meters && volume_marker_changed) {
            unsigned int volume_percent = levels[channel];
            if ((muted_mask & bit) != 0 || (online_mask & bit) == 0) {
                volume_percent = 0;
            }
            const uint16_t volume_value = (uint16_t)(
                (volume_percent * 65535u + 50u) / 100u);
            const int volume_result = send_native_volume_level(
                context, device, channel, volume_value, next_sequence);
            if (volume_result != LIBUSB_SUCCESS) {
                return volume_result;
            }
            volume_marker_updates += 1;
        }
        previous_levels[channel] = levels[channel];
        previous_meter_left_levels[channel] = meter_left_levels[channel];
        previous_meter_right_levels[channel] = meter_right_levels[channel];
        if (badge_changed || (volume_meters && volume_marker_changed) ||
            (volume_meters == 2 && activity_changed)) {
            sleep_ms(NATIVE_MESSAGE_GAP_MS);
        }
    }
    *previous_muted_mask = muted_mask;
    *previous_online_mask = online_mask;
    *previous_meter_mode = volume_meters;
    if (badge_updates != 0) {
        fprintf(stderr,
                "Native percentage objects updated: %d channel%s.\n",
                badge_updates, badge_updates == 1 ? "" : "s");
    }
    if (volume_marker_updates != 0) {
        fprintf(stderr,
                "Native white volume markers updated: %d channel%s.\n",
                volume_marker_updates,
                volume_marker_updates == 1 ? "" : "s");
    }
    if (activity_updates != 0 && badge_updates != 0) {
        fprintf(stderr,
                "Native paired activity meters updated: %d channel%s.\n",
                activity_updates, activity_updates == 1 ? "" : "s");
    }
    return LIBUSB_SUCCESS;
}

static int hold_startup_screen(libusb_context *context,
                               libusb_device_handle *device,
                               uint16_t *next_sequence,
                               int heartbeat_count) {
    /* Wait without losing the firmware lease. This is used while the black
     * primer settles and while the completed loading screen is visible. */
    for (int heartbeat = 0; heartbeat < heartbeat_count; ++heartbeat) {
        const int result = send_idle_heartbeat(context, device, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(KEEPALIVE_INTERVAL_MS);
    }
    return LIBUSB_SUCCESS;
}

static int run_native_object_test(libusb_context *context,
                                  libusb_device_handle *device,
                                  uint16_t *next_sequence) {
    /* Let the generated framebuffer settle while preserving the display lease. */
    for (int heartbeat = 0; heartbeat < 50; ++heartbeat) {
        const int result = send_idle_heartbeat(context, device, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(KEEPALIVE_INTERVAL_MS);
    }

    for (int channel = 0; channel < 4; ++channel) {
        const int result = send_native_object_badge(
            context, device, channel, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
    }
    fprintf(stderr,
            "Native 0x35 test badges 1-4 sent to object channels 0-3.\n");

    static const uint16_t levels[4] = {
        0x2666u, 0x6666u, 0xa666u, 0xe666u,
    };
    for (int channel = 0; channel < 4; ++channel) {
        const int result = send_native_volume_level(
            context, device, channel, levels[channel], next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
    }
    fprintf(stderr,
            "Native 0x41 channel levels sent at 15%%, 40%%, 65%%, and 90%%.\n");
    return LIBUSB_SUCCESS;
}

static int send_display_configuration(libusb_context *context,
                                      libusb_device_handle *device,
                                      uint32_t darkest_background,
                                      uint32_t lightest_background,
                                      unsigned char action_zone_style,
                                      uint32_t action_zone_color,
                                      uint16_t *next_sequence) {
    unsigned char body[17] = {
        0x38, 0x00, 0xff,
        (unsigned char)(darkest_background & 0xffu),
        (unsigned char)((darkest_background >> 8) & 0xffu),
        (unsigned char)((darkest_background >> 16) & 0xffu),
        (unsigned char)((darkest_background >> 24) & 0xffu),
        (unsigned char)(lightest_background & 0xffu),
        (unsigned char)((lightest_background >> 8) & 0xffu),
        (unsigned char)((lightest_background >> 16) & 0xffu),
        (unsigned char)((lightest_background >> 24) & 0xffu),
        action_zone_style,
        (unsigned char)(action_zone_color & 0xffu),
        (unsigned char)((action_zone_color >> 8) & 0xffu),
        (unsigned char)((action_zone_color >> 16) & 0xffu),
        (unsigned char)((action_zone_color >> 24) & 0xffu),
        0x00,
    };
    return send_short_message(context, device, body, sizeof(body), next_sequence);
}

static int hold_mask_phase(libusb_context *context,
                           libusb_device_handle *device,
                           uint16_t *next_sequence) {
    for (int heartbeat = 0; heartbeat < 130; ++heartbeat) {
        const int result = send_idle_heartbeat(context, device, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(KEEPALIVE_INTERVAL_MS);
    }
    return LIBUSB_SUCCESS;
}

static unsigned char native_meter_style_selector(unsigned char meter_style) {
    /* Stream Control presents Vumeter 1-4 in the order 1, 2, 4, 3. Our saved
     * names follow the visual order Classic, Segmented, Rounded, Slim. */
    static const unsigned char selectors[MAX_METER_STYLE + 1] = {
        1, 1, 2, 4, 3,
    };
    if (meter_style < DEFAULT_METER_STYLE ||
        meter_style > MAX_METER_STYLE) {
        return selectors[DEFAULT_METER_STYLE];
    }
    return selectors[meter_style];
}

static unsigned char native_meter_companion_mode(unsigned char meter_style) {
    /* Stream Control's E053 style builder sets this field to 2 only for
     * native style 2; every other enabled native geometry uses 1. Keeping
     * Classic's value here caused Segmented to combine its stereo pair. */
    return native_meter_style_selector(meter_style) == 2 ? 2 : 1;
}

#define NATIVE_PANEL_STATE_BYTES 27

static int build_native_panel_state(
    unsigned char body[NATIVE_PANEL_STATE_BYTES],
    int clear,
    unsigned char meter_style) {
    memset(body, 0, NATIVE_PANEL_STATE_BYTES);
    size_t cursor = 0;
    body[cursor++] = 0x32;
    body[cursor++] = 0;
    for (int channel = 0; channel < 4; ++channel) {
        if (clear) {
            cursor += 6;
        } else {
            unsigned char active_state[6] = {
                0x81, 0x01, 0x07, 0x07, 0xf9, 0x64,
            };
            /* FUN_180003a20 in the official E053 SDK places the style ID in
             * the low bits of each 0x32 channel record. Bit 7 is the existing
             * active flag from the hardware-validated Classic capture. The
             * official builder also pairs native style 2 with companion mode
             * 2; enabled styles 1, 3, and 4 use companion mode 1. */
            active_state[0] = (unsigned char)(
                0x80u | native_meter_style_selector(meter_style));
            active_state[1] = native_meter_companion_mode(meter_style);
            memcpy(body + cursor, active_state, sizeof(active_state));
            cursor += sizeof(active_state);
        }
    }
    body[cursor++] = 0;
    if (cursor != NATIVE_PANEL_STATE_BYTES) {
        return LIBUSB_ERROR_INVALID_PARAM;
    }
    return LIBUSB_SUCCESS;
}

static int send_native_panel_state(libusb_context *context,
                                   libusb_device_handle *device,
                                   int clear,
                                   unsigned char meter_style,
                                   uint16_t *next_sequence) {
    unsigned char body[NATIVE_PANEL_STATE_BYTES];
    const int build_result = build_native_panel_state(
        body, clear, meter_style);
    if (build_result != LIBUSB_SUCCESS) {
        return build_result;
    }
    return send_short_message(
        context, device, body, sizeof(body), next_sequence);
}

static int send_native_meter_state(libusb_context *context,
                                   libusb_device_handle *device,
                                   unsigned char surface,
                                   int enabled,
                                   unsigned char meter_style,
                                   const unsigned char *channel_color,
                                   uint16_t *next_sequence) {
    unsigned char body[26] = {0};
    size_t cursor = 0;
    body[cursor++] = 0x34;
    body[cursor++] = surface;
    if (enabled) {
        /* 0x34 carries RGB565 colours, not a shape selector. Its first two
         * configuration bytes are the captured background colour 0x2104. The
         * native shape selector is carried by the matching 0x32 record. */
        (void)meter_style;
        const unsigned char active_configuration[7] = {
            0x04, 0x21, 0x00, 0xf8, 0xff, 0xff, 0x02,
        };
        memcpy(body + cursor, active_configuration,
               sizeof(active_configuration));
        cursor += sizeof(active_configuration);
        /* The first surface is the paired VU fill and follows the channel's
         * assigned colour.  The 0x80 surface retains the captured neutral
         * colour used by the independent white volume marker. */
        const uint16_t color =
            channel_color != NULL && (surface & 0x80u) == 0 ?
            (uint16_t)native_rgb565(
                channel_color[0], channel_color[1], channel_color[2]) :
            ((surface & 0x80u) != 0 ? 0x8410u : 0x2945u);
        body[cursor++] = (unsigned char)(color & 0xffu);
        body[cursor++] = (unsigned char)(color >> 8);
        body[cursor++] = (unsigned char)(color & 0xffu);
        body[cursor++] = (unsigned char)(color >> 8);
    } else {
        /* This is the SDK's exact count-zero 0x34 reset form. */
        cursor += 7;
    }

    /* Restore the two resident level values used immediately before the
     * official application installs the active meter configuration. */
    body[cursor++] = 0x40;
    body[cursor++] = surface;
    body[cursor++] = 0;
    body[cursor++] = 0;
    body[cursor++] = 0;
    body[cursor++] = 0;
    body[cursor++] = 0x41;
    body[cursor++] = surface;
    body[cursor++] = 0;
    body[cursor++] = 0xc0;
    body[cursor++] = 0;
    body[cursor++] = 0xc0;
    body[cursor++] = 0;
    return send_short_message(context, device, body, cursor, next_sequence);
}

static int set_native_meter_surfaces(libusb_context *context,
                                     libusb_device_handle *device,
                                     int enable_first,
                                     int enable_second,
                                     unsigned char meter_style,
                                     const unsigned char channel_colors[4][3],
                                     uint16_t *next_sequence) {
    for (int channel = 0; channel < 4; ++channel) {
        const unsigned char *channel_color =
            channel_colors == NULL ? NULL : channel_colors[channel];
        const int first_result = send_native_meter_state(
            context, device, (unsigned char)channel,
            enable_first, meter_style, channel_color, next_sequence);
        if (first_result != LIBUSB_SUCCESS) {
            return first_result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
        const int second_result = send_native_meter_state(
            context, device, (unsigned char)(0x80 | channel),
            enable_second, meter_style, channel_color, next_sequence);
        if (second_result != LIBUSB_SUCCESS) {
            return second_result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
    }
    return LIBUSB_SUCCESS;
}

static int activate_fullscreen_layout(libusb_context *context,
                                      libusb_device_handle *device,
                                      unsigned char volume_meters,
                                      unsigned char meter_style,
                                      const unsigned char channel_colors[4][3],
                                      uint16_t *next_sequence) {
    /* IMG_1259.MOV confirms that SDK action-zone style 1 exposes the complete
     * 480x272 framebuffer without adding a native border. The original meter
     * layers require both their 0x34 configurations and the accompanying 0x32
     * panel records; clearing either family makes them invisible. */
    int result = send_display_configuration(
        context, device,
        0xffffffffu, 0xffffffffu, 1, 0xffffffffu,
        next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    result = send_native_panel_state(
        context, device, volume_meters ? 0 : 1, meter_style, next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    return set_native_meter_surfaces(
        context, device,
        volume_meters ? 1 : 0,
        volume_meters ? 1 : 0,
        meter_style,
        channel_colors,
        next_sequence);
}

static int activate_fullscreen_image_layout(libusb_context *context,
                                            libusb_device_handle *device,
                                            uint16_t *next_sequence) {
    /* Image mode retains the established black action-zone decoration. */
    int result = send_display_configuration(
        context, device,
        0xffffffffu, 0xffffffffu, 1, 0x00000000u,
        next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    result = send_native_panel_state(
        context, device, 1, DEFAULT_METER_STYLE, next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    return set_native_meter_surfaces(
        context, device, 0, 0, DEFAULT_METER_STYLE, NULL, next_sequence);
}

static int activate_notepad_layout(libusb_context *context,
                                   libusb_device_handle *device,
                                   uint16_t *next_sequence) {
    /* Hardware colour phases confirmed that SDK ARGB #181F2A makes the three
     * firmware-owned action-zone separators blend into the Notepad card. Keep
     * this mode separate from arbitrary full-screen images, whose pixels may
     * use unrelated colours beneath the action zone. */
    int result = send_display_configuration(
        context, device,
        0xffffffffu, 0xffffffffu, 1, 0xff181f2au,
        next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    result = send_native_panel_state(
        context, device, 1, DEFAULT_METER_STYLE, next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    return set_native_meter_surfaces(
        context, device, 0, 0, DEFAULT_METER_STYLE, NULL, next_sequence);
}

static int restore_native_compositor(libusb_context *context,
                                     libusb_device_handle *device,
                                     uint16_t *next_sequence) {
    int result = send_display_configuration(
        context, device,
        0xffffffffu, 0xffffffffu, 4, 0xffffffffu,
        next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    result = send_native_panel_state(
        context, device, 0, DEFAULT_METER_STYLE, next_sequence);
    if (result != LIBUSB_SUCCESS) {
        return result;
    }
    sleep_ms(NATIVE_MESSAGE_GAP_MS);
    return set_native_meter_surfaces(
        context, device, 1, 1, DEFAULT_METER_STYLE, NULL, next_sequence);
}

static int run_fullscreen_style_test(libusb_context *context,
                                     libusb_device_handle *device,
                                     uint16_t *next_sequence) {
    static const char *names[6] = {
        "action-zone style 0",
        "action-zone style 1",
        "action-zone style 2",
        "action-zone style 3",
        "action-zone style 4",
        "action-zone style 5",
    };

    for (int phase = 0; phase < 6; ++phase) {
        int result = send_display_configuration(
            context, device,
            0xffffffffu, 0xffffffffu,
            (unsigned char)phase, 0xffffffffu,
            next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
        result = send_native_panel_state(
            context, device, 1, DEFAULT_METER_STYLE, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
        result = set_native_meter_surfaces(
            context, device, 0, 0, DEFAULT_METER_STYLE, NULL, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        const int badge_result = send_native_percentage_badge(
            context, device,
            0, (unsigned int)phase,
            0, 1, default_channel_colors, NULL, next_sequence);
        if (badge_result != LIBUSB_SUCCESS) {
            return badge_result;
        }
        fprintf(stderr, "Fullscreen action-zone phase %d: %s.\n",
                phase, names[phase]);
        const int hold_result = hold_mask_phase(context, device, next_sequence);
        if (hold_result != LIBUSB_SUCCESS) {
            return hold_result;
        }
    }

    const int restore_result = restore_native_compositor(
        context, device, next_sequence);
    if (restore_result == LIBUSB_SUCCESS) {
        fprintf(stderr, "Fullscreen compositor baseline restored.\n");
    }
    return restore_result;
}

static int run_action_zone_color_test(libusb_context *context,
                                      libusb_device_handle *device,
                                      uint16_t *next_sequence) {
    /* The Notepad card is authored as RGB 24,31,42 and reaches the panel as
     * RGB565 24,28,41.  Exercise the RGB/alpha packings used by the Windows
     * SDK so the firmware-drawn separators can be matched empirically. */
    static const uint32_t colors[6] = {
        0xff181f2au, /* Qt ARGB, authored card colour. */
        0x002a1f18u, /* Windows COLORREF, authored card colour. */
        0xff181c29u, /* Qt ARGB, RGB565 card colour. */
        0x00291c18u, /* Windows COLORREF, RGB565 card colour. */
        0x00181f2au, /* Plain 0xRRGGBB, authored card colour. */
        0x00181c29u, /* Plain 0xRRGGBB, RGB565 card colour. */
    };
    static const char *names[6] = {
        "ARGB authored #181F2A",
        "COLORREF authored #181F2A",
        "ARGB RGB565 #181C29",
        "COLORREF RGB565 #181C29",
        "RRGGBB authored #181F2A",
        "RRGGBB RGB565 #181C29",
    };

    for (int phase = 0; phase < 6; ++phase) {
        int result = send_display_configuration(
            context, device,
            0xffffffffu, 0xffffffffu, 1, colors[phase],
            next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
        result = send_native_panel_state(
            context, device, 1, DEFAULT_METER_STYLE, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        sleep_ms(NATIVE_MESSAGE_GAP_MS);
        result = set_native_meter_surfaces(
            context, device, 0, 0, DEFAULT_METER_STYLE, NULL, next_sequence);
        if (result != LIBUSB_SUCCESS) {
            return result;
        }
        const int badge_result = send_native_percentage_badge(
            context, device,
            0, (unsigned int)phase,
            0, 1, default_channel_colors, NULL, next_sequence);
        if (badge_result != LIBUSB_SUCCESS) {
            return badge_result;
        }
        fprintf(stderr, "Action-zone colour phase %d: %s (0x%08x).\n",
                phase, names[phase], (unsigned int)colors[phase]);
        const int hold_result = hold_mask_phase(context, device, next_sequence);
        if (hold_result != LIBUSB_SUCCESS) {
            return hold_result;
        }
    }

    const int restore_result = restore_native_compositor(
        context, device, next_sequence);
    if (restore_result == LIBUSB_SUCCESS) {
        fprintf(stderr, "Fullscreen compositor baseline restored.\n");
    }
    return restore_result;
}

int main(int argc, char **argv) {
    const int native_object_test =
        argc == 3 && strcmp(argv[2], "--object-test") == 0;
    const int fullscreen_mask_test =
        argc == 3 && strcmp(argv[2], "--fullscreen-test") == 0;
    const int action_zone_color_test =
        argc == 3 && strcmp(argv[2], "--action-color-test") == 0;
    if (argc != 2 && !native_object_test && !fullscreen_mask_test &&
        !action_zone_color_test) {
        fprintf(stderr,
                "Usage: %s stream100-display-replay.bin "
                "[--object-test|--fullscreen-test|--action-color-test]\n",
                argv[0]);
        return 2;
    }

    const size_t replay_size = (size_t)REPLAY_PACKET_COUNT * ISO_PACKET_SIZE;
    unsigned char *replay = read_file(argv[1], replay_size);
    unsigned char *frame_packets =
        malloc((size_t)FRAME_PACKET_COUNT * ISO_PACKET_SIZE);
    unsigned char *frame_input = malloc(FRAME_INPUT_SIZE);
    libusb_context *context = NULL;
    libusb_device_handle *device = NULL;
    int interface_claimed = 0;
    int exit_code = 1;

    if (replay == NULL || frame_packets == NULL || frame_input == NULL) {
        fprintf(stderr, "Could not allocate the display buffers.\n");
        goto cleanup;
    }
    if (patch_initial_brightness(replay, 0) != 0) {
        goto cleanup;
    }
    if (patch_early_initial_brightness(replay, 0) != 0) {
        goto cleanup;
    }
    if (patch_initial_native_panel_state(replay) != 0) {
        goto cleanup;
    }
    if (patch_initial_native_meter_state(replay) != 0) {
        goto cleanup;
    }
    if (replace_initial_active_surface_batch(replay) != 0) {
        goto cleanup;
    }
    fprintf(stderr,
            "Early brightness gate installed; captured active surface batch "
            "replaced with the official reset sequence; late native layout "
            "neutralized.\n");

    int usb_result = libusb_init(&context);
    if (usb_result != LIBUSB_SUCCESS) {
        fprintf(stderr, "Could not start libusb: %s\n", libusb_error_name(usb_result));
        goto cleanup;
    }
    device = libusb_open_device_with_vid_pid(context, STREAM100_VID, STREAM100_PID);
    if (device == NULL) {
        fprintf(stderr,
                "Stream 100 display was not found or access was denied.\n");
        goto cleanup;
    }

    libusb_set_auto_detach_kernel_driver(device, 1);
    usb_result = libusb_claim_interface(device, DISPLAY_INTERFACE);
    if (usb_result != LIBUSB_SUCCESS) {
        fprintf(stderr, "Could not claim the display interface: %s\n",
                libusb_error_name(usb_result));
        goto cleanup;
    }
    interface_claimed = 1;
    usb_result = libusb_set_interface_alt_setting(
        device, DISPLAY_INTERFACE, DISPLAY_ALT_SETTING);
    if (usb_result != LIBUSB_SUCCESS) {
        fprintf(stderr, "Could not enable the display endpoint: %s\n",
                libusb_error_name(usb_result));
        goto cleanup;
    }

    fprintf(stderr, "Initializing the Stream 100 display...\n");
    int init_sent = 0;
    int init_group = 0;
    while (init_sent < INIT_PACKET_COUNT) {
        int packet_count = INIT_PACKET_COUNT - init_sent;
        if (packet_count > PACKETS_PER_GROUP) {
            packet_count = PACKETS_PER_GROUP;
        }
        usb_result = send_packet_group(
            context,
            device,
            replay + (size_t)init_sent * ISO_PACKET_SIZE,
            packet_count);
        if (usb_result != LIBUSB_SUCCESS) {
            goto cleanup;
        }
        init_sent += packet_count;
        if (init_sent < INIT_PACKET_COUNT) {
            sleep_ms(init_group == 0 ? 1100 : 700);
        }
        init_group += 1;
    }
    fprintf(stderr, "Display ready.\n");

    const unsigned char *template_packets =
        replay + (size_t)INIT_PACKET_COUNT * ISO_PACKET_SIZE;
    const unsigned char *first_frame_message = template_packets +
        (size_t)FRAME_DATA_OFFSET_PACKETS * ISO_PACKET_SIZE;
    uint16_t next_sequence =
        (uint16_t)first_frame_message[6] |
        ((uint16_t)first_frame_message[7] << 8);
    /* Initialization restores captured native panel and meter layers before
     * the first framebuffer can take ownership. Clear those mapped surfaces
     * immediately so they do not remain visible throughout primer convergence. */
    usb_result = activate_fullscreen_image_layout(
        context, device, &next_sequence);
    if (usb_result != LIBUSB_SUCCESS) {
        goto cleanup;
    }
    usb_result = clear_native_percentage_badges(
        context, device, &next_sequence);
    if (usb_result != LIBUSB_SUCCESS) {
        goto cleanup;
    }
    fprintf(stderr,
            "Resident startup panel, meters, and badges cleared before "
            "framebuffer priming.\n");
    int display_primed = 0;
    unsigned char active_display_mode = 0;
    unsigned char active_page_index = 0;
    unsigned char active_page_count = 1;
    int active_page_valid = 0;
    unsigned char active_volume_meter_mode = 0;
    unsigned char active_meter_style = DEFAULT_METER_STYLE;
    unsigned char active_palette[FRAME_METADATA_V2_OFFSET];
    int active_palette_valid = 0;
    int backlight_revealed = 0;
    unsigned char active_brightness = 0;
    int native_object_test_sent = 0;
    int fullscreen_mask_test_sent = 0;
    int action_zone_color_test_sent = 0;
    unsigned char previous_levels[4] = {0xff, 0xff, 0xff, 0xff};
    unsigned char previous_meter_left_levels[4] = {
        0xff, 0xff, 0xff, 0xff,
    };
    unsigned char previous_meter_right_levels[4] = {
        0xff, 0xff, 0xff, 0xff,
    };
    unsigned char previous_muted_mask = 0xff;
    unsigned char previous_online_mask = 0xff;
    unsigned char previous_meter_mode = 0xff;
    unsigned char previous_button_leds[4] = {0xff, 0xff, 0xff, 0xff};
    fprintf(stderr, "Waiting for the first generated framebuffer.\n");

    while (1) {
        struct pollfd input = {
            .fd = STDIN_FILENO,
            .events = POLLIN | POLLHUP,
            .revents = 0,
        };
        const int poll_result = poll(&input, 1, KEEPALIVE_INTERVAL_MS);
        if (poll_result < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "Display input poll failed: %s\n", strerror(errno));
            goto cleanup;
        }
        if (poll_result == 0) {
            usb_result = send_idle_heartbeat(context, device, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            continue;
        }

        const int frame_result = read_frame(frame_input);
        if (frame_result == 0) {
            exit_code = 0;
            break;
        }
        if (frame_result < 0) {
            goto cleanup;
        }

        unsigned char native_levels[4] = {0, 0, 0, 0};
        unsigned char native_meter_left_levels[4] = {0, 0, 0, 0};
        unsigned char native_meter_right_levels[4] = {0, 0, 0, 0};
        unsigned char native_muted_mask = 0;
        unsigned char native_online_mask = 0;
        unsigned char native_display_mode = 1;
        unsigned char native_channel_colors[4][3];
        unsigned char native_button_leds[4] = {0, 0, 0, 0};
        unsigned char native_page_index = 0;
        unsigned char native_page_count = 1;
        unsigned char native_meter_style = DEFAULT_METER_STYLE;
        unsigned char native_volume_meters = 0;
        unsigned char native_display_brightness = DEFAULT_DISPLAY_BRIGHTNESS;
        uint16_t native_badge_backdrops[4][32][BADGE_BACKDROP_COLUMNS];
        read_native_badge_backdrops(frame_input, native_badge_backdrops);
        int has_native_metadata = read_native_metadata(
            frame_input, native_levels,
            native_meter_left_levels, native_meter_right_levels,
            &native_muted_mask, &native_online_mask,
            &native_display_mode,
            native_channel_colors,
            native_button_leds,
            &native_page_index,
            &native_page_count,
            &native_meter_style,
            &native_volume_meters,
            &native_display_brightness);
        if (!has_native_metadata && is_black_startup_primer(frame_input)) {
            has_native_metadata = 1;
            native_display_mode = 4;
            fprintf(stderr,
                    "All-zero startup primer detected as display mode 4.\n");
        }

        const int custom_meter_active = has_native_metadata &&
            uses_custom_meter_geometry(
                native_display_mode, native_volume_meters,
                native_meter_style);

        const int display_mode_changed =
            display_primed && has_native_metadata &&
            native_display_mode != active_display_mode;
        const int primer_to_startup =
            display_mode_changed && active_display_mode == 4 &&
            native_display_mode == 3;
        const int startup_to_saved =
            display_mode_changed && active_display_mode == 3 &&
            native_display_mode != 3;
        const int saved_to_startup =
            display_mode_changed && active_display_mode != 3 &&
            active_display_mode != 4 && native_display_mode == 3;
        const int page_changed =
            display_primed && has_native_metadata && active_page_valid &&
            native_display_mode == 1 && active_display_mode == 1 &&
            (native_page_index != active_page_index ||
             native_page_count != active_page_count);
        const int previous_custom_meter_active =
            display_primed && uses_custom_meter_geometry(
                active_display_mode, active_volume_meter_mode,
                active_meter_style);
        const int custom_meter_geometry_changed =
            display_primed &&
            (custom_meter_active != previous_custom_meter_active ||
             (custom_meter_active && previous_custom_meter_active &&
              native_meter_style != active_meter_style));

        /* After the one-time framebuffer commit, every style uses only the
         * proven native percentage, activity, and volume objects. Custom
         * framebuffer artwork is decorative and is never resent for audio. */
        if (display_primed && has_native_metadata &&
            !display_mode_changed &&
            !page_changed &&
            !custom_meter_geometry_changed &&
            !native_object_test && !fullscreen_mask_test &&
            !action_zone_color_test) {
            usb_result = send_native_button_leds(
                context, device, native_button_leds, 0,
                previous_button_leds, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            if (native_display_mode == 1) {
                if ((native_volume_meters != 0) !=
                        (active_volume_meter_mode != 0) ||
                    (native_volume_meters != 0 &&
                        native_meter_style != active_meter_style)) {
                    usb_result = activate_fullscreen_layout(
                        context, device, native_volume_meters,
                        native_meter_style,
                        native_channel_colors,
                        &next_sequence);
                    if (usb_result != LIBUSB_SUCCESS) {
                        goto cleanup;
                    }
                    active_volume_meter_mode = native_volume_meters;
                    active_meter_style = native_meter_style;
                    fprintf(stderr,
                            "Native central meter surfaces %s with visualiser "
                            "style %u after the mixer setting changed.\n",
                            native_volume_meters ? "enabled" : "disabled",
                            (unsigned int)native_meter_style);
                }
                usb_result = send_native_percentages(
                    context, device, native_levels,
                    native_meter_left_levels, native_meter_right_levels,
                    native_muted_mask, native_online_mask,
                    native_channel_colors, native_badge_backdrops,
                    native_volume_meters, 0,
                    previous_levels,
                    previous_meter_left_levels,
                    previous_meter_right_levels,
                    &previous_muted_mask, &previous_online_mask,
                    &previous_meter_mode,
                    &next_sequence);
                if (usb_result != LIBUSB_SUCCESS) {
                    goto cleanup;
                }
            }
            if (backlight_revealed && native_display_mode != 4) {
                const unsigned char requested_brightness =
                    native_display_mode == 3
                        ? STARTUP_LOGO_BRIGHTNESS
                        : native_display_brightness;
                if (requested_brightness != active_brightness) {
                    usb_result = send_display_brightness(
                        context, device, requested_brightness, &next_sequence);
                    if (usb_result != LIBUSB_SUCCESS) {
                        goto cleanup;
                    }
                    active_brightness = requested_brightness;
                    fprintf(stderr,
                            "Screen brightness updated live to %u%%.\n",
                            (unsigned int)active_brightness);
                }
            }
            continue;
        }

        /* Ordinary established redraws retain the controller palette. The two
         * startup handoffs are already backlight-gated, so they may install the
         * dedicated logo palette and then restore the saved-screen palette. */
        if (display_mode_changed && !primer_to_startup && !startup_to_saved &&
            !saved_to_startup &&
            active_palette_valid &&
            memcmp(active_palette, frame_input,
                   FRAME_METADATA_V2_OFFSET) != 0) {
            fprintf(stderr,
                    "Display mode transition rejected: visible palette changed.\n");
            continue;
        }
        if ((startup_to_saved || saved_to_startup ||
             custom_meter_geometry_changed) && backlight_revealed) {
            /* The panel has no atomic framebuffer swap: an established redraw
             * exposes its 32 interleaved planes as they arrive. Hide both the
             * startup-to-saved upload and the graceful saved-to-logo shutdown
             * upload, then reveal the complete result through the common
             * post-layout path. */
            usb_result = send_display_brightness(
                context, device, 0, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            backlight_revealed = 0;
            active_brightness = 0;
            sleep_ms(NATIVE_MESSAGE_GAP_MS);
            if (custom_meter_geometry_changed) {
                fprintf(stderr,
                        "Backlight hidden for the visualiser silhouette "
                        "transition.\n");
            } else if (saved_to_startup) {
                fprintf(stderr,
                        "Backlight hidden for the graceful logo transition.\n");
            } else {
                fprintf(stderr,
                        "Backlight hidden for the final startup transition.\n");
            }
        }
        if (display_mode_changed) {
            if (primer_to_startup || startup_to_saved || saved_to_startup) {
                fprintf(stderr,
                        "Display mode transition %u -> %u will replace the "
                        "palette while hidden.\n",
                        (unsigned int)active_display_mode,
                        (unsigned int)native_display_mode);
            } else {
                fprintf(stderr,
                        "Display mode transition %u -> %u will reuse the "
                        "active palette.\n",
                        (unsigned int)active_display_mode,
                        (unsigned int)native_display_mode);
            }
        }

        const int page_palette_changed =
            page_changed && active_palette_valid &&
            memcmp(active_palette, frame_input,
                   FRAME_METADATA_V2_OFFSET) != 0;
        if (page_palette_changed) {
            /* Page navigation is an interactive operation. Replace the
             * palette and framebuffer through the established full-redraw
             * sequence while the panel remains visible, matching the native
             * application's blank-free behavior. Startup and display-mode
             * transitions retain their separate hidden safety gates. */
            fprintf(stderr,
                    "Mixer page palette will be replaced while the panel "
                    "remains visible.\n");
        }
        if (page_changed) {
            fprintf(stderr,
                    "Mixer page transition %u/%u -> %u/%u requested.\n",
                    (unsigned int)(active_page_index + 1u),
                    (unsigned int)active_page_count,
                    (unsigned int)(native_page_index + 1u),
                    (unsigned int)native_page_count);
        }

        memcpy(frame_packets, template_packets,
               (size_t)FRAME_PACKET_COUNT * ISO_PACKET_SIZE);
        const int should_prime = !display_primed;
        const int include_display_setup =
            should_prime || primer_to_startup || startup_to_saved ||
            saved_to_startup ||
            custom_meter_geometry_changed ||
            page_palette_changed;
        if (should_prime) {
            const uint16_t priming_sequence_start = next_sequence;
            if (patch_priming_frame(frame_packets,
                                    frame_input,
                                    frame_input + PALETTE_SIZE,
                                    &next_sequence) != 0) {
                goto cleanup;
            }
            fprintf(stderr,
                    "Priming CRCs regenerated for sequences %u-%u.\n",
                    (unsigned int)priming_sequence_start,
                    (unsigned int)(uint16_t)(next_sequence - 1u));
            const uint16_t cleanup_tail_sequence_start = next_sequence;
            if (patch_startup_cleanup_tails(
                    frame_packets, &next_sequence) != 0) {
                goto cleanup;
            }
            fprintf(stderr,
                    "Active startup cleanup interleaved for "
                    "sequences %u-%u.\n",
                    (unsigned int)cleanup_tail_sequence_start,
                    (unsigned int)(uint16_t)(next_sequence - 1u));
            usb_result = send_frame(context, device, frame_packets, 1);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            display_primed = 1;
            fprintf(stderr, "Controller state activated once.\n");
        }

        /* Exact native message boundaries remove the need for repeated
         * convergence redraws. Startup already receives the priming pass. */
        const int clean_passes = 1;
        for (int pass = 0; pass < clean_passes; ++pass) {
            const uint16_t clean_sequence_start = next_sequence;
            if (patch_clean_frame(frame_packets,
                                  frame_input,
                                  frame_input + PALETTE_SIZE,
                                  &next_sequence,
                                  0,
                                  include_display_setup) != 0) {
                goto cleanup;
            }
            fprintf(stderr,
                    "Clean redraw %d/%d CRCs regenerated for sequences %u-%u.\n",
                    pass + 1,
                    clean_passes,
                    (unsigned int)clean_sequence_start,
                    (unsigned int)(uint16_t)(next_sequence - 1u));
            usb_result = send_frame(context, device, frame_packets, 0);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            fprintf(stderr, "Clean redraw pass %d/%d sent.\n",
                    pass + 1, clean_passes);
            if (primer_to_startup) {
                fprintf(stderr,
                        "Established branded redraw replaced the resident-"
                        "matched primer palette while hidden.\n");
            } else if (startup_to_saved) {
                fprintf(stderr,
                        "Hidden final redraw replaced the dedicated logo "
                        "palette with the saved-screen palette.\n");
            } else if (saved_to_startup) {
                fprintf(stderr,
                        "Hidden graceful-stop redraw replaced the saved-screen "
                        "palette with the dedicated logo palette.\n");
            } else if (!should_prime) {
                fprintf(stderr,
                        "Established redraw sent pixels only; display setup "
                        "and palette retained.\n");
            }
        }

        const uint16_t latch_sequence = next_sequence;
        if (patch_single_latch_tail(frame_packets, &next_sequence) != 0) {
            goto cleanup;
        }
        fprintf(stderr,
                "Final latch CRC regenerated for sequence %u.\n",
                (unsigned int)latch_sequence);
        usb_result = send_single_latch_tail(context, device, frame_packets);
        if (usb_result != LIBUSB_SUCCESS) {
            goto cleanup;
        }
        fprintf(stderr,
                "Single post-frame latch sent after the completed image.\n");
        fprintf(stderr,
                "Neutral framebuffer committed and active lease renewed.\n");

        size_t changed_bytes = 0;
        for (size_t byte = 0;
             byte < (size_t)FRAME_PACKET_COUNT * ISO_PACKET_SIZE;
             ++byte) {
            if (frame_packets[byte] != template_packets[byte]) {
                changed_bytes += 1;
            }
        }
        fprintf(stderr,
                "Captured template byte match: %s\n",
                changed_bytes == 0 ? "YES" : "NO");
        fprintf(stderr,
                "Captured template bytes changed: %zu\n",
                changed_bytes);
        fprintf(stderr,
                "Payload chunk indices: %u,%u,%u,%u ... %u\n",
                (unsigned int)frame_input[PALETTE_SIZE],
                (unsigned int)frame_input[PALETTE_SIZE + CHUNK_SIZE],
                (unsigned int)frame_input[PALETTE_SIZE + 2 * CHUNK_SIZE],
                (unsigned int)frame_input[PALETTE_SIZE + 3 * CHUNK_SIZE],
                (unsigned int)frame_input[
                    PALETTE_SIZE + (CHUNK_COUNT - 1) * CHUNK_SIZE]);
        fprintf(stderr, "Generated framebuffer refresh completed.\n");

        if (!active_palette_valid || primer_to_startup || startup_to_saved ||
            saved_to_startup ||
            custom_meter_geometry_changed ||
            page_palette_changed) {
            memcpy(active_palette, frame_input, FRAME_METADATA_V2_OFFSET);
            active_palette_valid = 1;
        }

        if (has_native_metadata && !native_object_test) {
            if (!fullscreen_mask_test) {
                if (native_display_mode == 5) {
                    usb_result = activate_notepad_layout(
                        context, device, &next_sequence);
                } else if (native_display_mode == 2 ||
                           native_display_mode == 3 ||
                           native_display_mode == 4) {
                    usb_result = activate_fullscreen_image_layout(
                        context, device, &next_sequence);
                } else {
                    usb_result = activate_fullscreen_layout(
                        context, device, native_volume_meters,
                        native_meter_style,
                        native_channel_colors,
                        &next_sequence);
                }
                if (usb_result != LIBUSB_SUCCESS) {
                    goto cleanup;
                }
                if (native_display_mode == 5) {
                    fprintf(stderr,
                            "Notepad layout activated with action-zone "
                            "separators matched to the card.\n");
                } else if (native_display_mode == 2) {
                    fprintf(stderr,
                            "Full 480x272 image layout activated.\n");
                } else if (native_display_mode == 3) {
                    fprintf(stderr,
                            "OpenStream100 startup layout activated.\n");
                } else if (native_display_mode == 4) {
                    fprintf(stderr,
                            "Resident-matched startup primer layout activated.\n");
                } else {
                    fprintf(stderr,
                            "Full 480x272 layout activated with action-zone "
                            "style 1.\n");
                    fprintf(stderr,
                            "Native firmware volume meters: %s "
                            "(visualiser style %u).\n",
                            native_volume_meters ? "enabled" : "disabled",
                            (unsigned int)native_meter_style);
                }
            }
            usb_result = send_native_button_leds(
                context, device, native_button_leds, 1,
                previous_button_leds, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            if (native_display_mode == 2 || native_display_mode == 3 ||
                native_display_mode == 4 || native_display_mode == 5) {
                usb_result = clear_native_percentage_badges(
                    context, device, &next_sequence);
                if (usb_result != LIBUSB_SUCCESS) {
                    goto cleanup;
                }
                if (native_display_mode == 5) {
                    fprintf(stderr,
                            "Native percentage objects cleared for Notepad "
                            "mode.\n");
                } else if (native_display_mode == 2) {
                    fprintf(stderr,
                            "Native percentage objects cleared for full-screen "
                            "image mode.\n");
                } else if (native_display_mode == 4) {
                    fprintf(stderr,
                            "Native percentage objects cleared for the resident-matched "
                            "startup primer.\n");
                } else {
                    fprintf(stderr,
                            "Native percentage objects cleared for the startup "
                            "screen.\n");
                }
            } else {
                usb_result = send_native_percentages(
                    context, device, native_levels,
                    native_meter_left_levels, native_meter_right_levels,
                    native_muted_mask, native_online_mask,
                    native_channel_colors, native_badge_backdrops,
                    native_volume_meters, 1,
                    previous_levels,
                    previous_meter_left_levels,
                    previous_meter_right_levels,
                    &previous_muted_mask, &previous_online_mask,
                    &previous_meter_mode,
                    &next_sequence);
                if (usb_result != LIBUSB_SUCCESS) {
                    goto cleanup;
                }
                fprintf(stderr,
                        "Tear-free native percentage overlay initialized.\n");
            }
            active_display_mode = native_display_mode;
            active_page_index = native_page_index;
            active_page_count = native_page_count;
            active_page_valid = 1;
            active_volume_meter_mode =
                native_display_mode == 1 ? native_volume_meters : 0;
            active_meter_style = native_display_mode == 1
                ? native_meter_style
                : DEFAULT_METER_STYLE;
        }

        if (!backlight_revealed && has_native_metadata &&
            native_display_mode == 4) {
            /* The priming plus clean pass above completes a framebuffer whose
             * visible palette and pixels match the current saved screen. This
             * avoids replacing the resident mixer with black plane by plane
             * during the controller's unavoidable visible first composition.
             * Commit brightness zero now and proceed directly to the hidden,
             * established logo redraw. */
            fprintf(stderr,
                    "Resident-matched startup primer committed; preparing "
                    "the logo.\n");
            usb_result = send_display_brightness(
                context, device, 0, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            sleep_ms(NATIVE_MESSAGE_GAP_MS);
            active_brightness = 0;
            fprintf(stderr,
                    "Backlight remains hidden for the branded frame.\n");
            continue;
        }

        if (!backlight_revealed) {
            if (has_native_metadata && native_display_mode == 3) {
                fprintf(stderr,
                        "Established branded startup frame completed after "
                        "the black primer.\n");
            }
            const unsigned char reveal_brightness =
                has_native_metadata && native_display_mode == 3
                    ? STARTUP_LOGO_BRIGHTNESS
                    : native_display_brightness;
            usb_result = send_display_brightness(
                context, device, reveal_brightness, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            backlight_revealed = 1;
            active_brightness = reveal_brightness;
            fprintf(stderr,
                    "Backlight restored to %u%% after the complete generated "
                    "frame.\n",
                    (unsigned int)reveal_brightness);
        }
        if (has_native_metadata && native_display_mode == 3) {
            fprintf(stderr,
                    "Holding the OpenStream100 startup screen briefly.\n");
            usb_result = hold_startup_screen(
                context, device, &next_sequence, STARTUP_LOGO_HEARTBEATS);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
        }

        if (native_object_test && !native_object_test_sent) {
            fprintf(stderr,
                    "Waiting briefly before the native compositor diagnostic.\n");
            usb_result = run_native_object_test(context, device, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            native_object_test_sent = 1;
        }

        if (fullscreen_mask_test && !fullscreen_mask_test_sent) {
            fprintf(stderr,
                    "Starting six-phase native fullscreen action-zone diagnostic.\n");
            usb_result = run_fullscreen_style_test(
                context, device, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            fullscreen_mask_test_sent = 1;
        }

        if (action_zone_color_test && !action_zone_color_test_sent) {
            fprintf(stderr,
                    "Starting six-phase Notepad action-zone colour diagnostic.\n");
            usb_result = run_action_zone_color_test(
                context, device, &next_sequence);
            if (usb_result != LIBUSB_SUCCESS) {
                goto cleanup;
            }
            action_zone_color_test_sent = 1;
        }
    }

cleanup:
    if (device != NULL && interface_claimed) {
        libusb_set_interface_alt_setting(device, DISPLAY_INTERFACE, 0);
        libusb_release_interface(device, DISPLAY_INTERFACE);
    }
    if (device != NULL) {
        libusb_close(device);
    }
    if (context != NULL) {
        libusb_exit(context);
    }
    free(frame_input);
    free(frame_packets);
    free(replay);
    return exit_code;
}
