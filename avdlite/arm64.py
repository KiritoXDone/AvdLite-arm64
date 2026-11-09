"""Validate the target ABI and inspect the original ARM64 Linux Image."""
import gzip
import re
import subprocess
from pathlib import Path

ABI = 'arm64-v8a'
CPU = 'arm64'


def require_arm64(image):
    if image.get('abi') != ABI:
        raise ValueError('This project requires an official arm64-v8a system image')


def validate_emulation(image, accel):
    require_arm64(image)
    if accel == 'off' and image.get('page_size') != '4k':
        raise ValueError('Android Emulator TCG CPU models cannot boot this 16 KB image; use a 4 KB image or --accel on on a virtualization-capable ARM64 host')


def kernel_info(path):
    data = Path(path).read_bytes()
    if data.startswith(b'\x1f\x8b'):
        data = gzip.decompress(data)
    elif data[:4] in (b'\x02\x21\x4c\x18', b'\x04\x22\x4d\x18'):
        data = subprocess.run(['lz4', '-d', '-c', str(path)], capture_output=True, check=True).stdout
    if len(data) < 64 or data[56:60] != b'ARM\x64':
        raise ValueError('kernel-ranchu is not an ARM64 Linux Image')
    versions = set(re.findall(rb'Linux version (\d+\.\d+\.\d+-android\d+-[^\s\x00]+)', data))
    if len(versions) != 1:
        raise ValueError('Expected one unambiguous Android kernel version banner')
    version = versions.pop().decode('ascii')
    match = re.match(r'(\d+\.\d+)\.\d+-(android\d+)-', version)
    return version, f'{match[2]}-{match[1]}'
