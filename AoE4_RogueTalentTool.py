# -*- coding: utf-8 -*-
r"""
帝国时代4 炼狱模式 天赋点修改器（内存扫描版）
=================================================
原理：右上角“天赋”数值存在 RelicCardinal.exe 进程内存里。
先搜索当前显示值，再让数值变化一次（购买/退款天赋）后二次过滤，
最后把目标数值写入筛选出来的地址。

重要提醒：
- 仅建议用于单人/炼狱(PvE)模式，联机或排位使用后果自负。
- 目标点数建议不超过 33875（正常获取上限）。
- 修改前请先备份：
  Documents\My Games\Age of Empires IV\Users\<SteamID>\datastore\rogue_mode.rlc
  和 cloud\rogue_mode.rlc
- 写入后把右上角“已启用天赋”开关关一下再打开（不花点数，仅刷新保存），
  然后正常退出游戏。
"""

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import winreg

# ---------------------------------------------------------------- Win32
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_GUARD = 0x100
WRITABLE_PROTECTS = (PAGE_READWRITE, PAGE_WRITECOPY)
TH32CS_SNAPPROCESS = 0x2
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260
MAX_SAFE_POINTS = 33875
MAX_SAFE_CANDIDATES = 4
MAX_SCAN_RESULTS = 250_000
APP_VERSION = "1.4.0"
APP_CREDIT = "Laplace的ai站制作"
TARGET_PROCESS_NAME = "RelicCardinal.exe"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
version_dll = ctypes.WinDLL("version", use_last_error=True)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wt.ULONG)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wt.DWORD),
        ("dwStrucVersion", wt.DWORD),
        ("dwFileVersionMS", wt.DWORD),
        ("dwFileVersionLS", wt.DWORD),
        ("dwProductVersionMS", wt.DWORD),
        ("dwProductVersionLS", wt.DWORD),
        ("dwFileFlagsMask", wt.DWORD),
        ("dwFileFlags", wt.DWORD),
        ("dwFileOS", wt.DWORD),
        ("dwFileType", wt.DWORD),
        ("dwFileSubtype", wt.DWORD),
        ("dwFileDateMS", wt.DWORD),
        ("dwFileDateLS", wt.DWORD),
    ]


kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
                                    ctypes.c_size_t]
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.restype = wt.BOOL
kernel32.WriteProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32FirstW.argtypes = [ctypes.c_void_p,
                                     ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wt.BOOL
kernel32.Process32NextW.argtypes = [ctypes.c_void_p,
                                    ctypes.POINTER(PROCESSENTRY32W)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
version_dll.GetFileVersionInfoSizeW.restype = wt.DWORD
version_dll.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR,
                                               ctypes.POINTER(wt.DWORD)]
version_dll.GetFileVersionInfoW.restype = wt.BOOL
version_dll.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                           ctypes.c_void_p]
version_dll.VerQueryValueW.restype = wt.BOOL
version_dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR,
                                      ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.POINTER(wt.UINT)]


def list_process_ids(name="RelicCardinal.exe"):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pids = []
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == name.lower():
                pids.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def find_process_id(name="RelicCardinal.exe"):
    pids = list_process_ids(name)
    return pids[0] if len(pids) == 1 else None


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    path: str
    version: str


def get_file_version(path):
    unused = wt.DWORD(0)
    size = version_dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    if not size:
        return "未知"
    data = ctypes.create_string_buffer(size)
    if not version_dll.GetFileVersionInfoW(str(path), 0, size, data):
        return "未知"
    info_pointer = ctypes.c_void_p()
    info_size = wt.UINT(0)
    if not version_dll.VerQueryValueW(
            data, "\\", ctypes.byref(info_pointer), ctypes.byref(info_size)):
        return "未知"
    if info_size.value < ctypes.sizeof(VS_FIXEDFILEINFO):
        return "未知"
    info = ctypes.cast(
        info_pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    if info.dwSignature != 0xFEEF04BD:
        return "未知"
    return ".".join(str(part) for part in (
        info.dwFileVersionMS >> 16,
        info.dwFileVersionMS & 0xFFFF,
        info.dwFileVersionLS >> 16,
        info.dwFileVersionLS & 0xFFFF,
    ))


def read_process_identity(pid):
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        path_buffer = ctypes.create_unicode_buffer(32768)
        length = wt.DWORD(len(path_buffer))
        ok = kernel32.QueryFullProcessImageNameW(
            handle, 0, path_buffer, ctypes.byref(length))
        if not ok:
            return None
        path = path_buffer.value
        return ProcessIdentity(int(pid), path, get_file_version(path))
    finally:
        kernel32.CloseHandle(handle)


def open_process(pid, writable=False):
    access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
    if writable:
        access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        access = PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        h = kernel32.OpenProcess(access, False, pid)
    return h


def is_writable_protection(protect):
    protect = int(protect)
    return (not protect & (PAGE_NOACCESS | PAGE_GUARD) and
            (protect & 0xFF) in WRITABLE_PROTECTS)


def enum_regions(handle, writable_only=False):
    regions = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while addr < (1 << 63):
        got = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                      ctypes.byref(mbi), ctypes.sizeof(mbi))
        if got == 0:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        readable = (mbi.State == MEM_COMMIT and size > 0 and
                    not (mbi.Protect & PAGE_NOACCESS) and
                    not (mbi.Protect & PAGE_GUARD))
        writable = readable and is_writable_protection(mbi.Protect)
        if readable and (not writable_only or
                         (writable and mbi.Type == MEM_PRIVATE)):
            regions.append((base, size))
        nxt = base + size
        if nxt <= addr:
            break
        addr = nxt
    return regions


def pattern_for(value, kind):
    if kind == "int16":
        return struct.pack("<h", int(value))
    if kind == "int32":
        return struct.pack("<i", int(value))
    if kind == "int64":
        return struct.pack("<q", int(value))
    if kind == "float":
        return struct.pack("<f", float(value))
    if kind == "double":
        return struct.pack("<d", float(value))
    raise ValueError(kind)


def validate_point_values(current, target):
    """Parse the two point fields used by the public UI."""
    try:
        current_value = int(str(current).strip())
    except (TypeError, ValueError):
        raise ValueError("当前点数必须是整数") from None
    if not 0 <= current_value <= MAX_SAFE_POINTS:
        raise ValueError(f"当前点数必须在 0 到 {MAX_SAFE_POINTS} 之间")
    try:
        target_value = int(str(target).strip())
    except (TypeError, ValueError):
        raise ValueError("目标点数必须是整数") from None
    if not 1 <= target_value <= MAX_SAFE_POINTS:
        raise ValueError(f"目标点数必须在 1 到 {MAX_SAFE_POINTS} 之间")
    return current_value, target_value


class CandidateSelectionError(ValueError):
    """Raised when scan results are not safe enough for automatic writing."""


class OperationCancelled(RuntimeError):
    """Raised when the user cancels before an operation can finish safely."""


class ScanResultLimitError(RuntimeError):
    """Raised instead of retaining an unsafe number of raw scan matches."""


class BoundedScanResults:
    """Deduplicate scan overlap while enforcing a predictable memory ceiling."""

    def __init__(self, limit=MAX_SCAN_RESULTS):
        self.limit = int(limit)
        self.items = []
        self._seen = set()

    def add(self, item):
        if item in self._seen:
            return
        if len(self.items) >= self.limit:
            raise ScanResultLimitError(
                f"扫描结果超过 {self.limit} 个，已停止以避免占用过多内存。"
                "请先在游戏中改变点数后重试。"
            )
        self._seen.add(item)
        self.items.append(item)

    def __len__(self):
        return len(self.items)


