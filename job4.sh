#!/bin/bash
# usage: job4.sh NAME S E [VAR=val ...]
cd "$(dirname "$0")"
N=$1; S=$2; E=$3; shift 3
grep -q DONE logs/${N}_$S.log 2>/dev/null && exit 0
env NOSTATUS=1 UNIVF=$PWD/univ_mcap.pkl CAP=1.5 "$@" MODE=$N S=$S E=$E PD=30 python3 run_pit.py > logs/${N}_$S.log 2>&1
echo "$N $S exit $? $(grep DONE logs/${N}_$S.log)" >> logs/driver.txt
