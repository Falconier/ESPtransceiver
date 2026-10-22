/*
 * Standalone beacon for the ESP32-S3: the esp32-sdr-trx transmitter without a host, in CW and Olivia 16/500.
 *
 * Replaces the firmware's command loop (main.c) when built with `make BEACON=1` (see beacon/README.md). At power-up the
 * board waits BEACON_HOLDOFF_S seconds (LED blinking amber), sets up the radio (white) and the transmitter (cyan), then sends
 * one ID every BEACON_PERIOD_S seconds, forever, in one of three modes. The LED's colour shows the mode, bright while the board
 * transmits and dim while it does not:
 *   red     CW: the ID at BEACON_WPM (the LED follows the key), then a steady carrier to the end of the period, as the station
 *           window's beacon does;
 *   green   Olivia 16/500: a 2 s lead tone (the receivers' AFC locks on it), the ID in Olivia, then the carrier off until the
 *           next period;
 *   purple  both in turn: CW one period, Olivia the next.
 * A short press of BOOT moves to the next mode (it starts at once, with the next ID). A long press (1.5 s) pauses: the carrier
 * goes off and the LED blinks the mode's colour; another long press resumes. Blinking blue: the radio could not be set up.
 *
 * The image boots from flash address 0 without ESP-IDF's bootloader (the ROM loads it), or loads into RAM over USB.
 *
 * The carrier sits at dial + tone like an SSB rig's CW: a receiver tuned to BEACON_DIAL_HZ (USB) hears BEACON_TONE_HZ.
 * Between elements the carrier drops to the weakest gain code (only ~18 dB down) and moves BEACON_SPACE_HZ away, as the
 * station's "Shift carrier in spaces" option does.
 *
 * Transmitting needs an amateur licence that covers the frequency. Put your own call sign in BEACON_TEXT.
 */
#include <stdbool.h>
#include <stdint.h>

#include "board.h"
#include "control.h"
#include "platform.h"
#include "radio.h"
#include "transmit.h"
#include "olivia_symbols.h"
#include "soc/gpio_reg.h"
#include "soc/gpio_sig_map.h"
#include "soc/io_mux_reg.h"
#include "soc/rtc_cntl_reg.h"
#include "soc/rtc_io_reg.h"

/* ---- settings (numbers can also be set from make: BEACON_DEFS="-DBEACON_WPM=15") ------------------------------- */
#ifndef BEACON_TEXT
#define BEACON_TEXT "DE W4DQB/EXP"
#endif
#ifndef BEACON_DIAL_HZ
#define BEACON_DIAL_HZ 2304350000u /* the receiver's dial (USB); 2300.005 .. 2449.995 MHz */
#endif
#ifndef BEACON_TONE_HZ
#define BEACON_TONE_HZ 700 /* the carrier sits this far above the dial */
#endif
#ifndef BEACON_WPM
#define BEACON_WPM 20
#endif
#ifndef BEACON_PERIOD_S
#define BEACON_PERIOD_S 30 /* one ID per period; a steady carrier fills the rest */
#endif
#ifndef BEACON_SPACE_HZ
#define BEACON_SPACE_HZ 12000 /* carrier shift during key-up (0: stay on frequency at the weakest gain) */
#endif
#ifndef BEACON_PEAK_CODE
#define BEACON_PEAK_CODE TX_GAIN_STRONGEST /* gain code of key-down: 64 strongest (~ -17 dBm EIRP) .. 127 weakest */
#endif
#ifndef BEACON_HOLDOFF_S
#define BEACON_HOLDOFF_S 5 /* seconds between power-up and the first transmission */
#endif
#ifndef BEACON_MODE
#define BEACON_MODE 0 /* mode at power-up: 0 CW, 1 Olivia 16/500, 2 both in turn */
#endif
#ifndef BEACON_OLIVIA_LEAD_S
#define BEACON_OLIVIA_LEAD_S 2 /* steady tone before the Olivia symbols */
#endif
#ifndef LED_GPIO
#define LED_GPIO 48 /* WS2812 data pin: GPIO48 on ESP32-S3-DevKitC-1 v1.0, GPIO38 on v1.1 */
#endif
#ifndef LED_LEVEL
#define LED_LEVEL 40 /* LED brightness, 0 .. 255 */
#endif

