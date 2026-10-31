# Copyright 2026 NewChirp contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Experimental driver for the Uniden UBC370CLT scanner.

The scanner speaks a CR-terminated text protocol at 115200 8N1, not a binary
clone protocol. The conventional-channel commands were probed on a physical
unit (firmware 1.01.06):

    CIN,<n>,<tag>,<freq>,,<tone>,<delay>,<lockout>,<flag>

NewChirp keeps the 500 channels in a fixed-record image of its own so CHIRP
can load, edit, save, and compare them like any other radio. Trunking
systems and the bank scan mask are not exposed.

The 30 FM broadcast presets (three banks of ten, `RIN` commands) appear as
locations 501 to 530: FM1 is 501-510, FM2 is 511-520, FM3 is 521-530. The
radio cannot empty a preset, so an empty one is written as 87.3 MHz with no
name and reads back as empty.
"""

import struct
import time

from chirp import chirp_common, directory, errors, memmap
from chirp.settings import (
    RadioSetting,
    RadioSettingGroup,
    RadioSettingValueBoolean,
)


MODEL_REPLY = "MDL,UBC370CLT"
CHANNEL_COUNT = 500
FM_COUNT = 30
FM_PER_BANK = 10
TOTAL_COUNT = CHANNEL_COUNT + FM_COUNT
RECORD_SIZE = 32
TAG_LENGTH = 16
IMAGE_SIZE = TOTAL_COUNT * RECORD_SIZE
TRAILER = b"NewChirp UBC370CLT"
TRAILER_SIZE = 32
MEM_SIZE = IMAGE_SIZE + TRAILER_SIZE
# Record: frequency (u32, 100 Hz units), tone, delay, lockout, flag, 16-byte
# tag, 8 reserved bytes. An empty slot is all zero except lockout, which the
# scanner reports as 1.
RECORD = struct.Struct(">IBBBB16s8x")
FREQ_UNIT = 100
FREQ_STEP = 125  # 12.5 kHz in 100 Hz units: fallback tolerance
# Band ranges from the UBC370CLT manual: (lower Hz, upper Hz, mode, step Hz).
# The scanner does not receive 174-406 or 512-806 MHz. It picks the mode by
# itself and rounds a frequency down to the band's step.
BANDS = (
    (25_000_000, 29_995_000, "FM", 5_000),
    (30_000_000, 79_987_500, "FM", 12_500),
    (80_000_000, 82_990_000, "FM", 10_000),
    (83_000_000, 87_287_500, "FM", 12_500),
    (87_300_000, 107_900_000, "WFM", 100_000),
    (108_000_000, 136_991_600, "AM", 8_330),
    (137_000_000, 137_995_000, "FM", 5_000),
    (138_000_000, 157_987_500, "FM", 12_500),
    (158_000_000, 160_590_000, "FM", 10_000),
    (160_600_000, 162_587_500, "FM", 12_500),
    (162_600_000, 173_990_000, "FM", 10_000),
    (406_000_000, 439_993_700, "FM", 6_250),
    (440_000_000, 465_995_000, "FM", 5_000),
    (466_000_000, 469_990_000, "FM", 10_000),
    (470_000_000, 512_000_000, "FM", 6_250),
    (806_000_000, 960_000_000, "FM", 12_500),
)
# The radio keeps the old FM value outside 87.3-107.9 MHz, answering OK.
FM_MIN, FM_MAX = 873000, 1079000  # 100 Hz units
FM_OLD_CLEARED = 875000  # an earlier clear value: 87.5 MHz
FM_DASHED_TAG = "-" * 16  # an earlier clear value for the name
FM_TOLERANCE = 100  # 10 kHz: the radio rounds to its tuning step

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
# The manual lists 104 DCS codes.
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


def tone_known(code):
    """True for a tone number the scanner displays (CTCSS or DCS)."""
    return (CTCSS_BASE <= code < CTCSS_BASE + len(CTCSS_TONES)
            or DCS_BASE <= code < DCS_BASE + len(DCS_CODES))


def _band(freq):
    """The manual's band that holds `freq` (100 Hz units), or None."""
    hertz = freq * FREQ_UNIT
    for band in BANDS:
        if band[0] <= hertz <= band[1]:
            return band
    return None


