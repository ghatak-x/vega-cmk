"""Offline PSI validation tests with no hardware or Checkmk site required."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / "src/bin"
sys.path.insert(0, str(BIN))
from firetv_resource_pressure import parse_psi, render_psi

CPU = """some avg10=7.82 avg60=8.08 avg300=8.29 total=1164887556
full avg10=0.00 avg60=0.00 avg300=0.00 total=0
"""
IO = """some avg10=0.05 avg60=0.18 avg300=0.39 total=60171658
full avg10=0.04 avg60=0.10 avg300=0.14 total=37210073
"""


class TestPSI(unittest.TestCase):
    def test_cpu_uses_only_some(self):
        value = render_psi(parse_psi(CPU, "cpu"), "cpu")
        self.assertIn('0 "Vega CPU pressure"', value)
        self.assertEqual(value.count("_pct="), 3)
        self.assertIn("vega_cpu_psi_some_avg300_pct=8.29;;;0;100", value)
        self.assertNotIn("full", value)
        self.assertNotIn(";7.8;", value)  # no warning/critical levels

    def test_cpu_without_full_is_accepted(self):
        self.assertEqual(parse_psi(CPU.splitlines()[0], "cpu")["some"]["10"], 7.82)

    def test_io_some_and_full(self):
        value = render_psi(parse_psi(IO, "io"), "io")
        self.assertIn('0 "Vega I/O pressure"', value)
        self.assertEqual(value.count("_pct="), 6)
        self.assertIn("vega_io_psi_full_avg60_pct=0.10;;;0;100", value)

    def test_missing_or_invalid_never_becomes_zero(self):
        for raw, resource in [
            ("", "cpu"),
            ("""some avg10=0.0 avg60=0.0 avg300=0.0 total=0""", "io"),
            (CPU + CPU, "cpu"),
            (CPU.replace("avg10=7.82", "avg10=100.01"), "cpu"),
            (IO.replace("avg60=0.18", "avg60=NaN"), "io"),
        ]:
            with self.subTest(resource=resource, raw=raw[:25]):
                with self.assertRaises(ValueError):
                    parse_psi(raw, resource)

    def test_offline_files_and_missing_resource_independent(self):
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t)
            cpu_file, io_file = folder / "cpu", folder / "io"
            cpu_file.write_text(CPU)
            io_file.write_text("broken file")
            p = subprocess.run(
                [sys.executable, str(BIN / "firetv_resource_pressure.py"),
                 "--cpu-file", str(cpu_file), "--io-file", str(io_file)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('0 "Vega CPU pressure"', p.stdout)
            self.assertIn('3 "Vega I/O pressure"', p.stdout)
            self.assertNotIn("7.82", p.stderr)

    def test_config_boolean_flags(self):
        # No site modification: exercise private JSON parsing with mock paths.
        import json
        from firetv_config import load_config
        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            (home / "etc").mkdir()
            vda = home / "vda"
            vda.write_text("#!/bin/sh\nexit 0\n")
            vda.chmod(0o700)
            cfg = {"device": "192.0.2.10:5555", "vda_path": str(vda),
                   "cpu_pressure_enabled": False, "io_pressure_enabled": True}
            setting = home / "etc/firetv_vega.json"
            setting.write_text(json.dumps(cfg))
            setting.chmod(0o600)
            old = os.environ.get("OMD_ROOT")
            try:
                os.environ["OMD_ROOT"] = str(home)
                self.assertFalse(load_config()["cpu_pressure_enabled"])
                self.assertTrue(load_config()["io_pressure_enabled"])
                cfg["io_pressure_enabled"] = "false"  # string must fail closed
                setting.write_text(json.dumps(cfg))
                with self.assertRaisesRegex(RuntimeError, "io_pressure_enabled"):
                    load_config()
            finally:
                if old is None:
                    os.environ.pop("OMD_ROOT", None)
                else:
                    os.environ["OMD_ROOT"] = old

    def test_offline_cpu_only_omits_io(self):
        with tempfile.TemporaryDirectory() as t:
            cpu_file = Path(t) / "cpu"
            cpu_file.write_text(CPU)
            p = subprocess.run(
                [sys.executable, str(BIN / "firetv_resource_pressure.py"),
                 "--cpu-file", str(cpu_file)], capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(p.returncode, 0)
            self.assertIn('"Vega CPU pressure"', p.stdout)
            self.assertNotIn('"Vega I/O pressure"', p.stdout)


if __name__ == "__main__":
    unittest.main()