#define RATE 8000u                     /* updates per second, as the station's transmitter uses */
#define CPU_HZ 240000000u
#define TICK_CYCLES (CPU_HZ / RATE)
#define RAMP 40                        /* 5 ms raised-cosine key edges */
#define BUTTON_TICKS (RATE / 20)       /* 50 ms debounce */
#define LONG_PRESS_TICKS (RATE * 3 / 2) /* a press this long pauses or resumes; shorter ones change the mode */
#define BOOT_GPIO 0
#define MAX_Q4 ((TX_MAX_STEPS - 2) * 16) /* the reach of an offset, two steps inside the chip's clamp (as the host keeps it) */

_Static_assert(BEACON_DIAL_HZ >= TX_MIN_HZ + 5000u && BEACON_DIAL_HZ <= TX_MAX_HZ - 5000u, "the dial is outside the transmitter's band");
_Static_assert(BEACON_PEAK_CODE >= TX_GAIN_STRONGEST && BEACON_PEAK_CODE <= TX_GAIN_WEAKEST, "gain codes are 64 .. 127");
_Static_assert(BEACON_WPM >= 5 && BEACON_WPM <= 40, "5 .. 40 WPM");
_Static_assert(BEACON_MODE >= 0 && BEACON_MODE <= 2, "modes are 0 CW, 1 Olivia, 2 both");
_Static_assert(OLIVIA_TONE_HZ == BEACON_TONE_HZ, "olivia_symbols.h is out of date: run beacon/gen_olivia.py");
_Static_assert((4 + BEACON_OLIVIA_LEAD_S) * RATE + OLIVIA_SYMBOLS * OLIVIA_SYMBOL_TICKS + 2 * RATE < BEACON_PERIOD_S * RATE,
               "the Olivia ID does not fit the period");

/* ---- RGB LED (WS2812, GRB order) ------------------------------------------------------------------------------- */

static inline void led_pin(bool high)
{
#if LED_GPIO < 32
    REG(high ? GPIO_OUT_W1TS_REG : GPIO_OUT_W1TC_REG) = 1u << LED_GPIO;
#else
    REG(high ? GPIO_OUT1_W1TS_REG : GPIO_OUT1_W1TC_REG) = 1u << (LED_GPIO - 32);
#endif
}

static void led_init(void)
{
    REG(PERIPHS_IO_MUX_GPIO0_U + 4 * LED_GPIO) = (PIN_FUNC_GPIO << MCU_SEL_S) | (1u << FUN_DRV_S);
    REG(GPIO_FUNC0_OUT_SEL_CFG_REG + 4 * LED_GPIO) = SIG_GPIO_OUT_IDX | GPIO_FUNC0_OEN_SEL;
    led_pin(false);
#if LED_GPIO < 32
    REG(GPIO_ENABLE_W1TS_REG) = 1u << LED_GPIO;
#else
    REG(GPIO_ENABLE1_W1TS_REG) = 1u << (LED_GPIO - 32);
#endif
    delay_us(300);
}

/* 24 bits of 1.25 us (a one is 0.7 us high, a zero 0.3 us); ~30 us, well inside one 125 us update. */
static void led_set(uint8_t r, uint8_t g, uint8_t b)
{
    uint32_t grb = (uint32_t)g << 16 | (uint32_t)r << 8 | b;
    for (int bit = 23; bit >= 0; --bit) {
        uint32_t high = (grb >> bit) & 1 ? 168 : 72;
        uint32_t start = cpu_cycles();
        led_pin(true);
        while (cpu_cycles() - start < high) {}
        led_pin(false);
        while (cpu_cycles() - start < 300) {}
    }
}

