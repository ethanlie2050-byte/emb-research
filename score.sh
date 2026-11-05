#!/bin/bash
# score.sh NAME... -> one compact line each
cd "$(dirname "$0")"
for m in "$@"; do
  c=$(python3 cmp.py $m 2>/dev/null | tail -2 | tr '\n' ' ')
  s=$(python3 stress.py $m 2>/dev/null | grep "10-Oct" | grep -o "loss -[0-9]*%")
  echo "$c | crash $s" | awk -v m=$m '{print}' | sed -E 's/ +/ /g; s/version.*\|//'
done