def select_safe_candidates(verified_candidates, raw_hits):
    """Return addresses that passed the game-record structural signature."""
    selected = list(dict.fromkeys(verified_candidates))
    raw_count = raw_hits if isinstance(raw_hits, int) else len(raw_hits)
    if not selected:
        raise CandidateSelectionError(
            f"没有通过结构验证的候选地址（普通匹配 {raw_count} 个），已拒绝写入"
        )
    if len(selected) > MAX_SAFE_CANDIDATES:
        raise CandidateSelectionError(
            f"候选地址过多（{len(selected)} 个，安全上限 {MAX_SAFE_CANDIDATES} 个），已拒绝写入"
        )
    return selected


@dataclass(frozen=True)
class WriteTransactionResult:
    success: bool
    written: int = 0
    rolled_back: bool = False
    cancelled: bool = False
    error: str = ""


@dataclass
class ScanSession:
    results: list = field(default_factory=list)
    expected_value: int | None = None
    verified: bool = False
    pid: int | None = None

    def record_initial(self, results, current_value, pid=None):
        self.results = list(results)
        self.expected_value = int(current_value)
        self.verified = False
        self.pid = int(pid) if pid is not None else None

    def record_refinement(self, common, latest, current_value, pid=None):
        current_value = int(current_value)
        if self.expected_value is None or current_value == self.expected_value:
            self.verified = False
            return False
        if pid is not None and self.pid is not None and int(pid) != self.pid:
            self.verified = False
            return False
        self.results = list(common) if common else list(latest)
        self.expected_value = current_value
        self.verified = bool(common)
        if pid is not None:
            self.pid = int(pid)
        return self.verified

    def belongs_to(self, pid):
        return self.pid is not None and self.pid == int(pid)

    @property
    def can_write(self):
        return (self.verified and
                1 <= len(self.results) <= MAX_SAFE_CANDIDATES and
                all(kind in ("int32", "int64") for _, kind in self.results))


@dataclass(frozen=True)
class BackupResult:
    folder: Path | None
    files: tuple[Path, ...] = ()
    manifest_path: Path | None = None

    @property
    def count(self):
        return len(self.files)


class RestoreError(RuntimeError):
    """A save backup could not be safely restored."""


@dataclass(frozen=True)
class RestoreResult:
    count: int
    pre_restore_folder: Path