#define LED_OFF() led_set(0, 0, 0)
#define LED_RED() led_set(LED_LEVEL, 0, 0)
#define LED_GREEN() led_set(0, LED_LEVEL, 0)
#define LED_BLUE() led_set(0, 0, LED_LEVEL)
#define LED_AMBER() led_set(LED_LEVEL, LED_LEVEL / 3, 0)
#define LED_PURPLE() led_set(LED_LEVEL, 0, LED_LEVEL)
#define LED_CYAN() led_set(0, LED_LEVEL, LED_LEVEL)
#define LED_WHITE() led_set(LED_LEVEL / 2, LED_LEVEL / 2, LED_LEVEL / 2)

enum { MODE_CW, MODE_OLIVIA, MODE_BOTH, MODES };

/* The mode's colour: red, green or purple; bright, dim (a quarter) or off. */
static void led_mode(unsigned mode, unsigned level)
{
    led_set(mode != MODE_OLIVIA ? level : 0, mode == MODE_OLIVIA ? level : 0, mode == MODE_BOTH ? level : 0);
}
#define BRIGHT LED_LEVEL
#define DIM (LED_LEVEL / 4)

/* ---- BOOT button ------------------------------------------------------------------------------------------------ */

static void button_init(void)
{
    /* GPIO0 is also RTC pad 0. After a reset over USB (not after a power-up) its digital input can stay cut off: route the pad
     * to the digital side and release any pad hold, which only a power-on reset clears. */
    REG(RTC_IO_TOUCH_PAD0_REG) &= ~RTC_IO_TOUCH_PAD0_MUX_SEL;
    REG(RTC_CNTL_PAD_HOLD_REG) &= ~RTC_CNTL_TOUCH_PAD0_HOLD;
    REG(PERIPHS_IO_MUX_GPIO0_U + 4 * BOOT_GPIO) = (PIN_FUNC_GPIO << MCU_SEL_S) | FUN_IE | FUN_PU;
}

enum { BUTTON_NONE, BUTTON_SHORT, BUTTON_LONG };

static unsigned button_count, button_held;
static bool button_down, button_long_done;

/* Called once per tick. BUTTON_SHORT when a press shorter than 1.5 s ends, BUTTON_LONG once when a press reaches 1.5 s
 * (its release then does nothing). Changes of the pin count only after 50 ms (debounce). */
static unsigned button_event(void)
{
    bool now = !(REG(GPIO_IN_REG) & (1u << BOOT_GPIO));
    if (button_down && ++button_held == LONG_PRESS_TICKS && !button_long_done) {
        button_long_done = true;
        return BUTTON_LONG;
    }
    if (now == button_down) {
        button_count = 0;
        return BUTTON_NONE;
    }
    if (++button_count < BUTTON_TICKS)
        return BUTTON_NONE;
    button_count = 0;
    button_down = now;
    if (now) {
        button_held = BUTTON_TICKS;
        button_long_done = false;
        return BUTTON_NONE;
    }
    return button_long_done ? BUTTON_NONE : BUTTON_SHORT;
}

/* ---- CW schedule ----------------------------------------------------------------------------------------------- */

