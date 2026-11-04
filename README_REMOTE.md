# Remote research worker

Exact environment (results must be bit-identical):
    python 3.13, pip install --break-system-packages pandas==3.0.5 numpy==2.5.3 pyarrow

1. Calibration (must print `equity 14301.99`, else STOP and report the mismatch):
       mkdir -p logs && DIS=E2,E4 NOTREND=1 NOEUPH=1 UNIVF=$PWD/univ_mcap.pkl CAP=1.5 PRE=0D MODE=cal S=2024-03-01 E=2024-03-08 python3 run_pit.py | grep DONE
2. Run the assigned job file (one job per line: NAME START END [ENV=val ...]) with all cores:
       mkdir -p logs && xargs -P $(nproc) -L 1 ./job4.sh < JOBFILE
   Each job writes logs/NAME_START.log (ends with a DONE line) and out/NAME_START/logs/*.jsonl.
3. Keep only equity.jsonl, decisions.jsonl and fills.jsonl of each out/NAME_START/logs, tar+zstd them as results_<worker>.tar.zst,
   commit it on branch results-<worker> and push.
