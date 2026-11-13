#!/usr/bin/env python3
"""Forward the exporter's nvidia-smi arguments to a configured SSH host."""
import argparse
import os
import shlex

DEFAULT_CONNECT_TIMEOUT = 15


def ssh_options(control_path, multiplex=True, connect_timeout=DEFAULT_CONNECT_TIMEOUT):
    options = ["-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}",
               "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]
    if not multiplex:
        return options + ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
    return options + ["-o", "ControlMaster=auto", "-o", "ControlPersist=60",
                      "-o", "ControlPath=" + control_path]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect-timeout", type=int, default=DEFAULT_CONNECT_TIMEOUT)
    parser.add_argument("host")
    parser.add_argument("control_path")
    parser.add_argument("smi_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.execvp("ssh", ["ssh", *ssh_options(args.control_path, connect_timeout=args.connect_timeout),
                      args.host, shlex.join(["nvidia-smi", *args.smi_args])])