/* Compact, as the image's data region is only 8 KB: element count << 8 | elements, a dash is 1, first element highest. */
static const char morse_chars[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/?.,=";
static const uint16_t morse_codes[sizeof(morse_chars) - 1] = {
    0x201, 0x408, 0x40A, 0x304, 0x100, 0x402, 0x306, 0x400, 0x200, 0x407, 0x305, 0x404, 0x203, 0x202, /* A .. N */
    0x307, 0x406, 0x40D, 0x302, 0x300, 0x101, 0x301, 0x401, 0x303, 0x409, 0x40B, 0x40C,               /* O .. Z */
    0x51F, 0x50F, 0x507, 0x503, 0x501, 0x500, 0x510, 0x518, 0x51C, 0x51E,                             /* 0 .. 9 */
    0x512, 0x60C, 0x615, 0x633, 0x511,                                                                 /* / ? . , = */
};

#define MAX_SEGMENTS 600
static struct segment { uint8_t down; uint32_t ticks; } schedule[MAX_SEGMENTS];
static unsigned segments;

static void add(bool down, uint32_t ticks)
{
    if (segments && schedule[segments - 1].down == down)
        schedule[segments - 1].ticks += ticks;
    else if (segments < MAX_SEGMENTS)
        schedule[segments++] = (struct segment){down, ticks};
}

static unsigned code_of(char c) /* 0 for characters without a code (skipped) */
{
    if (c >= 'a' && c <= 'z')
        c = (char)(c - 'a' + 'A');
    for (unsigned i = 0; i < sizeof(morse_codes) / sizeof(morse_codes[0]); i++)
        if (morse_chars[i] == c)
            return morse_codes[i];
    return 0;
}

/* PARIS timing, as the station's morse_audio() but with a word space (7 units) first, a unit after every element, three after a
 * character, seven after a word; then the carrier to the end of the period. Returns false if the ID does not fit. */
static bool build_schedule(void)
{
    const uint32_t unit = RATE * 12u / (10u * BEACON_WPM);
    const char *t = BEACON_TEXT;
    add(false, 7 * unit); /* a word space after the carrier, so that the first element stands apart from it */
    while (*t) {
        while (*t == ' ')
            t++;
        if (!*t)
            break;
        for (; *t && *t != ' '; t++) {
            const unsigned code = code_of(*t);
            for (int e = (int)(code >> 8) - 1; e >= 0; e--) {
                add(true, (code >> e) & 1 ? 3 * unit : unit);
                add(false, unit);
            }
            add(false, 2 * unit);
        }
        add(false, 4 * unit);
    }
    uint32_t used = 0;
    for (unsigned i = 0; i < segments; i++)
        used += schedule[i].ticks;
    const uint32_t period = BEACON_PERIOD_S * RATE;
    if (segments >= MAX_SEGMENTS - 1 || used + 2 * RATE > period)
        return false; /* the carrier must last at least 2 s */
    add(true, period - used);
    return true;
}

/* ---- gain codes of the key edges ------------------------------------------------------------------------------- */

/* Carrier level against code 127 in 1/100 dB (PlutoSDR measurement, espdr.txmodes.GAIN_CURVE). */
static const struct { uint8_t code; uint16_t cdb; } curve[] = {
    {64, 1790}, {67, 1712}, {70, 1633}, {73, 1551}, {76, 1476}, {79, 1391}, {82, 1307}, {85, 1226},
    {88, 1157}, {91, 1056}, {94, 978},  {97, 879},  {100, 805}, {103, 703}, {106, 605}, {109, 522},
    {112, 474}, {115, 382}, {118, 279}, {121, 194}, {124, 132}, {127, 0},
};
#define CURVE_POINTS (sizeof(curve) / sizeof(curve[0]))

/* 20 log10 of the raised-cosine edge 0.5 - 0.5 cos(pi k / 40), k = 1 .. 39, in 1/100 dB. */
static const int16_t ramp_cdb[RAMP - 1] = {
    -5624, -4421, -3719, -3223, -2839, -2527, -2265, -2040, -1843, -1669, -1513, -1372, -1244, -1128, -1021, -923,
    -833,  -750,  -673,  -602,  -536,  -476,  -420,  -368,  -321,  -277,  -237,  -200,  -167,  -138,  -111,  -87,
    -66,   -49,   -34,   -22,   -12,   -5,    -1,
};

static uint8_t ramp_code[RAMP + 1]; /* gain code for each step of the edge: 0 = key up .. RAMP = key down */
static bool ramp_shifted[RAMP + 1]; /* the step is quiet enough (< 0.1 of the peak) to move the carrier away */

static int curve_cdb(unsigned code)
{
    for (unsigned i = 0; i + 1 < CURVE_POINTS; i++)
        if (code <= curve[i + 1].code)
            return curve[i].cdb + (int)(code - curve[i].code) * (curve[i + 1].cdb - curve[i].cdb) / (curve[i + 1].code - curve[i].code);
    return 0;
}

static void build_ramp(void)
{
    const int peak = curve_cdb(BEACON_PEAK_CODE);
    ramp_code[0] = TX_GAIN_WEAKEST;
    ramp_shifted[0] = true;
    ramp_code[RAMP] = BEACON_PEAK_CODE;
    for (unsigned k = 1; k < RAMP; k++) {
        int want = peak + ramp_cdb[k - 1];
        unsigned code = TX_GAIN_WEAKEST;
        int best = 1 << 30;
        for (unsigned c = BEACON_PEAK_CODE; c <= TX_GAIN_WEAKEST; c++) {
            int d = curve_cdb(c) - want;
            d = d < 0 ? -d : d;
            if (d < best) {
                best = d;
                code = c;
            }
        }
        ramp_code[k] = (uint8_t)(want <= 0 ? TX_GAIN_WEAKEST : code);
        ramp_shifted[k] = ramp_cdb[k - 1] < -2000;
    }
}

/* ---- frequency plan -------------------------------------------------------------------------------------------- */

static int32_t lo_word(uint32_t hz) { return (int32_t)(((uint64_t)hz * 65536u + 15000000u) / 30000000u) - 32 * 65536; }

/* The LO nearest the dial whose PLL word has its low byte in 48 .. 207, so that offsets never carry (txlink.choose_lo). */
static uint32_t choose_lo(uint32_t hz)
{
    hz = (hz + 50u) / 100u * 100u;
    for (uint32_t d = 0; d < 400; d++)
        for (int sign = 1; sign >= -1; sign -= 2) {
            uint32_t cand = sign > 0 ? hz + d * 100u : hz - d * 100u;
            uint32_t low = (uint32_t)lo_word(cand) & 0xFF;
            if (low >= TX_LOW_MARGIN && low <= 255 - TX_LOW_MARGIN)
                return cand;
        }
    return 0;
}

static int64_t div_round(int64_t a, int64_t b) { return a >= 0 ? (a + b / 2) / b : -((-a + b / 2) / b); }

/* Offset of `hz_above_dial` against the programmed LO, in records' units of 1/16 PLL step (30 MHz / 2^20 = 28.6 Hz). */
static int32_t q4_of(uint32_t word, int32_t hz_above_dial)
{
    int64_t off = (int64_t)BEACON_DIAL_HZ * 65536 - (int64_t)30000000 * (32 * 65536 + (int64_t)word) + (int64_t)hz_above_dial * 65536;
    return (int32_t)div_round(off * 16, 30000000);
}

/* The same in 1/65536 of a record unit, for a frequency in 1/16 Hz above the dial: Olivia's tones (31.25 Hz apart) fall between
 * the records' 28.6 Hz steps, so the records carry the remainder forward and each tone's average frequency is exact. */
static int64_t q4_fine(uint32_t word, int32_t hz16_above_dial)
{
    int64_t off = ((int64_t)BEACON_DIAL_HZ * 65536 - (int64_t)30000000 * (32 * 65536 + (int64_t)word)) * 16
                  + (int64_t)hz16_above_dial * 65536;              /* Hz * 2^20 */
    return div_round(off * 65536, 30000000);
}

static int32_t q4_mark, q4_space;
static uint32_t tx_word;                                          /* the PLL word of the prepared transmitter */

/* Programs the LO and prepares the transmitter (carrier still off). */
static bool prepare(void)
{
    uint32_t lo = choose_lo(BEACON_DIAL_HZ), word = 0;
    if (!lo || radio_tx_set(TX_OP_LO, lo / 100u) != CTL_OK)
        return false;
    if (radio_tx_begin(&word) != CTL_OK)
        return false;
    tx_word = word;
    q4_mark = q4_of(word, BEACON_TONE_HZ);
    q4_space = q4_mark;
    if (BEACON_SPACE_HZ) { /* the LO can sit ~15 kHz from the dial: shift whichever way stays inside the reach */
        int32_t up = q4_of(word, BEACON_TONE_HZ + BEACON_SPACE_HZ), down = q4_of(word, BEACON_TONE_HZ - BEACON_SPACE_HZ);
        q4_space = up <= MAX_Q4 && up >= -MAX_Q4 ? up : down;
    }
    const int32_t olivia_lo = (int32_t)(q4_fine(word, OLIVIA_FIRST_HZ16) >> 16),
                  olivia_hi = (int32_t)(q4_fine(word, OLIVIA_FIRST_HZ16 + 15 * OLIVIA_SPACING_HZ16) >> 16) + 1;
    if (q4_mark > MAX_Q4 || q4_mark < -MAX_Q4 || q4_space > MAX_Q4 || q4_space < -MAX_Q4 || olivia_lo < -MAX_Q4 ||
        olivia_hi > MAX_Q4) {
        radio_tx_key_off();
        return false;
    }
    radio_tx_key_on();
    return true;
}

/* ---- main ------------------------------------------------------------------------------------------------------ */

static unsigned mode = BEACON_MODE;
static bool keyed, tuned_once; /* keyed: the transmitter is prepared and its carrier on */
static unsigned level;         /* step of the key edge, 0 .. RAMP; carried from one period to the next */
static uint32_t next_tick;

static void fail_forever(void)
{
    for (;;) { /* blinking blue; retry by pressing RESET */
        LED_BLUE();
        delay_us(250000);
        LED_OFF();
        delay_us(750000);
    }
}

/* Waits for the next 125 us tick and returns the button's event. After a long gap (radio set-up) it starts afresh rather than
 * hurrying to catch up. */
static unsigned tick(void)
{
    if ((int32_t)(cpu_cycles() - next_tick) > (int32_t)(16 * TICK_CYCLES))
        next_tick = cpu_cycles();
    while ((int32_t)(cpu_cycles() - next_tick) < 0) {}
    next_tick += TICK_CYCLES;
    return button_event();
}

static void key_on(void)
{
    if (keyed)
        return;
    if (!tuned_once)
        LED_CYAN(); /* tuning the transmitter (the first time) */
    if (!prepare())
        fail_forever();
    keyed = tuned_once = true;
    level = 0; /* the chip keys on at the weakest gain */
}

static void key_off(void)
{
    if (keyed)
        radio_tx_key_off();
    keyed = false;
    level = 0;
}

/* One CW period: a word space, the ID, then the carrier to the period's end (the schedule fills the period exactly). Returns the
 * button event that ended it early, or BUTTON_NONE. */
static unsigned cw_period(void)
{
    key_on();
    bool lit = level > 0;
    led_mode(mode, lit ? BRIGHT : DIM);
    for (unsigned i = 0; i < segments; i++) {
        const bool down = schedule[i].down;
        for (uint32_t n = schedule[i].ticks; n; n--) {
            unsigned ev = tick();
            if (down && level < RAMP)
                level++;
            else if (!down && level > 0)
                level--;
            radio_tx_key_update(ramp_shifted[level] ? q4_space : q4_mark, ramp_code[level]);
            if (down != lit) { /* the LED follows the key; its ~30 us write fits inside this tick */
                lit = down;
                led_mode(mode, lit ? BRIGHT : DIM);
            }
            if (ev)
                return ev;
        }
    }
    return BUTTON_NONE;
}

/* Plays one record at `target` (1/65536 record units) with the remainder carried in *rem, at the current edge level. */
static void play(int64_t target, int64_t *rem)
{
    *rem += target;
    const int32_t q4 = (int32_t)((*rem + 0x8000) >> 16);
    *rem -= (int64_t)q4 << 16;
    radio_tx_key_update(q4, ramp_code[level]);
}

/* One Olivia period: the lead tone at the band's centre, the symbols, a 5 ms fade, then the carrier off until the period ends.
 * Returns the button event that ended it early, or BUTTON_NONE. */
static unsigned olivia_period(void)
{
    const uint64_t end = timer_ticks() + (uint64_t)BEACON_PERIOD_S * 16000000u; /* 16 MHz system timer */
    unsigned ev;
    if (keyed) {
        /* After a CW period the carrier is on at the lead tone's frequency. An Olivia receiver locks on a tone that is new, so
         * the carrier goes off first for longer than receivers take to let go of a signal (~3 s). */
        key_off();
        led_mode(mode, DIM);
        for (uint32_t n = 0; n < 4 * RATE; n++)
            if ((ev = tick()))
                return ev;
    }
    key_on();
    led_mode(mode, BRIGHT);
    int64_t rem = 0, target = q4_fine(tx_word, OLIVIA_CENTER_HZ16);
    for (uint32_t n = 0; n < BEACON_OLIVIA_LEAD_S * RATE; n++) {
        if ((ev = tick()))
            return ev;
        if (level < RAMP)
            level++;
        play(target, &rem);
    }
    const volatile uint32_t *words = olivia_words; /* instruction RAM: whole-word reads */
    for (unsigned i = 0; i < OLIVIA_SYMBOLS; i++) {
        const unsigned tone = (words[i / 8] >> (4 * (i % 8))) & 15;
        target = q4_fine(tx_word, OLIVIA_FIRST_HZ16 + (int32_t)tone * OLIVIA_SPACING_HZ16);
        for (uint32_t n = 0; n < OLIVIA_SYMBOL_TICKS; n++) {
            if ((ev = tick()))
                return ev;
            play(target, &rem);
        }
    }
    while (level > 0) {
        if ((ev = tick()))
            return ev;
        level--;
        play(target, &rem);
    }
    key_off();
    led_mode(mode, DIM);
    while (timer_ticks() < end)
        if ((ev = tick()))
            return ev;
    return BUTTON_NONE;
}

/* Paused: carrier off, the LED blinks the mode's colour. A short press changes the mode, a long one resumes. */
static void paused(void)
{
    key_off();
    for (uint32_t t = 0;; t++) {
        if (t % RATE == 0)
            led_mode(mode, BRIGHT);
        else if (t % RATE == RATE / 2)
            LED_OFF();
        unsigned ev = tick();
        if (ev == BUTTON_LONG)
            return;
        if (ev == BUTTON_SHORT) {
            mode = (mode + 1) % MODES;
            t = RATE - 1; /* show the new colour at once */
        }
    }
}

void app_main(void)
{
    platform_init();
    led_init();
    button_init();
#ifdef BEACON_BUTTON_TEST
#if BEACON_BUTTON_TEST >= 2 /* 2: after the radio's calibration; 3: with the transmitter keyed too */
    LED_WHITE();
    if (radio_init() != ESP_RADIO_OK)
        fail_forever();
#endif
#if BEACON_BUTTON_TEST >= 3
    build_ramp();
    if (!prepare())
        fail_forever();
    radio_tx_key_update(q4_mark, ramp_code[0]);
#endif
    for (bool last = true, first = true;; first = false) { /* diagnostic build: white while GPIO0 reads low, dim blue otherwise */
        bool high = REG(GPIO_IN_REG) & (1u << BOOT_GPIO);
        if (high != last || first)
            high ? led_set(0, 0, LED_LEVEL / 4) : led_set(LED_LEVEL, LED_LEVEL, LED_LEVEL);
        last = high;
        delay_us(1000);
    }
#endif
    build_ramp();
    if (!build_schedule())
        fail_forever();

    /* Hold-off before the radio is set up: amber blinks once a second (a check of the CPU clock too), the mode's colour shows
     * dimly in between. A short press of BOOT here changes the mode, a long one starts paused. */
    bool pause = false;
    next_tick = cpu_cycles();
    for (uint32_t t = 0; t < BEACON_HOLDOFF_S * RATE; t++) {
        if (t % RATE == 0)
            LED_AMBER();
        else if (t % RATE == RATE / 2)
            led_mode(mode, DIM);
        unsigned ev = tick();
        if (ev == BUTTON_SHORT) {
            mode = (mode + 1) % MODES;
            led_mode(mode, DIM);
        } else if (ev == BUTTON_LONG)
            pause = true;
    }
    LED_WHITE(); /* calibrating the radio */
    if (radio_init() != ESP_RADIO_OK)
        fail_forever();

    for (unsigned count = 0;; count++) {
        if (pause) {
            paused();
            pause = false;
            count = 0;
        }
        const bool olivia = mode == MODE_OLIVIA || (mode == MODE_BOTH && (count & 1));
        const unsigned ev = olivia ? olivia_period() : cw_period();
        if (ev == BUTTON_SHORT) { /* next mode, starting with a fresh period */
            key_off();
            mode = (mode + 1) % MODES;
            led_mode(mode, BRIGHT);
            delay_us(500000);
            count = (unsigned)-1;
        } else if (ev == BUTTON_LONG) {
            pause = true;
        }
    }
}
