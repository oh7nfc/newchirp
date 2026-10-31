"""Offline tests for the experimental Uniden UBCD260DN driver."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "assets/python/pyserial-3.5-py2.py3-none-any.whl"


def chirp_source():
    for path in (
        ROOT / "build_support/chirp-20261016",
        ROOT / "build_support/chirp-20261023",
        ROOT.parent / "chirp-20260626",
    ):
        if (path / "chirp/chirp_common.py").is_file():
            return path
    return None


SOURCE = chirp_source()


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "newchirp_chirp_bridge_ubcd260dn", ROOT / "scripts/chirp_bridge.py"
    )
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    bridge.load_chirp(str(SOURCE), str(VENDOR), str(ROOT / "drivers"))
    return sys.modules["newchirp.drivers.newchirp_ubcd260dn"]


# Fields after the index, as a real UBCD260DN returns them (tag first).
EMPTY = "AUTO,2,0,0,0,,1,0,0,0,0,0,WHITE,3,400,0,15".split(",")
HAMPAGER = "HAMPAGER,04336250,FM,0,1,81,0,,0,1,1,1,1,0,YELLOW,0,400,0,15"
DMR = "DMR HOTSPOT,04344625,NFM,0,2,,,100B,0,0,1,1,2,0,BLUE,1,400,0,15"
AIRBAND = "AIR,01180000,AM,2,1,128,0,,0,0,0,1,0,0,WHITE,3,400,0,15"


class FakeScannerPort:
    """Answers like the UBCD260DN read in docs/PROTOCOL.md of the standalone
    programmer: CR-terminated commands, 21-field CIN replies, unused slots as
    frequency 0 with a default tag, DCH clearing a slot."""

    def __init__(self, stored=None, model="UBCD260DN", scanning=True):
        self.timeout = 1.0
        self.model = model
        self.scanning = scanning
        self.stored = {n: v.split(",") for n, v in (stored or {}).items()}
        self.sent = []
        self.programming = False
        self.settings = {
            "VOL": "5", "SQL": "3", "KBP": "0,0", "BSV": "1,14",
            "BKL": "MIDL,WHITE", "CNT": "8",
            "OMS": "Uniden Bearcat,UBCD260DN,,", "PRI": "0,100,2",
        }
        self.banks = {n: "Bank %i" % n for n in range(1, 11)}
        self._reply = b""

    def reset_input_buffer(self):
        self._reply = b""

    def write(self, data):
        line = bytes(data).decode("ascii").rstrip("\r")
        self.sent.append(line)
        self._reply = (self._answer(line) + "\r").encode("ascii")
        return len(data)

    def read(self, count=1):
        reply, self._reply = self._reply[:count], self._reply[count:]
        return reply

    def _answer(self, line):
        f = line.split(",")
        if f[0] == "MDL":
            return "MDL,%s" % self.model
        if f[0] == "STS":
            return "STS,Bank 1,%s" % ("SCAN" if self.scanning else "HOLD")
        if f[0] == "PRG":
            self.programming = True
            return "PRG,OK"
        if f[0] == "EPG":
            self.programming = False
            return "EPG,OK"
        if f[0] == "KEY":
            return "KEY,OK"
        if f[0] == "BIN" and self.programming:
            if len(f) == 2:
                return "BIN,%s,%s" % (f[1], self.banks[int(f[1])])
            self.banks[int(f[1])] = ",".join(f[2:])
            return "BIN,OK"
        if f[0] in self.settings:
            if f[0] not in ("VOL", "SQL") and not self.programming:
                return "NG"
            if len(f) == 1:
                return "%s,%s" % (f[0], self.settings[f[0]])
            self.settings[f[0]] = ",".join(f[1:])
            return "%s,OK" % f[0]
        if f[0] == "JPM":
            raise AssertionError("JPM,SCN_MODE would start scanning")
        if f[0] == "CIN" and self.programming:
            n = int(f[1])
            if len(f) == 2:
                row = self.stored.get(n)
                if row is None:
                    tag = "Bank %i- %03i" % ((n - 1) // 100 + 1,
                                             (n - 1) % 100 + 1)
                    row = [tag, "00000000"] + EMPTY[:]
                return ",".join(["CIN", f[1]] + row)
            freq = int(f[3])
            if not 250000 <= freq <= 13000000:
                return "CIN,ERR"
            self.stored[n] = [f[2], "%08i" % (freq // 125 * 125)] + f[4:]
            return "CIN,OK"
        if f[0] == "DCH" and self.programming:
            self.stored.pop(int(f[1]), None)
            return "DCH,OK"
        return "ERR"

    def writes(self):
        return [s for s in self.sent if s.startswith(("CIN,", "DCH,"))
                and s.count(",") > 1]


@unittest.skipIf(SOURCE is None, "CHIRP source is not unpacked")
class UBCD260DNDriverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drv = load_driver()
        from chirp import errors, memmap

        cls.errors = errors
        cls.memmap = memmap

    def radio(self, port):
        radio = self.drv.UBCD260DNRadio(
            self.memmap.MemoryMapBytes(self.drv.blank_image())
        )
        radio.pipe = port
        radio.status_fn = lambda status: None
        return radio

    def test_blank_image_matches_model(self):
        image = self.drv.blank_image()
        self.assertEqual(len(image), self.drv.MEM_SIZE)
        self.assertTrue(self.drv.UBCD260DNRadio.match_model(image, "x.img"))
        self.assertFalse(
            self.drv.UBCD260DNRadio.match_model(image[:-1], "x.img")
        )

    def test_download_decodes_channels(self):
        port = FakeScannerPort({1: HAMPAGER, 2: DMR, 101: AIRBAND})
        radio = self.radio(port)
        radio.sync_in()
        one = radio.get_memory(1)
        self.assertEqual((one.freq, one.name), (433_625_000, "HAMPAGER"))
        self.assertEqual((one.mode, one.tmode, one.ctone), ("FM", "TSQL", 118.8))
        self.assertEqual(one.skip, "")
        two = radio.get_memory(2)
        self.assertEqual((two.mode, two.tmode), ("NFM", ""))
        extra = {s.get_name(): s.value for s in two.extra}
        self.assertEqual(str(extra["digital_code"]), "DMR colour code 11")
        self.assertEqual(str(extra["alert_light"]), "BLUE")
        self.assertEqual(str(extra["alert_pattern"]), "Slow")
        self.assertEqual(int(extra["alert_tone"]), 2)
        air = radio.get_memory(101)
        self.assertEqual((air.mode, air.tmode, air.dtcs), ("AM", "DTCS", 23))
        self.assertTrue(radio.get_memory(3).empty)
        self.assertEqual(port.sent[0:3], ["MDL", "STS", "PRG"])
        self.assertEqual(port.sent[-2:], ["EPG", "KEY,S,P"])
        self.assertFalse(port.programming)

    def test_bands_follow_the_manual(self):
        bands = self.radio(FakeScannerPort()).get_features().valid_bands
        allowed = lambda hz: any(lo <= hz <= hi for lo, hi in bands)
        self.assertTrue(allowed(1_299_925_000))  # 1200 MHz band
        self.assertTrue(allowed(806_000_000))
        self.assertTrue(allowed(145_500_000))
        self.assertFalse(allowed(600_000_000))  # 513-758 MHz
        self.assertFalse(allowed(830_000_000))  # 823.99-849.01 MHz
        self.assertFalse(allowed(1_310_000_000))
        # The upper limit is inclusive on the scanner, exclusive in CHIRP.
        self.assertTrue(allowed(1_300_000_000))
        self.assertTrue(allowed(512_999_900))
        self.assertFalse(allowed(513_000_000))

    def test_delay_is_one_of_the_manual_values(self):
        port = FakeScannerPort({1: HAMPAGER})
        radio = self.radio(port)
        radio.sync_in()
        mem = radio.get_memory(1)
        extra = {s.get_name(): s for s in mem.extra}
        self.assertEqual(str(extra["delay"].value), "0")
        extra["delay"].value = "-5"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.stored[1][3], "-5")

    def test_held_scanner_is_not_told_to_scan(self):
        port = FakeScannerPort(scanning=False)
        self.radio(port).sync_in()
        self.assertEqual(port.sent[-1], "EPG")
        self.assertNotIn("KEY,S,P", port.sent)

    def test_download_accepts_family_models(self):
        port = FakeScannerPort(model="BCD160")
        self.radio(port).sync_in()
        self.assertIn("PRG", port.sent)

    def test_download_rejects_other_models(self):
        port = FakeScannerPort(model="UBC370CLT")
        with self.assertRaises(self.errors.RadioError):
            self.radio(port).sync_in()
        self.assertNotIn("PRG", port.sent)

    def test_unchanged_upload_writes_nothing(self):
        port = FakeScannerPort({1: HAMPAGER, 2: DMR})
        radio = self.radio(port)
        radio.sync_in()
        port.sent.clear()
        radio.sync_out()
        self.assertEqual(port.writes(), [])

    def test_edit_is_written_with_all_fields_and_read_back(self):
        port = FakeScannerPort({1: HAMPAGER, 2: DMR})
        radio = self.radio(port)
        radio.sync_in()
        port.sent.clear()
        mem = radio.get_memory(2)
        mem.name = "New, name"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.writes(), [
            "CIN,2,New  name,04344625,NFM,0,2,,,100B,0,0,1,1,2,Auto,BLUE,1,"
            "400,0,15"
        ])
        self.assertEqual(port.stored[2][0], "New  name")

    def test_delete_uses_dch(self):
        port = FakeScannerPort({1: HAMPAGER})
        radio = self.radio(port)
        radio.sync_in()
        radio.erase_memory(1)
        radio.sync_out()
        self.assertIn("DCH,1", port.sent)
        self.assertNotIn(1, port.stored)

    def test_new_channel_with_tone_round_trips(self):
        from chirp import chirp_common

        port = FakeScannerPort()
        radio = self.radio(port)
        mem = chirp_common.Memory()
        mem.number = 5
        mem.freq = 433_625_000
        mem.name = "PMR 1"
        mem.mode = "NFM"
        mem.tmode = "TSQL"
        mem.ctone = 94.8
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(
            ",".join(port.stored[5]),
            "PMR 1,04336250,NFM,2,1,74,0,,0,0,0,1,Off,Auto,WHITE,3,400,0,15",
        )

    def test_digital_code_can_be_cleared_to_analog(self):
        port = FakeScannerPort({2: DMR})
        radio = self.radio(port)
        radio.sync_in()
        mem = radio.get_memory(2)
        for setting in mem.extra:
            if setting.get_name() == "digital_code":
                setting.value = "Off (analog)"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.stored[2][4:8], ["0", "0", "0", ""])

    def test_digital_code_is_chosen_by_name(self):
        port = FakeScannerPort({2: DMR})
        radio = self.radio(port)
        radio.sync_in()
        mem = radio.get_memory(2)
        for setting in mem.extra:
            if setting.get_name() == "digital_code":
                setting.value = "NXDN RAN 5"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.stored[2][4:8], ["2", "", "", "2005"])
        mem = radio.get_memory(2)
        extra = {s.get_name(): s.value for s in mem.extra}
        self.assertEqual(str(extra["digital_code"]), "NXDN RAN 5")
        self.assertEqual(self.drv.digital_label("SRCH"),
                         "Any digital signal (search)")
        self.assertEqual(self.drv.digital_label("4001"), "dPMR common ID 1")
        self.assertEqual(self.drv.digital_label("5ABC"), "Code 5ABC")
        self.assertEqual(self.drv.digital_code("Code 5ABC"), "5ABC")

    def test_audio_type_is_shown_and_can_be_set(self):
        port = FakeScannerPort({1: HAMPAGER, 2: DMR, 3: AIRBAND.replace(
            ",1,128,", ",1,0,")})
        radio = self.radio(port)
        radio.sync_in()

        def audio(number):
            extra = {s.get_name(): s for s in radio.get_memory(number).extra}
            return str(extra["audio_type"].value)

        self.assertEqual(audio(1), "Analog only")  # CTCSS tone
        self.assertEqual(audio(2), "Digital only")
        self.assertEqual(audio(3), "Analog only")  # no tone
        # Analog only without a tone survives an unrelated edit.
        mem = radio.get_memory(3)
        mem.name = "AIR2"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.stored[3][3:6], ["2", "1", "0"])
        # Choosing Digital only on an analog channel drops the tone.
        mem = radio.get_memory(1)
        for setting in mem.extra:
            if setting.get_name() == "audio_type":
                setting.value = "Digital only"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.stored[1][4:8], ["2", "", "", "SRCH"])
        # All clears a digital channel back to open audio.
        mem = radio.get_memory(2)
        for setting in mem.extra:
            if setting.get_name() == "audio_type":
                setting.value = "All"
            elif setting.get_name() == "digital_code":
                setting.value = "Off (analog)"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.stored[2][4:8], ["0", "0", "0", ""])

    def test_fmb_mode_is_selectable_and_round_trips(self):
        port = FakeScannerPort({
            1: "X,04336250,FMB,2,0,0,0,,0,0,0,1,0,0,WHITE,3,400,0,15",
        })
        radio = self.radio(port)
        radio.sync_in()
        mem = radio.get_memory(1)
        self.assertEqual(mem.mode, "FMB")
        self.assertIn("FMB", radio.get_features().valid_modes)
        mem.name = "Y"
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(port.stored[1][2], "FMB")
        # FM broadcast station, new channel
        from chirp import chirp_common

        station = chirp_common.Memory()
        station.number = 9
        station.freq = 96_500_000
        station.name = "YLE 1"
        station.mode = "FMB"
        self.assertEqual(radio.validate_memory(station), [])
        radio.set_memory(station)
        radio.sync_out()
        self.assertEqual(port.stored[9][2], "FMB")
        self.assertEqual(radio.get_memory(9).mode, "FMB")

    def test_settings_are_read_into_the_image(self):
        port = FakeScannerPort()
        radio = self.radio(port)
        with self.assertRaises(self.errors.RadioError):
            radio.get_settings()  # a blank image holds none
        radio.sync_in()
        items = {s.get_name(): s.value for g in radio.get_settings()
                 for s in g}
        self.assertEqual(int(items["volume"]), 5)
        self.assertEqual(str(items["beep"]), "Auto")
        self.assertEqual(str(items["dimmer"]), "Middle")
        self.assertEqual(str(items["color"]), "WHITE")
        self.assertEqual(str(items["message1"]), "Uniden Bearcat")
        self.assertEqual(str(items["message3"]), "")
        self.assertEqual(str(items["priority_mode"]), "Off")

    def test_changed_settings_are_written_and_others_are_not(self):
        port = FakeScannerPort()
        radio = self.radio(port)
        radio.sync_in()
        tree = radio.get_settings()
        for group in tree:
            for item in group:
                name = item.get_name()
                if name == "volume":
                    item.value = 9
                elif name == "beep":
                    item.value = "Off"
                elif name == "dimmer":
                    item.value = "Auto, + polarity"
                elif name == "message2":
                    item.value = "Hello you"
        radio.set_settings(tree)
        port.sent.clear()
        radio.sync_out()
        sent = [s for s in port.sent
                if s.split(",")[0] in ("VOL", "SQL", "KBP", "BSV", "BKL",
                                       "CNT", "OMS", "PRI")
                and s.count(",") > 0]
        self.assertEqual(sent, [
            "VOL,9", "KBP,99,0", "BKL,+POL,WHITE",
            "OMS,Uniden Bearcat,Hello you,                ,                ",
        ])
        self.assertEqual(port.sent[port.sent.index("PRG") - 1], "VOL,9")
        self.assertEqual(port.settings["KBP"], "99,0")

    def test_bank_names_are_read_edited_and_written(self):
        port = FakeScannerPort()
        port.banks[3] = "Marine"
        radio = self.radio(port)
        radio.sync_in()
        names = {s.get_name(): s for g in radio.get_settings() for s in g
                 if s.get_name().startswith("bank")}
        self.assertEqual(len(names), 10)
        self.assertEqual(str(names["bank1"].value), "Bank 1")
        self.assertEqual(str(names["bank3"].value), "Marine")
        tree = radio.get_settings()
        for group in tree:
            for item in group:
                if item.get_name() == "bank1":
                    item.value = "Ham"
                elif item.get_name() == "bank10":
                    item.value = "Air band"
        radio.set_settings(tree)
        port.sent.clear()
        radio.sync_out()
        self.assertEqual(
            [s for s in port.sent if s.startswith("BIN,") and
             s.count(",") > 1],
            ["BIN,1,Ham", "BIN,10,Air band"])
        self.assertEqual(port.banks[1], "Ham")
        self.assertEqual(port.banks[3], "Marine")

    def test_blank_image_never_writes_settings(self):
        port = FakeScannerPort()
        self.radio(port).sync_out()
        self.assertNotIn("VOL,5", port.sent)
        self.assertFalse([s for s in port.sent if s.startswith("KBP,")])

    def test_rejected_frequency_is_an_error(self):
        from chirp import chirp_common

        port = FakeScannerPort()
        radio = self.radio(port)
        mem = chirp_common.Memory()
        mem.number = 1
        mem.freq = 100_000  # skips validation, as a hand-edited image
        mem.mode = "FM"
        radio.set_memory(mem)
        with self.assertRaises(self.errors.RadioError):
            radio.sync_out()
        self.assertIn("EPG", port.sent)


if __name__ == "__main__":
    unittest.main()
