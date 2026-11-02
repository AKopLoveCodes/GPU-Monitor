#!/usr/bin/env python3
"""Forward the exporter's nvidia-smi arguments to a configured SSH host."""
import os
import shlex
import sys


def ssh_options(control_path, multiplex=True):
    options = ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
               "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]
    if not multiplex:
        return options + ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
    return options + ["-o", "ControlMaster=auto", "-o", "ControlPersist=60",
                      "-o", "ControlPath=" + control_path]


if __name__ == "__main__":
    host, control_path = sys.argv[1:3]
    os.execvp("ssh", ["ssh", *ssh_options(control_path), host,
                      shlex.join(["nvidia-smi", *sys.argv[3:]])])