def backup_save_files(documents_dir, backup_root, timestamp=None):
    """Copy every detected rogue-mode save to a timestamped folder."""
    timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    users_root = (Path(documents_dir) / "My Games" /
                  "Age of Empires IV" / "Users")
    sources = sorted(users_root.glob("*/datastore/rogue_mode.rlc"))
    sources += sorted(users_root.glob("*/cloud/rogue_mode.rlc"))
    if not sources:
        return BackupResult(folder=None)
    base_folder = Path(backup_root) / timestamp
    folder = base_folder
    suffix = 1
    while True:
        try:
            folder.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            folder = base_folder.with_name(f"{base_folder.name}-{suffix:02d}")
            suffix += 1
    copied = []
    manifest_files = []
    for source in sources:
        relative = source.relative_to(users_root)
        destination = folder / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)
        manifest_files.append({
            "path": relative.as_posix(),
            "sha256": sha256_file(destination),
        })
    manifest_path = folder / "manifest.json"
    manifest_path.write_text(
        json.dumps({"files": manifest_files}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return BackupResult(
        folder=folder,
        files=tuple(copied),
        manifest_path=manifest_path,
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_file_from(source, destination):
    """Stage a full copy next to the destination, then replace atomically."""
    temporary_path = None
    try:
        with (Path(source).open("rb") as source_stream,
              tempfile.NamedTemporaryFile(
                  mode="wb", prefix=".aoe4-restore-", suffix=".tmp",
                  dir=Path(destination).parent, delete=False) as temporary):
            temporary_path = Path(temporary.name)
            shutil.copyfileobj(source_stream, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def find_latest_backup_folder(backup_root):
    root = Path(backup_root)
    if not root.is_dir():
        return None
    backups = [
        folder for folder in root.iterdir()
        if folder.is_dir() and (folder / "manifest.json").is_file()
    ]
    return max(backups, key=lambda folder: folder.name) if backups else None


def restore_save_files(backup_folder, documents_dir, backup_root,
                       game_running=False):
    """Restore a backup only after preserving the current save state."""
    if game_running:
        raise RestoreError("请先完全退出游戏，再恢复存档")
    folder = Path(backup_folder)
    try:
        manifest = json.loads(
            (folder / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["files"]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RestoreError(f"无法读取备份清单：{exc}") from exc
    if not isinstance(entries, list):
        raise RestoreError("备份清单格式不正确")
    users_root = (Path(documents_dir) / "My Games" /
                  "Age of Empires IV" / "Users")
    files = []
    seen_paths = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RestoreError("备份清单格式不正确")
        relative_text = entry.get("path")
        if not isinstance(relative_text, str):
            raise RestoreError("备份清单格式不正确")
        relative_posix = PurePosixPath(relative_text)
        parts = relative_posix.parts
        if ("\\" in relative_text or ":" in relative_text or
                relative_posix.is_absolute() or
                len(parts) != 3 or parts[0] in (".", "..") or
                parts[1] not in ("datastore", "cloud") or
                parts[2] != "rogue_mode.rlc"):
            raise RestoreError("备份清单包含非天赋存档路径，已拒绝恢复")
        relative = Path(*parts)
        if relative in seen_paths:
            raise RestoreError("备份清单有重复存档路径，已拒绝恢复")
        seen_paths.add(relative)
        source = folder / relative
        destination = users_root / relative
        if (not source.resolve().is_relative_to(folder.resolve()) or
                not destination.resolve().is_relative_to(users_root.resolve())):
            raise RestoreError("备份路径超出预期目录，已拒绝恢复")
        if not source.is_file() or not destination.is_file():
            raise RestoreError("备份文件或对应的当前存档不存在，已拒绝恢复")
        try:
            matches = sha256_file(source) == entry["sha256"]
        except (OSError, KeyError, TypeError) as exc:
            raise RestoreError(f"备份校验失败：{exc}") from exc
        if not matches:
            raise RestoreError("备份校验失败，已拒绝恢复")
        files.append((relative, source, destination))
    if not files:
        raise RestoreError("备份清单没有存档文件")
    previous = backup_save_files(documents_dir, Path(backup_root) / "pre-restore")
    if previous.count == 0 or previous.folder is None:
        raise RestoreError("无法备份当前存档，已拒绝覆盖")
    for relative, _source, _destination in files:
        if not (previous.folder / relative).is_file():
            raise RestoreError("恢复前的安全快照不完整，已拒绝覆盖")
    replaced = []
    try:
        for relative, source, destination in files:
            before = previous.folder / relative
            if sha256_file(destination) != sha256_file(before):
                raise RestoreError("当前存档在恢复期间发生变化，已停止恢复")
            replace_file_from(source, destination)
            replaced.append((before, destination))
    except (OSError, RestoreError) as exc:
        failed_rollback = False
        for before, destination in reversed(replaced):
            try:
                replace_file_from(before, destination)
            except OSError:
                failed_rollback = True
        if failed_rollback:
            raise RestoreError(
                f"恢复未完成，自动回滚也失败。请从 {previous.folder} "
                "手动恢复原存档。"
            ) from exc
        raise RestoreError("恢复未完成，原存档已自动回滚；安全快照已保留") from exc
    return RestoreResult(count=len(files), pre_restore_folder=previous.folder)


def get_documents_directory():
    """Resolve the Windows Documents known folder, including OneDrive redirects."""
    try:
        key_path = (r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                    r"\User Shell Folders")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "Personal")
        return Path(os.path.expandvars(value)).expanduser()
    except OSError:
        return Path.home() / "Documents"


def get_default_backup_root():
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "AoE4_RogueTalentTool" / "backups"


def create_save_backup():
    return backup_save_files(
        get_documents_directory(),
        get_default_backup_root(),
    )


@dataclass(frozen=True)
class SupportSnapshot:
    app_version: str
    windows_version: str
    game_process_count: int
    game_version: str
    save_count: int
    backup_available: bool
    last_result: str


def format_support_report(snapshot):
    """Only include bounded, non-identifying fields for buyer support."""
    def version_or_unknown(value):
        value = str(value)
        return value if re.fullmatch(r"\d+(?:\.\d+){0,3}", value) else "未知"

    outcomes = {
        "idle": "尚未操作",
        "scan_completed": "扫描完成",
        "scan_cancelled": "扫描已取消",
        "scan_failed": "扫描失败",
        "write_completed": "写入完成",
        "write_failed": "写入失败",
        "restore_completed": "恢复完成",
        "restore_failed": "恢复失败",
        "backup_completed": "备份完成",
        "backup_failed": "备份失败",
    }
    return "\n".join((
        "帝国时代4天赋点工具 · 售后诊断信息",
        APP_CREDIT,
        f"工具版本：{version_or_unknown(snapshot.app_version)}",
        f"Windows 版本：{version_or_unknown(snapshot.windows_version)}",
        f"检测到游戏进程：{max(0, int(snapshot.game_process_count))} 个",
        f"游戏文件版本：{version_or_unknown(snapshot.game_version)}",
        f"检测到存档：{max(0, int(snapshot.save_count))} 个",
        f"可恢复备份：{'有' if snapshot.backup_available else '无'}",
        f"最近结果：{outcomes.get(snapshot.last_result, '未知')}",
        "此信息不含用户名、存档内容、内存地址或完整文件路径。",
    ))


def collect_support_snapshot(last_result="idle"):
    pids = list_process_ids(TARGET_PROCESS_NAME)
    identity = read_process_identity(pids[0]) if len(pids) == 1 else None
    users_root = (get_documents_directory() / "My Games" /
                  "Age of Empires IV" / "Users")
    save_count = sum(1 for _ in users_root.glob("*/datastore/rogue_mode.rlc"))
    save_count += sum(1 for _ in users_root.glob("*/cloud/rogue_mode.rlc"))
    return SupportSnapshot(
        app_version=APP_VERSION,
        windows_version=platform.win32_ver()[1],
        game_process_count=len(pids),
        game_version=identity.version if identity else "未知",
        save_count=save_count,
        backup_available=find_latest_backup_folder(
            get_default_backup_root()) is not None,
        last_result=last_result,
    )


def safe_write_values(memory, results, expected_value, new_value,
                      cancel_event=None, progress_cb=None):
    """Write point values through a process-memory boundary."""
    if cancel_event and cancel_event.is_set():
        return WriteTransactionResult(
            success=False,
            cancelled=True,
            error="操作已取消，未写入任何地址",
        )
    try:
        expected_value, new_value = validate_point_values(
            expected_value, new_value)
        prepared_results = [(int(addr), str(kind)) for addr, kind in results]
    except (TypeError, ValueError) as exc:
        return WriteTransactionResult(success=False, error=str(exc))
    if not 1 <= len(prepared_results) <= MAX_SAFE_CANDIDATES:
        return WriteTransactionResult(
            success=False,
            error=(f"写入地址必须至少 1 个，且安全上限 "
                   f"{MAX_SAFE_CANDIDATES} 个"),
        )
    if len({addr for addr, _kind in prepared_results}) != len(prepared_results):
        return WriteTransactionResult(
            success=False,
            error="写入地址中存在重复项，已取消全部写入",
        )
    if any(kind not in ("int32", "int64")
           for _addr, kind in prepared_results):
        return WriteTransactionResult(
            success=False,
            error="只允许写入 int32 或 int64 类型，已取消全部写入",
        )

    prepared = []
    for addr, kind in prepared_results:
        info = memory.region_info(addr)
        if (not info or info.get("type") != MEM_PRIVATE or
                not is_writable_protection(info.get("prot", PAGE_NOACCESS))):
            return WriteTransactionResult(
                success=False,
                error=f"地址 0x{addr:X} 不在可写的游戏私有内存中，已取消全部写入",
            )
        expected = pattern_for(expected_value, kind)
        if memory.read(addr, len(expected)) != expected:
            return WriteTransactionResult(
                success=False,
                error=f"地址 0x{addr:X} 的当前值已经变化，已取消全部写入",
            )
        prepared.append((addr, expected, pattern_for(new_value, kind)))

    if cancel_event and cancel_event.is_set():
        return WriteTransactionResult(
            success=False,
            cancelled=True,
            error="操作已取消，未写入任何地址",
        )

    def rollback(entries):
        rollback_ok = True
        for rollback_addr, rollback_bytes in reversed(entries):
            restored = memory.write(rollback_addr, rollback_bytes)
            if (not restored or
                    memory.read(rollback_addr, len(rollback_bytes)) != rollback_bytes):
                rollback_ok = False
        return rollback_ok

    attempted = []
    for idx, (addr, old_bytes, new_bytes) in enumerate(prepared):
        if memory.read(addr, len(old_bytes)) != old_bytes:
            rollback_ok = rollback(attempted)
            return WriteTransactionResult(
                success=False,
                rolled_back=rollback_ok,
                error=f"地址 0x{addr:X} 的当前值在写入前再次变化，已取消并回滚",
            )
        attempted.append((addr, old_bytes))
        write_ok = memory.write(addr, new_bytes)
        readback_ok = write_ok and memory.read(addr, len(new_bytes)) == new_bytes
        if not readback_ok:
            rollback_ok = rollback(attempted)
            return WriteTransactionResult(
                success=False,
                rolled_back=rollback_ok,
                error=f"地址 0x{addr:X} 写入或回读失败，已尝试回滚",
            )
        if progress_cb:
            progress_cb(idx + 1, len(prepared), idx + 1)
    return WriteTransactionResult(success=True, written=len(prepared))


# 实际天赋点记录的特征：记录体为 16 字节，[0:8] 是 int64 数值，
# [8:16] 是类型/标记，高位通常是 0x8800 / 0x8801 这类 tag。
VALID_TAG_HIGHS = (0x8800, 0x8801, 0x87FF, 0x87FE, 0x87FD)


def get_region_info(handle, addr):
    mbi = MEMORY_BASIC_INFORMATION()
    got = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                   ctypes.byref(mbi), ctypes.sizeof(mbi))
    if not got:
        return None
    return {
        "base": int(mbi.BaseAddress or 0),
        "alloc": int(mbi.AllocationBase or 0),
        "size": int(mbi.RegionSize),
        "prot": int(mbi.Protect),
        "type": int(mbi.Type),
    }


class Win32ProcessMemory:
    """Public boundary for reading and writing one Windows process."""

    def __init__(self, pid, writable=False):
        self.pid = int(pid)
        self.writable = bool(writable)
        self.handle = open_process(self.pid, writable=self.writable)
        if not self.handle:
            code = ctypes.get_last_error()
            raise RuntimeError(f"无法打开目标进程（Windows 错误码 {code}）")

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def region_info(self, address):
        return get_region_info(self.handle, address)

    def read(self, address, size):
        if size <= 0:
            return b""
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buf,
            size,
            ctypes.byref(read),
        )
        if not ok or int(read.value) != size:
            return None
        return bytes(buf.raw[:size])

    def write(self, address, data):
        if not self.writable:
            return False
        payload = bytes(data)
        buf = ctypes.create_string_buffer(payload)
        written = ctypes.c_size_t(0)
        ok = kernel32.WriteProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buf,
            len(payload),
            ctypes.byref(written),
        )
        return bool(ok and int(written.value) == len(payload))


def has_talent_record_signature(handle, address, value, scratch):
    info = get_region_info(handle, address)
    if (info is None or info["type"] != MEM_PRIVATE or
            not is_writable_protection(info["prot"]) or address < 8):
        return False
    read = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address - 8), scratch, 48,
            ctypes.byref(read)):
        return False
    if read.value < 40:
        return False
    for baseoff in (8, 0):
        val = struct.unpack_from("<Q", scratch, baseoff)[0]
        tag = struct.unpack_from("<Q", scratch, baseoff + 8)[0]
        if val == int(value) and ((tag >> 48) & 0xFFFF) in VALID_TAG_HIGHS:
            return True
    return False


