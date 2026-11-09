"""Import an AvdLite bundle into an Android SDK and create an independent AVD."""
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path


def install(bundle, sdk, avd_home, name=None):
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    image_name = 'AvdLiteArm64WithKsu' if manifest['ksu'] else 'AvdLiteArm64WithoutKsu'
    image = manifest['image']
    if image.get('abi') != 'arm64-v8a':
        raise ValueError('This importer requires an ARM64 bundle')
    values = json.loads((bundle / 'avd-config.json').read_text())
    if values.get('abi.type') != 'arm64-v8a' or values.get('hw.cpu.arch') != 'arm64':
        raise ValueError('AVD template has the wrong CPU architecture')
    name = name or image_name
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('AVD names may contain letters, digits, periods, underscores, and hyphens')
    for line in (bundle / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest, filename = line.split('  ', 1)
        path = bundle / filename
        path.resolve().relative_to(bundle.resolve())
        actual = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                actual.update(chunk)
        if actual.hexdigest() != digest:
            raise ValueError('Bundle checksum verification failed: ' + filename)
    sdk, avd_home = sdk.resolve(), avd_home.resolve()
    emulator = sdk / 'emulator' / ('emulator.exe' if os.name == 'nt' else 'emulator')
    if not emulator.exists():
        raise ValueError('The selected SDK is missing Android Emulator')
    target = sdk / 'system-images' / ('android-' + image['api']) / name / 'arm64-v8a'
    avd = avd_home / (name + '.avd')
    registry = avd_home / (name + '.ini')
    for path in (target, avd, registry):
        if path.exists():
            raise FileExistsError('Target already exists; choose another SDK or AVD name: ' + str(path))
    shutil.copytree(bundle / 'arm64-v8a', target)
    avd.mkdir(parents=True)
    values.update({'AvdId': name, 'avd.ini.displayname': name, 'image.sysdir.1': target.as_posix() + '/'})
    (avd / 'config.ini').write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')
    registry.write_text(f'avd.ini.encoding=UTF-8\npath={avd}\ntarget=android-{image["api"]}\n', encoding='utf-8')
    print(f'Created AVD: {name}\nSystem image: {target}\nAVD directory: {avd}')
    print(f'Launch command: "{emulator}" -avd {name}')
    if avd_home != Path(os.environ.get('ANDROID_AVD_HOME', Path.home() / '.android/avd')).resolve():
        print(f'Set ANDROID_AVD_HOME={avd_home} before launching')
    return avd


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True, help='Android SDK directory')
    parser.add_argument('--avd-home', type=Path, default=Path(os.environ.get('ANDROID_AVD_HOME', Path.home() / '.android/avd')))
    parser.add_argument('--name')
    args = parser.parse_args()
    install(Path(__file__).resolve().parent, args.sdk, args.avd_home, args.name)
