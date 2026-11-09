"""Package an explicitly unverified 4 KB ARM64 image without bypassing the verified release gate."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from .arm64 import ABI, require_arm64
from .checksums import file_digest
from .package import _bundle


def extract(archive, checksum_file, work):
    digest, name = checksum_file.read_text().strip().split('  ', 1)
    if name != archive.name or not re.fullmatch(r'[a-f0-9]{64}', digest) or file_digest(archive) != digest:
        raise ValueError('Candidate archive checksum does not match')
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise ValueError('Candidate extraction requires an empty work directory')
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.name or not path.parts
                    or path.parts[0] not in ('output', 'manifest.json', 'build-report.json', 'preflight.json')
                    or not (member.isfile() or member.isdir()) or member.name in seen):
                raise ValueError('Unsafe candidate archive member: ' + member.name)
            seen.add(member.name)
        source.extractall(work, members=members)


def package_candidate(work, build_run, expect_ksu):
    if not re.fullmatch(r'[1-9]\d*', build_run):
        raise ValueError('Build run must be a numeric GitHub Actions run ID')
    manifest = json.loads((work / 'manifest.json').read_text())
    report = json.loads((work / 'build-report.json').read_text())
    image = manifest['image']
    require_arm64(image)
    if image.get('page_size') != '4k' or not re.fullmatch(r'\d+(?:\.\d+)?', image['api']):
        raise ValueError('Candidate must be a stable 4 KB ARM64 image')
    if report.get('source') != image or bool(manifest.get('ksu')) != expect_ksu or bool(report.get('ksu')) != expect_ksu:
        raise ValueError('Candidate build metadata or KernelSU variant does not match')
    if expect_ksu and (report['ksu'].get('release') != manifest['ksu']['tag'] or
                       report['ksu'].get('module') != 'lkm-aarch64-' + report['ksu']['kmi'] + '_kernelsu.ko'):
        raise ValueError('KernelSU release or module does not match the build manifest')
    output = work / 'output' / ABI
    if file_digest(output / 'kernel-ranchu') != report.get('kernel_sha256'):
        raise ValueError('Candidate kernel differs from the build report')
    verification = {'status': 'unverified', 'system_sha256': report['system_sha256'], 'build_run': build_run,
                    'reason': 'Cold boot, reboot and Root verification were not completed.'}
    (work / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    return _bundle(work, verification, unverified=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--checksum', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--build-run', required=True)
    parser.add_argument('--ksu', choices=('true', 'false'), required=True)
    args = parser.parse_args()
    extract(args.archive, args.checksum, args.work)
    package_candidate(args.work, args.build_run, args.ksu == 'true')
