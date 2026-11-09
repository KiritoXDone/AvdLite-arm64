# AvdLite ARM64

ARM64 variant of [Kindness-Kismet/AvdLite](https://github.com/Kindness-Kismet/AvdLite), based on upstream commit `766f1587e17112b59656dc9818df1ed2d3209f42`.

The Python build tools modify official Google APIs **arm64-v8a** emulator images. Two variants are produced: `AvdLiteArm64WithKsu` and `AvdLiteArm64WithoutKsu`. The original ARM64 kernel and upstream build properties are retained.

## Images

- Android 15 / API 35 and newer stable official images.
- Explicit API selection, plus `auto`, `4k`, and `16k` page-size selection.
- Optional official KernelSU LKM matched to the kernel KMI, with an ARM64 manager daemon and init payload.
- The upstream debloat list, retained GMS/WebView/keyboard/launcher, theme overlays, hardware-port checks and SELinux Enforcing policy.
- Independent AVD names, source checksums, image checksums and boot evidence.

## Build and verification

**Build Latest ARM64** runs on pushes and pull requests using the latest official **4 KB** ARM64 image. **Build ARM64 Version** accepts a selected API, page size and KernelSU setting.

1. An Ubuntu x86_64 runner downloads and verifies official sources, modifies filesystem copies and patches the ARM64 ramdisk.
2. The candidate image is transferred to an Apple Silicon runner. The verifier checks the runtime ABI, page size, core packages, SELinux, KernelSU, cold boot and reboot persistence.
3. Both variants must pass before the workflow publishes release ZIPs. Packaging binds the boot report to the exact system-image SHA-256. Candidate artifacts are not verified releases.

GitHub-hosted Apple Silicon runners do not support nested virtualization. Cloud verification explicitly selects TCG software emulation, with a three-minute boot limit and progress logs every 30 seconds. The Android Emulator 37.2.13 TCG `cortex-a53` and `max` CPU models do not advertise 16 KB translation granules; a 16 KB kernel parks before starting Android. The verifier rejects this combination immediately. A 16 KB candidate requires an ARM64 host with hardware virtualization and `--accel on`.

**Diagnose ARM64 Candidate** reuses an existing build run's candidate for a 90-second cold-boot diagnosis. It does not rebuild images or publish a release. Reports and kernel logs are retained as artifacts. A successful filesystem build alone is not a successful boot.

## Import a verified image

Download a ZIP from this repository's Releases and extract it. With Python 3.9 or later:

```sh
python3 import-avd.py --sdk /path/to/android-sdk --name AvdLiteArm64
```

Use an ARM64 Android Emulator host, such as an Apple Silicon Mac. The importer checks the bundle checksums and ABI before creating an independent AVD. It refuses to overwrite an existing AVD or SDK image directory. `--avd-home` selects a separate data directory.

Defaults are 2 CPU cores, 4096 MB RAM, 32 GB storage, a Resizable display and Quick Boot. CI uses a smaller disposable data partition and disables snapshots.

## Local build

Image construction requires Linux x86_64, root for mounting private filesystem copies, Android SDK build-tools 35.0.0/platform 35, `e2fsprogs`, `gdisk`, `zerofree`, `lz4`, `cpio`, `curl` and Java. The build does not flash a physical device.

```sh
python3 -m unittest discover -s tests -v
python3 -m avdlite.sources resolve --api latest --page-size auto --ksu --manifest .work/manifest.json
python3 -m avdlite.sources download --manifest .work/manifest.json --cache .cache
python3 -m avdlite.preflight --archive .cache/image/source.zip --work .work
sudo python3 -m avdlite.build --manifest .work/manifest.json --cache .cache --work .work --sdk "$ANDROID_HOME"
```

Transfer `.work/output`, `.work/manifest.json` and `.work/build-report.json` to an ARM64 emulator host, then run:

```sh
python3 -m avdlite.smoke --manifest .work/manifest.json --work .work --sdk "$ANDROID_HOME" --accel on
python3 -m avdlite.package --work .work
```

Build source and the original upstream history are preserved. Google images and KernelSU assets are obtained from their official publishers; no third-party Android image mirrors are used.
