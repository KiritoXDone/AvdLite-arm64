import gzip
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from avdlite.arm64 import kernel_info, require_arm64
from avdlite.ksu import install


def kernel(version='6.12.81-android16-6-gabc'):
    data = bytearray(128)
    data[56:60] = b'ARM\x64'
    return data + b'Linux version ' + version.encode() + b' (builder)\x00'


class Arm64KernelTests(unittest.TestCase):
    def test_raw_and_gzip_images_preserve_kernel_kmi(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'kernel-ranchu'
            for data in (kernel(), gzip.compress(kernel())):
                image.write_bytes(data)
                self.assertEqual(kernel_info(image), ('6.12.81-android16-6-gabc', 'android16-6.12'))

    def test_rejects_x86_or_ambiguous_kernel(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'kernel-ranchu'
            image.write_bytes(b'Linux version 6.12.81-android16-6-test\x00')
            with self.assertRaisesRegex(ValueError, 'ARM64'):
                kernel_info(image)
            image.write_bytes(kernel() + b'Linux version 6.18.1-android17-0-other\x00')
            with self.assertRaisesRegex(ValueError, 'unambiguous'):
                kernel_info(image)

    def test_rejects_wrong_manifest_abi(self):
        with self.assertRaisesRegex(ValueError, 'arm64-v8a'):
            require_arm64({'abi': 'x86_64'})

    def test_kernel_su_uses_arm_module_arch_and_manager_library(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root, cache, product = work / 'image', work / 'cache', work / 'product'
            root.mkdir(); (cache / 'ksu').mkdir(parents=True)
            (product / 'etc/avdlite').mkdir(parents=True)
            (product / 'etc/init').mkdir()
            (root / 'kernel-ranchu').write_bytes(kernel())
            (root / 'ramdisk.img').write_bytes(b'original')
            (cache / 'ksu/ksud-x86_64-unknown-linux-musl').touch()
            module = 'lkm-aarch64-android16-6.12_kernelsu.ko'
            manager = 'KernelSU_v3-release.apk'
            (cache / 'ksu' / module).touch()
            elf = bytearray(64); elf[:6] = b'\x7fELF\x02\x01'; struct.pack_into('<H', elf, 18, 183)
            with zipfile.ZipFile(cache / 'ksu' / manager, 'w') as archive:
                archive.writestr('lib/arm64-v8a/libksud.so', elf)
            release = {'tag': 'v3', 'assets': [{'name': module}, {'name': manager}]}
            def patch_ramdisk(command, **kwargs):
                self.assertEqual(command[command.index('--arch') + 1], 'aarch64')
                self.assertEqual(Path(command[command.index('--module') + 1]).name, module)
                (work / 'ksu-patched/ramdisk.img').write_bytes(b'patched')
            with patch('avdlite.ksu.subprocess.run', side_effect=patch_ramdisk):
                report = install(root, {'product': product}, cache, release, work)
            self.assertEqual(report['module'], module)
            self.assertEqual((root / 'ramdisk.img').read_bytes(), b'patched')
            self.assertEqual((product / 'app/KernelSU/lib/arm64/libksud.so').read_bytes(), elf)
            self.assertIn('/lib/arm64/', (product / 'etc/avdlite/ksu-setup.sh').read_text())

    def test_missing_arm_module_stops_before_patching(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'kernel-ranchu').write_bytes(kernel())
            with patch('avdlite.ksu.subprocess.run') as run:
                with self.assertRaisesRegex(ValueError, 'missing a matching'):
                    install(root, {}, root, {'tag': 'v3', 'assets': []}, root)
                run.assert_not_called()
