from pathlib import Path
import unittest
from avdlite.smoke import emulator_command


class EmulatorCommandTests(unittest.TestCase):
    def test_software_mode_overrides_the_arm64_launchers_hvf_default(self):
        command = emulator_command(Path('/sdk'), 'off', 5580)
        self.assertEqual(command[command.index('-accel') + 1], 'off')
        self.assertIn('-HVF', command[command.index('-feature') + 1])
        self.assertEqual(command[-5:], ['-qemu', '-machine', 'accel=tcg', '-cpu', 'max'])

    def test_hardware_mode_keeps_hardware_acceleration_available(self):
        command = emulator_command(Path('/sdk'), 'on', 5580)
        self.assertEqual(command[command.index('-accel') + 1], 'on')
        self.assertNotIn('-HVF', command[command.index('-feature') + 1])
        self.assertNotIn('-qemu', command)
