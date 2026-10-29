"""Offline tests for the experimental Uniden UBC370CLT driver."""

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
        "newchirp_chirp_bridge_ubc370clt", ROOT / "scripts/chirp_bridge.py"
    )
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    bridge.load_chirp(str(SOURCE), str(VENDOR), str(ROOT / "drivers"))
    return sys.modules["newchirp.drivers.newchirp_ubc370clt"]


class FakeScannerPort:
    """Answers like the UBC370CLT probed in docs/PROTOCOL_370.md of the
    standalone programmer: CR-terminated commands, CIN in program mode only,
    frequencies rounded down to 12.5 kHz, unreceivable ones kept as before."""

    def __init__(self, stored=None, model="UBC370CLT", fm=None):
        self.timeout = 1.0
        self.model = model
        self.stored = dict(stored or {})
        self.fm = dict(fm or {})
        self.sent = []
        self.programming = False
        self.powered_off = False
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
        if f[0] == "PRG":
            self.programming = True
            return "PRG,OK"
        if f[0] == "JPM":
            raise AssertionError("JPM,SCN_MODE forces scanning over FM mode")
        if f[0] == "EPG":
            self.programming = False
            self.powered_off = True
            return "EPG,OK"
        if f[0] == "CIN" and self.programming:
            n = int(f[1])
            empty = ["", "00000000", "", "0", "0", "1", "0"]
            if len(f) == 2:
                return ",".join(["CIN", f[1]] + self.stored.get(n, empty))
            old = self.stored.get(n, empty)
            freq = int(f[3])
            if freq and not 250000 <= freq <= 9600000:
                freq = int(old[1])
            self.stored[n] = [
                f[2][:16], "%08i" % (freq // 125 * 125), "", f[5],
                f[6], f[7], f[8],
            ]
            return "CIN,OK"
        if f[0] == "RIN" and self.programming:
            n = int(f[1])
            if len(f) == 2:
                return "RIN,%i,%s,%s" % (n, *self.fm.get(n, ("", "00000000")))
            freq = int(f[3])
            if not 873000 <= freq <= 1079000:
                # Answered OK but not stored; this includes 0, so a preset
                # cannot be emptied.
                freq = int(self.fm.get(n, ("", "0"))[1])
            self.fm[n] = (f[2][:16], "%08i" % freq)
            return "RIN,OK"
        return "ERR"


@unittest.skipIf(SOURCE is None, "CHIRP source is not unpacked")
class UBC370CLTDriverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drv = load_driver()
        from chirp import errors, memmap

        cls.errors = errors
        cls.memmap = memmap

    def radio(self, port=None):
        radio = self.drv.UBC370CLTRadio(
            self.memmap.MemoryMapBytes(self.drv.blank_image())
        )
        radio.pipe = port
        radio.status_fn = lambda status: None
        return radio

    def test_blank_image_matches_model(self):
        image = self.drv.blank_image()
        self.assertEqual(len(image), self.drv.MEM_SIZE)
        self.assertTrue(self.drv.UBC370CLTRadio.match_model(image, "x.img"))
        self.assertFalse(
            self.drv.UBC370CLTRadio.match_model(image[:-1], "x.img")
        )

    def test_download_decodes_channels(self):
        port = FakeScannerPort({
            1: ["Rescue", "01460125", "", "76", "1", "0", "0"],
            2: ["Police", "04600000", "", "150", "0", "1", "0"],
        })
        radio = self.radio(port)
        radio.pipe = port
        radio.sync_in()
        one = radio.get_memory(1)
        self.assertEqual(one.freq, 146_012_500)
        self.assertEqual(one.name, "Rescue")
        self.assertEqual(one.tmode, "TSQL")
        self.assertEqual(one.ctone, 100.0)
        self.assertEqual(one.skip, "")
        two = radio.get_memory(2)
        self.assertEqual(two.tmode, "DTCS")
        self.assertEqual(two.dtcs, 132)
        self.assertEqual(two.skip, "S")
        self.assertTrue(radio.get_memory(3).empty)
        self.assertEqual(port.sent[0:2], ["MDL", "PRG"])
        self.assertEqual(port.sent[-1], "EPG")
        self.assertFalse(port.programming)
        self.assertTrue(port.powered_off)

    def test_mode_is_am_on_the_air_band_and_fm_elsewhere(self):
        port = FakeScannerPort({
            1: ["Tower", "01180000", "", "0", "1", "0", "0"],
            2: ["Edge", "01370000", "", "0", "1", "0", "0"],
            3: ["Police", "04600000", "", "0", "1", "0", "0"],
            4: ["Below", "01079000", "", "0", "1", "0", "0"],
        })
        radio = self.radio(port)
        radio.pipe = port
        radio.sync_in()
        modes = [radio.get_memory(n).mode for n in (1, 2, 3, 4)]
        self.assertEqual(modes, ["AM", "AM", "FM", "FM"])
        self.assertTrue(radio.NEWCHIRP_MODE_READ_ONLY)

    def test_download_rejects_other_models(self):
        port = FakeScannerPort(model="BCD260DN")
        with self.assertRaises(self.errors.RadioError):
            self.radio(port).sync_in()
        self.assertNotIn("PRG", port.sent)

    def test_upload_writes_only_changes_and_reads_back(self):
        port = FakeScannerPort({
            1: ["Rescue", "01460125", "", "76", "1", "0", "0"],
            2: ["Old", "04600000", "", "0", "1", "0", "0"],
        })
        radio = self.radio(port)
        radio.pipe = port
        radio.sync_in()
        mem = radio.get_memory(2)
        mem.name = "New, name"
        radio.set_memory(mem)
        radio.sync_out()
        writes = [line for line in port.sent if line.count(",") == 8]
        self.assertEqual(writes, ["CIN,2,New  name,04600000,,0,1,0,0"])
        self.assertEqual(port.stored[2][0], "New  name")
        self.assertEqual(port.sent[-1], "EPG")

    def test_upload_clears_deleted_channel(self):
        port = FakeScannerPort({
            1: ["Rescue", "01460125", "", "76", "1", "0", "0"],
        })
        radio = self.radio(port)
        radio.pipe = port
        radio.sync_in()
        radio.erase_memory(1)
        radio.sync_out()
        self.assertIn("CIN,1,,00000000,,0,0,1,0", port.sent)
        self.assertNotIn("DCH,1", port.sent)

    def test_new_channel_with_tone_round_trips(self):
        from chirp import chirp_common

        port = FakeScannerPort()
        radio = self.radio(port)
        radio.pipe = port
        mem = chirp_common.Memory()
        mem.number = 5
        mem.freq = 433_625_000
        mem.name = "PMR 1"
        mem.mode = "FM"
        mem.tmode = "TSQL"
        mem.ctone = 94.8
        radio.set_memory(mem)
        radio.sync_out()
        self.assertEqual(
            port.stored[5], ["PMR 1", "04336250", "", "74", "1", "0", "0"]
        )

    def test_download_reads_fm_presets_as_locations_501_to_530(self):
        port = FakeScannerPort(fm={
            1: ("Yle Radio Suomi", "00965000"),
            12: ("Radio Nova", "01006000"),
            30: ("", "00873000"),  # the cleared look: reads as empty
        })
        radio = self.radio(port)
        radio.pipe = port
        radio.sync_in()
        one = radio.get_memory(501)
        self.assertEqual((one.freq, one.name), (96_500_000, "Yle Radio Suomi"))
        self.assertEqual(one.mode, "WFM")
        self.assertEqual(radio.get_memory(512).freq, 100_600_000)
        self.assertTrue(radio.get_memory(530).empty)
        self.assertTrue(radio.get_memory(502).empty)
        self.assertEqual(self.drv.fm_label(512), "FM2-02")

    def test_fm_edit_is_written_and_clear_uses_lowest_frequency(self):
        from chirp import chirp_common

        port = FakeScannerPort(fm={2: ("Old", "00905000")})
        radio = self.radio(port)
        radio.pipe = port
        radio.sync_in()
        mem = chirp_common.Memory()
        mem.number = 521
        mem.freq = 93_900_000
        mem.name = "Rock"
        mem.mode = "WFM"
        radio.set_memory(mem)
        radio.erase_memory(502)
        radio.sync_out()
        self.assertEqual(port.fm[21], ("Rock", "00939000"))
        self.assertEqual(port.fm[2], ("", "00873000"))
        self.assertIn("RIN,21,Rock,00939000", port.sent)
        self.assertIn("RIN,2,,00873000", port.sent)
        self.assertEqual(port.sent[-1], "EPG")

    def test_fm_preset_outside_the_band_is_rejected(self):
        from chirp import chirp_common

        radio = self.radio(FakeScannerPort())
        mem = chirp_common.Memory()
        mem.number = 505
        mem.freq = 145_000_000
        mem.mode = "WFM"
        errors = [
            m for m in radio.validate_memory(mem)
            if isinstance(m, chirp_common.ValidationError)
        ]
        self.assertEqual(len(errors), 1)
        mem.freq = 98_000_000
        self.assertEqual(
            [m for m in radio.validate_memory(mem)
             if isinstance(m, chirp_common.ValidationError)],
            [],
        )

    def test_fm_preset_the_radio_ignores_is_an_error(self):
        from chirp import chirp_common

        port = FakeScannerPort()
        radio = self.radio(port)
        radio.pipe = port
        mem = chirp_common.Memory()
        mem.number = 501
        mem.freq = 145_000_000  # skips validation, as a hand-edited image
        mem.mode = "WFM"
        radio.set_memory(mem)
        with self.assertRaises(self.errors.RadioError):
            radio.sync_out()
        self.assertEqual(port.sent[-1], "EPG")

    def test_unreceivable_frequency_is_an_error(self):
        from chirp import chirp_common

        port = FakeScannerPort()
        radio = self.radio(port)
        radio.pipe = port
        mem = chirp_common.Memory()
        mem.number = 1
        mem.freq = 100_000  # 0.1 MHz: answered OK but not stored
        mem.mode = "FM"
        radio.set_memory(mem)
        with self.assertRaises(self.errors.RadioError):
            radio.sync_out()
        self.assertEqual(port.sent[-1], "EPG")


if __name__ == "__main__":
    unittest.main()
