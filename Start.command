#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
python3 scripts/manage.py start --open
if [[ $? -ne 0 ]]; then
  read "?Startup failed; press Enter to close this window..."
fi
