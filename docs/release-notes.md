Both ARM64 variants passed cold boot, reboot, runtime ABI/page-size, core-package, SELinux and settings checks. The KernelSU variant also passed Root and manager-installation checks.

- `AvdLiteArm64WithKsu-Android<version>-api<API>.zip`
- `AvdLiteArm64WithoutKsu-Android<version>-api<API>.zip`

Each ZIP includes source metadata, build and boot reports, file SHA-256 checksums, an ARM64 AVD template and a standalone importer. Consult `verification.json` for the exact host and acceleration mode used.

Extract the ZIP and run `python3 import-avd.py --sdk /path/to/android-sdk` on an ARM64 emulator host. Python 3.9 or later is required. The importer refuses to overwrite existing AVDs.
