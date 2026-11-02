#!/usr/bin/env python3
"""Install a pinned official exporter archive, verifying its SHA-256."""
import hashlib
import io
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.16.0"
ASSETS = {
    ("Darwin", "arm64"): ("darwin_x86_64", "3afd9bc6138ab66cfe2d08e8db4c43c5a602e825cfa7be3edb1585870e1559c3"),
    ("Darwin", "x86_64"): ("darwin_x86_64", "3afd9bc6138ab66cfe2d08e8db4c43c5a602e825cfa7be3edb1585870e1559c3"),
    ("Linux", "x86_64"): ("linux_x86_64", "cbb01772167bc17dfb5f2c8b8275df4aa266dc5bd8e612eae59d8ea441d543c3"),
    ("Linux", "aarch64"): ("linux_arm64", "0ef05270017626ac1da3d45d57afdd819c39c0f53de4e6145bad3ee8884389b1"),
}


def custom_exporter_path():
    """Return an explicitly supplied exporter binary, if the user chose one."""
    value = os.environ.get("GPU_MONITOR_EXPORTER")
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_file():
        raise RuntimeError("GPU_MONITOR_EXPORTER does not name a regular file: " + str(path))
    return path


def exporter_path():
    """Return the executable path selected by install() and inherited by server.py."""
    return custom_exporter_path() or ROOT / ".tools" / "nvidia_gpu_exporter"


def asset_for_current_platform():
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        raise RuntimeError("no official exporter build is registered for %s/%s; supported: %s"
                           % (key[0], key[1], ", ".join("/".join(item) for item in ASSETS)))
    return ASSETS[key]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode):
    """Write a private cache file without ever leaving a partial target behind."""
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def archive_members(archive):
    """Read only the two exact regular files expected from the release archive."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        result = {}
        for name in ("nvidia_gpu_exporter", "LICENSE"):
            member = tar.getmember(name)
            if not member.isfile():
                raise RuntimeError("Unexpected archive member: " + name)
            handle = tar.extractfile(member)
            if handle is None:
                raise RuntimeError("Could not read archive member: " + name)
            result[name] = handle.read()
    return result


def load_verified_archive(cache, flavor, expected):
    archive_path = cache / ("nvidia_gpu_exporter-" + VERSION + "-" + flavor + ".tar.gz")
    if archive_path.exists():
        archive = archive_path.read_bytes()
        if sha256(archive) == expected:
            return archive
        # A corrupt cache is never executed or extracted; refresh it below.
        archive_path.unlink()
    url = "https://github.com/utkuozdemir/nvidia_gpu_exporter/releases/download/v%s/nvidia_gpu_exporter_%s_%s.tar.gz" % (VERSION, VERSION, flavor)
    print("Downloading official nvidia_gpu_exporter v" + VERSION, flush=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read()
    if sha256(archive) != expected:
        raise RuntimeError("SHA-256 checksum mismatch for the downloaded archive")
    atomic_write(archive_path, archive, 0o600)
    return archive


def install():
    supplied = custom_exporter_path()
    if supplied:
        subprocess.run([str(supplied), "--version"], check=True)
        return supplied
    cache = ROOT / ".tools"
    cache.mkdir(mode=0o700, exist_ok=True)
    cache.chmod(0o700)
    target = cache / "nvidia_gpu_exporter"
    try:
        flavor, expected = asset_for_current_platform()
    except RuntimeError:
        # Retain the documented escape hatch for an architecture for which the
        # pinned upstream release has no asset: the user may supply this one
        # private executable themselves.
        if target.exists():
            subprocess.run([str(target), "--version"], check=True)
            return target
        raise
    archive = load_verified_archive(cache, flavor, expected)
    members = archive_members(archive)
    # Compare with the binary extracted from the already verified archive on
    # every start; a truncated or altered cached executable is replaced.
    if not target.exists() or sha256(target.read_bytes()) != sha256(members["nvidia_gpu_exporter"]):
        atomic_write(target, members["nvidia_gpu_exporter"], 0o755)
    license_path = cache / "LICENSE"
    if not license_path.exists() or license_path.read_bytes() != members["LICENSE"]:
        atomic_write(license_path, members["LICENSE"], 0o600)
    subprocess.run([str(target), "--version"], check=True)
    return target


if __name__ == "__main__":
    install()
