#!/usr/bin/env python3
"""Remove selected/all remote recorders and their history, restoring pre-install lingering."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.reset_usage import main

if __name__ == '__main__':
    import sys
    try:
        main('rollback')
    except (RuntimeError, ValueError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
