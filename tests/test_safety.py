import unittest
import struct
import threading
import ctypes
import json
import hashlib
import os
import tempfile
import sys
import time
from pathlib import Path
from unittest.mock import patch

from AoE4_RogueTalentTool import (
    APP_VERSION,
    BoundedScanResults,
    BackupResult,
    CandidateSelectionError,
    MEM_PRIVATE,
    OperationCancelled,
    PAGE_GUARD,
    PAGE_READWRITE,
    App,
    RestoreError,
    ScanResultLimitError,
    quick_find_candidates,
    ScanSession,
    Win32ProcessMemory,
    WriteTransactionResult,
    backup_save_files,
    find_latest_backup_folder,
    list_process_ids,
    read_process_identity,
    SupportSnapshot,
    format_support_report,
    restore_save_files,
    safe_write_values,
    scan_value,
    select_safe_candidates,
    validate_point_values,
    write_value,
    run_gui_smoke_test,
    run_self_test,
)


class PointValidationTests(unittest.TestCase):
    def test_user_can_use_points_within_safe_range(self):
        self.assertEqual(validate_point_values("694", "33875"), (694, 33875))

    def test_user_gets_clear_error_when_current_points_are_blank(self):
        with self.assertRaisesRegex(ValueError, "当前点数必须是整数"):
            validate_point_values("", "33875")

    def test_user_cannot_write_above_the_supported_limit(self):
        with self.assertRaisesRegex(ValueError, "目标点数必须在 1 到 33875 之间"):
            validate_point_values("694", "33876")

    def test_user_cannot_scan_an_out_of_range_current_value(self):
        with self.assertRaisesRegex(ValueError, "当前点数必须在 0 到 33875 之间"):
            validate_point_values("-1", "33875")


class CandidateSelectionTests(unittest.TestCase):
    def test_user_can_write_a_small_set_of_structurally_verified_addresses(self):
        self.assertEqual(
            select_safe_candidates([0x1000, 0x2000], [0x1000, 0x2000, 0x3000]),
            [0x1000, 0x2000],
        )

    def test_raw_match_count_can_be_reported_without_retaining_every_address(self):
        with self.assertRaisesRegex(CandidateSelectionError, "普通匹配 500000 个"):
            select_safe_candidates([], 500000)

    def test_user_is_protected_from_unverified_raw_matches(self):
        with self.assertRaisesRegex(CandidateSelectionError, "没有通过结构验证"):
            select_safe_candidates([], [0x1000, 0x2000])

    def test_user_is_protected_from_too_many_verified_addresses(self):
        addresses = [0x1000, 0x2000, 0x3000, 0x4000, 0x5000]
        with self.assertRaisesRegex(CandidateSelectionError, "候选地址过多"):
            select_safe_candidates(addresses, addresses)

    def test_duplicate_scan_hits_are_deduplicated_before_the_limit_check(self):
        self.assertEqual(
            select_safe_candidates(
                [0x1000, 0x1000, 0x2000],
                [0x1000, 0x1000, 0x2000],
            ),
            [0x1000, 0x2000],
        )


class ScanSessionTests(unittest.TestCase):
    def test_user_cannot_write_after_only_one_scan(self):
        session = ScanSession()
        session.record_initial([(0x1000, "int64")], current_value=694)

        self.assertEqual(
            (session.can_write, session.expected_value),
            (False, 694),
        )

    def test_user_can_write_after_a_second_scan_confirms_the_addresses(self):
        session = ScanSession()
        session.record_initial([(0x1000, "int64"), (0x2000, "int64")], 694)
        session.record_refinement(
            common=[(0x1000, "int64"), (0x2000, "int64")],
            latest=[(0x1000, "int64"), (0x2000, "int64")],
            current_value=650,
        )

        self.assertEqual(
            (session.can_write, session.expected_value, session.results),
            (True, 650, [(0x1000, "int64"), (0x2000, "int64")]),
        )

    def test_user_must_refine_again_when_addresses_move(self):
        latest = [(0x3000, "int64"), (0x4000, "int64")]
        session = ScanSession()
        session.record_initial([(0x1000, "int64")], 694)

        confirmed = session.record_refinement([], latest, current_value=650)

        self.assertEqual(
            (confirmed, session.can_write, session.results),
            (False, False, latest),
        )

    def test_repeating_the_same_value_does_not_unlock_writing(self):
        original = [(0x1000, "int64")]
        session = ScanSession()
        session.record_initial(original, 694, pid=111)

        confirmed = session.record_refinement(
            common=original,
            latest=original,
            current_value=694,
            pid=111,
        )

        self.assertEqual(
            (confirmed, session.can_write, session.expected_value, session.results),
            (False, False, 694, original),
        )

    def test_scan_session_is_bound_to_the_process_it_scanned(self):
        session = ScanSession()
        session.record_initial([(0x1000, "int64")], 694, pid=111)

        self.assertTrue(session.belongs_to(111))
        self.assertFalse(session.belongs_to(222))


