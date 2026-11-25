"""Windows AppContainer helpers for the explicit per-plugin Windows pilot.

The ToolRegistry uses these helpers only for plugins whose verified manifest
selects ``execution_sandbox: appcontainer``. The launcher creates a no-capability
AppContainer process and requires callers to grant narrowly scoped ACLs to its
runtime, staged source and private IPC paths. This is not a blanket sandbox for
plugins that remain on the default same-user worker.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
import uuid
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger("voiceassistant.appcontainer")


class AppContainerUnavailable(RuntimeError):
    pass


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.c_void_p),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFO), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _windows_apis():
    if os.name != "nt":
        raise AppContainerUnavailable("AppContainer is available only on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]

    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]

    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]

    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32, userenv, advapi32


def _create_profile(userenv, profile_name: str):
    sid = ctypes.c_void_p()
    hr = userenv.CreateAppContainerProfile(
        profile_name, "VoiceAssistant temporary plugin probe",
        "Disposable AppContainer profile for a security regression test",
        None, 0, ctypes.byref(sid),
    )
    if hr < 0 or not sid.value:
        raise AppContainerUnavailable(f"CreateAppContainerProfile failed: 0x{hr & 0xffffffff:08x}")
    return sid


def _sid_string(kernel32, advapi32, sid) -> str:
    value = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(value)):
        raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
    try:
        return value.value
    finally:
        kernel32.LocalFree(value)


def _grant_acl(path: Path, sid_text: str, permission: str) -> None:
    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    result = subprocess.run(
        [str(icacls), str(path), "/grant", f"*{sid_text}:{permission}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise AppContainerUnavailable("icacls could not grant temporary AppContainer access")


def _remove_acl(path: Path, sid_text: str) -> bool:
    """Best-effort ACL cleanup that never prevents later profile/temp cleanup."""
    target = Path(path)
    if not target.exists():
        return True
    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    for attempt in range(2):
        try:
            result = subprocess.run(
                [str(icacls), str(target), "/remove:g", f"*{sid_text}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        if attempt == 0:
            time.sleep(0.1)
    logger.error(
        "Could not remove temporary AppContainer ACL",
        extra={"event": "appcontainer.acl_cleanup_failed", "path": str(target)},
    )
    return False


def _workspace_exceeds_limits(
    directory: Path,
    *,
    max_bytes: int,
    max_files: int,
    max_file_bytes: int,
) -> bool:
    """Bound AppContainer-writable workspace bytes and file count.

    The worker workspace is deliberately flat. Unexpected directories, links,
    unreadable entries or missing workspace paths fail closed.
    """
    total_bytes = 0
    file_count = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                file_count += 1
                if file_count > max_files or not entry.is_file(follow_symlinks=False):
                    return True
                size = entry.stat(follow_symlinks=False).st_size
                if size < 0 or size > max_file_bytes:
                    return True
                total_bytes += size
                if total_bytes > max_bytes:
                    return True
    except OSError:
        return True
    return False


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOB_BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOB_EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOB_BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _assign_bounded_job(kernel32, process_handle):
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = _JOB_EXTENDED_LIMIT()
    limits.BasicLimitInformation.LimitFlags = 0x00000008 | 0x00000100 | 0x00000200 | 0x00002000
    limits.BasicLimitInformation.ActiveProcessLimit = 8
    limits.ProcessMemoryLimit = 512 * 1024 * 1024
    limits.JobMemoryLimit = 1024 * 1024 * 1024
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")
    if not kernel32.AssignProcessToJobObject(job, process_handle):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "AssignProcessToJobObject failed")
    return job


def run_appcontainer_command(
    executable: Path,
    arguments: list[str],
    profile_name: str | None = None,
    timeout_seconds: int = 15,
    appcontainer_sid=None,
    environment: dict[str, str] | None = None,
    monitored_directory: Path | None = None,
    max_workspace_bytes: int = 2 * 1024 * 1024,
    max_workspace_files: int = 3,
    max_workspace_file_bytes: int = 1024 * 1024,
) -> int:
    """Run one process in a no-capability AppContainer; return its exit code.

    No network capabilities are added. Callers must explicitly grant AppContainer
    access to the executable and any required input/output paths. This helper does
    not automatically broaden ACLs on application or user data directories.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be an integer from 1 to 120")
    kernel32, userenv, advapi32 = _windows_apis()
    profile_name = profile_name or ("VoiceAssistant.Probe." + uuid.uuid4().hex)
    owns_profile = appcontainer_sid is None
    sid = _create_profile(userenv, profile_name) if owns_profile else appcontainer_sid
    attr_buffer = None
    attr_list = None
    process_info = _PROCESS_INFORMATION()
    job_handle = None
    environment_buffer = None
    try:
        capabilities = _SECURITY_CAPABILITIES(sid, None, 0, 0)
        required = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(required))
        attr_buffer = ctypes.create_string_buffer(required.value)
        candidate_attr_list = ctypes.cast(attr_buffer, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(candidate_attr_list, 1, 0, ctypes.byref(required)):
            raise OSError(ctypes.get_last_error(), "InitializeProcThreadAttributeList failed")
        attr_list = candidate_attr_list
        attribute_security_capabilities = 0x00020009
        if not kernel32.UpdateProcThreadAttribute(
            attr_list, 0, attribute_security_capabilities,
            ctypes.byref(capabilities), ctypes.sizeof(capabilities), None, None,
        ):
            raise OSError(ctypes.get_last_error(), "UpdateProcThreadAttribute failed")

        startup = _STARTUPINFOEX()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEX)
        startup.lpAttributeList = attr_list
        command_line = subprocess.list2cmdline([str(executable), *[str(x) for x in arguments]])
        mutable_command = ctypes.create_unicode_buffer(command_line)
        flags = 0x00080000 | 0x08000000 | 0x00000004  # EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_SUSPENDED
        environment_pointer = None
        if environment is not None:
            pairs = []
            for key, value in environment.items():
                key = str(key)
                value = str(value)
                if not key or "=" in key or "\x00" in key or "\x00" in value:
                    raise ValueError("invalid environment variable")
                pairs.append((key, value))
            # Preserve Windows hidden per-drive current-directory entries.
            # Omitting =C:=... from a custom block can make CreateProcessW fail
            # with ERROR_ENVVAR_NOT_FOUND (203) on hosted Windows runners.
            current_directory = os.getcwd()
            kernel32.GetEnvironmentStringsW.restype = ctypes.c_void_p
            kernel32.FreeEnvironmentStringsW.argtypes = [ctypes.c_void_p]
            environment_address = kernel32.GetEnvironmentStringsW()
            if not environment_address:
                raise OSError(ctypes.get_last_error(), "GetEnvironmentStringsW failed")
            special_entries = []
            try:
                characters = ctypes.cast(
                    environment_address, ctypes.POINTER(ctypes.c_wchar)
                )
                current_entry = []
                for index in range(32767):
                    character = characters[index]
                    if character == "\x00":
                        if not current_entry:
                            break
                        entry = "".join(current_entry)
                        if entry.startswith("="):
                            special_entries.append(entry)
                        current_entry = []
                    else:
                        current_entry.append(character)
            finally:
                kernel32.FreeEnvironmentStringsW(environment_address)
            drive, _ = os.path.splitdrive(current_directory)
            drive_key = f"={drive}"
            if drive and not any(item.startswith(drive_key + "=") for item in special_entries):
                special_entries.append(f"={drive}={current_directory}")
            special_entries.sort(key=str.upper)
            ordinary_entries = [
                f"{key}={value}"
                for key, value in sorted(pairs, key=lambda item: item[0].upper())
            ]
            block = "\x00".join(special_entries + ordinary_entries) + "\x00\x00"
            environment_buffer = ctypes.create_unicode_buffer(block)
            environment_pointer = ctypes.cast(environment_buffer, ctypes.c_void_p)
            flags |= 0x00000400  # CREATE_UNICODE_ENVIRONMENT
        if not kernel32.CreateProcessW(
            str(executable), mutable_command, None, None, False, flags,
            environment_pointer, None, ctypes.byref(startup), ctypes.byref(process_info),
        ):
            raise OSError(ctypes.get_last_error(), "CreateProcessW AppContainer launch failed")
        job_handle = _assign_bounded_job(kernel32, process_info.hProcess)
        resume_result = kernel32.ResumeThread(process_info.hThread)
        if resume_result == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread failed")

        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kernel32.TerminateJobObject(job_handle, 124)
                kernel32.WaitForSingleObject(process_info.hProcess, 5000)
                raise TimeoutError("AppContainer process timed out")
            wait_result = kernel32.WaitForSingleObject(
                process_info.hProcess, min(50, max(1, int(remaining * 1000))),
            )
            if wait_result == 0:
                break
            if wait_result != 0x00000102:  # WAIT_TIMEOUT
                kernel32.TerminateJobObject(job_handle, 125)
                kernel32.WaitForSingleObject(process_info.hProcess, 5000)
                raise OSError(ctypes.get_last_error(), "WaitForSingleObject failed")
            if monitored_directory is not None and _workspace_exceeds_limits(
                Path(monitored_directory),
                max_bytes=max_workspace_bytes,
                max_files=max_workspace_files,
                max_file_bytes=max_workspace_file_bytes,
            ):
                kernel32.TerminateJobObject(job_handle, 127)
                kernel32.WaitForSingleObject(process_info.hProcess, 5000)
                raise OSError("AppContainer writable workspace exceeded its size/file limit")
        # Check once more after process exit so a very fast final write cannot
        # escape the polling interval immediately before termination.
        if monitored_directory is not None and _workspace_exceeds_limits(
            Path(monitored_directory),
            max_bytes=max_workspace_bytes,
            max_files=max_workspace_files,
            max_file_bytes=max_workspace_file_bytes,
        ):
            raise OSError("AppContainer writable workspace exceeded its size/file limit")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
        return int(exit_code.value)
    finally:
        if process_info.hProcess and job_handle is None:
            kernel32.TerminateProcess(process_info.hProcess, 126)
            kernel32.WaitForSingleObject(process_info.hProcess, 5000)
        if job_handle:
            kernel32.CloseHandle(job_handle)  # KILL_ON_JOB_CLOSE terminates any surviving descendants.
        if process_info.hThread:
            kernel32.CloseHandle(process_info.hThread)
        if process_info.hProcess:
            kernel32.CloseHandle(process_info.hProcess)
        if attr_list:
            kernel32.DeleteProcThreadAttributeList(attr_list)
        if owns_profile:
            userenv.DeleteAppContainerProfile(profile_name)
            if sid:
                advapi32.FreeSid(sid)
