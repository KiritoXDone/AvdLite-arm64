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

    def kernel(self, tail, now):
        lines = [line for line in tail.splitlines() if re.search(r'\[\s*\d+\.\d+\]', line)]
        for line in lines:
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
