"""Cold boot, reboot, and verify an isolated AVD, then stop the test emulator."""
import argparse
import json
import os
import platform
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from .arm64 import ABI, validate_emulation
from .checksums import file_digest
from .boot_progress import BootProgress, process_cpu_samples, emulator_cpu_sample


def emulator_command(sdk, accel, port):
    command = [str(sdk / 'emulator/emulator'), '-avd', 'AvdLiteArm64CI', '-port', str(port), '-no-window', '-no-audio',
               '-no-boot-anim', '-no-snapshot', '-no-metrics', '-show-kernel', '-memory', '2048', '-gpu', 'swiftshader_indirect',
               '-feature', '-Vulkan,-HVF' if accel == 'off' else '-Vulkan', '-accel', accel, '-verbose']
    if accel == 'off':
        # The ARM64 launcher can still inject -enable-hvf after -accel off.
        # QEMU's final machine option explicitly selects its software backend.
        command += ['-qemu', '-machine', 'accel=tcg', '-cpu', 'max']
    return command


class Device:
    def __init__(self, sdk, evidence, env, timeout=420, port=5580):
        self.adb = str(sdk / 'platform-tools/adb')
        self.evidence = evidence
        self.env = env
        self.process = None
        self.timeout = timeout
        self.serial = f'emulator-{port}'

    def call(self, *args, check=True, timeout=30):
        result = subprocess.run([self.adb, '-s', self.serial, *args], capture_output=True, text=True, timeout=timeout, env=self.env)
        if check and result.returncode:
            raise RuntimeError(result.stderr + result.stdout)
        return result.stdout.strip()

    def shell(self, *args, **kwargs):
        return self.call('shell', shlex.join(args), **kwargs)

    def wait(self, probe, description, timeout=420):
        started = time.monotonic()
        deadline, next_report = started + timeout, started
        progress = BootProgress(started)
        label = description.removeprefix('Timed out waiting for ')
        print(f'[wait] {label}; timeout={timeout}s', flush=True)
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f'Emulator exited early with code {self.process.returncode}')
            log = self.evidence / 'emulator.log'
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 16384))
                tail = stream.read().decode(errors='replace')
            if 'Kernel panic - not syncing:' in tail:
                raise RuntimeError('Guest kernel panic; see evidence/emulator.log')
            progress.kernel(tail, time.monotonic())
            if probe():
                print(f'[ready] {label}; elapsed={time.monotonic() - started:.0f}s', flush=True)
                return
            now = time.monotonic()
            if now >= next_report:
                try:
                    state = self.call('get-state', check=False, timeout=5) or 'unavailable'
                except subprocess.TimeoutExpired:
                    state = 'adb-timeout'
                android_log = ''
                cpu = {}
                observer_error = None
                if state == 'device':
                    try:
                        android_log = self.shell('logcat', '-b', 'all', '-d', '-t', '80', check=False, timeout=5)
                    except subprocess.TimeoutExpired:
                        observer_error = 'logcat sampling timed out'
                progress.android(state, android_log, time.monotonic())
                if state == 'device' and progress.cpu_targets:
                    names = {pid: name for name, pid in progress.cpu_targets.items()}
                    try:
                        stats = self.shell('cat', *(f'/proc/{pid}/stat' for pid in names), check=False, timeout=5)
                        cpu = process_cpu_samples(stats, names)
                        if not cpu:
                            observer_error = 'framework /proc stat unavailable'
                    except subprocess.TimeoutExpired:
                        observer_error = 'framework CPU sampling timed out'
                progress.cpu(cpu, time.monotonic())
                if (state != 'device' or not cpu) and progress.kernel_line:
                    try:
                        listing = subprocess.run(['ps', '-ax', '-o', 'pid=,ppid=,time=,comm='],
                                                 capture_output=True, text=True, check=True, timeout=5).stdout
                        host_cpu = emulator_cpu_sample(listing, self.process.pid)
                        progress.cpu(host_cpu, time.monotonic(), minimum_ticks=100)
                        cpu.update(host_cpu)
                    except (subprocess.SubprocessError, OSError):
                        pass
                idle = progress.idle_seconds(time.monotonic())
                last_line = progress.android_line or progress.kernel_line
                print(f'[waiting] {label}; elapsed={now - started:.0f}s; adb={state}; guest_idle={idle:.0f}s; cpu={cpu}', flush=True)
                print(f'[guest] {last_line[:300]}', flush=True)
                if observer_error:
                    print(f'[observer] {observer_error}', flush=True)
                (self.evidence / 'progress.json').write_text(json.dumps({'stage': label, 'elapsed_seconds': int(now - started), 'adb': state, 'guest_idle_seconds': int(idle), 'last_guest_line': last_line, 'process_cpu': cpu, 'cpu_targets': progress.cpu_targets, 'observer_error': observer_error}) + '\n')
                next_report = now + 30
            if progress.idle_seconds(time.monotonic()) >= 90:
                raise TimeoutError(f'Guest made no progress for 90 seconds while waiting for {label}')
            time.sleep(3)
        raise TimeoutError(description)

    def ready(self):
        self.wait(lambda: self.shell('getprop', 'sys.boot_completed', check=False) == '1', 'Timed out waiting for Android boot', timeout=self.timeout)
        self.wait(lambda: 'package:' in self.shell('pm', 'path', 'com.android.systemui', check=False), 'Timed out waiting for the package manager')

    def capture(self, name, *args):
        text = self.shell(*args, check=False, timeout=60)
        (self.evidence / name).write_text(text)
        return text


