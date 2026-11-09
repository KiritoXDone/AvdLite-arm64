"""Package verified system images, AVD templates, the importer, and file checksums."""
import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path
from .arm64 import ABI, require_arm64
from .checksums import file_digest


def package(work):
    verification = json.loads((work / 'verification.json').read_text())
    if verification.get('status') != 'passed':
        raise ValueError('Packaging requires passed boot verification')
    return _bundle(work, verification, unverified=False)


def _bundle(work, verification, *, unverified):
    """Write a bundle after the caller has enforced the appropriate release gate."""
    output = work / 'output'
    expected = 'unverified' if unverified else 'passed'
    if verification.get('status') != expected:
        raise ValueError('Bundle status does not match the selected release gate')
    if verification.get('system_sha256') != file_digest(output / ABI / 'system.img'):
        raise ValueError('Candidate system image differs from the build report' if unverified else
                         'Boot verification does not match the image being packaged')
    project = Path(__file__).resolve().parent.parent
    for name in ('manifest.json', 'build-report.json', 'verification.json'):
        shutil.copyfile(work / name, output / name)
    shutil.copyfile(project / 'avdlite/import_avd.py', output / 'import-avd.py')
    shutil.copyfile(project / 'resources/avd-config.json', output / 'avd-config.json')
    state = ('**Unverified build:** cold boot, reboot and Root have not been validated.\n\n'
             if unverified else 'See verification.json for the actual boot environment.\n\n')
    (output / 'README.md').write_text('# AvdLite ARM64\n\n' + state + 'Import with Python 3.9 or later:\n\n```text\npython import-avd.py --sdk <Android-SDK-directory>\n```\n\nUse an ARM64 Android Emulator host. Use `--avd-home <directory>` and `--name <name>` for an independent AVD. Defaults: 2 CPU cores, 4096 MB RAM, 32 GB storage, Resizable display, and Quick Boot. File checksums are verified before import.\n', encoding='utf-8')
    hashes = []
    for path in sorted(output.rglob('*')):
        if path.is_file() and path.name != 'SHA256SUMS':
            digest = file_digest(path)
            hashes.append(f'{digest}  {path.relative_to(output).as_posix()}')
    (output / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n', encoding='utf-8')
    manifest = json.loads((work / 'manifest.json').read_text())
    source = manifest['image']
    require_arm64(source)
    image_name = 'AvdLiteArm64WithKsu' if manifest['ksu'] else 'AvdLiteArm64WithoutKsu'
    properties = dict(line.split('=', 1) for line in (output / ABI / 'build.prop').read_text(encoding='utf-8').splitlines() if '=' in line)
    android_version = properties['ro.build.version.release']
    if not re.fullmatch(r'\d+(?:\.\d+)*', android_version):
        raise ValueError('Packaging requires a stable Android version number: ' + android_version)
    prefix = 'Unverified-' if unverified else ''
    name = f'{prefix}{image_name}-Android{android_version}-api{source["api"]}'
    release = work / 'release'
    release.mkdir()
    archive = release / (name + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                target.write(path, path.relative_to(output))
    digest = file_digest(archive)
    (release / (name + '.sha256')).write_text(f'{digest}  {archive.name}\n')
    print(f'Bundle: {archive.name}, {archive.stat().st_size} bytes, SHA-256 {digest}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    package(parser.parse_args().work)
