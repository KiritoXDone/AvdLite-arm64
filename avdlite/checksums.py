"""Streaming file checksums, including on Python versions without hashlib.file_digest."""
import hashlib
from pathlib import Path


def file_digest(path, algorithm='sha256'):
    digest = hashlib.new(algorithm)
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