class SaveBackupTests(unittest.TestCase):
    def test_user_gets_timestamped_copies_of_local_and_cloud_saves(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            users = documents / "My Games" / "Age of Empires IV" / "Users"
            local_save = users / "123456" / "datastore" / "rogue_mode.rlc"
            cloud_save = users / "123456" / "cloud" / "rogue_mode.rlc"
            local_save.parent.mkdir(parents=True)
            cloud_save.parent.mkdir(parents=True)
            local_save.write_bytes(b"local-save")
            cloud_save.write_bytes(b"cloud-save")

            result = backup_save_files(
                documents,
                root / "Backups",
                timestamp="20260901-210000",
            )

            self.assertEqual(
                (
                    result.count,
                    (result.folder / "123456/datastore/rogue_mode.rlc").read_bytes(),
                    (result.folder / "123456/cloud/rogue_mode.rlc").read_bytes(),
                ),
                (2, b"local-save", b"cloud-save"),
            )

    def test_user_gets_a_manifest_for_backup_integrity_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            save = (documents / "My Games" / "Age of Empires IV" / "Users" /
                    "123456" / "datastore" / "rogue_mode.rlc")
            save.parent.mkdir(parents=True)
            save.write_bytes(b"local-save")

            result = backup_save_files(
                documents,
                root / "Backups",
                timestamp="20260901-210000",
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(
                manifest["files"][0]["sha256"],
                "8cb942b75cc6685e1369ae97a2644267f37f06367416134cffd00bbf0ad3ee12",
            )

    def test_two_backups_in_the_same_second_never_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            save = (documents / "My Games" / "Age of Empires IV" / "Users" /
                    "123456" / "datastore" / "rogue_mode.rlc")
            save.parent.mkdir(parents=True)
            save.write_bytes(b"first")

            first = backup_save_files(
                documents, root / "Backups", timestamp="20260901-210000")
            save.write_bytes(b"second")
            second = backup_save_files(
                documents, root / "Backups", timestamp="20260901-210000")

            self.assertNotEqual(first.folder, second.folder)
            self.assertEqual(first.files[0].read_bytes(), b"first")
            self.assertEqual(second.files[0].read_bytes(), b"second")

    def test_restore_returns_save_to_backup_state_and_preserves_current_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            save = (documents / "My Games" / "Age of Empires IV" / "Users" /
                    "123456" / "datastore" / "rogue_mode.rlc")
            save.parent.mkdir(parents=True)
            save.write_bytes(b"known-good")
            backup_root = root / "Backups"
            backup = backup_save_files(
                documents, backup_root, timestamp="20260922-100000")
            save.write_bytes(b"current-state")

            result = restore_save_files(
                backup.folder, documents, backup_root, game_running=False)

            self.assertEqual(save.read_bytes(), b"known-good")
            self.assertEqual(result.count, 1)
            self.assertEqual(
                (result.pre_restore_folder /
                 "123456/datastore/rogue_mode.rlc").read_bytes(),
                b"current-state",
            )

    def test_restore_rejects_files_other_than_the_expected_save_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            users = documents / "My Games" / "Age of Empires IV" / "Users"
            save = users / "123456" / "datastore" / "rogue_mode.rlc"
            other = users / "123456" / "datastore" / "other.rlc"
            save.parent.mkdir(parents=True)
            save.write_bytes(b"normal-save")
            other.write_bytes(b"keep-me")
            backup_root = root / "Backups"
            backup = backup_save_files(
                documents, backup_root, timestamp="20260922-100000")
            copied_other = backup.folder / "123456" / "datastore" / "other.rlc"
            copied_other.write_bytes(b"overwrite-me")
            backup.manifest_path.write_text(json.dumps({"files": [{
                "path": "123456/datastore/other.rlc",
                "sha256": hashlib.sha256(b"overwrite-me").hexdigest(),
            }]}), encoding="utf-8")

            with self.assertRaises(RestoreError):
                restore_save_files(
                    backup.folder, documents, backup_root, game_running=False)

            self.assertEqual(other.read_bytes(), b"keep-me")

    def test_restore_rejects_a_tampered_backup_without_touching_current_save(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            save = (documents / "My Games" / "Age of Empires IV" / "Users" /
                    "123456" / "datastore" / "rogue_mode.rlc")
            save.parent.mkdir(parents=True)
            save.write_bytes(b"known-good")
            backup_root = root / "Backups"
            backup = backup_save_files(
                documents, backup_root, timestamp="20260922-100000")
            backup.files[0].write_bytes(b"changed-after-backup")
            save.write_bytes(b"current-state")

            with self.assertRaisesRegex(RestoreError, "校验失败"):
                restore_save_files(
                    backup.folder, documents, backup_root, game_running=False)

            self.assertEqual(save.read_bytes(), b"current-state")

    def test_restore_rolls_back_all_files_if_a_later_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            users = documents / "My Games" / "Age of Empires IV" / "Users"
            local_save = users / "123456" / "datastore" / "rogue_mode.rlc"
            cloud_save = users / "123456" / "cloud" / "rogue_mode.rlc"
            local_save.parent.mkdir(parents=True)
            cloud_save.parent.mkdir(parents=True)
            local_save.write_bytes(b"old-local")
            cloud_save.write_bytes(b"old-cloud")
            backup_root = root / "Backups"
            backup = backup_save_files(
                documents, backup_root, timestamp="20260922-100000")
            local_save.write_bytes(b"new-local")
            cloud_save.write_bytes(b"new-cloud")
            real_replace = os.replace

            def replace_except_cloud(source, destination, *args, **kwargs):
                if Path(destination) == cloud_save:
                    raise OSError("simulated disk error")
                return real_replace(source, destination, *args, **kwargs)

            with patch("AoE4_RogueTalentTool.os.replace",
                       side_effect=replace_except_cloud):
                with self.assertRaises(RestoreError):
                    restore_save_files(
                        backup.folder, documents, backup_root,
                        game_running=False)

            self.assertEqual(local_save.read_bytes(), b"new-local")
            self.assertEqual(cloud_save.read_bytes(), b"new-cloud")

    def test_latest_backup_excludes_pre_restore_safety_snapshots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            save = (documents / "My Games" / "Age of Empires IV" / "Users" /
                    "123456" / "datastore" / "rogue_mode.rlc")
            save.parent.mkdir(parents=True)
            save.write_bytes(b"save")
            backup_root = root / "Backups"
            older = backup_save_files(
                documents, backup_root, timestamp="20260922-100000")
            newer = backup_save_files(
                documents, backup_root, timestamp="20260922-110000")
            backup_save_files(
                documents, backup_root / "pre-restore",
                timestamp="20260922-120000")

            self.assertNotEqual(older.folder, newer.folder)
            self.assertEqual(find_latest_backup_folder(backup_root), newer.folder)

    def test_restore_never_runs_while_the_game_is_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            save = (documents / "My Games" / "Age of Empires IV" / "Users" /
                    "123456" / "datastore" / "rogue_mode.rlc")
            save.parent.mkdir(parents=True)
            save.write_bytes(b"old-save")
            backup_root = root / "Backups"
            backup = backup_save_files(
                documents, backup_root, timestamp="20260922-100000")
            save.write_bytes(b"current-save")

            with self.assertRaisesRegex(RestoreError, "退出游戏"):
                restore_save_files(
                    backup.folder, documents, backup_root, game_running=True)

            self.assertEqual(save.read_bytes(), b"current-save")

    def test_restore_rejects_path_traversal_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            documents = root / "Documents"
            save = (documents / "My Games" / "Age of Empires IV" / "Users" /
                    "123456" / "datastore" / "rogue_mode.rlc")
            save.parent.mkdir(parents=True)
            save.write_bytes(b"current-save")
            backup_root = root / "Backups"
            backup = backup_save_files(
                documents, backup_root, timestamp="20260922-100000")
            backup.manifest_path.write_text(json.dumps({"files": [{
                "path": "../../123456/datastore/rogue_mode.rlc",
                "sha256": hashlib.sha256(b"current-save").hexdigest(),
            }]}), encoding="utf-8")

            with self.assertRaises(RestoreError):
                restore_save_files(
                    backup.folder, documents, backup_root, game_running=False)

            self.assertEqual(save.read_bytes(), b"current-save")


class InMemoryProcessMemory:
    def __init__(self, values):
        self.values = dict(values)

    def region_info(self, _address):
        return {"type": MEM_PRIVATE, "prot": PAGE_READWRITE}

    def read(self, address, size):
        return self.values[address][:size]

    def write(self, address, data):
        self.values[address] = bytes(data)
        return True


class ReadOnlyProcessMemory(InMemoryProcessMemory):
    def region_info(self, _address):
        return {"type": MEM_PRIVATE, "prot": 0x02}


class GuardedProcessMemory(InMemoryProcessMemory):
    def region_info(self, _address):
        return {"type": MEM_PRIVATE, "prot": PAGE_READWRITE | PAGE_GUARD}


class FailingWriteProcessMemory(InMemoryProcessMemory):
    def __init__(self, values, fail_address):
        super().__init__(values)
        self.fail_address = fail_address
        self.failed_once = False

    def write(self, address, data):
        if address == self.fail_address and not self.failed_once:
            self.failed_once = True
            return False
        return super().write(address, data)


class ChangesBetweenPreflightAndCommitMemory(InMemoryProcessMemory):
    def __init__(self, values, stale_value):
        super().__init__(values)
        self.stale_value = stale_value
        self.reads = 0

    def read(self, address, size):
        self.reads += 1
        if self.reads == 2:
            self.values[address] = self.stale_value
        return super().read(address, size)


class SafeWriteTests(unittest.TestCase):
    def test_user_gets_an_all_or_nothing_verified_write(self):
        old = struct.pack("<q", 694)
        new = struct.pack("<q", 33875)
        memory = InMemoryProcessMemory({0x1000: old, 0x2000: old})

        result = safe_write_values(
            memory,
            [(0x1000, "int64"), (0x2000, "int64")],
            expected_value=694,
            new_value=33875,
        )

        self.assertEqual(
            (result.success, memory.values),
            (True, {0x1000: new, 0x2000: new}),
        )

    def test_user_is_protected_when_any_candidate_has_gone_stale(self):
        expected = struct.pack("<q", 694)
        stale = struct.pack("<q", 693)
        memory = InMemoryProcessMemory({0x1000: expected, 0x2000: stale})

        result = safe_write_values(
            memory,
            [(0x1000, "int64"), (0x2000, "int64")],
            expected_value=694,
            new_value=33875,
        )

        self.assertEqual(
            (result.success, result.written, memory.values),
            (False, 0, {0x1000: expected, 0x2000: stale}),
        )

    def test_user_is_protected_from_writing_a_read_only_region(self):
        old = struct.pack("<q", 694)
        memory = ReadOnlyProcessMemory({0x1000: old})

        result = safe_write_values(
            memory,
            [(0x1000, "int64")],
            expected_value=694,
            new_value=33875,
        )

        self.assertEqual(
            (result.success, result.written, memory.values[0x1000]),
            (False, 0, old),
        )

    def test_user_gets_automatic_rollback_when_a_write_fails(self):
        old = struct.pack("<q", 694)
        memory = FailingWriteProcessMemory(
            {0x1000: old, 0x2000: old},
            fail_address=0x2000,
        )

        result = safe_write_values(
            memory,
            [(0x1000, "int64"), (0x2000, "int64")],
            expected_value=694,
            new_value=33875,
        )

        self.assertEqual(
            (result.success, result.rolled_back, memory.values),
            (False, True, {0x1000: old, 0x2000: old}),
        )

    def test_user_can_cancel_before_the_write_transaction_starts(self):
        old = struct.pack("<q", 694)
        memory = InMemoryProcessMemory({0x1000: old})
        cancel_event = threading.Event()
        cancel_event.set()

        result = safe_write_values(
            memory,
            [(0x1000, "int64")],
            expected_value=694,
            new_value=33875,
            cancel_event=cancel_event,
        )

        self.assertEqual(
            (result.cancelled, result.written, memory.values[0x1000]),
            (True, 0, old),
        )

    def test_public_transaction_rejects_an_empty_address_set(self):
        result = safe_write_values(
            InMemoryProcessMemory({}),
            [],
            expected_value=694,
            new_value=33875,
        )

        self.assertFalse(result.success)
        self.assertRegex(result.error, "至少 1 个")

    def test_public_transaction_enforces_the_four_address_limit(self):
        old = struct.pack("<q", 694)
        addresses = [(0x1000 + i * 0x100, "int64") for i in range(5)]
        memory = InMemoryProcessMemory({addr: old for addr, _ in addresses})

        result = safe_write_values(
            memory,
            addresses,
            expected_value=694,
            new_value=33875,
        )

        self.assertFalse(result.success)
        self.assertRegex(result.error, "安全上限 4")
        self.assertTrue(all(value == old for value in memory.values.values()))

    def test_public_transaction_rejects_duplicate_addresses(self):
        old = struct.pack("<q", 694)
        memory = InMemoryProcessMemory({0x1000: old})

        result = safe_write_values(
            memory,
            [(0x1000, "int64"), (0x1000, "int64")],
            expected_value=694,
            new_value=33875,
        )

        self.assertFalse(result.success)
        self.assertRegex(result.error, "重复")

    def test_public_transaction_rejects_unsupported_value_types(self):
        old = struct.pack("<f", 694.0)
        memory = InMemoryProcessMemory({0x1000: old})

        result = safe_write_values(
            memory,
            [(0x1000, "float")],
            expected_value=694,
            new_value=33875,
        )

        self.assertFalse(result.success)
        self.assertRegex(result.error, "int32 或 int64")

    def test_guarded_memory_is_never_treated_as_writable(self):
        old = struct.pack("<q", 694)
        memory = GuardedProcessMemory({0x1000: old})

        result = safe_write_values(
            memory,
            [(0x1000, "int64")],
            expected_value=694,
            new_value=33875,
        )

        self.assertEqual((result.success, memory.values[0x1000]), (False, old))

    def test_value_is_checked_again_immediately_before_commit(self):
        old = struct.pack("<q", 694)
        stale = struct.pack("<q", 693)
        memory = ChangesBetweenPreflightAndCommitMemory({0x1000: old}, stale)

        result = safe_write_values(
            memory,
            [(0x1000, "int64")],
            expected_value=694,
            new_value=33875,
        )

        self.assertEqual((result.success, memory.values[0x1000]), (False, stale))
        self.assertRegex(result.error, "写入前再次变化")


class Win32ProcessMemoryTests(unittest.TestCase):
    def test_transaction_works_through_the_real_windows_memory_boundary(self):
        value = ctypes.c_longlong(694)
        address = ctypes.addressof(value)

        with Win32ProcessMemory(os.getpid(), writable=True) as memory:
            result = safe_write_values(
                memory,
                [(address, "int64")],
                expected_value=694,
                new_value=33875,
            )

        self.assertEqual((result.success, value.value), (True, 33875))


class ScanTypeTests(unittest.TestCase):
    def test_default_scan_ignores_float_lookalikes(self):
        float_lookalike = ctypes.c_double(694.0)
        address = ctypes.addressof(float_lookalike)

        results = scan_value(os.getpid(), 694)

        self.assertNotIn((address, "double"), results)

    def test_scan_results_are_deduplicated_and_bounded(self):
        results = BoundedScanResults(limit=2)
        results.add((0x1000, "int64"))
        results.add((0x1000, "int64"))
        results.add((0x2000, "int64"))

        with self.assertRaises(ScanResultLimitError):
            results.add((0x3000, "int64"))

        self.assertEqual(results.items, [(0x1000, "int64"), (0x2000, "int64")])

    def test_quick_scan_streams_raw_hits_but_keeps_verified_candidates(self):
        class TalentRecord(ctypes.Structure):
            _fields_ = [("value", ctypes.c_uint64), ("tag", ctypes.c_uint64)]

        record = TalentRecord(32123, 0x8800 << 48)
        address = ctypes.addressof(record)

        candidates, raw_count = quick_find_candidates(os.getpid(), 32123)

        self.assertIn(address, candidates)
        self.assertIsInstance(raw_count, int)
        self.assertGreaterEqual(raw_count, len(candidates))


class CancellationTests(unittest.TestCase):
    def test_cancelled_scan_never_returns_partial_results(self):
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(OperationCancelled):
            scan_value(
                os.getpid(),
                694,
                kinds=("int64",),
                cancel_event=cancel_event,
            )

    def test_cancelled_scan_drops_results_even_after_scanning_has_started(self):
        class CancelAfterStart:
            def __init__(self):
                self.checks = 0

            def is_set(self):
                self.checks += 1
                return self.checks >= 2

        with self.assertRaises(OperationCancelled):
            scan_value(
                os.getpid(),
                694,
                kinds=("int64",),
                cancel_event=CancelAfterStart(),
            )

    def test_cancelled_quick_scan_never_reaches_the_write_stage(self):
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(OperationCancelled):
            quick_find_candidates(
                os.getpid(),
                694,
                cancel_event=cancel_event,
            )


class PublicWriterTests(unittest.TestCase):

    def test_public_writer_uses_expected_value_verification(self):
        value = ctypes.c_longlong(694)
        address = ctypes.addressof(value)

        with patch(
                "AoE4_RogueTalentTool.find_process_id",
                return_value=os.getpid()):
            result = write_value(
                os.getpid(),
                [(address, "int64")],
                new_value=33875,
                expected_value=694,
            )

        self.assertEqual((result.success, value.value), (True, 33875))

    def test_public_writer_rechecks_the_unique_game_pid_at_commit_time(self):
        value = ctypes.c_longlong(694)
        address = ctypes.addressof(value)

        with patch(
                "AoE4_RogueTalentTool.find_process_id",
                return_value=os.getpid() + 1):
            result = write_value(
                os.getpid(),
                [(address, "int64")],
                new_value=33875,
                expected_value=694,
            )

        self.assertFalse(result.success)
        self.assertEqual(value.value, 694)
        self.assertRegex(result.error, "写入前已经变化")


class ProcessIdentityTests(unittest.TestCase):
    def test_running_process_can_be_identified_with_its_file_version(self):
        self.assertIn(os.getpid(), list_process_ids(Path(sys.executable).name))

        identity = read_process_identity(os.getpid())

        self.assertEqual(identity.pid, os.getpid())
        self.assertEqual(Path(identity.path).resolve(), Path(sys.executable).resolve())
        self.assertTrue(identity.version)


class SupportReportTests(unittest.TestCase):
    def test_buyer_can_share_a_useful_report_without_private_paths_or_values(self):
        snapshot = SupportSnapshot(
            app_version="1.2.0",
            windows_version="10.0.26200",
            game_process_count=1,
            game_version="11.0.1234.0",
            save_count=2,
            backup_available=True,
            last_result="write_failed",
        )

        report = format_support_report(snapshot)

        self.assertIn("工具版本：1.2.0", report)
        self.assertIn("游戏文件版本：11.0.1234.0", report)
        self.assertIn("检测到存档：2 个", report)
        self.assertNotIn("C:\\Users", report)
        self.assertNotIn("0x", report)


class PackagedEntryPointTests(unittest.TestCase):
    def test_self_test_exercises_the_write_boundary_without_opening_the_gui(self):
        ok, details = run_self_test()

        self.assertTrue(ok, details)
        self.assertRegex(details, APP_VERSION)

    def test_gui_can_be_created_and_closed_for_release_smoke_testing(self):
        ok, details = run_gui_smoke_test()

        self.assertTrue(ok, details)
        self.assertRegex(details, APP_VERSION)


class RestoreInterfaceTests(unittest.TestCase):
    def test_restore_button_refuses_while_game_is_running(self):
        app = App()
        app.withdraw()
        try:
            with patch("AoE4_RogueTalentTool.list_process_ids", return_value=[1234]), \
                    patch("AoE4_RogueTalentTool.messagebox.showwarning") as warning, \
                    patch("AoE4_RogueTalentTool.restore_save_files") as restore:
                app.restore_latest_backup()
            self.assertIn("退出", warning.call_args.args[1])
            restore.assert_not_called()
        finally:
            app.destroy()

    def test_window_stays_open_until_memory_write_finishes(self):
        app = App()
        app.withdraw()
        try:
            app.is_busy = True
            app.active_operation = "write"
            with patch("AoE4_RogueTalentTool.messagebox.showwarning") as warning:
                app.on_close()
            self.assertEqual(app.winfo_exists(), 1)
            warning.assert_called_once()
        finally:
            app.destroy()

    def test_cancel_before_quick_write_is_reported_as_cancel_not_failure(self):
        app = App()
        app.withdraw()
        try:
            app.queue.put(("quick_done", (
                WriteTransactionResult(
                    success=False, cancelled=True, error="操作已取消，未写入任何地址"),
                1,
            )))
            with patch("AoE4_RogueTalentTool.messagebox.showinfo") as info, \
                    patch("AoE4_RogueTalentTool.messagebox.showerror") as error:
                app.poll_queue()
            self.assertEqual(app.last_result, "scan_cancelled")
            self.assertEqual(app.result_title_var.get(), "已取消")
            info.assert_not_called()
            error.assert_not_called()
        finally:
            app.destroy()

class OneClickFlowTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.app.cur_var.set("694")
        self.app.target_var.set("33875")

    def tearDown(self):
        self.app.destroy()

    def finish_operation(self):
        deadline = time.monotonic() + 5
        while self.app.is_busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.assertFalse(self.app.is_busy, "one-click operation did not finish")

    def test_one_click_backs_up_and_writes_without_redundant_prompts(self):
        probe = ctypes.c_longlong(694)
        self.app.pid = os.getpid()
        candidates = [(ctypes.addressof(probe), "int64")]
        self.app.scan_session.record_initial(candidates, 693, pid=os.getpid())
        self.app.scan_session.record_refinement(candidates, candidates, 694, pid=os.getpid())
        backup = BackupResult(Path("test-backup"), (Path("test-save"),))
        with patch("AoE4_RogueTalentTool.list_process_ids", return_value=[os.getpid()]), \
                patch("AoE4_RogueTalentTool.create_save_backup", return_value=backup) as save, \
                patch("AoE4_RogueTalentTool.quick_find_candidates",
                      return_value=([ctypes.addressof(probe)], 1)), \
                patch("AoE4_RogueTalentTool.messagebox.askyesno",
                      side_effect=AssertionError("unexpected confirmation")), \
                patch("AoE4_RogueTalentTool.messagebox.showinfo",
                      side_effect=AssertionError("unexpected success dialog")):
            self.app.quick_btn.invoke()
            self.app.quick_write()  # Repeated clicks must not start another write.
            self.finish_operation()
        self.assertEqual(probe.value, 33875)
        self.assertEqual(self.app.last_result, "write_completed")
        self.assertEqual(self.app.cur_var.get(), "33875")
        self.assertIn("33875", self.app.result_title_var.get())
        self.assertFalse(self.app.scan_session.can_write)
        save.assert_called_once()

    def test_game_version_is_only_read_when_process_changes(self):
        with patch("AoE4_RogueTalentTool.list_process_ids", side_effect=[[11], [11], [22]]), \
                patch("AoE4_RogueTalentTool.read_process_identity", return_value=None) as identity:
            self.app.detect_process()
            self.app.detect_process()
            self.app.detect_process()
        self.assertEqual(identity.call_count, 2)
        self.assertEqual(self.app.pid, 22)

    def test_missing_points_are_reported_inline_before_any_operation(self):
        self.app.cur_var.set("")
        with patch("AoE4_RogueTalentTool.create_save_backup") as backup, \
                patch("AoE4_RogueTalentTool.write_value") as write:
            self.app.quick_write()
        self.assertIn("当前点数", self.app.info_var.get())
        backup.assert_not_called()
        write.assert_not_called()

    def test_backup_failure_prevents_one_click_write(self):
        with patch("AoE4_RogueTalentTool.list_process_ids", return_value=[os.getpid()]), \
                patch("AoE4_RogueTalentTool.create_save_backup", side_effect=OSError("disk full")), \
                patch("AoE4_RogueTalentTool.write_value") as write:
            self.app.quick_write()
        self.assertIn("备份失败", self.app.result_title_var.get())
        self.assertFalse(self.app.is_preparing)
        write.assert_not_called()

    def test_unverified_scan_results_never_reach_one_click_writer(self):
        backup = BackupResult(Path("test-backup"), (Path("test-save"),))
        with patch("AoE4_RogueTalentTool.list_process_ids", return_value=[os.getpid()]), \
                patch("AoE4_RogueTalentTool.create_save_backup", return_value=backup), \
                patch("AoE4_RogueTalentTool.quick_find_candidates", return_value=([], 120)), \
                patch("AoE4_RogueTalentTool.write_value") as write:
            self.app.quick_write()
            self.finish_operation()
        self.assertEqual(self.app.last_result, "scan_failed")
        self.assertIn("没有通过结构验证", self.app.info_var.get())
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
