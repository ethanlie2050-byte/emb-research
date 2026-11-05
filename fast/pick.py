"""python3 pick.py res/e1_a.csv [min_n] -> top configs chosen on TRAIN only, with their unseen TEST result"""
import sys, json, pandas as pd
d = pd.read_csv(sys.argv[1]); mn = int(sys.argv[2]) if len(sys.argv) > 2 else 30
d = d[(d.n >= mn) & (d.hpos >= 7)]
d = d.sort_values('tr_cal', ascending=False)
pd.set_option('display.width', 250)
cols = ['n', 'win', 'cagr', 'dd', 'cal', 'tr_cal', 'te_cal', 'cal2', 'hpos']
print(len(d), 'pass filters')
print(d.head(25)[cols].round(2).to_string())
print('\nmedian TEST Calmar of top-25 by TRAIN:', d.head(25).te_cal.median().round(2), '| of all:', d.te_cal.median().round(2))
for p in d.head(8).P: print(p)