def quick_find_candidates(pid, value, progress_cb=None, cancel_event=None):
    """一键定位：只扫 int64，并用 16 字节记录特征过滤。"""
    if cancel_event and cancel_event.is_set():
        raise OperationCancelled("操作已取消，未保留不完整的扫描结果")
    handle = open_process(pid)
    if not handle:
        raise RuntimeError("无法打开游戏进程，请以管理员身份运行本工具。")
    pat = struct.pack("<q", int(value))
    buf = ctypes.create_string_buffer(2 * 1024 * 1024)
    small = ctypes.create_string_buffer(48)
    raw_count = 0
    candidates = []
    candidate_set = set()
    try:
        regions = enum_regions(handle, writable_only=True)
        total = len(regions)
        for idx, (base, size) in enumerate(regions):
            if cancel_event and cancel_event.is_set():
                raise OperationCancelled("操作已取消，未保留不完整的扫描结果")
            off = 0
            while off < size:
                if cancel_event and cancel_event.is_set():
                    raise OperationCancelled("操作已取消，未保留不完整的扫描结果")
                want = min(2 * 1024 * 1024, size - off)
                read = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(base + off), buf, want,
                    ctypes.byref(read))
                n = int(read.value)
                if not ok or n == 0:
                    break
                data = buf.raw[:n]
                pos = data.find(pat)
                while pos >= 0:
                    address = base + off + pos
                    raw_count += 1
                    if ((raw_count & 0xFFF) == 0 and cancel_event and
                            cancel_event.is_set()):
                        raise OperationCancelled(
                            "操作已取消，未保留不完整的扫描结果")
                    if (address not in candidate_set and
                            has_talent_record_signature(
                                handle, address, value, small)):
                        candidate_set.add(address)
                        candidates.append(address)
                        if len(candidates) > MAX_SAFE_CANDIDATES:
                            return candidates, raw_count
                    pos = data.find(pat, pos + 1)
                off += n
                if n > 7:
                    off -= 7
                else:
                    break
            if progress_cb:
                progress_cb(idx + 1, total, raw_count)
        return candidates, raw_count
    finally:
        kernel32.CloseHandle(handle)


def scan_value(pid, value, kinds=("int64",),
               progress_cb=None, cancel_event=None):
    if cancel_event and cancel_event.is_set():
        raise OperationCancelled("操作已取消，未保留不完整的扫描结果")
    handle = open_process(pid)
    if not handle:
        raise RuntimeError("无法打开游戏进程，请以管理员身份运行本工具。")
    patterns = {}
    for k in kinds:
        try:
            patterns[k] = pattern_for(value, k)
        except Exception:
            pass
    results = BoundedScanResults()
    try:
        regions = enum_regions(handle, writable_only=True)
        total = len(regions)
        overlap = max(len(p) - 1 for p in patterns.values()) if patterns else 0
        buf_size = 1024 * 1024
        buf = ctypes.create_string_buffer(buf_size)
        for idx, (base, size) in enumerate(regions):
            if cancel_event and cancel_event.is_set():
                raise OperationCancelled("操作已取消，未保留不完整的扫描结果")
            offset = 0
            while offset < size:
                if cancel_event and cancel_event.is_set():
                    raise OperationCancelled("操作已取消，未保留不完整的扫描结果")
                want = min(buf_size, size - offset)
                read = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(base + offset), buf, want,
                    ctypes.byref(read))
                n = int(read.value)
                if not ok or n == 0:
                    break
                data = buf.raw[:n]
                for k, pat in patterns.items():
                    pos = data.find(pat)
                    while pos >= 0:
                        results.add((base + offset + pos, k))
                        pos = data.find(pat, pos + 1)
                offset += n
                if n > overlap:
                    offset -= overlap
            if progress_cb:
                progress_cb(idx + 1, total, len(results))
        return results.items
    finally:
        kernel32.CloseHandle(handle)


def refine_results(pid, previous_results, new_value, progress_cb=None,
                   cancel_event=None):
    """对“新的显示数值”做一次全量扫描，再和上一轮结果取交集。

    游戏购买/退款后，目标对象可能会在内存中移动，所以不能只读旧地址；
    全量扫描 + 交集才是 Cheat Engine “Next Scan” 的等价做法。
    """
    new_results = scan_value(pid, new_value, progress_cb=progress_cb,
                             cancel_event=cancel_event)
    previous = set(previous_results)
    common = sorted(previous & set(new_results))
    return common, new_results


def write_value(pid, results, new_value, expected_value, progress_cb=None,
                cancel_event=None, process_name=TARGET_PROCESS_NAME):
    """Safely write all selected addresses or leave them unchanged."""
    if process_name and find_process_id(process_name) != int(pid):
        return WriteTransactionResult(
            success=False,
            error="目标游戏进程在写入前已经变化或不再唯一，已拒绝写入",
        )
    with Win32ProcessMemory(pid, writable=True) as memory:
        return safe_write_values(
            memory,
            results,
            expected_value=expected_value,
            new_value=new_value,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
        )