def channel_mode(freq):
    """The mode the scanner picks by itself for this band (AM on the air band,
    WFM on the FM broadcast band, FM elsewhere). The scanner stores no mode,
    so this is informative."""
    band = _band(freq)
    return band[2] if band else "FM"


def freq_tolerance(freq):
    """How far a frequency may differ from what the scanner reads back: its
    tuning step in this band, in 100 Hz units."""
    band = _band(freq)
    return -(-band[3] // FREQ_UNIT) if band else FREQ_STEP


def is_fm(number):
    return CHANNEL_COUNT < number <= TOTAL_COUNT


def fm_label(number):
    """'FM2-03' for location 513."""
    index = number - CHANNEL_COUNT - 1
    return "FM%i-%02i" % (index // FM_PER_BANK + 1, index % FM_PER_BANK + 1)


def fm_cleared(freq, tag):
    """The look of an emptied preset: the radio cannot empty one, so a cleared
    preset is the lowest frequency with no name (earlier clears also read)."""
    return freq in (FM_MIN, FM_OLD_CLEARED) and tag in ("", FM_DASHED_TAG)


def rin_line(index, tag, freq):
    return "RIN,%i,%s,%08i" % (index, tag, freq)


def parse_rin(line):
    """Decode a RIN reply into (index, tag, freq)."""
    fields = line.strip().split(",")
    if fields[0] != "RIN" or len(fields) != 4:
        raise errors.RadioError("Unexpected FM preset reply: %r" % line)
    try:
        return int(fields[1]), fields[2], int(fields[3])
    except ValueError:
        raise errors.RadioError("Unreadable FM preset reply: %r" % line)


def blank_image():
    empty = RECORD.pack(0, 0, 0, 1, 0, b"")
    return empty * TOTAL_COUNT + TRAILER.ljust(TRAILER_SIZE, b"\x00")


def encode_tag(text):
    """ASCII tag the scanner can show: no commas or line breaks, 16 characters."""
    plain = text.encode("ascii", "replace").decode("ascii")
    plain = "".join(" " if c in ",\r\n" else c for c in plain)
    return plain[:TAG_LENGTH].rstrip()


def cin_line(number, tag, freq, tone, delay, lockout, flag):
    return "CIN,%i,%s,%08i,,%i,%i,%i,%i" % (
        number, tag, freq, tone, delay, lockout, flag)


def parse_cin(line):
    """Decode a CIN reply into (number, tag, freq, tone, delay, lockout, flag)."""
    fields = line.strip().split(",")
    if fields[0] != "CIN" or len(fields) != 9:
        raise errors.RadioError("Unexpected channel reply: %r" % line)
    try:
        return (
            int(fields[1]), fields[2], int(fields[3]), int(fields[5]),
            int(fields[6]), int(fields[7]), int(fields[8]),
        )
    except ValueError:
        raise errors.RadioError("Unreadable channel reply: %r" % line)


def _setting(name, label, value, documentation):
    setting = RadioSetting(name, label, value)
    setting.set_doc(documentation)
    return setting


@directory.register
class UBC370CLTRadio(chirp_common.CloneModeRadio,
                     chirp_common.ExperimentalRadio):
    """Uniden UBC370CLT experimental driver (conventional channels only)."""

    VENDOR = "Uniden"
    MODEL = "UBC370CLT"
    BAUD_RATE = 115200
    WANTS_DTR = True
    WANTS_RTS = True
    REPLY_TIMEOUT = 1.0
    # The scanner stores no mode per channel; NewChirp shows it read-only.
    NEWCHIRP_MODE_READ_ONLY = True
    _memsize = MEM_SIZE

    @classmethod
    def get_prompts(cls):
        prompts = chirp_common.RadioPrompts()
        prompts.experimental = (
            "The Uniden UBC370CLT driver is experimental. It reads and "
            "writes the 500 conventional channels (frequency, tag, CTCSS/DCS, "
            "delay, and lockout) and the 30 FM presets (locations 501 to 530) "
            "over the scanner's USB serial port. Trunking systems and the "
            "bank scan mask are left untouched. Every write is read back "
            "from the scanner."
        )
        prompts.info = (
            "Connect the UBC370CLT with its USB cable and turn it on. The "
            "interface uses 115200 baud, 8 data bits, no parity, and one stop "
            "bit. The scanner has 10 banks of 50 channels: channel 1 to 50 "
            "is bank 1, 51 to 100 is bank 2, and so on. FM1 is 501 to 510, FM2 "
            "is 511 to 520, and FM3 is 521 to 530 (87.3 to 107.9 MHz)."
        )
        prompts.pre_download = (
            "1. Connect the scanner with its USB cable and turn it on.\n"
            "2. Close ARC370 so it releases the serial port.\n"
            "3. Start the download. The scanner switches itself off when "
            "the download ends; switch it on again and it starts in the "
            "mode of the slider (FM, Service or Scanner)."
        )
        prompts.pre_upload = (
            "Download the scanner first and keep that image as a backup.\n"
            "1. Connect the scanner with its USB cable and turn it on.\n"
            "2. Close ARC370 so it releases the serial port.\n"
            "3. Start the upload. Only channels that differ from the "
            "scanner are written, and each one is read back. The scanner "
            "switches itself off when the upload ends; switch it on again."
        )
        return prompts

    @classmethod
    def match_model(cls, filedata, filename):
        return (len(filedata) == MEM_SIZE
                and filedata[IMAGE_SIZE:IMAGE_SIZE + len(TRAILER)] == TRAILER)

    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_settings = False
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
        rf.valid_modes = ["FM", "AM", "WFM"]
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
        rf.valid_bands = [(low, high + 1) for low, high, _, _ in BANDS]
        rf.memory_bounds = (1, TOTAL_COUNT)
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
                "The UBC370CLT did not answer %r. Check the USB cable, turn "
                "the scanner on, and close other radio software." % line
            )
        fields = reply.split(",")
        command = line.split(",")[0]
        if fields[0] in ERROR_REPLIES or (
            len(fields) > 1 and fields[1] in ERROR_REPLIES
        ):
            raise errors.RadioError(
                "The UBC370CLT rejected %r: %s" % (line, reply)
            )
        if fields[0] != command:
            raise errors.RadioError(
                "Unexpected reply to %r: %s" % (line, reply)
            )
        return reply

    def _enter_program_mode(self):
        if self._exchange("MDL") != MODEL_REPLY:
            raise errors.RadioError(
                "The connected scanner is not a UBC370CLT."
            )
        self._exchange("PRG")

    def _leave_program_mode(self):
        # Nothing but EPG or JPM,SCN_MODE leaves program mode, and the
        # slider does not. JPM,SCN_MODE forces scanning even when the slider
        # is on FM. EPG powers the scanner off, so it starts again in the mode
        # the slider selects. It may close the port before answering.
        try:
            self._exchange("EPG")
        except errors.RadioError:
            pass

    def _read_channel(self, number):
        return parse_cin(self._exchange("CIN,%i" % number))

    def _status(self, current, total, message):
        status = chirp_common.Status()
        status.cur = current
        status.max = total
        status.msg = message
        self.status_fn(status)

    # -- clone ------------------------------------------------------------

    def sync_in(self):
        entered = False
        try:
            self._enter_program_mode()
            entered = True
            image = bytearray(blank_image())
            for number in range(1, CHANNEL_COUNT + 1):
                _, tag, freq, tone, delay, lockout, flag = (
                    self._read_channel(number)
                )
                if freq:
                    RECORD.pack_into(
                        image, (number - 1) * RECORD_SIZE, freq, tone & 0xFF,
                        1 if delay else 0, 1 if lockout else 0,
                        1 if flag else 0, tag.encode("ascii", "replace"),
                    )
                self._status(number, TOTAL_COUNT, "Reading UBC370CLT")
            for index in range(1, FM_COUNT + 1):
                _, tag, freq = parse_rin(self._exchange("RIN,%i" % index))
                number = CHANNEL_COUNT + index
                if freq and not fm_cleared(freq, tag):
                    RECORD.pack_into(
                        image, (number - 1) * RECORD_SIZE, freq, 0, 0, 0, 0,
                        tag.encode("ascii", "replace"),
                    )
                self._status(number, TOTAL_COUNT, "Reading UBC370CLT")
            self._mmap = memmap.MemoryMapBytes(bytes(image))
            self.process_mmap()
        except errors.RadioError:
            raise
        except Exception as exc:
            raise errors.RadioError(
                "Failed to communicate with the UBC370CLT: %s" % exc
            ) from exc
        finally:
            if entered:
                self._leave_program_mode()

    def sync_out(self):
        entered = False
        try:
            self._enter_program_mode()
            entered = True
            for number in range(1, TOTAL_COUNT + 1):
                if is_fm(number):
                    self._write_fm(number)
                else:
                    self._write_channel(number)
                self._status(number, TOTAL_COUNT, "Writing UBC370CLT")
        except errors.RadioError:
            raise
        except Exception as exc:
            raise errors.RadioError(
                "Failed to communicate with the UBC370CLT: %s" % exc
            ) from exc
        finally:
            if entered:
                self._leave_program_mode()

    def _write_channel(self, number):
        freq, tone, delay, lockout, flag, tag = self._record(number)
        _, now_tag, now_freq, now_tone, now_delay, now_lockout, _ = (
            self._read_channel(number)
        )
        if not freq:
            if not now_freq:
                return
            # The scanner rejects DCH. An empty slot is written instead.
            tag, tone, delay, lockout, flag = "", 0, 0, 1, 0
        elif (abs(now_freq - freq) < freq_tolerance(freq) and now_tag == tag
                and now_tone == tone and now_delay == delay
                and now_lockout == lockout):
            return
        self._exchange(cin_line(number, tag, freq, tone, delay, lockout, flag))
        back_freq = self._read_channel(number)[2]
        # The scanner answers OK when it cannot receive a frequency and
        # keeps the old one, and it rounds the rest to its step.
        if abs(back_freq - freq) >= freq_tolerance(freq):
            raise errors.RadioError(
                "The UBC370CLT did not accept channel %i: it holds %08i."
                % (number, back_freq)
            )

    def _write_fm(self, number):
        freq, _, _, _, _, tag = self._record(number)
        index = number - CHANNEL_COUNT
        _, now_tag, now_freq = parse_rin(self._exchange("RIN,%i" % index))
        if not freq:
            if fm_cleared(now_freq, now_tag):
                return
            # The radio cannot empty a preset: write the cleared look.
            freq, tag = FM_MIN, ""
        elif abs(now_freq - freq) < FM_TOLERANCE and now_tag == tag:
            return
        self._exchange(rin_line(index, tag, freq))
        back_freq = parse_rin(self._exchange("RIN,%i" % index))[2]
        # The radio answers OK and keeps the old value outside 87.3-107.9.
        if abs(back_freq - freq) >= FM_TOLERANCE:
            raise errors.RadioError(
                "The UBC370CLT did not accept %s: it holds %08i."
                % (fm_label(number), back_freq)
            )

    # -- image ------------------------------------------------------------

    def process_mmap(self):
        if len(self._mmap.get_packed()) != MEM_SIZE:
            raise errors.RadioError(
                "This image is not a UBC370CLT channel image"
            )

    def _record(self, number):
        raw = self._mmap.get((number - 1) * RECORD_SIZE, RECORD_SIZE)
        freq, tone, delay, lockout, flag, tag = RECORD.unpack(bytes(raw))
        text = tag.split(b"\x00", 1)[0].decode("ascii", "replace").rstrip()
        return freq, tone, delay, lockout, flag, text

    def validate_memory(self, mem):
        msgs = super().validate_memory(mem)
        if is_fm(mem.number) and not mem.empty:
            low, high = FM_MIN * FREQ_UNIT, FM_MAX * FREQ_UNIT
            if not low <= mem.freq <= high:
                msgs.append(chirp_common.ValidationError(
                    "%s is an FM broadcast preset: 87.3 to 107.9 MHz only"
                    % fm_label(mem.number)
                ))
            elif mem.tmode:
                msgs.append(chirp_common.ValidationWarning(
                    "%s is an FM broadcast preset and stores no tone"
                    % fm_label(mem.number)
                ))
        return msgs

    def get_raw_memory(self, number):
        offset = (number - 1) * RECORD_SIZE
        return repr(self._mmap.get(offset, RECORD_SIZE))

    def get_memory(self, number):
        if not 1 <= number <= TOTAL_COUNT:
            raise errors.InvalidMemoryLocation(
                "Channel number must be between 1 and %i" % TOTAL_COUNT
            )
        freq, tone, delay, lockout, flag, tag = self._record(number)
        mem = chirp_common.Memory()
        mem.number = number
        if not freq:
            mem.empty = True
            return mem

        mem.freq = freq * FREQ_UNIT
        mem.name = tag
        mem.mode = "WFM" if is_fm(number) else channel_mode(freq)
        mem.duplex = ""
        mem.offset = 0
        if is_fm(number):
            # An FM preset holds a frequency and a name only.
            return mem
        mem.skip = "S" if lockout else ""
        if CTCSS_BASE <= tone < CTCSS_BASE + len(CTCSS_TONES):
            mem.tmode = "TSQL"
            mem.ctone = CTCSS_FLOATS[tone - CTCSS_BASE]
            mem.rtone = mem.ctone
        elif DCS_BASE <= tone < DCS_BASE + len(DCS_CODES):
            mem.tmode = "DTCS"
            mem.dtcs = DCS_CODES[tone - DCS_BASE]
            mem.dtcs_polarity = "NN"

        mem.extra = RadioSettingGroup("Extra", "extra")
        mem.extra.append(_setting(
            "delay",
            "Delay",
            RadioSettingValueBoolean(bool(delay)),
            "Holds on the channel for a moment after the signal ends "
            "(the DELAY indicator).",
        ))
        return mem

    def set_memory(self, memory):
        number = memory.number
        if not 1 <= number <= TOTAL_COUNT:
            raise errors.InvalidMemoryLocation(
                "Channel number must be between 1 and %i" % TOTAL_COUNT
            )
        offset = (number - 1) * RECORD_SIZE
        old_freq, old_tone, old_delay, _, old_flag, _ = self._record(number)

        if memory.empty or not memory.freq:
            self._mmap.set(offset, RECORD.pack(0, 0, 0, 1, 0, b""))
            return

        freq = memory.freq // FREQ_UNIT
        if is_fm(number):
            tag = encode_tag(memory.name or "").encode("ascii")
            self._mmap.set(offset, RECORD.pack(freq, 0, 0, 0, 0, tag))
            return
        if memory.tmode == "TSQL":
            tone = CTCSS_BASE + CTCSS_FLOATS.index(float(memory.ctone))
        elif memory.tmode == "DTCS":
            tone = DCS_BASE + DCS_CODES.index(int(memory.dtcs))
        elif tone_known(old_tone):
            tone = 0
        else:
            # A tone number the scanner does not show; keep it as read.
            tone = old_tone

        # A channel programmed on the scanner has delay on.
        delay = old_delay if old_freq else 1
        for setting in memory.extra:
            if setting.get_name() == "delay":
                delay = 1 if bool(setting.value) else 0

        tag = encode_tag(memory.name or "").encode("ascii")
        self._mmap.set(offset, RECORD.pack(
            freq, tone, delay, 1 if memory.skip == "S" else 0,
            old_flag, tag,
        ))
