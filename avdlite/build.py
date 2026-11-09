"""Modify filesystem copies, sign vbmeta, and produce a standalone emulator image."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from . import assemble, customize, ksu, overlays, tools
from .layout import partitions, read_at, transfer
from .arm64 import ABI, require_arm64, kernel_info
from .checksums import file_digest


def run(*args, allowed=(0,)):
    print('+', ' '.join(map(str, args)), flush=True)
    result = subprocess.run(list(map(str, args)))
    if result.returncode not in allowed:
        raise subprocess.CalledProcessError(result.returncode, args)


def sha256(path):
    return file_digest(path)


def sign_vbmeta(source, layout, work, avb):
    entry = next(p for p in layout['gpt'] if p['name'] == 'vbmeta')
    original = work / 'original-vbmeta.img'
    original.write_bytes(read_at(source, entry['offset'], entry['size']))
    tool, key = avb / 'avbtool.py', avb / 'avb-testkey.pem'
    public = work / 'avb-public.bin'
    run(sys.executable, tool, 'extract_public_key', '--key', key, '--output', public)
    data = original.read_bytes()
    if data[:4] != b'AVB0':
        raise ValueError('Missing AVB header')
    auth_size = struct.unpack_from('>Q', data, 12)[0]
    key_offset, key_size = struct.unpack_from('>QQ', data, 64)
    if data[256 + auth_size + key_offset:256 + auth_size + key_offset + key_size] != public.read_bytes():
        raise ValueError('AVB public key verification failed; signing requires the matching AOSP test key')
    spec = importlib.util.spec_from_file_location('avdlite_avbtool', tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    header = module.AvbVBMetaHeader(data[:256])
    if not module.verify_vbmeta_signature(header, data[:256 + header.authentication_data_block_size + header.auxiliary_data_block_size]):
        raise ValueError('Original vbmeta signature verification failed')
    output = work / 'vbmeta.img'
    rollback = struct.unpack_from('>Q', data, 112)[0]
    location = struct.unpack_from('>I', data, 124)[0]
    # Resized partitions require fresh vbmeta signed with the trusted test key and old hashtrees disabled.
    run(sys.executable, tool, 'make_vbmeta_image', '--output', output, '--algorithm', 'SHA256_RSA4096',
        '--key', key, '--flags', '3', '--rollback_index', rollback, '--rollback_index_location', location, '--padding_size', '4096')
    run(sys.executable, tool, 'verify_image', '--image', output, '--key', key)
    return output


@contextlib.contextmanager
def mounted(image, folder):
    folder.mkdir(parents=True)
    run('mount', '-t', 'ext4', '-o', 'loop,rw', image, folder)
    try:
        yield folder
    finally:
        run('umount', folder)


def build(manifest, cache, work, sdk):
    require_arm64(manifest['image'])
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('Image construction requires a Linux x86_64 build host')
    if os.geteuid() != 0:
        raise ValueError('Building requires root to mount filesystem copies')
    roots = list((work / 'source').rglob('system.img'))
    if len(roots) != 1:
        raise ValueError('Run preflight to extract the official image first')
    root = roots[0].parent
    original = root / 'system.img'
    layout = partitions(original)
    output = work / 'output' / ABI
    output.mkdir(parents=True)
    for path in root.iterdir():
        if path.is_file() and path.name not in ('system.img', 'package.xml'):
            shutil.copyfile(path, output / path.name)
        elif path.name == 'data' and path.is_dir():
            shutil.copytree(path, output / 'data')
    kernel_hash = sha256(root / 'kernel-ranchu')
    original_kernel, _ = kernel_info(root / 'kernel-ranchu')
    avb = tools.prepare(work)
    vbmeta = sign_vbmeta(original, layout, work, avb)
    files, trees, before = {}, {}, {}
    parts = work / 'partitions'
    parts.mkdir()
    with contextlib.ExitStack() as stack:
        for entry in layout['partitions']:
            path = parts / (entry['name'] + '.img')
            transfer(original, path, entry['extents'])
            files[entry['name']] = path
            if read_at(path, 1080, 2) != b'\x53\xef':
                if entry['name'] != 'system_dlkm':
                    raise ValueError('Editable partitions must use ext4: ' + entry['name'])
                continue
            run('e2fsck', '-fy', path, allowed=(0, 1))
            # Reserve space for added resources in the independent partition copy.
            with path.open('r+b') as stream:
                stream.truncate(path.stat().st_size + 512 * 1024 * 1024)
            run('resize2fs', path)
            if struct.unpack('<I', read_at(path, 1124, 4))[0] & 0x4000:
                # Expand Android shared blocks before mounting ext4 for modification.
                run('e2fsck', '-fy', '-E', 'unshare_blocks', path, allowed=(0, 1))
            tree = stack.enter_context(mounted(path, work / 'mounts' / entry['name']))
            trees[entry['name']] = tree
            before[entry['name']] = {str(p.relative_to(tree)): sha256(p) for p in tree.rglob('build.prop')}
        sdk_tools = overlays.build(work, sdk, trees)
        report = {'source': manifest['image'], 'debloat': customize.debloat(trees, sdk_tools / 'aapt2'),
                  'hardware': customize.hardware(trees), 'hardware_policy': customize.hardware_policy(trees), 'ksu': None}
        if manifest['ksu']:
            report['ksu'] = ksu.install(output, trees, cache, manifest['ksu'], work)
        customize.labels(trees)
        after = {name: {str(p.relative_to(tree)): sha256(p) for p in tree.rglob('build.prop')} for name, tree in trees.items()}
        if before != after:
            raise ValueError('Upstream build.prop changed unexpectedly')
        report['upstream_build_properties_unchanged'] = True
    for name, path in files.items():
        if name not in trees:
            continue
        run('e2fsck', '-fy', path, allowed=(0, 1))
        run('resize2fs', '-M', path)
        run('zerofree', path)
        run('e2fsck', '-fn', path)
    report['layout'] = assemble.assemble(original, files, output / 'system.img', vbmeta)
    run('sgdisk', '--verify', output / 'system.img')
    vb = vbmeta.read_bytes()
    meaningful = 256 + struct.unpack_from('>Q', vb, 12)[0] + struct.unpack_from('>Q', vb, 20)[0]
    digest = hashlib.sha256(vb[:meaningful]).hexdigest()
    (output / 'VerifiedBootParams.textproto').write_text(f'major_version: 2\nparam: "androidboot.vbmeta.size={meaningful}"\nparam: "androidboot.vbmeta.hash_alg=sha256"\nparam: "androidboot.vbmeta.digest={digest}"\n')
    props = (output / 'source.properties').read_text()
    image_name = 'AvdLiteArm64WithKsu' if manifest['ksu'] else 'AvdLiteArm64WithoutKsu'
    props = re.sub(r'^Pkg.Desc=.*$', f'Pkg.Desc={image_name} Google APIs ARM64', props, flags=re.MULTILINE)
    (output / 'source.properties').write_text(props)
    if sha256(output / 'kernel-ranchu') != kernel_hash:
        raise ValueError('Original kernel changed unexpectedly')
    report['kernel_sha256'] = kernel_hash
    report['kernel_version'] = original_kernel
    report['original_bytes'] = original.stat().st_size
    report['system_sha256'] = sha256(output / 'system.img')
    (work / 'build-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('manifest', 'cache', 'work', 'sdk'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    build(json.loads(args.manifest.read_text()), args.cache.resolve(), args.work.resolve(), args.sdk.resolve())
