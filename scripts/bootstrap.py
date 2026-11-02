#!/usr/bin/env python3
"""Install a pinned official exporter archive, verifying its SHA-256."""
import hashlib
import io
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.16.0"
ASSETS = {
    ("Darwin", "arm64"): ("darwin_x86_64", "3afd9bc6138ab66cfe2d08e8db4c43c5a602e825cfa7be3edb1585870e1559c3"),
    ("Darwin", "x86_64"): ("darwin_x86_64", "3afd9bc6138ab66cfe2d08e8db4c43c5a602e825cfa7be3edb1585870e1559c3"),
    ("Linux", "x86_64"): ("linux_x86_64", "cbb01772167bc17dfb5f2c8b8275df4aa266dc5bd8e612eae59d8ea441d543c3"),
    ("Linux", "aarch64"): ("linux_arm64", "0ef05270017626ac1da3d45d57afdd819c39c0f53de4e6145bad3ee8884389b1"),
}


def install():
    target = ROOT / ".tools" / "nvidia_gpu_exporter"
    if target.exists():
        subprocess.run([str(target), "--version"], check=True)
        return
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        raise RuntimeError("no official exporter build is registered for %s/%s; supported: %s"
                           % (key[0], key[1], ", ".join("/".join(item) for item in ASSETS)))
    flavor, expected = ASSETS[key]
    url = "https://github.com/utkuozdemir/nvidia_gpu_exporter/releases/download/v%s/nvidia_gpu_exporter_%s_%s.tar.gz" % (VERSION, VERSION, flavor)
    print("Downloading official nvidia_gpu_exporter v" + VERSION, flush=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != expected:
        raise RuntimeError("SHA-256 checksum mismatch for the downloaded archive")
    target.parent.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for name in ("nvidia_gpu_exporter", "LICENSE"):
            member = tar.getmember(name)
            if not member.isfile():
                raise RuntimeError("Unexpected archive member: " + name)
            (target.parent / name).write_bytes(tar.extractfile(member).read())
    target.chmod(0o755)
    subprocess.run([str(target), "--version"], check=True)


if __name__ == "__main__":
    install()
