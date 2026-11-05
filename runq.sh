#!/bin/bash
# runs queue files in order: runq.sh f1 f2 ...
cd "$(dirname "$0")"
for f in "$@"; do cat $f | xargs -P2 -L1 ./job4.sh; echo "$f finished $(date +%T)"; done
