import unittest
from avdlite.boot_progress import BootProgress


class BootProgressTests(unittest.TestCase):
    def test_repeated_host_messages_do_not_extend_a_stuck_boot(self):
        progress = BootProgress(0)
        progress.kernel('DEBUG | Reposting framebuffer.\nWARNING | no color buffer', 80)
        progress.android('unavailable', '', 80)
        self.assertEqual(progress.idle_seconds(90), 90)

    def test_new_kernel_activity_resets_idle_time_but_repeated_lines_do_not(self):
        progress = BootProgress(0)
        progress.kernel('[   25.123][ T1] init: starting zygote', 30)
        progress.kernel('[   25.123][ T1] init: starting zygote', 80)
        self.assertEqual(progress.idle_seconds(90), 60)
        progress.kernel('[   88.123][ T1] init: starting adbd', 90)
        self.assertEqual(progress.idle_seconds(100), 10)

    def test_framework_logs_keep_a_quiet_kernel_boot_alive(self):
        progress = BootProgress(0)
        progress.android('device', '--------- beginning of main\n11-09 08:01 SystemServer: starting', 40)
        progress.android('device', '11-09 08:02 PackageManager: scanned packages', 100)
        self.assertEqual(progress.idle_seconds(120), 20)

    def test_retry_timestamps_and_pids_are_not_new_progress(self):
        progress = BootProgress(0)
        progress.kernel("[  10.123][ T1] init: service zygote pid 123 failed", 10)
        progress.kernel("[  80.234][ T1] init: service zygote pid 456 failed", 80)
        progress.android('unavailable', '11-09 08:10:10.123 123 456 I logd: new reader', 80)
        self.assertEqual(progress.idle_seconds(100), 90)
