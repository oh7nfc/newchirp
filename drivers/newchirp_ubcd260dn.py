# Copyright 2026 NewChirp contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Experimental driver for the Uniden UBCD260DN (and UBCD160DN) scanner.

The scanner speaks a CR-terminated text protocol at 115200 8N1, the one BuTel's
ARC260 uses, not a binary clone protocol. The 1000 conventional channels (10
banks of 100) are read and written with

    CIN,<n>,<tag>,<freq>,<mode>,<delay>,<tone on>,<tone code>,<tone lockout>,
        <digital code>,<lockout>,<priority>,<attenuator>,<record>,<alert tone>,
        <alert level>,<alert light>,<alert pattern>,<digital wait>,
        <volume offset>,<dmr slot>

The layout comes from ARC260 captures and from reads of a physical UBCD260DN
(firmware 1.01.00) in the standalone bcd260dn-programmer repository.

NewChirp keeps the channels in a fixed-record image of its own so CHIRP can
load, edit, save and compare them like any other radio. Bank names, custom
search ranges, the scan bank mask and the scanner-wide settings are not
exposed and are never written.
"""

import struct
import time

from chirp import chirp_common, directory, errors, memmap
from chirp.settings import (
    RadioSetting,
    RadioSettingGroup,
    RadioSettingValueBoolean,
    RadioSettingValueInteger,
    RadioSettingValueList,
    RadioSettingValueString,
    RadioSettings,
)


MODELS = (
    "BCD160", "BCD260", "UBCD160", "UBCD260",
    "BCD160DN", "BCD260DN", "UBCD160DN", "UBCD260DN",
)
CHANNEL_COUNT = 1000
BANK_SIZE = 100
RECORD_SIZE = 64
TAG_LENGTH = 16
IMAGE_SIZE = CHANNEL_COUNT * RECORD_SIZE
# After the channel records: the scanner-wide settings, then the trailer.
SETTINGS_OFFSET = IMAGE_SIZE
SETTINGS_SIZE = 128
# Then the ten bank names (BIN), 16 characters each. They are valid when the
# settings block is.
BANK_COUNT = CHANNEL_COUNT // BANK_SIZE
BANK_NAME_LENGTH = 16
BANK_NAMES_OFFSET = SETTINGS_OFFSET + SETTINGS_SIZE
BANK_NAMES_SIZE = BANK_COUNT * BANK_NAME_LENGTH
TRAILER_OFFSET = BANK_NAMES_OFFSET + BANK_NAMES_SIZE
TRAILER = b"NewChirp UBCD260DN"
TRAILER_SIZE = 32
MEM_SIZE = TRAILER_OFFSET + TRAILER_SIZE
# Record: frequency (u32, 100 Hz units), mode, delay, tone on, tone code, tone
# lockout, lockout, priority, attenuator, record, alert tone, alert level,
# alert light, alert pattern, digital wait (u16), volume offset (i8), DMR slot,
# digital code (4 bytes), 16-byte tag, 23 reserved bytes. An empty slot has
# frequency 0.
RECORD = struct.Struct(">IBbBBBBBBBBBBBHbB4s16s23x")
FREQ_UNIT = 100
# Tolerance when comparing a frequency with what the scanner reads back: the
# scanner rounds to the step of the band (up to 12.5 kHz).
FREQ_TOLERANCE = 125
# Owner's manual, "Valid Frequency Ranges": 25.000 to 1300.000 MHz, except
# 513.0000-758.0000 and 823.9900-849.0100 MHz. In Hz.
BANDS = (
    (25_000_000, 512_999_900),
    (758_000_100, 823_989_900),
    (849_010_100, 1_300_000_000),
)

# The scanner's mode names, in the order of the mode menus. CHIRP calls AUTO
# "Auto". FMB (FM broadcast stations) is not on the scanner's own channel
# menu but the scanner stores it; CHIRP has no such mode, so the driver adds
# it to the modes a memory accepts.
SCANNER_MODES = ("AUTO", "AM", "FM", "NFM", "WFM", "FMB")
CHIRP_MODES = ("Auto", "AM", "FM", "NFM", "WFM", "FMB")
if "FMB" not in chirp_common.Memory._valid_map["mode"]:
    chirp_common.Memory._valid_map["mode"] = (
        tuple(chirp_common.Memory._valid_map["mode"]) + ("FMB",)
    )
LIGHTS = ("OFF", "RED", "BLUE", "GREEN", "MAGENTA", "CYAN", "WHITE", "YELLOW")
LIGHT_WHITE = LIGHTS.index("WHITE")
PATTERNS = {"On": 0, "Slow": 1, "Fast": 2, "Off": 3}
PATTERN_NAMES = {number: name for name, number in PATTERNS.items()}
PATTERN_OFF = PATTERNS["Off"]
DIGITAL_SEARCH = "SRCH"
DMR_SLOT_ANY = 15
DEFAULT_WAIT = 400  # digital waiting time in ms, as the scanner reads it

TERMINATOR = b"\r"
ERROR_REPLIES = ("ERR", "NG", "FER", "ORER")

CTCSS_BASE = 64
DCS_BASE = 128
CTCSS_TONES = (
    "67.0", "69.3", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4",
    "88.5", "91.5", "94.8", "97.4", "100.0", "103.5", "107.2", "110.9",
    "114.8", "118.8", "123.0", "127.3", "131.8", "136.5", "141.3", "146.2",
    "151.4", "156.7", "159.8", "162.2", "165.5", "167.9", "171.3", "173.8",
    "177.3", "179.9", "183.5", "186.2", "189.9", "192.8", "196.6", "199.5",
    "203.5", "206.5", "210.7", "218.1", "225.7", "229.1", "233.6", "241.8",
    "250.3", "254.1",
)
# The 104 standard DCS codes in the scanner's order. Codes 232-239 (006, 007,
# 015, 017, 021, 050, 141, 214) are kept as read but not offered.
DCS_CODES = (
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74,
    114, 115, 116, 122, 125, 131, 132, 134, 143, 145, 152, 155, 156,
    162, 165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245, 246,
    251, 252, 255, 261, 263, 265, 266, 271, 274, 306, 311, 315, 325,
    331, 332, 343, 346, 351, 356, 364, 365, 371, 411, 412, 413, 423,
    431, 432, 445, 446, 452, 454, 455, 462, 464, 465, 466, 503, 506,
    516, 523, 526, 532, 546, 565, 606, 612, 624, 627, 631, 632, 654,
    662, 664, 703, 712, 723, 731, 732, 734, 743, 754,
)
CTCSS_FLOATS = tuple(float(t) for t in CTCSS_TONES)

# Field names of a record, in RECORD order (the tag is last).
FIELDS = (
    "freq", "mode", "delay", "tone_on", "tone_code", "tone_lockout",
    "lockout", "priority", "attenuator", "record", "alert_tone",
    "alert_level", "alert_light", "alert_pattern", "wait", "volume",
    "slot", "digital_code", "tag",
)
EMPTY = {
    "freq": 0, "mode": 0, "delay": 2, "tone_on": 0, "tone_code": 0,
    "tone_lockout": 0, "lockout": 1, "priority": 0, "attenuator": 0,
    "record": 0, "alert_tone": 0, "alert_level": 0,
    "alert_light": LIGHT_WHITE, "alert_pattern": PATTERN_OFF,
    "wait": DEFAULT_WAIT, "volume": 0, "slot": DMR_SLOT_ANY,
    "digital_code": "", "tag": "",
}
# What a channel programmed from scratch starts with: what ARC260 sends for a
# new row, with the record flag on.
NEW_CHANNEL = dict(EMPTY, lockout=0, record=1)


# Scanner-wide settings: a block of the image of its own. `valid` is 0 until a
# download fills it, so an image that never held the scanner's settings never
# writes them.
SETTINGS = struct.Struct(">BBBBBBBBBBB4s8s64s41x")
SETTING_FIELDS = (
    "valid", "volume", "squelch", "beep", "key_lock", "battery_save",
    "charge_time", "contrast", "priority_mode", "priority_max",
    "priority_interval", "dimmer", "color", "message",
)
MESSAGE_LINES = 4
MESSAGE_WIDTH = 16
BEEP_AUTO, BEEP_OFF = 0, 99
DIMMERS = {
    "OFF": "Off", "LOW": "Low", "MIDL": "Middle", "HIGH": "High",
    "+POL": "Auto, + polarity", "-POL": "Auto, - polarity",
}
BACKLIGHT_COLORS = (
    "BLUE", "RED", "MAGENTA", "GREEN", "CYAN", "YELLOW", "WHITE",
)
PRIORITY_MODES = {0: "Off", 1: "On", 2: "Plus on", 3: "DND"}
# VOL and SQL work in scan mode; the rest need program mode.
LIVE_COMMANDS = ("VOL", "SQL")
# BSV (battery save, charge time) is left out: the DN has no battery pack.
PROGRAM_COMMANDS = ("KBP", "BKL", "CNT", "OMS", "PRI")
DEFAULT_SETTINGS = {
    "valid": 0, "volume": 5, "squelch": 3, "beep": BEEP_AUTO,
    "key_lock": 0, "battery_save": 1, "charge_time": 14, "contrast": 8,
    "priority_mode": 0, "priority_max": 100, "priority_interval": 2,
    "dimmer": "MIDL", "color": "WHITE", "message": [""] * MESSAGE_LINES,
}


def pack_settings(values):
    def field(name):
        if name == "message":
            return b"".join(
                line.encode("ascii", "replace")[:MESSAGE_WIDTH]
                .ljust(MESSAGE_WIDTH)
                for line in values["message"]
            )
        if name in ("dimmer", "color"):
            return values[name].encode("ascii")
        return values[name]

    return SETTINGS.pack(*(field(name) for name in SETTING_FIELDS))


def unpack_settings(raw):
    values = dict(zip(SETTING_FIELDS, SETTINGS.unpack(bytes(raw))))
    for name in ("dimmer", "color"):
        values[name] = values[name].split(b"\x00", 1)[0].decode("ascii")
    text = values["message"].decode("ascii", "replace")
    values["message"] = [
        text[i:i + MESSAGE_WIDTH].rstrip()
        for i in range(0, MESSAGE_LINES * MESSAGE_WIDTH, MESSAGE_WIDTH)
    ]
    return values


def settings_from_replies(replies):
    """Settings dict from the reply fields (after the command name) of each
    settings command, keyed by command. Missing or odd fields keep defaults."""
    values = dict(DEFAULT_SETTINGS, message=list(DEFAULT_SETTINGS["message"]))
    for command, index, name in (
        ("VOL", 0, "volume"), ("SQL", 0, "squelch"), ("KBP", 0, "beep"),
        ("KBP", 1, "key_lock"), ("BSV", 0, "battery_save"),
        ("BSV", 1, "charge_time"), ("CNT", 0, "contrast"),
        ("PRI", 0, "priority_mode"), ("PRI", 1, "priority_max"),
        ("PRI", 2, "priority_interval"),
    ):
        try:
            values[name] = int(replies[command][index])
        except (KeyError, IndexError, ValueError):
            pass
    backlight = list(replies.get("BKL") or []) + ["", ""]
    if backlight[0] in DIMMERS:
        values["dimmer"] = backlight[0]
    if backlight[1] in BACKLIGHT_COLORS:
        values["color"] = backlight[1]
    lines = list(replies.get("OMS", [])) + [""] * MESSAGE_LINES
    values["message"] = [
        line.rstrip()[:MESSAGE_WIDTH] for line in lines[:MESSAGE_LINES]
    ]
    values["valid"] = 1
    return values


def settings_lines(values):
    """The write line of each settings command, keyed by command. A blank
    opening message line is sent as spaces; an empty field would leave it."""
    return {
        "VOL": "VOL,%i" % values["volume"],
        "SQL": "SQL,%i" % values["squelch"],
        "KBP": "KBP,%i,%i" % (values["beep"], values["key_lock"]),
        "BSV": "BSV,%i,%i" % (values["battery_save"], values["charge_time"]),
        "BKL": "BKL,%s,%s" % (values["dimmer"], values["color"]),
        "CNT": "CNT,%i" % values["contrast"],
        "OMS": "OMS," + ",".join(
            encode_tag(line, MESSAGE_WIDTH) or " " * MESSAGE_WIDTH
            for line in values["message"]
        ),
        "PRI": "PRI,%i,%i,%i" % (
            values["priority_mode"], values["priority_max"],
            values["priority_interval"]),
    }


DIGITAL_OFF = "Off (analog)"
DIGITAL_ANY = "Any digital signal (search)"
# Per protocol: a DMR colour code is 0-15, an NXDN RAN 0-63 and area 0-1, a
# dPMR common ID 1-63. The wire code is the base plus the number, in hex.
DIGITAL_RANGES = (
    (0x1000, range(0, 16), "DMR colour code %i"),
    (0x2000, range(0, 64), "NXDN RAN %i"),
    (0x3000, range(0, 2), "NXDN area %i"),
    (0x4000, range(1, 64), "dPMR common ID %i"),
)
DIGITAL_CODES = {
    label % n: "%04X" % (base + n)
    for base, numbers, label in DIGITAL_RANGES for n in numbers
}
DIGITAL_LABELS = {code: label for label, code in DIGITAL_CODES.items()}


def digital_label(code):
    """Readable name of a channel's digital code ('' is analog, SRCH is a
    search); a code in none of the known ranges shows as 'Code XXXX'."""
    if not code:
        return DIGITAL_OFF
    if code == DIGITAL_SEARCH:
        return DIGITAL_ANY
    return DIGITAL_LABELS.get(code.upper(), "Code %s" % code.upper())


def digital_code(label):
    """The wire code for a label from `digital_label`."""
    if label == DIGITAL_OFF:
        return ""
    if label == DIGITAL_ANY:
        return DIGITAL_SEARCH
    if label.startswith("Code "):
        return label[5:]
    return DIGITAL_CODES[label]


def delay_choices(current):
    """The scanner's delay times; a value read from the scanner outside the
    list is kept so it is not changed by accident."""
    choices = list(DELAYS)
    return choices + ([str(current)] if str(current) not in choices else [])


def digital_choices(current):
    choices = [DIGITAL_OFF, DIGITAL_ANY] + list(DIGITAL_CODES)
    label = digital_label(current)
    return choices + ([label] if label not in choices else [])


# The scanner's audio type (the "tone on" field of a channel): which kinds of
# signal open the channel. Uniden's remote-command spec: 0 All, 1 Analog only,
# 2 Digital only.
AUDIO_TYPES = ("All", "Analog only", "Digital only")
DELAYS = ("-10", "-5", "-2", "0", "1", "2", "5", "10", "30")


def tone_known(code):
    """True for a tone number the scanner displays (CTCSS or standard DCS)."""
    return (CTCSS_BASE <= code < CTCSS_BASE + len(CTCSS_TONES)
            or DCS_BASE <= code < DCS_BASE + len(DCS_CODES))


def default_bank_names():
    return ["Bank %i" % n for n in range(1, BANK_COUNT + 1)]


def pack_bank_names(names):
    return b"".join(
        encode_tag(name, BANK_NAME_LENGTH).encode("ascii")
        .ljust(BANK_NAME_LENGTH, b"\x00")
        for name in names
    )


def unpack_bank_names(raw):
    raw = bytes(raw)
    return [
        raw[i:i + BANK_NAME_LENGTH].split(b"\x00", 1)[0]
        .decode("ascii", "replace").rstrip()
        for i in range(0, BANK_NAMES_SIZE, BANK_NAME_LENGTH)
    ]


def blank_image():
    empty = pack_record(EMPTY)
    return (
        empty * CHANNEL_COUNT + pack_settings(DEFAULT_SETTINGS)
        + pack_bank_names(default_bank_names())
        + TRAILER.ljust(TRAILER_SIZE, b"\x00")
    )


def pack_record(values):
    return RECORD.pack(
        *(
            values[name].encode("ascii", "replace")
            if name in ("digital_code", "tag") else values[name]
            for name in FIELDS
        )
    )


def unpack_record(raw):
    values = dict(zip(FIELDS, RECORD.unpack(bytes(raw))))
    for name in ("digital_code", "tag"):
        values[name] = (
            values[name].split(b"\x00", 1)[0].decode("ascii", "replace")
            .rstrip()
        )
    return values


def encode_tag(text, width=TAG_LENGTH):
    """ASCII text the scanner can show: no commas or line breaks, `width`
    characters."""
    plain = text.encode("ascii", "replace").decode("ascii")
    plain = "".join(" " if c in ",\r\n" else c for c in plain)
    return plain[:width].rstrip()


def cin_line(number, values):
    """The 21-field write line a UBCD260DN accepts."""
    digital = values["tone_on"] == 2
    return ",".join(str(x) for x in (
        "CIN", number, values["tag"], "%08i" % values["freq"],
        SCANNER_MODES[values["mode"]], values["delay"], values["tone_on"],
        "" if digital else values["tone_code"],
        "" if digital else values["tone_lockout"],
        values["digital_code"] or (DIGITAL_SEARCH if digital else ""),
        values["lockout"], values["priority"], values["attenuator"],
        values["record"],
        values["alert_tone"] or "Off", values["alert_level"] or "Auto",
        LIGHTS[values["alert_light"]], values["alert_pattern"],
        values["wait"], values["volume"], values["slot"],
    ))


def parse_cin(line):
    """Decode a CIN reply (20 or 21 fields) into (number, values)."""
    fields = line.strip().split(",")
    if fields[0] != "CIN" or len(fields) not in (20, 21):
        raise errors.RadioError("Unexpected channel reply: %r" % line)
    try:
        mode = fields[4].upper()
        light = fields[16].upper()
        return int(fields[1]), {
            "tag": fields[2],
            "freq": int(fields[3]),
            "mode": SCANNER_MODES.index(mode) if mode in SCANNER_MODES else 0,
            "delay": int(fields[5]),
            "tone_on": int(fields[6]),
            "tone_code": int(fields[7]) if fields[7] else 0,
            "tone_lockout": int(fields[8]) if fields[8] else 0,
            "digital_code": "" if fields[9] == DIGITAL_SEARCH else fields[9],
            "lockout": int(fields[10]),
            "priority": int(fields[11]),
            "attenuator": int(fields[12]),
            "record": int(fields[13]),
            "alert_tone": (
                0 if fields[14] in ("", "0", "Off") else int(fields[14])
            ),
            "alert_level": (
                0 if fields[15] in ("", "0", "Auto") else int(fields[15])
            ),
            "alert_light": (
                LIGHTS.index(light) if light in LIGHTS else LIGHT_WHITE
            ),
            "alert_pattern": int(fields[17]),
            "wait": int(fields[18]),
            "volume": int(fields[19]),
            "slot": int(fields[20]) if len(fields) == 21 else DMR_SLOT_ANY,
        }
    except ValueError:
        raise errors.RadioError("Unreadable channel reply: %r" % line)


def bank_label(number):
    """'B3-07' for channel 207."""
    index = number - 1
    return "B%i-%02i" % (index // BANK_SIZE + 1, index % BANK_SIZE + 1)


def _setting(name, label, value, documentation):
    setting = RadioSetting(name, label, value)
    setting.set_doc(documentation)
    return setting


@directory.register
class UBCD260DNRadio(chirp_common.CloneModeRadio,
                     chirp_common.ExperimentalRadio):
    """Uniden UBCD260DN experimental driver (conventional channels only)."""

    VENDOR = "Uniden"
    MODEL = "UBCD260DN"
    BAUD_RATE = 115200
    WANTS_DTR = True
    WANTS_RTS = True
    REPLY_TIMEOUT = 1.0
    _memsize = MEM_SIZE

    @classmethod
    def get_prompts(cls):
        prompts = chirp_common.RadioPrompts()
        prompts.experimental = (
            "The Uniden UBCD260DN driver is experimental. It reads and "
            "writes the 1000 conventional channels (frequency, tag, mode, "
            "CTCSS/DCS or digital code, delay, lockout, priority, "
            "attenuator, record, alert and volume offset) over the "
            "scanner's USB serial port. Bank names, custom search ranges, "
            "and the scan bank mask are left untouched. The scanner "
            "settings (volume, squelch, key beep, backlight, contrast, "
            "opening message, priority) are read and written with the "
            "channels. Every channel write is read back."
        )
        prompts.info = (
            "Connect the UBCD260DN (or UBCD160DN) with its USB cable and "
            "turn it on. The interface uses 115200 baud, 8 data bits, no "
            "parity, and one stop bit. The scanner has 10 banks of 100 "
            "channels: channel 1 to 100 is bank 1, 101 to 200 is bank 2, "
            "and so on. An empty channel has frequency 0."
        )
        prompts.pre_download = (
            "1. Connect the scanner with its USB cable and turn it on.\n"
            "2. Close ARC260 so it releases the serial port.\n"
            "3. Start the download. The scanner enters program mode and "
            "returns to scanning when the download ends."
        )
        prompts.pre_upload = (
            "Download the scanner first and keep that image as a backup.\n"
            "1. Connect the scanner with its USB cable and turn it on.\n"
            "2. Close ARC260 so it releases the serial port.\n"
            "3. Start the upload. Only channels that differ from the "
            "scanner are written, and each one is read back. The scanner "
            "returns to scanning when the upload ends."
        )
        return prompts

    @classmethod
    def match_model(cls, filedata, filename):
        return (len(filedata) == MEM_SIZE
                and filedata[TRAILER_OFFSET:TRAILER_OFFSET + len(TRAILER)]
                == TRAILER)

    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_settings = True
        rf.has_bank = False
        rf.has_bank_index = False
        rf.has_bank_names = False
        rf.has_name = True
        rf.has_offset = False
        rf.has_mode = True
        rf.has_dtcs = True
        rf.has_rx_dtcs = False
        rf.has_dtcs_polarity = False
        rf.has_ctone = True
        rf.has_cross = False
        rf.has_tuning_step = False
        rf.has_nostep_tuning = True
        rf.has_comment = False
        rf.has_sub_devices = False
        rf.can_delete = True
        rf.valid_modes = list(CHIRP_MODES)
        rf.valid_duplexes = [""]
        rf.valid_tmodes = ["", "TSQL", "DTCS"]
        rf.valid_tones = list(CTCSS_FLOATS)
        rf.valid_dtcs_codes = list(DCS_CODES)
        rf.valid_dtcs_pols = ["NN"]
        rf.valid_skips = ["", "S"]
        rf.valid_characters = "".join(
            c for c in chirp_common.CHARSET_ASCII if c != ","
        )
        rf.valid_name_length = TAG_LENGTH
        # CHIRP treats the upper limit as exclusive; the scanner's is not.
        rf.valid_bands = [(low, high + 1) for low, high in BANDS]
        rf.memory_bounds = (1, CHANNEL_COUNT)
        return rf

    # -- serial -----------------------------------------------------------

    def _read_line(self):
        data = bytearray()
        deadline = time.monotonic() + self.REPLY_TIMEOUT
        while time.monotonic() < deadline:
            chunk = self.pipe.read(1)
            if not chunk:
                continue
            if chunk == TERMINATOR:
                break
            data.extend(chunk)
        return data.decode("ascii", "replace").strip()

    def _exchange(self, line):
        """Send one command and return the reply text."""
        self.pipe.timeout = 0.25
        reset = getattr(self.pipe, "reset_input_buffer", None)
        if reset:
            reset()
        self.pipe.write(line.encode("ascii") + TERMINATOR)
        reply = self._read_line()
        if not reply:
            raise errors.RadioNoResponse(
                "The UBCD260DN did not answer %r. Check the USB cable, turn "
                "the scanner on, and close other radio software." % line
            )
        fields = reply.split(",")
        command = line.split(",")[0]
        if fields[0] in ERROR_REPLIES or (
            len(fields) > 1 and fields[1] in ERROR_REPLIES
        ):
            raise errors.RadioError(
                "The UBCD260DN rejected %r: %s" % (line, reply)
            )
        if fields[0] != command:
            raise errors.RadioError(
                "Unexpected reply to %r: %s" % (line, reply)
            )
        return reply

    def _is_scanning(self):
        """True when the display says the scanner is scanning."""
        try:
            return "SCAN" in self._exchange("STS").replace(",", " ").split()
        except errors.RadioError:
            return False

    def _identify(self):
        fields = self._exchange("MDL").split(",")
        if len(fields) < 2 or fields[1] not in MODELS:
            raise errors.RadioError(
                "The connected scanner is not a UBCD260DN or UBCD160DN."
            )
        self._was_scanning = self._is_scanning()

    def _enter_program_mode(self):
        self._exchange("PRG")

    def _read_settings(self, commands):
        """Reply fields (after the command name) of each settings command."""
        return {
            command: self._exchange(command).split(",")[1:]
            for command in commands
        }

    def _leave_program_mode(self):
        # Leaving program mode puts the scanner on hold, so a scanner that was
        # scanning is told to scan again. A scanner on hold is left alone: the
        # Scan key would not toggle it back.
        try:
            self._exchange("EPG")
            if self._was_scanning:
                self._exchange("KEY,S,P")
        except errors.RadioError:
            pass

    def _read_channel(self, number):
        return parse_cin(self._exchange("CIN,%i" % number))[1]

    def _status(self, current, message):
        status = chirp_common.Status()
        status.cur = current
        status.max = CHANNEL_COUNT
        status.msg = message
        self.status_fn(status)

    # -- clone ------------------------------------------------------------

    def sync_in(self):
        entered = False
        try:
            self._identify()
            self._enter_program_mode()
            entered = True
            image = bytearray(blank_image())
            for number in range(1, CHANNEL_COUNT + 1):
                values = self._read_channel(number)
                if values["freq"]:
                    image[(number - 1) * RECORD_SIZE:
                          number * RECORD_SIZE] = pack_record(values)
                self._status(number, "Reading UBCD260DN")
            image[SETTINGS_OFFSET:SETTINGS_OFFSET + SETTINGS_SIZE] = (
                pack_settings(settings_from_replies(self._read_settings(
                    LIVE_COMMANDS + PROGRAM_COMMANDS)))
            )
            names = []
            for bank in range(1, BANK_COUNT + 1):
                reply = self._exchange("BIN,%i" % bank).split(",")
                names.append(",".join(reply[2:]).strip()
                             or "Bank %i" % bank)
            image[BANK_NAMES_OFFSET:BANK_NAMES_OFFSET + BANK_NAMES_SIZE] = (
                pack_bank_names(names)
            )
            self._mmap = memmap.MemoryMapBytes(bytes(image))
            self.process_mmap()
        except errors.RadioError:
            raise
        except Exception as exc:
            raise errors.RadioError(
                "Failed to communicate with the UBCD260DN: %s" % exc
            ) from exc
        finally:
            if entered:
                self._leave_program_mode()

    def sync_out(self):
        entered = False
        try:
            self._identify()
            wanted = self._settings()
            if wanted["valid"]:
                # VOL and SQL are written in scan mode, before program mode.
                self._write_settings(wanted, LIVE_COMMANDS)
            self._enter_program_mode()
            entered = True
            for number in range(1, CHANNEL_COUNT + 1):
                self._write_channel(number)
                self._status(number, "Writing UBCD260DN")
            if wanted["valid"]:
                self._write_settings(wanted, PROGRAM_COMMANDS)
                self._write_bank_names()
        except errors.RadioError:
            raise
        except Exception as exc:
            raise errors.RadioError(
                "Failed to communicate with the UBCD260DN: %s" % exc
            ) from exc
        finally:
            if entered:
                self._leave_program_mode()

    def _write_settings(self, wanted, commands):
        """Send the settings commands whose line differs from the scanner's."""
        current = settings_lines(
            settings_from_replies(self._read_settings(commands)))
        for command, line in settings_lines(wanted).items():
            if command in commands and line != current[command]:
                self._exchange(line)

    def _bank_names(self):
        return unpack_bank_names(
            self._mmap.get(BANK_NAMES_OFFSET, BANK_NAMES_SIZE))

    def _write_bank_names(self):
        """Send the BIN line of each bank whose name differs."""
        for bank, name in enumerate(self._bank_names(), 1):
            name = encode_tag(name, BANK_NAME_LENGTH) or "Bank %i" % bank
            now = ",".join(
                self._exchange("BIN,%i" % bank).split(",")[2:]).strip()
            if now != name:
                self._exchange("BIN,%i,%s" % (bank, name))

    def _write_channel(self, number):
        values = self._values(number)
        now = self._read_channel(number)
        if not values["freq"]:
            if not now["freq"]:
                return
            self._exchange("DCH,%i" % number)
            if self._read_channel(number)["freq"]:
                raise errors.RadioError(
                    "The UBCD260DN did not clear channel %i (%s)."
                    % (number, bank_label(number))
                )
            return
        if (abs(now["freq"] - values["freq"]) < FREQ_TOLERANCE
                and {**now, "freq": 0} == {**values, "freq": 0}):
            return
        self._exchange(cin_line(number, values))
        back = self._read_channel(number)["freq"]
        # Guard against a silent keep of the old value: the scanner rounds to
        # its step, so a read-back a full step away was not accepted.
        if abs(back - values["freq"]) >= FREQ_TOLERANCE:
            raise errors.RadioError(
                "The UBCD260DN did not accept channel %i (%s): it holds %08i."
                % (number, bank_label(number), back)
            )

    # -- image ------------------------------------------------------------

    def process_mmap(self):
        if len(self._mmap.get_packed()) != MEM_SIZE:
            raise errors.RadioError(
                "This image is not a UBCD260DN channel image"
            )

    def _settings(self):
        return unpack_settings(
            self._mmap.get(SETTINGS_OFFSET, SETTINGS_SIZE))

    def get_settings(self):
        values = self._settings()
        if not values["valid"]:
            raise errors.RadioError(
                "Download from the scanner before changing settings"
            )
        group = RadioSettingGroup("scanner", "Scanner")
        group.set_doc("Scanner-wide settings, read and written with the "
                      "channels.")

        def add(name, label, value, doc):
            group.append(_setting(name, label, value, doc))

        def choice(options, current):
            return RadioSettingValueList(
                options, current_index=list(options).index(current))

        beeps = ["Auto"] + [str(n) for n in range(1, 16)] + ["Off"]
        beep = ("Off" if values["beep"] == BEEP_OFF
                else "Auto" if not values["beep"] else str(values["beep"]))
        if beep not in beeps:
            beep = "Auto"
        add("volume", "Volume",
            RadioSettingValueInteger(0, 29, values["volume"]),
            "Speaker volume, 0 to 29.")
        add("squelch", "Squelch",
            RadioSettingValueInteger(0, 15, values["squelch"]),
            "Squelch level, 0 to 15.")
        add("beep", "Key beep", choice(beeps, beep),
            "Key beep level: Auto, 1 to 15 or Off.")
        add("key_lock", "Key lock",
            RadioSettingValueBoolean(bool(values["key_lock"])),
            "Lock the keys.")
        add("dimmer", "Backlight",
            choice(list(DIMMERS.values()),
                   DIMMERS.get(values["dimmer"], DIMMERS["MIDL"])),
            "Backlight brightness or automatic mode.")
        add("color", "Backlight colour",
            choice(list(BACKLIGHT_COLORS),
                   values["color"] if values["color"] in BACKLIGHT_COLORS
                   else "WHITE"),
            "Colour of the display backlight.")
        add("contrast", "Contrast",
            RadioSettingValueInteger(1, 15, values["contrast"]),
            "Display contrast, 1 to 15.")
        charset = "".join(
            c for c in chirp_common.CHARSET_ASCII if c != ",")
        for index, line in enumerate(values["message"], 1):
            add("message%i" % index, "Opening message %i" % index,
                RadioSettingValueString(
                    0, MESSAGE_WIDTH, line, autopad=False, charset=charset),
                "Line %i of the opening message, up to 16 characters."
                % index)
        add("priority_mode", "Priority mode",
            choice(list(PRIORITY_MODES.values()),
                   PRIORITY_MODES.get(values["priority_mode"], "Off")),
            "Priority scan mode.")
        add("priority_max", "Priority channels",
            RadioSettingValueInteger(1, 100, values["priority_max"]),
            "Most priority channels checked in one priority interval, "
            "1 to 100; more channels are checked in groups.")
        add("priority_interval", "Priority interval (s)",
            RadioSettingValueInteger(1, 10, values["priority_interval"]),
            "Seconds between priority checks, 1 to 10.")
        banks = RadioSettingGroup("banks", "Bank names")
        banks.set_doc("Names of the ten banks (100 channels each), up to 16 "
                      "characters.")
        for index, name in enumerate(self._bank_names(), 1):
            banks.append(_setting(
                "bank%i" % index, "Bank %i name" % index,
                RadioSettingValueString(
                    0, BANK_NAME_LENGTH, name, autopad=False,
                    charset=charset),
                "Name of bank %i (channels %i to %i)."
                % (index, (index - 1) * BANK_SIZE + 1, index * BANK_SIZE),
            ))
        return RadioSettings(group, banks)

    def set_settings(self, ui_settings):
        values = self._settings()
        if not values["valid"]:
            raise errors.RadioError(
                "Download from the scanner before changing settings"
            )
        by_label = {v: k for k, v in DIMMERS.items()}
        by_priority = {v: k for k, v in PRIORITY_MODES.items()}
        names = self._bank_names()

        def apply(element):
            if not isinstance(element, RadioSetting):
                for child in element:
                    apply(child)
                return
            name, value = element.get_name(), element.value
            if not value.get_mutable():
                return
            if name == "beep":
                text = str(value)
                values["beep"] = (BEEP_OFF if text == "Off"
                                  else BEEP_AUTO if text == "Auto"
                                  else int(text))
            elif name == "dimmer":
                values["dimmer"] = by_label[str(value)]
            elif name == "color":
                values["color"] = str(value)
            elif name == "priority_mode":
                values["priority_mode"] = by_priority[str(value)]
            elif name.startswith("bank"):
                names[int(name[4:]) - 1] = encode_tag(
                    str(value), BANK_NAME_LENGTH)
            elif name.startswith("message"):
                values["message"][int(name[7:]) - 1] = encode_tag(
                    str(value), MESSAGE_WIDTH)
            elif name == "key_lock":
                values[name] = 1 if bool(value) else 0
            elif name in DEFAULT_SETTINGS and name != "valid":
                values[name] = int(value)
            else:
                raise errors.InvalidValueError("Unknown scanner setting")

        for element in ui_settings:
            apply(element)
        self._mmap.set(SETTINGS_OFFSET, pack_settings(values))
        self._mmap.set(BANK_NAMES_OFFSET, pack_bank_names(names))

    def _values(self, number):
        offset = (number - 1) * RECORD_SIZE
        return unpack_record(self._mmap.get(offset, RECORD_SIZE))

    def get_raw_memory(self, number):
        offset = (number - 1) * RECORD_SIZE
        return repr(self._mmap.get(offset, RECORD_SIZE))

    def get_memory(self, number):
        if not 1 <= number <= CHANNEL_COUNT:
            raise errors.InvalidMemoryLocation(
                "Channel number must be between 1 and %i" % CHANNEL_COUNT
            )
        v = self._values(number)
        mem = chirp_common.Memory()
        mem.number = number
        if not v["freq"]:
            mem.empty = True
            return mem

        mem.freq = v["freq"] * FREQ_UNIT
        mem.name = v["tag"]
        mem.mode = CHIRP_MODES[min(v["mode"], len(CHIRP_MODES) - 1)]
        mem.duplex = ""
        mem.offset = 0
        mem.skip = "S" if v["lockout"] else ""
        code = v["tone_code"]
        if v["tone_on"] == 1:
            if CTCSS_BASE <= code < CTCSS_BASE + len(CTCSS_TONES):
                mem.tmode = "TSQL"
                mem.ctone = CTCSS_FLOATS[code - CTCSS_BASE]
                mem.rtone = mem.ctone
            elif DCS_BASE <= code < DCS_BASE + len(DCS_CODES):
                mem.tmode = "DTCS"
                mem.dtcs = DCS_CODES[code - DCS_BASE]
                mem.dtcs_polarity = "NN"

        digital = v["digital_code"] if v["tone_on"] == 2 else ""
        if v["tone_on"] == 2 and not digital:
            digital = DIGITAL_SEARCH
        mem.extra = RadioSettingGroup("Extra", "extra")
        mem.extra.append(_setting(
            "delay", "Delay (s)",
            RadioSettingValueList(
                delay_choices(v["delay"]),
                current_index=delay_choices(v["delay"]).index(
                    str(v["delay"]))),
            "0, 1, 2, 5, 10 or 30: seconds to stay on the channel after "
            "the transmission ends, to catch a delayed reply. -2, -5 or "
            "-10: stay on the channel only that many seconds after a "
            "signal opens, so only the start of a longer transmission is "
            "heard.",
        ))
        mem.extra.append(_setting(
            "priority", "Priority",
            RadioSettingValueBoolean(bool(v["priority"])),
            "Check this channel while receiving another.",
        ))
        mem.extra.append(_setting(
            "attenuator", "Attenuator",
            RadioSettingValueBoolean(bool(v["attenuator"])),
            "Reduce the input level for a very strong signal.",
        ))
        mem.extra.append(_setting(
            "record", "Record",
            RadioSettingValueBoolean(bool(v["record"])),
            "Feed the audio of this channel to the back panel record output.",
        ))
        mem.extra.append(_setting(
            "tone_lockout", "Tone lockout",
            RadioSettingValueBoolean(bool(v["tone_lockout"])),
            "Ignore the selected tone (the T.Lockout setting).",
        ))
        mem.extra.append(_setting(
            "audio_type", "Audio type",
            RadioSettingValueList(
                list(AUDIO_TYPES),
                current_index=v["tone_on"] if v["tone_on"] < 3 else 0),
            "Which signals open the channel: All, Analog only (the "
            "CTCSS/DCS tone applies) or Digital only (the digital code "
            "applies). Choosing a tone makes the channel Analog only.",
        ))
        mem.extra.append(_setting(
            "digital_code", "Digital code",
            RadioSettingValueList(
                digital_choices(digital),
                current_index=digital_choices(digital).index(
                    digital_label(digital))),
            "Off for an analog channel. Otherwise the DMR colour code, NXDN "
            "RAN or area, or dPMR common ID the channel opens on, or any "
            "digital signal.",
        ))
        mem.extra.append(_setting(
            "volume_offset", "Volume offset",
            RadioSettingValueInteger(-3, 3, v["volume"]),
            "Volume difference for this channel, -3 to 3.",
        ))
        mem.extra.append(_setting(
            "alert_tone", "Alert tone",
            RadioSettingValueInteger(0, 9, v["alert_tone"]),
            "Tone number 1 to 9 when the channel opens; 0 is off.",
        ))
        mem.extra.append(_setting(
            "alert_level", "Alert level",
            RadioSettingValueInteger(0, 15, v["alert_level"]),
            "Alert tone volume 1 to 15; 0 is Auto.",
        ))
        mem.extra.append(_setting(
            "alert_light", "Alert light",
            RadioSettingValueList(
                LIGHTS[1:],
                current_index=max(v["alert_light"], 1) - 1),
            "Colour the display backlight takes when this channel is "
            "received (the alert light). Pick one that differs from your "
            "normal backlight colour.",
        ))
        mem.extra.append(_setting(
            "alert_pattern", "Alert pattern",
            RadioSettingValueList(
                list(PATTERNS),
                current_index=list(PATTERNS).index(
                    PATTERN_NAMES.get(v["alert_pattern"], "Off"))),
            "Off: the alert light stays on in its colour for 5 seconds. "
            "On: the same, overriding the backlight colour. Slow blinks up "
            "to three times, Fast up to five times.",
        ))
        return mem

    def set_memory(self, memory):
        number = memory.number
        if not 1 <= number <= CHANNEL_COUNT:
            raise errors.InvalidMemoryLocation(
                "Channel number must be between 1 and %i" % CHANNEL_COUNT
            )
        offset = (number - 1) * RECORD_SIZE
        old = self._values(number)

        if memory.empty or not memory.freq:
            self._mmap.set(offset, pack_record(EMPTY))
            return

        v = dict(old if old["freq"] else NEW_CHANNEL)
        v["freq"] = memory.freq // FREQ_UNIT
        v["tag"] = encode_tag(memory.name or "")
        v["mode"] = CHIRP_MODES.index(memory.mode)
        v["lockout"] = 1 if memory.skip == "S" else 0

        extra = {s.get_name(): s.value for s in memory.extra}
        digital = digital_code(str(extra["digital_code"])) \
            if "digital_code" in extra else (
                old["digital_code"] or DIGITAL_SEARCH
                if old["tone_on"] == 2 else "")
        old_audio = old["tone_on"] if old["tone_on"] < 3 else 0
        audio = (AUDIO_TYPES.index(str(extra["audio_type"]))
                 if "audio_type" in extra else old_audio)
        if audio != old_audio and audio == 2:
            # Digital only chosen by hand: the tone does not apply.
            v["tone_on"] = 2
            v["tone_code"] = 0
            v["digital_code"] = "" if digital in ("", DIGITAL_SEARCH) \
                else digital
        elif audio != old_audio and audio == 1 and not memory.tmode:
            v["tone_on"] = 1  # analog only, any tone
            v["tone_code"] = 0
            v["digital_code"] = ""
        elif audio != old_audio and audio == 0 and not memory.tmode:
            v["tone_on"] = v["tone_code"] = 0
            v["digital_code"] = ""
        elif memory.tmode == "TSQL":
            v["tone_on"] = 1
            v["tone_code"] = CTCSS_BASE + CTCSS_FLOATS.index(
                float(memory.ctone))
            v["digital_code"] = ""
        elif memory.tmode == "DTCS":
            v["tone_on"] = 1
            v["tone_code"] = DCS_BASE + DCS_CODES.index(int(memory.dtcs))
            v["digital_code"] = ""
        elif digital:
            v["tone_on"] = 2
            v["tone_code"] = 0
            v["digital_code"] = "" if digital == DIGITAL_SEARCH else digital
        elif old["tone_on"] == 1 and (
                not tone_known(old["tone_code"]) or not old["tone_code"]):
            # A tone number the scanner shows but this driver does not, or
            # Analog only with no tone: keep it as read.
            pass
        else:
            v["tone_on"] = v["tone_code"] = 0
            v["digital_code"] = ""

        if "delay" in extra:
            v["delay"] = int(str(extra["delay"]))
        for name, key in (("priority", "priority"),
                          ("attenuator", "attenuator"),
                          ("record", "record"),
                          ("tone_lockout", "tone_lockout")):
            if name in extra:
                v[key] = 1 if bool(extra[name]) else 0
        if "volume_offset" in extra:
            v["volume"] = int(extra["volume_offset"])
        if "alert_tone" in extra:
            v["alert_tone"] = int(extra["alert_tone"])
        if "alert_level" in extra:
            v["alert_level"] = int(extra["alert_level"])
        if "alert_light" in extra:
            v["alert_light"] = LIGHTS.index(str(extra["alert_light"]))
        if "alert_pattern" in extra:
            v["alert_pattern"] = PATTERNS[str(extra["alert_pattern"])]
        self._mmap.set(offset, pack_record(v))
