#!/bin/sh
# usage: sweep.sh PORT TAG GAIN...
port=$1; tag=$2; shift 2
for g in "$@"; do
  f=captures/heatsink/sweep_${tag}_g${g}_$(date +%H%M%S).log
  RX_GAIN=$g RX_SHIFT=2 .venv/Scripts/python.exe -u scripts/beacon_check.py - $port 100 - olivia-16/500 > $f 2>&1
  t=$(tr '\r' '\n' < $f | grep '^decoded:' | tail -1)
  echo "$tag gain $g: ids=$(echo "$t" | grep -o 'W4DQB/EXP' | wc -l) $t"
done
