"""End-to-end tests: run factory_system.py in a temp folder with scripted keyboard input."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN = ["admin", "SmartTech@2026"]


class FactorySystemTest(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp()
        shutil.copy(os.path.join(HERE, "factory_system.py"), self.workdir)

    def tearDown(self):
        shutil.rmtree(self.workdir)

    def run_program(self, lines):
        return subprocess.run([sys.executable, os.path.join(self.workdir, "factory_system.py")],
                              input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=30)

    def test_three_failed_logins_terminate(self):
        result = self.run_program(["admin", "wrong", "nobody", "x", "admin", "smarttech@2026"])
        self.assertIn("2 attempt(s) remaining", result.stdout)
        self.assertIn("Maximum login attempts exceeded", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_full_workflow(self):
        result = self.run_program(LOGIN + [
            "9",                                                             # invalid menu choice
            "2", "SMT Line 1", "SMT Placement", "-5", "120", "250", "", "0", "Y",
                 "Wave Solder 1", "Soldering", "60", "180", "", "1000", "N",
            "4", "Jane", "Brown", "876-555-0101", "1", "SMT Placement", "1",
            "4", "Omar", "Reid", "abc", "876-555-0102", "3", "Soldering", "3",
            "1", "P009", "P001", "M001", "E001", "-3", "100", "Y", "Y", "90", "10",
            "6", "M002", "E001", "E002", "2", "Faulty conveyor sensor", "450", "Y", "N",
            "1", "P002", "M002", "M001", "E001", "50", "Y", "N",             # M002 under maintenance
            "5", "M001", "Q",                                                # busy machine, then cancel
            "6", "2", "MT0001", "Y",
            "5", "M002", "broken", "out of service",
            "1", "2", "PO0002", "48", "2",
            "3", "2", "P001", "", "", "", "1600", "",
            "7", "1", "2", "3", "4", "5", "6", "7", "0",
            "8",
        ])
        out = result.stdout
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in (
            "'9' is not a valid menu choice",
            "value cannot be negative",
            "'P009' is not a valid product ID",
            "invalid format",
            "$151,666.67",                                  # 100*1500 + (400 min / 60) h * $250
            "Defect rate               : 10.00%",
            "E001 is a Machine Operator",
            "machine M002 is MAINTENANCE and cannot be assigned",
            "has in-progress production order PO0002",
            "Operation cancelled",
            "'broken' is not a valid option",
            "status changed from AVAILABLE to OUT OF SERVICE",
            "11.67",                                        # 6.67 h + 5.00 h on M001
            "Overall defect rate       : 8.00%",            # 12 defective / 150 total
            "$1,600.00",                                    # updated material cost
            "$1,200.00",                                    # 2800 - 1600 profit
            "Total out of service machines: 1",
            "Total maintenance cost: $450.00",
            "Morning : 1",
            "Night   : 1",
            "'7' is not a valid report choice",
            "Goodbye",
        ):
            self.assertIn(expected, out)

        # Records persist across runs.
        rerun = self.run_program(LOGIN + ["7", "1", "0", "8"])
        self.assertIn("SMT Line 1", rerun.stdout)
        self.assertIn("Total number of machines: 2", rerun.stdout)


if __name__ == "__main__":
    unittest.main()
