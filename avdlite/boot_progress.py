"""Track guest activity without treating host renderer noise as Android progress."""
import re


def fingerprint(line):
    line = re.sub(r'^\[\s*\d+\.\d+\]\[[^]]+\]\s*', '', line)
    line = re.sub(r'^\d\d-\d\d\s+\d\d:\d\d:\d\d\.\d+\s+\d+\s+\d+\s+\w\s+', '', line)
    line = re.sub(r'0x[0-9a-fA-F]+', '<address>', line)
    return re.sub(r'\d+', '<number>', line)


class BootProgress:
    def __init__(self, started):
        self.last_activity = started
        self.kernel_line = ''
        self.android_line = ''
        self.adb_state = 'unavailable'
        self.seen_kernel = set()
        self.seen_android = set()
        self.process_samples = {}
        self.cpu_targets = {}

    def kernel(self, tail, now):
        lines = [line for line in tail.splitlines() if re.search(r'\[\s*\d+\.\d+\]', line)]
        for line in lines:
            service = re.search(r"started service '(zygote|artd|installd)' has pid (\d+)", line)
            if service:
                name = 'zygote64' if service[1] == 'zygote' else service[1]
                self.cpu_targets[name] = int(service[2])
            key = fingerprint(line)
            if key not in self.seen_kernel:
                self.seen_kernel.add(key)
                self.kernel_line = line
                self.last_activity = now

    def android(self, state, logcat, now):
        lines = [line for line in logcat.splitlines() if line.strip() and not line.startswith('---------')]
        if state == 'device' and self.adb_state != 'device':
            self.last_activity = now
        for line in lines:
            header = re.match(r'^\d\d-\d\d\s+\S+\s+(\d+)\s+\d+\s+\w\s+([^:]+):', line)
            if header:
                tag = header[2].strip()
                role = ('system_server' if re.search(r'PackagePars|PackageManager|SystemServer|SystemServiceManager|ActivityManager|WindowManager|boot_progress', tag)
                        else 'zygote64' if 'Zygote' in tag else tag if tag in ('artd', 'dex2oat64', 'installd') else None)
                if role:
                    self.cpu_targets[role] = int(header[1])
            if not re.search(r'boot_progress|PackagePars|PackageManager|SystemServer|SystemServiceManager|ActivityManager|Zygote|zygote|apexd|WindowManager|KernelSU|dex2oat|artd', line):
                continue
            key = fingerprint(line)
            if key not in self.seen_android:
                self.seen_android.add(key)
                self.android_line = line
                self.last_activity = now
        self.adb_state = state

    def idle_seconds(self, now):
        return max(0, now - self.last_activity)

    def cpu(self, samples, now, minimum_ticks=25):
        for name, (pid, ticks) in samples.items():
            previous = self.process_samples.get(name)
            # Android's /proc stat counters use USER_HZ=100. Require measurable
            # CPU work in the same process, not just another restarted PID.
            if previous and previous[0] == pid and ticks - previous[1] >= minimum_ticks:
                self.last_activity = now
            self.process_samples[name] = (pid, ticks)


def process_cpu_samples(output, names_by_pid):
    samples = {}
    for line in output.splitlines():
        try:
            pid = int(line.split(' ', 1)[0])
            name = names_by_pid.get(pid)
            if name is None:
                continue
            fields = line.rsplit(')', 1)[1].split()
            samples[name] = (pid, int(fields[11]) + int(fields[12]))
        except (ValueError, IndexError):
            continue
    return samples


def emulator_cpu_sample(output, parent_pid):
    rows = []
    for line in output.splitlines():
        try:
            pid, parent, elapsed, command = line.split(None, 3)
            days, _, elapsed = elapsed.rpartition('-')
            seconds = 0
            for part in elapsed.split(':'):
                seconds = seconds * 60 + float(part)
            seconds += int(days or 0) * 86400
            rows.append((int(pid), int(parent), int(seconds * 100), command))
        except (ValueError, IndexError):
            continue
    children = {parent_pid}
    while True:
        found = {pid for pid, parent, _, _ in rows if parent in children}
        if found <= children:
            break
        children.update(found)
    return {'host_qemu': (pid, ticks) for pid, _, ticks, name in rows
            if pid in children and 'qemu-system-aarch64' in name}
