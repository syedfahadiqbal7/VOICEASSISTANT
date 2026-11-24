"""Windows AppContainer proof-of-concept helpers.

This module is deliberately not wired into production plugin execution yet.
It probes OS-enforced package isolation using a disposable profile and temporary
ACL grants. A passing probe is not sufficient to sandbox the Python plugin
runtime; its executable, DLLs, standard library and approved package files still
need a carefully managed AppContainer-readable runtime.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import uuid
from ctypes import wintypes
from pathlib import Path


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
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise AppContainerUnavailable("icacls could not grant temporary AppContainer access")


def _remove_acl(path: Path, sid_text: str) -> None:
    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    subprocess.run(
        [str(icacls), str(path), "/remove:g", f"*{sid_text}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )


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
    limits.BasicLimitInformation.ActiveProcessLimit = 16
    limits.ProcessMemoryLimit = 2 * 1024 * 1024 * 1024
    limits.JobMemoryLimit = 3 * 1024 * 1024 * 1024
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
) -> int:
    """Run one process in a no-capability AppContainer; return its exit code.

    No network capabilities are added. Callers must explicitly grant AppContainer
    access to the executable and any required input/output paths. This helper does
    not automatically broaden ACLs on application or user data directories.
    """
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

        wait_result = kernel32.WaitForSingleObject(
            process_info.hProcess, max(1, int(timeout_seconds)) * 1000,
        )
        if wait_result == 0x00000102:  # WAIT_TIMEOUT
            kernel32.TerminateJobObject(job_handle, 124)
            kernel32.WaitForSingleObject(process_info.hProcess, 5000)
            raise TimeoutError("AppContainer process timed out")
        if wait_result != 0:
            kernel32.TerminateJobObject(job_handle, 125)
            kernel32.WaitForSingleObject(process_info.hProcess, 5000)
            raise OSError(ctypes.get_last_error(), "WaitForSingleObject failed")
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
