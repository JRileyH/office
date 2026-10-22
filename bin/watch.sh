#!/usr/bin/env bash
# What office is doing: `bin/watch.sh` on the box, in the office checkout. Shows the
# queue, then each phase step by step as it runs; stops when nothing more is about to
# run (Ctrl-C stops sooner). `bin/watch.sh JOB` prints everything one job's runs did,
# start to finish: `bin/watch.sh 3 > job3.txt` to keep or share it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -t 1 ]]; then
  exec docker compose exec office python -m office.watch "$@"
else
  # No terminal to draw on (redirected or piped): plain output, no stray carriage returns.
  exec docker compose exec -T office python -m office.watch "$@"
fi