# ---------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"帝国时代4 · 天赋点修改 v{APP_VERSION}")
        self.geometry("560x610")
        self.minsize(520, 610)
        self.scan_session = ScanSession()
        self.pid = None
        self.game_version = "未知"
        self.last_backup = None
        self.last_result = "idle"
        self.active_operation = None
        self.is_busy = False
        self.is_preparing = False
        self.pending_target = None
        self.cancel_event = threading.Event()
        self.queue = queue.Queue()
        self.configure(background="#F4F5F1")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10),
                        background="#F4F5F1", foreground="#29382F")
        style.configure("TButton", padding=(10, 7), borderwidth=1)
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 24, "bold"))
        style.configure("Hint.TLabel", foreground="#6B756E")
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("Card.TLabel", background="#FFFFFF")
        style.configure("CardHint.TLabel", background="#FFFFFF",
                        foreground="#6B756E", font=("Microsoft YaHei UI", 9))
        style.configure("Point.TEntry", padding=(10, 9), fieldbackground="#FFFFFF")
        style.configure("Primary.TButton", background="#365E4D", foreground="#FFFFFF",
                        font=("Microsoft YaHei UI", 12, "bold"), padding=(12, 14),
                        borderwidth=0, focusthickness=2, focuscolor="#B7CBBE")
        style.map("Primary.TButton", background=[("disabled", "#B3BDB5"),
                  ("pressed", "#294A3A"), ("active", "#426F5B")],
                  foreground=[("disabled", "#F4F5F1")])
        style.configure("Link.TButton", background="#F4F5F1", borderwidth=0,
                        foreground="#506558", padding=(2, 5))
        style.map("Link.TButton", background=[("active", "#E5EBE4")])
        style.configure("Horizontal.TProgressbar", background="#365E4D",
                        troughcolor="#E3E8E1", bordercolor="#E3E8E1",
                        lightcolor="#365E4D", darkcolor="#365E4D", borderwidth=0)

        main = ttk.Frame(self, padding=(28, 22, 28, 20))
        main.pack(fill=tk.BOTH, expand=True)
        heading = ttk.Frame(main)
        heading.pack(fill=tk.X)
        ttk.Label(heading, text="帝国时代 IV · 单人炼狱",
                  style="Hint.TLabel").pack(side=tk.LEFT)
        ttk.Label(heading, text=f"v{APP_VERSION}", style="Hint.TLabel").pack(side=tk.RIGHT)
        ttk.Label(main, text="天赋点修改", style="Heading.TLabel").pack(
            anchor="w", pady=(5, 10))
        self.status_var = tk.StringVar(value="正在检测游戏…")
        self.status_label = ttk.Label(main, textvariable=self.status_var,
                                      style="Hint.TLabel")
        self.status_label.pack(anchor="w", pady=(0, 18))

        self.cur_var = tk.StringVar(value="")
        self.target_var = tk.StringVar(value="33875")
        fields = ttk.Frame(main, style="Card.TFrame", padding=16)
        fields.pack(fill=tk.X)
        fields.columnconfigure((0, 1), weight=1, uniform="points")
        ttk.Label(fields, text="当前点数", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(fields, text="修改为", style="Card.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 0))
        self.current_entry = ttk.Entry(fields, textvariable=self.cur_var,
                                        width=8, font=("Consolas", 23), style="Point.TEntry")
        self.current_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=8)
        self.target_entry = ttk.Entry(fields, textvariable=self.target_var,
                                       width=8, font=("Consolas", 23), style="Point.TEntry")
        self.target_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=8)
        ttk.Label(fields, text="填写游戏右上角的数字", style="CardHint.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10))
        ttk.Label(fields, text="可改为 1–33875", style="CardHint.TLabel").grid(
            row=2, column=1, sticky="w", padx=(10, 0))
        self.point_entries = [self.current_entry, self.target_entry]
        for entry in self.point_entries:
            entry.bind("<Return>", lambda _event: self.quick_write())
        self.bind("<Escape>", lambda _event: self.do_cancel())

        self.quick_btn = ttk.Button(main, text="一键修改天赋点", style="Primary.TButton",
                                    command=self.quick_write)
        self.quick_btn.pack(fill=tk.X, pady=(18, 6))
        ttk.Label(main, text="自动备份 · 自动定位 · 修改后校验",
                  style="Hint.TLabel").pack(anchor="center")
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(19, 10))
        self.result_title_var = tk.StringVar(value="准备就绪")
        self.result_title = ttk.Label(main, textvariable=self.result_title_var,
                                       font=("Microsoft YaHei UI", 11, "bold"))
        self.result_title.pack(anchor="w")
        self.info_var = tk.StringVar(value="进入单人炼狱界面，填写当前点数后点击修改。")
        self.info_label = ttk.Label(main, textvariable=self.info_var,
                                    style="Hint.TLabel", wraplength=470, justify=tk.LEFT)
        self.info_label.pack(fill=tk.X, pady=(5, 12))

        ttk.Label(main, text=APP_CREDIT, style="Hint.TLabel").pack(
            side=tk.BOTTOM, pady=(12, 0))
        footer = ttk.Frame(main)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        ttk.Button(footer, text="更多工具", style="Link.TButton",
                   command=self.show_tools).pack(side=tk.LEFT)
        ttk.Button(footer, text="使用帮助", style="Link.TButton",
                   command=self.show_help).pack(side=tk.LEFT, padx=(16, 0))
        self.cancel_btn = ttk.Button(footer, text="取消扫描", style="Link.TButton",
                                      command=self.do_cancel, state=tk.DISABLED)
        main.bind("<Configure>", lambda event: self.info_label.configure(
            wraplength=max(300, event.width - 56)))

        self.tools_window = tk.Toplevel(self)
        self.tools_window.withdraw()
        self.tools_window.title("更多工具 · 天赋点修改")
        self.tools_window.geometry("880x540")
        self.tools_window.minsize(820, 500)
        self.tools_window.configure(background="#F4F5F1")
        self.tools_window.protocol("WM_DELETE_WINDOW", self.tools_window.withdraw)
        details = ttk.Frame(self.tools_window, padding=18)
        details.pack(fill=tk.BOTH, expand=True)
        self.status_details_var = tk.StringVar(value="等待检测")
        connection = ttk.Frame(details)
        connection.pack(fill=tk.X)
        ttk.Label(connection, textvariable=self.status_details_var,
                  style="Hint.TLabel").pack(side=tk.LEFT)
        self.detect_btn = ttk.Button(connection, text="重新检测", command=self.detect_process)
        self.detect_btn.pack(side=tk.RIGHT)
        tools = ttk.Frame(details)
        tools.pack(fill=tk.X, pady=(14, 16))
        self.backup_btn = ttk.Button(tools, text="备份存档", command=self.backup_saves)
        self.backup_btn.pack(side=tk.LEFT, padx=(0, 7))
        self.restore_btn = ttk.Button(tools, text="恢复最近备份",
                                      command=self.restore_latest_backup)
        self.restore_btn.pack(side=tk.LEFT, padx=(0, 7))
        ttk.Button(tools, text="备份目录", command=self.open_backup_dir).pack(
            side=tk.LEFT, padx=(0, 7))
        ttk.Button(tools, text="存档目录", command=self.open_save_dir).pack(
            side=tk.LEFT, padx=(0, 7))
        ttk.Button(tools, text="复制诊断", command=self.copy_diagnostics).pack(side=tk.LEFT)
        ttk.Separator(details).pack(fill=tk.X, pady=(0, 14))
        ttk.Label(details, text="手动筛选", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(details, text="首次扫描 → 回游戏改变点数 → 填写新点数并再次扫描 → 写入",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 12))
        values = ttk.Frame(details)
        values.pack(fill=tk.X, pady=(0, 10))
        for label, variable in (("当前点数", self.cur_var), ("目标点数", self.target_var)):
            ttk.Label(values, text=label).pack(side=tk.LEFT, padx=(0, 8))
            entry = ttk.Entry(values, textvariable=variable, width=12)
            entry.pack(side=tk.LEFT, padx=(0, 20))
            self.point_entries.append(entry)
        manual = ttk.Frame(details)
        manual.pack(fill=tk.X, pady=(0, 10))
        self.scan_btn = ttk.Button(manual, text="① 首次扫描", command=self.first_scan)
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.next_btn = ttk.Button(manual, text="② 再次扫描",
                                   command=self.refine_scan, state=tk.DISABLED)
        self.next_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.write_btn = ttk.Button(manual, text="③ 写入",
                                    command=self.write_selected, state=tk.DISABLED)
        self.write_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(manual, text="全选", command=lambda: self.set_all(True)).pack(
            side=tk.LEFT, padx=(8, 3))
        ttk.Button(manual, text="清空选择",
                   command=lambda: self.set_all(False)).pack(side=tk.LEFT)
        self.scan_details_var = tk.StringVar(value="尚未进行手动扫描。")
        ttk.Label(details, textvariable=self.scan_details_var, style="Hint.TLabel",
                  wraplength=780).pack(fill=tk.X, pady=(0, 8))
        cols = ("sel", "addr", "kind", "note")
        self.tree = ttk.Treeview(details, columns=cols, show="headings",
                                 selectmode="extended")
        self.tree.heading("sel", text="选")
        self.tree.heading("addr", text="地址")
        self.tree.heading("kind", text="类型")
        self.tree.heading("note", text="说明")
        self.tree.column("sel", width=46, anchor=tk.CENTER, stretch=False)
        self.tree.column("addr", width=170, anchor=tk.W)
        self.tree.column("kind", width=90, anchor=tk.CENTER, stretch=False)
        self.tree.column("note", width=380, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.toggle_item)

        self.after(100, self.poll_queue)
        self.after(150, self.refresh_connection)
        self.current_entry.focus_set()
        self.bind("<Map>", self.fit_initial_size, add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def fit_initial_size(self, event):
        if event.widget is self and not getattr(self, "initial_size_fitted", False):
            self.initial_size_fitted = True
            self.update_idletasks()
            self.minsize(max(520, self.winfo_reqwidth()),
                         max(610, self.winfo_reqheight() + 48))

    def show_tools(self):
        self.tools_window.deiconify()
        self.tools_window.lift()

    def show_help(self):
        messagebox.showinfo(
            "使用帮助",
            f"帝国时代4 · 天赋点修改 v{APP_VERSION}\n{APP_CREDIT}\n\n"
            "1. 进入单人炼狱界面，填写游戏右上角的当前点数。\n"
            "2. 目标默认 33875，点击“一键修改天赋点”或按回车。\n"
            "3. 完成后回游戏，把“已启用天赋”关闭再打开一次，刷新保存。\n\n"
            "扫描期间按 Esc 可取消。备份恢复和诊断位于“更多工具”。",
            parent=self,
        )

    def set_feedback(self, title, message, tone="neutral"):
        colors = {"neutral": "#29382F", "working": "#365E4D",
                  "success": "#365E4D", "error": "#9A4A35"}
        self.result_title_var.set(title)
        self.result_title.configure(foreground=colors.get(tone, colors["neutral"]))
        self.info_var.set(message)

    def refresh_connection(self):
        if not self.is_busy and not self.is_preparing:
            self.detect_process()
        self.after(2000, self.refresh_connection)

    def detect_process(self):
        pids = list_process_ids(TARGET_PROCESS_NAME)
        pid = pids[0] if len(pids) == 1 else None
        if pid:
            if self.pid != pid:
                self.scan_session = ScanSession()
                self.tree.delete(*self.tree.get_children())
                self.next_btn.config(state=tk.DISABLED)
                self.write_btn.config(state=tk.DISABLED)
                identity = read_process_identity(pid)
                self.game_version = identity.version if identity else "未知"
            self.pid = pid
            self.status_var.set("●  游戏已连接")
            self.status_label.configure(foreground="#365E4D")
            self.status_details_var.set(f"PID {pid} · 游戏文件版本 {self.game_version}")
            return True
        else:
            if self.pid is not None:
                self.scan_session = ScanSession()
                self.tree.delete(*self.tree.get_children())
                self.next_btn.config(state=tk.DISABLED)
                self.write_btn.config(state=tk.DISABLED)
            self.pid = None
            self.game_version = "未知"
            self.status_var.set(
                "●  请只保留一个游戏进程" if pids else "●  等待游戏启动")
            self.status_label.configure(foreground="#6B756E")
            self.status_details_var.set(
                "检测到多个游戏进程" if pids else f"未检测到 {TARGET_PROCESS_NAME}")
            return False

    def ensure_ready(self):
        if self.is_busy or self.is_preparing:
            return False
        if self.detect_process():
            return True
        self.set_feedback("还未连接游戏", "请启动游戏并进入单人炼狱界面，然后再点击修改。", "error")
        return False

    def set_all(self, selected):
        mark = "√" if selected else ""
        for item in self.tree.get_children():
            vals = list(self.tree.item(item, "values"))
            vals[0] = mark
            self.tree.item(item, values=vals)

    def toggle_item(self, _event):
        for item in self.tree.selection():
            vals = list(self.tree.item(item, "values"))
            vals[0] = "" if vals[0] == "√" else "√"
            self.tree.item(item, values=vals)

    def open_save_dir(self):
        path = (get_documents_directory() / "My Games" /
                "Age of Empires IV" / "Users")
        if path.is_dir():
            os.startfile(path)
        else:
            messagebox.showinfo("提示", "未找到存档目录：\n" + str(path))

    def backup_saves(self):
        if self.is_busy:
            messagebox.showwarning("请稍候", "扫描或写入期间请勿操作存档。")
            return None
        try:
            result = create_save_backup()
        except OSError as exc:
            self.last_result = "backup_failed"
            messagebox.showerror("备份失败", str(exc))
            return None
        self.last_backup = result
        if result.count:
            self.last_result = "backup_completed"
            messagebox.showinfo(
                "备份完成",
                f"已备份 {result.count} 个存档文件到：\n{result.folder}",
            )
            self.info_var.set(f"存档备份完成：{result.folder}")
        else:
            self.last_result = "backup_failed"
            messagebox.showwarning(
                "未找到存档",
                "没有找到 rogue_mode.rlc，请确认游戏版本和文档目录。",
            )
        return result

    def restore_latest_backup(self):
        if self.is_busy:
            messagebox.showwarning("请稍候", "请等待当前扫描或写入完成后再恢复存档。")
            return
        if list_process_ids(TARGET_PROCESS_NAME):
            messagebox.showwarning("请先退出游戏", "请完全退出《帝国时代4》后再恢复存档。")
            return
        backup_root = get_default_backup_root()
        latest = find_latest_backup_folder(backup_root)
        if latest is None:
            messagebox.showinfo("没有可恢复的备份", "当前没有找到由本工具创建的存档备份。")
            return
        if not messagebox.askyesno(
                "确认恢复最近备份",
                f"准备恢复备份：{latest.name}\n\n"
                "当前存档会先另存一份安全快照，然后被这份备份覆盖。\n"
                "请确认游戏已经退出，并暂时关闭可能同步存档的程序。\n\n"
                "确定要继续吗？"):
            return
        try:
            result = restore_save_files(
                latest,
                get_documents_directory(),
                backup_root,
                game_running=bool(list_process_ids(TARGET_PROCESS_NAME)),
            )
        except (RestoreError, OSError) as exc:
            self.last_result = "restore_failed"
            messagebox.showerror("恢复失败", str(exc))
            return
        self.scan_session = ScanSession()
        self.tree.delete(*self.tree.get_children())
        self.next_btn.config(state=tk.DISABLED)
        self.write_btn.config(state=tk.DISABLED)
        self.last_result = "restore_completed"
        self.info_var.set(f"已恢复 {result.count} 个存档；恢复前的存档已另存。")
        messagebox.showinfo(
            "恢复完成",
            f"已恢复 {result.count} 个存档文件。\n\n"
            f"恢复前的存档保存在：\n{result.pre_restore_folder}\n\n"
            "现在可以重新启动游戏检查点数。",
        )

    def open_backup_dir(self):
        path = get_default_backup_root()
        if path.is_dir():
            os.startfile(path)
        else:
            messagebox.showinfo("提示", "尚未生成备份，请先点击“备份存档”。")

    def copy_diagnostics(self):
        try:
            report = format_support_report(
                collect_support_snapshot(self.last_result))
            self.clipboard_clear()
            self.clipboard_append(report)
            self.update_idletasks()
        except (OSError, tk.TclError) as exc:
            messagebox.showerror("复制失败", str(exc))
            return
        self.info_var.set("已复制售后诊断信息；不包含用户名、存档内容或内存地址。")
        messagebox.showinfo("复制完成", "诊断信息已复制，可以粘贴给售后。")

    def prepare_backup_before_write(self):
        try:
            result = create_save_backup()
        except OSError as exc:
            self.set_feedback("备份失败，尚未修改", str(exc), "error")
            return False
        self.last_backup = result
        if result.count:
            self.set_feedback("备份完成", "正在准备定位天赋点。", "working")
            return True
        return messagebox.askyesno(
            "没有找到可备份的存档",
            "程序没有找到 rogue_mode.rlc，因此无法自动备份。\n\n"
            "继续写入将失去自动恢复保障，是否仍要继续？",
        )

    def do_cancel(self):
        if not self.is_busy or self.active_operation == "write":
            return
        self.cancel_event.set()
        self.cancel_btn.config(state=tk.DISABLED)
        self.set_feedback("正在取消", "请稍候，当前扫描即将停止。")

    def run_async(self, fn, *args, operation="scan"):
        self.cancel_event.clear()
        self.is_busy = True
        self.active_operation = operation
        self.cancel_btn.config(state=tk.NORMAL if operation != "write"
                               else tk.DISABLED)
        if operation != "write":
            self.cancel_btn.pack(side=tk.RIGHT)
        else:
            self.cancel_btn.pack_forget()
        self.progress.config(value=0, maximum=100)
        self.quick_btn.config(state=tk.DISABLED,
                              text="正在修改…" if operation == "write" else "正在定位…")
        self.set_feedback("正在修改" if operation == "write" else "正在定位",
                          "请保持游戏停留在当前界面，等待完成。", "working")
        for entry in self.point_entries:
            entry.config(state=tk.DISABLED)
        self.scan_btn.config(state=tk.DISABLED)
        self.next_btn.config(state=tk.DISABLED)
        self.write_btn.config(state=tk.DISABLED)
        self.detect_btn.config(state=tk.DISABLED)
        self.backup_btn.config(state=tk.DISABLED)
        self.restore_btn.config(state=tk.DISABLED)
        threading.Thread(target=lambda: self.worker(fn, *args), daemon=True).start()

    def worker(self, fn, *args):
        try:
            fn(*args)
        except OperationCancelled as exc:
            self.queue.put(("canceled", str(exc)))
        except Exception as e:
            self.queue.put(("error", str(e)))
        finally:
            self.queue.put(("done", None))

    def poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "progress":
                    done, total, found = payload
                    self.progress.config(maximum=max(total, 1), value=done)
                    if self.active_operation == "write":
                        self.info_var.set("正在修改点数并检查结果，请稍候。")
                    else:
                        percent = min(100, int(done * 100 / max(total, 1)))
                        self.info_var.set(f"定位进度 {percent}% · 请保持游戏停留在当前界面。")
                        self.scan_details_var.set(
                            f"扫描 {done}/{total} 个区域，发现 {found} 个匹配。")
                elif kind == "phase":
                    if payload == "writing":
                        self.active_operation = "write"
                        self.cancel_btn.config(state=tk.DISABLED)
                        self.cancel_btn.pack_forget()
                        self.quick_btn.config(text="正在修改…")
                        self.set_feedback("正在修改", "已找到天赋点，正在修改并检查结果。", "working")
                elif kind == "initial_results":
                    source_pid, results, current_value = payload
                    if source_pid != self.pid:
                        continue
                    self.scan_session.record_initial(
                        results, current_value, pid=source_pid)
                    self.show_results(
                        self.scan_session.results, verified=False)
                    self.last_result = "scan_completed"
                elif kind == "refinement":
                    source_pid, common, latest, current_value = payload
                    if (source_pid != self.pid or
                            not self.scan_session.belongs_to(source_pid)):
                        continue
                    confirmed = self.scan_session.record_refinement(
                        common, latest, current_value, pid=source_pid)
                    self.show_results(
                        self.scan_session.results, verified=confirmed)
                    self.last_result = "scan_completed"
                    if not confirmed:
                        messagebox.showinfo(
                            "还不能安全写入",
                            "这轮没有保留下相同地址。已把本轮结果作为新的基线，"
                            "请再改变一次点数并继续点“再次扫描”。")
                elif kind == "quick_done":
                    result, n = payload
                    if result.cancelled:
                        self.last_result = "scan_cancelled"
                        self.set_feedback("已取消", "本次没有修改天赋点。")
                    elif result.success:
                        self.last_result = "write_completed"
                        self.scan_session = ScanSession()
                        self.tree.delete(*self.tree.get_children())
                        self.next_btn.config(state=tk.DISABLED)
                        self.write_btn.config(state=tk.DISABLED)
                        title = "修改完成"
                        if self.pending_target is not None:
                            title = f"已修改为 {self.pending_target} 点"
                            self.cur_var.set(str(self.pending_target))
                        self.progress.config(maximum=100, value=100)
                        self.set_feedback(title,
                            "回到游戏，把“已启用天赋”关闭再打开一次，即可刷新并保存。", "success")
                        self.scan_details_var.set(f"已写入并回读验证 {n} 个地址。")
                    else:
                        self.last_result = "write_failed"
                        self.set_feedback("未能完成修改",
                            result.error or "写入或校验失败，请在更多工具中查看备份。", "error")
                elif kind == "write_done":
                    result = payload
                    if result.success:
                        self.last_result = "write_completed"
                        messagebox.showinfo(
                            "完成",
                            f"{result.written} 个地址均已写入并回读验证。\n\n"
                            "请回游戏把右上角“已启用天赋”开关关一下再打开。")
                        self.info_var.set(
                            "写入和回读验证完成。请切换一次“已启用天赋”开关刷新保存。")
                    else:
                        self.last_result = "write_failed"
                        rollback = "旧值已恢复。" if result.rolled_back else "请勿继续操作并恢复存档备份。"
                        messagebox.showerror("安全写入失败", f"{result.error}\n{rollback}")
                elif kind == "canceled":
                    self.last_result = "scan_cancelled"
                    self.set_feedback("已取消", "本次没有修改天赋点。")
                elif kind == "error":
                    self.last_result = (
                        "write_failed" if self.active_operation == "write"
                        else "scan_failed")
                    self.set_feedback("未能完成修改", payload, "error")
                elif kind == "done":
                    self.is_busy = False
                    self.active_operation = None
                    self.cancel_btn.config(state=tk.DISABLED)
                    self.cancel_btn.pack_forget()
                    self.quick_btn.config(state=tk.NORMAL, text="一键修改天赋点")
                    for entry in self.point_entries:
                        entry.config(state=tk.NORMAL)
                    self.scan_btn.config(state=tk.NORMAL)
                    self.detect_btn.config(state=tk.NORMAL)
                    self.backup_btn.config(state=tk.NORMAL)
                    self.restore_btn.config(state=tk.NORMAL)
                    if (self.scan_session.results and self.pid is not None and
                            self.scan_session.belongs_to(self.pid)):
                        self.next_btn.config(state=tk.NORMAL)
                    if (self.scan_session.can_write and self.pid is not None and
                            self.scan_session.belongs_to(self.pid)):
                        self.write_btn.config(state=tk.NORMAL)
        except queue.Empty:
            pass
        self.after(100, self.poll_queue)

    def show_results(self, results, verified=False):
        self.tree.delete(*self.tree.get_children())
        shown = results if len(results) <= 2000 else results[:2000]
        for addr, kind in shown:
            self.tree.insert("", tk.END, values=(
                "√",
                f"0x{addr:X}",
                kind,
                "已通过两轮数值验证" if verified else "尚未完成二次验证",
            ))
        msg = f"本轮有效结果 {len(results)} 个。"
        if len(results) > len(shown):
            msg += f" 列表仅展示前 {len(shown)} 个。"
        if not verified:
            msg += " 当前结果不可写；请改变点数后执行“再次扫描”。"
        elif len(results) > MAX_SAFE_CANDIDATES:
            msg += (f" 虽已二次验证，但仍超过 {MAX_SAFE_CANDIDATES} 个安全上限；"
                    "请再次改变点数继续过滤。")
        else:
            msg += " 二次验证完成，可以安全写入。"
        self.info_var.set(msg)
        self.scan_details_var.set(msg)

    def get_selected_results(self):
        out = []
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if vals and vals[0] == "√":
                out.append((int(vals[1], 16), vals[2]))
        return out

    def first_scan(self):
        if not self.ensure_ready():
            return
        try:
            cur, _target = validate_point_values(
                self.cur_var.get(), self.target_var.get())
        except ValueError as e:
            messagebox.showwarning("输入错误", str(e))
            return
        self.scan_session = ScanSession()
        self.tree.delete(*self.tree.get_children())
        pid = self.pid

        def work():
            def cb(done, total, found):
                self.queue.put(("progress", (done, total, found)))
            res = scan_value(pid, cur, progress_cb=cb,
                             cancel_event=self.cancel_event)
            self.queue.put(("initial_results", (pid, res, cur)))

        self.run_async(work)

    def quick_write(self):
        if self.is_busy or self.is_preparing:
            return
        try:
            cur, target = validate_point_values(
                self.cur_var.get(), self.target_var.get())
        except ValueError as e:
            self.set_feedback("请检查点数", str(e), "error")
            entry = self.target_entry if str(e).startswith("目标") else self.current_entry
            entry.focus_set()
            entry.selection_range(0, tk.END)
            return
        if cur == target:
            self.set_feedback("无需修改", "当前点数与目标点数相同。")
            return
        if not self.ensure_ready():
            return
        self.is_preparing = True
        try:
            if not self.prepare_backup_before_write():
                return
        finally:
            self.is_preparing = False
        pid = self.pid
        self.pending_target = target

        def work():
            def cb(done, total, found):
                self.queue.put(("progress", (done, total, found)))
            candidates, writable_raw = quick_find_candidates(
                pid, cur, progress_cb=cb, cancel_event=self.cancel_event)
            safe_addresses = select_safe_candidates(candidates, writable_raw)
            sel = [(addr, "int64") for addr in safe_addresses]
            self.queue.put(("phase", "writing"))
            result = write_value(
                pid,
                sel,
                new_value=target,
                expected_value=cur,
                cancel_event=self.cancel_event,
            )
            self.queue.put(("quick_done", (result, len(safe_addresses))))

        self.run_async(work)

    def refine_scan(self):
        if not self.scan_session.results:
            messagebox.showinfo("提示", "请先做首次扫描")
            return
        if not self.ensure_ready():
            return
        if (not self.scan_session.results or
                not self.scan_session.belongs_to(self.pid)):
            messagebox.showinfo(
                "需要重新扫描",
                "游戏进程已经变化，请从“首次扫描”重新开始。",
            )
            return
        try:
            new, _target = validate_point_values(
                self.cur_var.get(), self.target_var.get())
        except ValueError as e:
            messagebox.showwarning("输入错误", str(e))
            return
        if new == self.scan_session.expected_value:
            messagebox.showwarning(
                "点数还没有变化",
                "请先回到游戏购买或退款一次天赋，让右上角点数变化后再扫描。",
            )
            return
        previous_results = list(self.scan_session.results)
        pid = self.pid

        def work():
            def cb(done, total, found):
                self.queue.put(("progress", (done, total, found)))
            common, latest = refine_results(pid, previous_results, new,
                                            progress_cb=cb,
                                            cancel_event=self.cancel_event)
            self.queue.put(("refinement", (pid, common, latest, new)))

        self.run_async(work)

    def write_selected(self):
        if not self.scan_session.can_write:
            messagebox.showwarning(
                "还不能写入",
                "必须先完成至少一次有效的“数值变化后再次扫描”，且候选地址不超过 4 个。",
            )
            return
        if not self.ensure_ready():
            return
        if (not self.scan_session.can_write or
                not self.scan_session.belongs_to(self.pid)):
            messagebox.showwarning(
                "需要重新验证",
                "游戏进程或扫描状态已经变化，请重新完成首次扫描和再次扫描。",
            )
            return
        sel = self.get_selected_results()
        if not sel:
            messagebox.showwarning("提示", "请先在结果列表中勾选地址（默认全选）。")
            return
        if len(sel) > MAX_SAFE_CANDIDATES:
            messagebox.showwarning(
                "结果太多",
                f"勾选了 {len(sel)} 个地址，安全上限为 {MAX_SAFE_CANDIDATES} 个。\n"
                "请先在游戏里购买/退款一次天赋改变数值，再用“再次扫描”过滤。")
            return
        try:
            expected, target = validate_point_values(
                self.scan_session.expected_value, self.target_var.get())
        except ValueError as e:
            messagebox.showwarning("输入错误", str(e))
            return
        if not self.prepare_backup_before_write():
            return
        if not messagebox.askyesno(
                "确认写入",
                f"已二次验证 {len(sel)} 个地址。\n"
                f"预期当前值：{expected}\n目标值：{target}\n\n"
                "写入后请立刻回游戏：\n"
                "1) 找到右上角“已启用天赋”开关；\n"
                "2) 点一下关掉，再点一下打开（不花点数，仅刷新保存）；\n"
                "3) 确认右上角数字已变化后正常退出游戏。\n\n是否继续？"):
            return
        pid = self.pid

        def work():
            def cb(done, total, found):
                self.queue.put(("progress", (done, total, found)))
            result = write_value(
                pid,
                sel,
                new_value=target,
                expected_value=expected,
                progress_cb=cb,
                cancel_event=self.cancel_event,
            )
            self.queue.put(("write_done", result))

        self.run_async(work, operation="write")

    def on_close(self):
        if self.is_busy and self.active_operation == "write":
            messagebox.showwarning(
                "请等待写入完成",
                "正在写入并回读验证，请等待完成提示后再关闭程序。",
            )
            return
        self.cancel_event.set()
        self.destroy()

    def destroy(self):
        self.cancel_event.set()
        for timer in self.tk.splitlist(self.tk.call("after", "info")):
            self.after_cancel(timer)
        super().destroy()


def run_self_test():
    """Exercise the packaged Win32 read/write boundary without opening the GUI."""
    try:
        validate_point_values("694", str(MAX_SAFE_POINTS))
        probe = ctypes.c_longlong(694)
        with Win32ProcessMemory(os.getpid(), writable=True) as memory:
            result = safe_write_values(
                memory,
                [(ctypes.addressof(probe), "int64")],
                expected_value=694,
                new_value=MAX_SAFE_POINTS,
            )
        if not result.success or probe.value != MAX_SAFE_POINTS:
            return False, result.error or "内存写入后回读不一致"
        return True, f"AoE4_RogueTalentTool v{APP_VERSION} self-test OK"
    except Exception as exc:
        return False, f"self-test failed: {exc}"


def run_gui_smoke_test():
    """Create and render the GUI once, then close it without touching the game."""
    app = None
    try:
        app = App()
        app.withdraw()
        app.update_idletasks()
        expected_title = f"v{APP_VERSION}"
        if expected_title not in app.title():
            return False, "GUI title does not contain the packaged version"
        return True, f"AoE4_RogueTalentTool v{APP_VERSION} GUI smoke-test OK"
    except Exception as exc:
        return False, f"GUI smoke-test failed: {exc}"
    finally:
        if app is not None:
            app.destroy()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        ok, details = run_self_test()
        if sys.stdout is not None:
            print(details)
        return 0 if ok else 1
    if "--smoke-test" in args:
        ok, details = run_gui_smoke_test()
        if sys.stdout is not None:
            print(details)
        return 0 if ok else 1
    if "--version" in args:
        if sys.stdout is not None:
            print(APP_VERSION)
        return 0
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
