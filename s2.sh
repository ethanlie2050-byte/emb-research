#!/bin/bash
cd "$(dirname "$0")"
for m in "$@"; do
 l=$(python3 cmp.py $m 2>/dev/null | tail -1); cr=$(python3 stress.py $m 2>/dev/null | grep "10-Oct" | grep -o "loss -[0-9]*%" | grep -o "\-[0-9]*%")
 echo "$l" | awk -v m=$m -v cr="$cr" -F'|' '{split($2,a," "); split($3,r," "); rc=r[1]; rd=r[2]; gsub("%","",rc); gsub("%","",rd); printf "%-8s real %s%% / %s%% (Cal %.1f) | backtest %s %s Cal %s | crash %s | %s\n", m, rc, rd, -rc/rd, a[1], a[2], a[3], cr, $6}'
done
