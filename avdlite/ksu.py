"""Patch a standalone ramdisk with the latest official matching LKM and retain the stock kernel."""
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path
from .arm64 import kernel_info

RESOURCES = Path(__file__).resolve().parent.parent / 'resources'


def kernel_version(path):
    return kernel_info(path)


def install(root, trees, cache, release, work):
    version, kmi = kernel_version(root / 'kernel-ranchu')
    name = f'lkm-aarch64-{kmi}_kernelsu.ko'
    if name not in {asset['name'] for asset in release['assets']}:
        raise ValueError(f'Latest KernelSU {release["tag"]} is missing a matching {kmi} module')
    tool = cache / 'ksu/ksud-x86_64-unknown-linux-musl'
    tool.chmod(0o755)
    patched = work / 'ksu-patched'
    patched.mkdir()
    subprocess.run([str(tool), 'boot-patch', '--boot', str(root / 'ramdisk.img'), '--ramdisk', '--arch', 'aarch64',
                    '--module', str(cache / 'ksu' / name), '--kmi', kmi, '--allow-shell',
                    '--out', str(patched), '--out-name', 'ramdisk.img'], check=True)
    shutil.copyfile(patched / 'ramdisk.img', root / 'ramdisk.img')
    managers = list((cache / 'ksu').glob('KernelSU_*-release.apk'))
    expected = {asset['name'] for asset in release['assets']}
    managers = [p for p in managers if p.name in expected]
    if len(managers) != 1:
        raise ValueError('The selected release must contain exactly one KernelSU manager')
    app = trees['product'] / 'app/KernelSU'
    (app / 'lib/arm64').mkdir(parents=True)
    shutil.copyfile(managers[0], app / 'KernelSU.apk')
    with zipfile.ZipFile(managers[0]) as archive:
        native = archive.read('lib/arm64-v8a/libksud.so')
        if native[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', native, 18)[0] != 183:
            raise ValueError('KernelSU manager does not contain an AArch64 daemon')
        (app / 'lib/arm64/libksud.so').write_bytes(native)
    for resource, target in [('ksu-setup.sh', 'etc/avdlite/ksu-setup.sh'), ('ksu-setup.rc', 'etc/init/avdlite-ksu.rc')]:
        shutil.copyfile(RESOURCES / resource, trees['product'] / target)
    return {'release': release['tag'], 'kernel': version, 'kmi': kmi, 'module': name}
