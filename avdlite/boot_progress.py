"""Track guest activity without treating host renderer noise as Android progress."""
import re


class BootProgress:
    def __init__(self, started):
        self.last_activity = started
        self.kernel_line = ''
        self.android_line = ''
        self.adb_state = 'unavailable'

    def kernel(self, tail, now):
        lines = [line for line in tail.splitlines() if re.search(r'\[\s*\d+\.\d+\]', line)]
        if lines and lines[-1] != self.kernel_line:
            self.kernel_line = lines[-1]
            self.last_activity = now

    def android(self, state, logcat, now):
        lines = [line for line in logcat.splitlines() if line.strip() and not line.startswith('---------')]
        if state == 'device' and self.adb_state != 'device':
            self.last_activity = now
        if lines and lines[-1] != self.android_line:
            self.android_line = lines[-1]
            self.last_activity = now
        self.adb_state = state

    def idle_seconds(self, now):
        return max(0, now - self.last_activity)
