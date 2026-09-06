"""Dependency-free host and process resource telemetry for the local dashboard."""
from __future__ import annotations

import copy
import ctypes
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class ResourceMonitor:
    """Collect comparable CPU and RAM measurements without a third-party package.

    CPU values are normalized to the machine's total logical CPU capacity, so both
    the host and this Python process are reported on a 0-100% scale.
    """

    def __init__(self, min_sample_interval_seconds: float = 0.5) -> None:
        self._lock = threading.Lock()
        self._min_sample_interval_seconds = max(0.0, min_sample_interval_seconds)
        self._last_sample_monotonic: Optional[float] = None
        self._last_process_cpu_time: Optional[float] = None
        self._last_system_cpu: Optional[Tuple[int, int]] = None
        self._cached: Optional[Dict[str, Any]] = None

    @staticmethod
    def _clamp_percent(value: float) -> float:
        return round(max(0.0, min(100.0, value)), 1)

    @staticmethod
    def _filetime_to_int(value: _FileTime) -> int:
        return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

    def _read_system_cpu_counters(self) -> Optional[Tuple[int, int]]:
        """Return (busy, total) CPU ticks from the operating system."""
        try:
            if sys.platform == "win32":
                idle = _FileTime()
                kernel = _FileTime()
                user = _FileTime()
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                get_system_times = kernel32.GetSystemTimes
                get_system_times.argtypes = [
                    ctypes.POINTER(_FileTime),
                    ctypes.POINTER(_FileTime),
                    ctypes.POINTER(_FileTime),
                ]
                get_system_times.restype = ctypes.c_bool
                if not get_system_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                    return None
                total = self._filetime_to_int(kernel) + self._filetime_to_int(user)
                busy = total - self._filetime_to_int(idle)
                return busy, total

            if os.path.exists("/proc/stat"):
                with open("/proc/stat", "r", encoding="utf-8") as stat_file:
                    fields = stat_file.readline().split()
                if not fields or fields[0] != "cpu":
                    return None
                ticks = [int(value) for value in fields[1:]]
                if len(ticks) < 4:
                    return None
                total = sum(ticks)
                idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
                return total - idle, total
        except (OSError, ValueError, AttributeError):
            return None
        return None

    def _sample_system_cpu_percent(self) -> Optional[float]:
        current = self._read_system_cpu_counters()
        previous = self._last_system_cpu
        self._last_system_cpu = current
        if current is None or previous is None:
            return None
        busy_delta = current[0] - previous[0]
        total_delta = current[1] - previous[1]
        if total_delta <= 0:
            return None
        return self._clamp_percent((busy_delta / total_delta) * 100.0)

    def _sample_process_cpu_percent(self, now_monotonic: float) -> Optional[float]:
        current_process_cpu = time.process_time()
        previous_cpu = self._last_process_cpu_time
        previous_wall = self._last_sample_monotonic
        self._last_process_cpu_time = current_process_cpu
        if previous_cpu is None or previous_wall is None:
            return None
        wall_elapsed = now_monotonic - previous_wall
        if wall_elapsed <= 0:
            return None
        logical_cpus = max(1, os.cpu_count() or 1)
        used = (current_process_cpu - previous_cpu) / wall_elapsed
        return self._clamp_percent((used / logical_cpus) * 100.0)

    @staticmethod
    def _read_linux_memory() -> Tuple[Optional[int], Optional[int]]:
        try:
            fields: Dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as memory_file:
                for line in memory_file:
                    key, value = line.split(":", 1)
                    fields[key] = int(value.strip().split()[0]) * 1024
            total = fields.get("MemTotal")
            available = fields.get("MemAvailable")
            if available is None:
                available = sum(fields.get(key, 0) for key in ("MemFree", "Buffers", "Cached"))
            return total, available
        except (OSError, ValueError, IndexError):
            return None, None

    @staticmethod
    def _read_windows_memory() -> Tuple[Optional[int], Optional[int]]:
        try:
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            global_memory_status = kernel32.GlobalMemoryStatusEx
            global_memory_status.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
            global_memory_status.restype = ctypes.c_bool
            if not global_memory_status(ctypes.byref(status)):
                return None, None
            return int(status.ullTotalPhys), int(status.ullAvailPhys)
        except (AttributeError, OSError):
            return None, None

    def _read_system_memory(self) -> Tuple[Optional[int], Optional[int]]:
        if sys.platform == "win32":
            return self._read_windows_memory()
        if os.path.exists("/proc/meminfo"):
            return self._read_linux_memory()
        return None, None

    @staticmethod
    def _read_windows_process_rss() -> Optional[int]:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            get_process_memory_info = psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_ProcessMemoryCountersEx),
                ctypes.c_ulong,
            ]
            get_process_memory_info.restype = ctypes.c_bool
            counters = _ProcessMemoryCountersEx()
            counters.cb = ctypes.sizeof(_ProcessMemoryCountersEx)
            if not get_process_memory_info(
                kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                return None
            return int(counters.WorkingSetSize)
        except (AttributeError, OSError):
            return None

    @staticmethod
    def _read_linux_process_rss() -> Optional[int]:
        try:
            with open("/proc/self/statm", "r", encoding="utf-8") as stat_file:
                fields = stat_file.readline().split()
            if len(fields) < 2:
                return None
            return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, AttributeError):
            return None

    def _read_process_rss(self) -> Optional[int]:
        if sys.platform == "win32":
            return self._read_windows_process_rss()
        if os.path.exists("/proc/self/statm"):
            return self._read_linux_process_rss()
        return None

    def snapshot(self, *, force: bool = False) -> Dict[str, Any]:
        """Return a cached sample unless enough time elapsed to produce useful CPU rates."""
        now_monotonic = time.monotonic()
        with self._lock:
            if (
                not force
                and self._cached is not None
                and self._last_sample_monotonic is not None
                and now_monotonic - self._last_sample_monotonic < self._min_sample_interval_seconds
            ):
                return copy.deepcopy(self._cached)

            previous_sample_at = self._last_sample_monotonic
            system_cpu_percent = self._sample_system_cpu_percent()
            process_cpu_percent = self._sample_process_cpu_percent(now_monotonic)
            total_memory, available_memory = self._read_system_memory()
            used_memory = (
                max(0, total_memory - available_memory)
                if total_memory is not None and available_memory is not None
                else None
            )
            process_rss = self._read_process_rss()
            memory_percent = (
                self._clamp_percent((used_memory / total_memory) * 100.0)
                if used_memory is not None and total_memory
                else None
            )
            process_memory_percent = (
                round((process_rss / total_memory) * 100.0, 3)
                if process_rss is not None and total_memory
                else None
            )
            sample_interval_ms = (
                round((now_monotonic - previous_sample_at) * 1000.0, 1)
                if previous_sample_at is not None
                else None
            )
            self._last_sample_monotonic = now_monotonic
            self._cached = {
                "sampled_at": datetime.now(timezone.utc).isoformat(),
                "sample_interval_ms": sample_interval_ms,
                "logical_cpu_count": max(1, os.cpu_count() or 1),
                "system_cpu_percent": system_cpu_percent,
                "process_cpu_percent": process_cpu_percent,
                "system_memory_total_bytes": total_memory,
                "system_memory_used_bytes": used_memory,
                "system_memory_available_bytes": available_memory,
                "system_memory_percent": memory_percent,
                "process_memory_rss_bytes": process_rss,
                "process_memory_percent": process_memory_percent,
            }
            return copy.deepcopy(self._cached)
