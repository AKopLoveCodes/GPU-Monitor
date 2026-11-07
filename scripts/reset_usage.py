#!/usr/bin/env python3
"""Clear selected/all remote history while keeping continuous recording enabled."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.manage import agent_command


def main(action='clear-data'):
    parser = argparse.ArgumentParser(description=__doc__ if action == 'clear-data' else 'Remove remote recorders and history; restore lingering changed by this installation.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--all', action='store_true', help='apply to all configured servers')
    group.add_argument('--servers', nargs='+', help='configured server names or SSH aliases')
    args = parser.parse_args()
    agent_command([action, *(args.servers or [])])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
