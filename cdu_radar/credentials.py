"""Current-user Windows DPAPI storage. Never used by public HTTP readers."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Windows DPAPI credential requires Windows; use the key environment variable elsewhere')
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('DPAPI credential operation failed')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def save_key(path, key):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not key.strip():
        raise ValueError('Invalid credential destination')
    encrypted = _crypt(key.strip().encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(encrypted)
    os.replace(temporary, path)


def read_key(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Credential file unavailable')
    return _crypt(path.read_bytes(), decrypt=True).decode().strip()
