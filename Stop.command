#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
python3 scripts/manage.py stop
if [[ $? -ne 0 ]]; then
  read "?Stop failed; press Enter to close this window..."
fi