def verify(sdk, work, manifest, accel='on', boot_timeout=1200, port=5580, cold_boot_only=False):
    validate_emulation(manifest['image'], accel)
    if platform.system() not in ('Darwin', 'Linux') or platform.machine().lower() not in ('arm64', 'aarch64'):
        raise ValueError('ARM64 boot verification requires a native ARM64 emulator host')
    if accel not in ('on', 'off') or not 60 <= boot_timeout <= 1800 or port % 2 or not 5554 <= port <= 5682:
        raise ValueError('Invalid emulator acceleration, timeout, or console port')
    sdk, work = sdk.resolve(), work.resolve()
    image = work / 'output' / ABI
    evidence = work / 'evidence'
    evidence.mkdir()
    avds = work / 'avds'
    avd = avds / 'AvdLiteArm64CI.avd'
    avd.mkdir(parents=True)
    (avds / 'AvdLiteArm64CI.ini').write_text(f'avd.ini.encoding=UTF-8\npath={avd}\ntarget=android-{manifest["image"]["api"]}\n')
    values = json.loads((Path(__file__).resolve().parent.parent / 'resources/avd-config.json').read_text())
    values.update({'AvdId': 'AvdLiteArm64CI', 'avd.ini.displayname': 'AvdLiteArm64CI', 'image.sysdir.1': str(image) + '/',
                   'hw.gpu.mode': 'swiftshader_indirect', 'hw.ramSize': '2048', 'disk.dataPartition.size': '8G',
                   'fastboot.forceColdBoot': 'yes', 'fastboot.forceFastBoot': 'no'})
    (avd / 'config.ini').write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
    emulator_home = work / 'emulator-home'
    emulator_home.mkdir()
    env = dict(os.environ, ANDROID_AVD_HOME=str(avds), ANDROID_EMULATOR_HOME=str(emulator_home),
               ANDROID_USER_HOME=str(emulator_home), QT_QPA_PLATFORM='offscreen')
    device = Device(sdk, evidence, env, timeout=boot_timeout, port=port)
    subprocess.run([device.adb, 'start-server'], check=True, env=env)
    report = {'ksu_enabled': bool(manifest['ksu']), 'checks': {}, 'requested_acceleration': accel,
              'host': {'os': platform.system(), 'arch': platform.machine()}, 'system_sha256': file_digest(image / 'system.img')}
    try:
        with (evidence / 'emulator.log').open('w') as log:
            command = emulator_command(sdk, accel, port)
            print(f'[start] {shlex.join(command)}', flush=True)
            device.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            device.ready()
            report['checks']['abi'] = device.shell('getprop', 'ro.product.cpu.abi')
            report['checks']['machine'] = device.shell('/system/bin/toybox', 'uname', '-m')
            report['checks']['page_size'] = int(device.shell('getconf', 'PAGESIZE'))
            if report['checks']['abi'] != ABI or report['checks']['machine'] != 'aarch64':
                raise AssertionError('Runtime is not ARM64')
            expected_page = 16384 if manifest['image']['page_size'] == '16k' else 4096
            if report['checks']['page_size'] != expected_page:
                raise AssertionError('Runtime page size differs from the official image selection')
            print(f'[runtime] ABI={report["checks"]["abi"]}; page_size={report["checks"]["page_size"]}', flush=True)
            for package in ('com.google.android.gms', 'com.google.android.gsf', 'com.google.android.apps.nexuslauncher', 'com.google.android.inputmethod.latin'):
                if 'package:' not in device.shell('pm', 'path', package):
                    raise AssertionError('Core application verification failed: ' + package)
            report['checks']['core_packages'] = True
            if device.shell('getenforce') != 'Enforcing':
                raise AssertionError('SELinux must be Enforcing')
            report['checks']['selinux_enforcing'] = True
            features = device.capture('features.txt', 'pm', 'list', 'features')
            for feature in ('uwb', 'nfc'):
                port = device.shell('getprop', 'vendor.qemu.vport.' + feature)
                report['checks'][feature + '_port'] = port
                if not port and re.search(r'feature:android\.hardware\.' + feature + r'(?:[.=\s]|$)', features):
                    raise AssertionError('Hardware feature declared with its emulator port absent: ' + feature)
            for name in ('config_lockDayNightMode', 'config_lockUiMode'):
                value = device.shell('cmd', 'overlay', 'lookup', 'android', 'android:bool/' + name)
                if value != 'false':
                    raise AssertionError(name + ' lock state verification failed: ' + value)
            for name in ('status_bar_padding_start', 'status_bar_padding_end'):
                value = device.shell('cmd', 'overlay', 'lookup', 'com.android.systemui', 'com.android.systemui:dimen/' + name)
                if not re.fullmatch(r'12(?:\.0+)?(?:dip|dp)', value):
                    raise AssertionError('Status bar padding verification failed: ' + value)
            report['checks']['overlays'] = True
            if manifest['ksu']:
                print('[check] KernelSU initialization and shell authorization', flush=True)
                device.wait(lambda: device.shell('getprop', 'sys.avdlite.ksu_ready', check=False) == '1', 'Timed out waiting for KernelSU initialization', timeout=120)
                uid = device.shell('su', '-c', 'id')
                if 'uid=0' not in uid:
                    raise AssertionError('KernelSU root verification failed')
                report['checks']['ksu_version'] = device.shell('su', '-c', '/data/adb/ksud debug version')
                if not device.shell('pm', 'path', 'me.weishu.kernelsu').startswith('package:/data/app/'):
                    raise AssertionError('KernelSU manager must be installed in the data partition')
            else:
                if 'package:' in device.shell('pm', 'path', 'me.weishu.kernelsu', check=False):
                    raise AssertionError('Standard image unexpectedly contains the KernelSU manager')
            if cold_boot_only:
                report['status'] = 'cold_boot_passed'
                print('[cold-boot-passed] Diagnostic only; release packaging remains blocked', flush=True)
                return
            device.shell('cmd', 'uimode', 'night', 'custom_schedule')
            for key, value in [('dark_theme_custom_start_time', '79200000000'), ('dark_theme_custom_end_time', '21600000000')]:
                device.shell('settings', 'put', 'secure', key, value)
            before = device.shell('cat', '/proc/sys/kernel/random/boot_id')
            print('[reboot] Checking a new boot identity and persistent settings', flush=True)
            device.call('reboot')
            device.wait(lambda: (value := device.shell('cat', '/proc/sys/kernel/random/boot_id', check=False)) not in ('', before), 'Timed out waiting for a new boot_id')
            device.ready()
            expected = {'ui_night_mode': '3', 'dark_theme_custom_start_time': '79200000000', 'dark_theme_custom_end_time': '21600000000'}
            actual = {key: device.shell('settings', 'get', 'secure', key) for key in expected}
            if actual != expected:
                raise AssertionError('Night schedule persistence verification failed: ' + repr(actual))
            report['checks']['night_schedule_after_reboot'] = actual
            if manifest['ksu']:
                device.wait(lambda: device.shell('getprop', 'sys.avdlite.ksu_ready', check=False) == '1', 'Timed out waiting for KernelSU initialization after reboot', timeout=120)
                if 'uid=0' not in device.shell('su', '-c', 'id'):
                    raise AssertionError('Root verification failed after reboot')
            displays = device.shell('dumpsys', 'SurfaceFlinger', '--display-id')
            match = re.search(r'Display (\d+)', displays)
            if not match:
                raise AssertionError('Display ID query returned no display for the screenshot')
            with (evidence / 'screen.png').open('wb') as screenshot:
                subprocess.run([device.adb, '-s', device.serial, 'exec-out', 'screencap', '-d', match[1], '-p'], stdout=screenshot, check=True, timeout=30, env=env)
            device.capture('uimode.txt', 'dumpsys', 'uimode')
            device.capture('overlays.txt', 'cmd', 'overlay', 'list')
            report['status'] = 'passed'
            print('[passed] ARM64 cold boot, reboot and runtime checks', flush=True)
    except BaseException as error:
        report.update(status='cancelled' if isinstance(error, KeyboardInterrupt) else 'failed', error=str(error))
        (work / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
        raise
    finally:
        if device.process is not None:
            for filename, arguments in [('logcat.txt', ('logcat', '-d', '-b', 'all')), ('properties.txt', ('getprop',))]:
                try:
                    device.capture(filename, *arguments)
                except (OSError, subprocess.TimeoutExpired) as error:
                    report.setdefault('cleanup_errors', []).append(str(error))
            try:
                device.call('emu', 'kill', check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                report.setdefault('cleanup_errors', []).append(str(error))
            try:
                device.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(device.process.pid, signal.SIGKILL)
                device.process.wait(timeout=10)
        (work / 'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('sdk', 'work', 'manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--accel', choices=('on', 'off'), default='on')
    parser.add_argument('--boot-timeout', type=int, default=1200)
    parser.add_argument('--port', type=int, default=5580)
    parser.add_argument('--cold-boot-only', action='store_true')
    args = parser.parse_args()
    verify(args.sdk, args.work, json.loads(args.manifest.read_text()), args.accel, args.boot_timeout, args.port, args.cold_boot_only)
