"""Windows named-pipe transport with an explicit AppContainer SID ACL.

No pickle or object deserialization is used. Messages are bounded UTF-8 JSON
lines. This module is a transport primitive; it is not wired into production
worker launch until the AppContainer worker lifecycle is integrated.
"""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes


MAX_PIPE_MESSAGE_BYTES = 64 * 1024
ERROR_PIPE_CONNECTED = 535
ERROR_BROKEN_PIPE = 109
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class NamedPipeUnavailable(RuntimeError):
    pass


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _apis():
    if os.name != "nt":
        raise NamedPipeUnavailable("Windows named pipes are available only on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    ]
    kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    return kernel32, advapi32


def _read_line(kernel32, handle, limit: int) -> bytes:
    result = bytearray()
    while len(result) <= limit:
        buffer = ctypes.create_string_buffer(4096)
        read = wintypes.DWORD(0)
        if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
            error = ctypes.get_last_error()
            if error in (ERROR_BROKEN_PIPE, 0):
                break
            raise OSError(error, "ReadFile on named pipe failed")
        if read.value == 0:
            break
        chunk = buffer.raw[:read.value]
        result.extend(chunk)
        if b"\n" in chunk:
            return bytes(result).split(b"\n", 1)[0] + b"\n"
    if len(result) > limit:
        raise ValueError("named-pipe message exceeds the configured size limit")
    if result:
        raise ValueError("named-pipe message is not newline terminated")
    return b""


def _write_all(kernel32, handle, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = wintypes.DWORD(0)
        chunk = payload[offset:]
        buffer = ctypes.create_string_buffer(chunk)
        if not kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            raise OSError(ctypes.get_last_error(), "WriteFile on named pipe failed")
        if written.value <= 0:
            raise OSError("named-pipe write returned zero bytes")
        offset += written.value


class WindowsNamedPipeServer:
    """Single-instance-at-a-time named-pipe server scoped to host and package SID."""

    def __init__(self, pipe_name: str, appcontainer_sid: str, on_message):
        if not pipe_name.startswith(r"\\.\pipe\"):
            raise ValueError("pipe_name must use the local named-pipe namespace")
        if not appcontainer_sid.startswith("S-1-15-2-"):
            raise ValueError("an AppContainer package SID is required")
        self.pipe_name = pipe_name
        self.appcontainer_sid = appcontainer_sid
        self.on_message = on_message
        self._stop = threading.Event()
        self._thread = None
        self._error = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("named pipe server already started")
        self._thread = threading.Thread(target=self._serve, name="PluginNamedPipeBroker", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        kernel32, advapi32 = _apis()
        # Owner Rights lets the host process access its own pipe; SYSTEM retains
        # management access. Only the designated AppContainer SID gets client
        # read/write access. Remote clients are rejected at pipe creation.
        sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;OW)(A;;GRGW;;;{self.appcontainer_sid})"
        descriptor = ctypes.c_void_p()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None,
        ):
            self._error = OSError(ctypes.get_last_error(), "Could not create named-pipe security descriptor")
            return
        security = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
        try:
            while not self._stop.is_set():
                handle = kernel32.CreateNamedPipeW(
                    self.pipe_name,
                    0x00000003,  # PIPE_ACCESS_DUPLEX
                    0x00000000 | 0x00000000 | 0x00000000 | 0x00000008,  # byte mode, blocking, reject remote
                    1, MAX_PIPE_MESSAGE_BYTES, MAX_PIPE_MESSAGE_BYTES, 5000,
                    ctypes.byref(security),
                )
                if handle == INVALID_HANDLE_VALUE:
                    self._error = OSError(ctypes.get_last_error(), "CreateNamedPipeW failed")
                    return
                try:
                    connected = kernel32.ConnectNamedPipe(handle, None)
                    if not connected and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
                        if self._stop.is_set():
                            return
                        continue
                    if self._stop.is_set():
                        return
                    try:
                        request = _read_line(kernel32, handle, MAX_PIPE_MESSAGE_BYTES)
                        response = self.on_message(request) if request else b'{"ok":false,"error_code":"empty_request"}\n'
                        if not isinstance(response, bytes):
                            raise TypeError("named-pipe handler must return bytes")
                        if len(response) > MAX_PIPE_MESSAGE_BYTES:
                            response = b'{"ok":false,"error_code":"response_too_large"}\n'
                        if not response.endswith(b"\n"):
                            response += b"\n"
                        _write_all(kernel32, handle, response)
                        kernel32.FlushFileBuffers(handle)
                    except Exception as error:
                        self._error = error
                        try:
                            _write_all(kernel32, handle, b'{"ok":false,"error_code":"broker_transport_error"}\n')
                        except Exception:
                            pass
                finally:
                    kernel32.DisconnectNamedPipe(handle)
                    kernel32.CloseHandle(handle)
        finally:
            kernel32.LocalFree(descriptor)

    def close(self) -> None:
        self._stop.set()
        kernel32, _ = _apis()
        # Connect once to wake a blocking ConnectNamedPipe call. The server
        # notices the stop flag and closes this wake-up instance without dispatch.
        if self._thread is not None and self._thread.is_alive():
            handle = kernel32.CreateFileW(
                self.pipe_name, 0xC0000000, 0, None, 3, 0, None,
            )
            if handle != INVALID_HANDLE_VALUE:
                _write_all(kernel32, handle, b"\n")
                kernel32.CloseHandle(handle)
            self._thread.join(timeout=3)
        self._thread = None


def send_named_pipe_message(pipe_name: str, payload: bytes, timeout_ms: int = 5000) -> bytes:
    """Send one bounded newline-delimited message without pickle."""
    kernel32, _ = _apis()
    if not payload.endswith(b"\n") or len(payload) > MAX_PIPE_MESSAGE_BYTES:
        raise ValueError("payload must be a bounded newline-terminated message")
    if not kernel32.WaitNamedPipeW(pipe_name, max(1, int(timeout_ms))):
        raise OSError(ctypes.get_last_error(), "WaitNamedPipeW failed")
    handle = kernel32.CreateFileW(pipe_name, 0xC0000000, 0, None, 3, 0, None)
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateFileW named pipe failed")
    try:
        _write_all(kernel32, handle, payload)
        return _read_line(kernel32, handle, MAX_PIPE_MESSAGE_BYTES)
    finally:
        kernel32.CloseHandle(handle)
