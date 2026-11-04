#!/bin/bash
# usage: ./run_worker.sh w1    (runs jobs_w1.txt, pushes results to branch results-w1)
set -u; W=$1; cd "$(dirname "$0")"
pip install -q --break-system-packages pandas==3.0.5 numpy==2.5.3 pyarrow zstandard 2>&1 | tail -1
mkdir -p logs
C=$(DIS=E2,E4 NOTREND=1 NOEUPH=1 UNIVF=$PWD/univ_mcap.pkl CAP=1.5 PRE=0D MODE=cal S=2024-03-01 E=2024-03-08 python3 run_pit.py 2>&1 | grep DONE)
echo "calibration: $C"
case "$C" in *"equity 14301.99"*) echo "CALIBRATION OK";; *) echo "CALIBRATION FAILED - stopping"; exit 2;; esac
echo "cores: $(nproc)"
xargs -P $(nproc) -L 1 ./job4.sh < jobs_$W.txt
grep -h DONE logs/*.log | grep -v " cal " > summary_$W.txt; echo "chunks done: $(wc -l < summary_$W.txt) of $(wc -l < jobs_$W.txt)"
tar -cf - logs out/*/logs/equity.jsonl out/*/logs/decisions.jsonl out/*/logs/fills.jsonl | python3 -c "import sys,zstandard;sys.stdout.buffer.write(zstandard.ZstdCompressor(level=10).compress(sys.stdin.buffer.read()))" > results_$W.tar.zst
git checkout -q -b results-$W && git add -f results_$W.tar.zst summary_$W.txt && git -c user.name=worker -c user.email=noreply@anthropic.com commit -qm "results $W" && git push -q origin results-$W && echo "PUSHED results-$W"
