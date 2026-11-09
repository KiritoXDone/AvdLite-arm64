import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from avdlite.candidate import extract, package_candidate
from avdlite.package import package


class CandidateTests(unittest.TestCase):
    def fixture(self, work):
        image = {'api': '37.0', 'page_size': '4k', 'abi': 'arm64-v8a'}
        (work / 'output/arm64-v8a').mkdir(parents=True)
        (work / 'output/arm64-v8a/system.img').write_bytes(b'candidate-system')
        (work / 'output/arm64-v8a/kernel-ranchu').write_bytes(b'candidate-kernel')
        (work / 'output/arm64-v8a/build.prop').write_text('ro.build.version.release=17\n')
        (work / 'manifest.json').write_text(json.dumps({'image': image, 'ksu': None}))
        (work / 'build-report.json').write_text(json.dumps({'source': image, 'ksu': None,
            'system_sha256': hashlib.sha256(b'candidate-system').hexdigest(),
            'kernel_sha256': hashlib.sha256(b'candidate-kernel').hexdigest()}))

    def test_candidate_is_importable_but_never_claims_boot_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.fixture(work)
            package_candidate(work, '123', False)
            archive = next((work / 'release').glob('*.zip'))
            self.assertTrue(archive.name.startswith('Unverified-'))
            with zipfile.ZipFile(archive) as content:
                self.assertEqual(json.loads(content.read('verification.json'))['status'], 'unverified')
                self.assertIn('import-avd.py', content.namelist())
            with self.assertRaisesRegex(ValueError, 'boot verification'):
                package(work)

    def test_changed_image_and_wrong_variant_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.fixture(work)
            with self.assertRaisesRegex(ValueError, 'variant'):
                package_candidate(work, '123', True)
            (work / 'output/arm64-v8a/system.img').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'system image'):
                package_candidate(work, '123', False)
            self.assertFalse((work / 'release').exists())

    def test_archive_extraction_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'candidate.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                member = tarfile.TarInfo('../escape')
                member.size = 1
                tar.addfile(member, io.BytesIO(b'x'))
            checksum = root / 'candidate.sha256'
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                extract(archive, checksum, root / 'work')
            self.assertFalse((root / 'escape').exists())
