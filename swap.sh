#!/bin/bash
cd /home/claude/emb-research
until [ $(grep -l DONE logs/nRBe15c7_*.log 2>/dev/null | wc -l) = 10 ] && [ $(grep -l DONE logs/nRBe15e3_*.log 2>/dev/null | wc -l) = 10 ]; do sleep 10; done
pkill -f runq.sh; pkill -f "xargs -P2 -L1"; pkill -9 -f "python3 run_pit.py"; sleep 2
for f in logs/*_20*.log; do grep -q DONE $f || rm -f $f; done
exec ./runq.sh jobs_b5.txt jobs_b3.txt jobs_b1.txt
