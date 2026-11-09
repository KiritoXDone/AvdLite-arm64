import unittest
from avdlite.boot_progress import BootProgress, process_cpu_samples, emulator_cpu_sample


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

    def test_cpu_bound_package_work_is_not_mistaken_for_a_stall(self):
        progress = BootProgress(0)
        progress.cpu({'system_server': (123, 100)}, 30)
        progress.cpu({'system_server': (123, 300)}, 60)
        self.assertEqual(progress.idle_seconds(90), 30)
        progress.cpu({'system_server': (456, 500)}, 90)
        self.assertEqual(progress.idle_seconds(100), 40)

    def test_proc_stat_parser_uses_user_and_system_cpu_fields(self):
        fields = ['S'] + ['0'] * 10 + ['125', '75'] + ['0'] * 8
        samples = process_cpu_samples('123 (system server) ' + ' '.join(fields) + '\ndenied', {123: 'system_server'})
        self.assertEqual(samples, {'system_server': (123, 200)})

    def test_host_cpu_only_observes_this_emulators_process_tree(self):
        listing = '10 1 0:00.01 emulator\n11 10 2:30.50 qemu-system-aarch64-headless\n12 1 9:00.00 qemu-system-aarch64-headless'
        self.assertEqual(emulator_cpu_sample(listing, 10), {'host_qemu': (11, 15050)})

    def test_cpu_targets_are_learned_without_repeated_process_scans(self):
        progress = BootProgress(0)
        progress.kernel("[  10.123][ T1] init: ... started service 'zygote' has pid 100", 10)
        progress.android('device', '11-09 08:12:34.123 200 201 I PackageManager: collecting certs', 20)
        self.assertEqual(progress.cpu_targets, {'zygote64': 100, 'system_server': 200})
