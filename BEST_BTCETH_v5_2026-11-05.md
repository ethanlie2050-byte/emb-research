# BTC+ETH v5 candidate (2026-11-05 evening) - research only, NOT deployed
Realistic (cmp.py drag) unless noted. Backtest 2022-01..2026-10, real bot code via mock, calibration 14301.99.

| | realistic CAGR | realistic DD | realistic Calmar | median / worst realistic half-year | 2x costs Calmar | in-candle worst | crash (>=15 min) |
|---|---|---|---|---|---|---|---|
| last night's best (lead) | 89% | -24% | 3.7 | +36% / -16% | 1.1 | -29% | -62% |
| v4 (p_e2z125) | 95% | -11% | 8.6 | +17% / 0% | 6.8 | -27% | -63% |
| **v5 (c_b)** | **117%** | **-13%** | **9.0** | +26% / -1% | 5.6 | -28% | -64% |

v5 backtest train 2022-01..2024-06 182%/-11%, test 2024-07..2026-10 114%/-10%. Years +63% +161% +427% +201% +34% (c_a; c_b similar).
30m E1 take-profit: 3.1 -> 7.9, 3.5 -> 9.2, 4.0 -> 9.0, 4.5 -> 7.6, 5.0 -> 8.0 (good zone 3.5-4; expect ~8-9, not more).

v5 = v4 + breakout engine RB1 at 0.5x only when BTC > its 100-day average (RB1TREND=100; 200 works too) + E1 tp 4 ATR + E5 tp 3.5 ATR.
Env: `UNIVF=univ_top2.pkl DIS=E2,E4,E5,RB1 NOTREND=1 NOEUPH=1 QE1=1.7 QE5=1.8 CAP=3 DDT=0.12/0.5 ENGC=E5=ETH E3R=-0.07 E5Z=1.85 DIS=E4 PCAP=2.5/2 E1TF=30m E1ZW=10 E1ATRN=28 E1AREGN=2000 E1Z=1.92 E1AR=1.6 QE1=1.9 E5Z=1.75 E5AR=1.8 QE5=1.7 ENGC= ENGP=E1.lv:1.03/2.17/5.57,E1.fr:0.1/0.3/0.6,E1.Wn:2,E1.tp:3.14,E1.H:96,E5.lv:1.5/3/5,E5.fr:0.25/0.25/0.5,E5.tp:3,E5.H:96,E5.Wn:1,E2.lv:1.52/3.91/6.32,E2.fr:0.4/0.3/0.3,E2.Wn:2,E2.tp:2.25,E2.H:96,E2.st:0 WMUL=E1:1.1,E5:2.3,E2:1.6,E3:1.5 DIPG=E1,E2 CORESW=0 E2ZW=10 E2ATRN=48 E2AREGN=1000 E2Z=1.11 E2AR=1.64 QE2=1.9 E2QT=E1 E2Z=1.25 WMUL=E1:1.1,E5:2.3,E2:1.6,E3:1.5,RB1:0.5 RB1TREND=100 ENGP=E1.lv:1.03/2.17/5.57,E1.fr:0.1/0.3/0.6,E1.Wn:2,E1.tp:3.14,E1.H:96,E5.lv:1.5/3/5,E5.fr:0.25/0.25/0.5,E5.tp:3,E5.H:96,E5.Wn:1,E2.lv:1.52/3.91/6.32,E2.fr:0.4/0.3/0.3,E2.Wn:2,E2.tp:2.25,E2.H:96,E2.st:0,E1.tp:4,E5.tp:3.5`

Tested and dropped this evening: top-5 coins (all versions worse, crash risk >100%), E4 momentum engine, vol-target changes, loss-pause 1/6 days,
bigger E3, E3 weekly drop 5%/9%, overall size up (just more risk), breakout 0.75x/1x, longer/shorter breakout hold, 1h engine wider tp, trend part 0.3/0.6.

## Later (21:00): centred v5 + risk levels (realistic; crash = BTC -15%/ETH -20% at worst moment held >=15 min)
| version | CAGR | DD | Calmar | in-candle worst | crash | worst/median half-year |
|---|---|---|---|---|---|---|
| v5 centred (r_base, E1 tp 3.75) | 114% | -12% | 9.5 | -28% | -64% | -2% / +25% |
| safer (tc3e18: TCAP=3 PCAPE=1.8) | 89% | -11% | 8.1 | -27% | -49% | -1% / +24% |
| v5 centred x1.5 risk (lev15) | 196% | -17% | 11.5 | -39% | -99% | 0% / +37% |
| v5 centred x2 risk (lev2) | 252% | -21% | 12.0 | -49% | -133% | 0% / +48% |
| safer x1.5 (slev15) | 146% | -15% | 9.7 | -38% | -75% | 0% / +34% |
| safer x2 (slev2) | 207% | -17% | 12.2 | -49% | -102% | 0% / +45% |
Per-trade volatility limits (VR), ETH-only limits (PCAPE alone), breakout filter 150: no gain.
v5 centred env: `UNIVF=univ_top2.pkl DIS=E2,E4,E5,RB1 NOTREND=1 NOEUPH=1 QE1=1.7 QE5=1.8 CAP=3 DDT=0.12/0.5 ENGC=E5=ETH E3R=-0.07 E5Z=1.85 DIS=E4 PCAP=2.5/2 E1TF=30m E1ZW=10 E1ATRN=28 E1AREGN=2000 E1Z=1.92 E1AR=1.6 QE1=1.9 E5Z=1.75 E5AR=1.8 QE5=1.7 ENGC= ENGP=E1.lv:1.03/2.17/5.57,E1.fr:0.1/0.3/0.6,E1.Wn:2,E1.tp:3.14,E1.H:96,E5.lv:1.5/3/5,E5.fr:0.25/0.25/0.5,E5.tp:3,E5.H:96,E5.Wn:1,E2.lv:1.52/3.91/6.32,E2.fr:0.4/0.3/0.3,E2.Wn:2,E2.tp:2.25,E2.H:96,E2.st:0 WMUL=E1:1.1,E5:2.3,E2:1.6,E3:1.5 DIPG=E1,E2 CORESW=0 E2ZW=10 E2ATRN=48 E2AREGN=1000 E2Z=1.11 E2AR=1.64 QE2=1.9 E2QT=E1 E2Z=1.25 WMUL=E1:1.1,E5:2.3,E2:1.6,E3:1.5,RB1:0.5 RB1TREND=100 ENGP=E1.lv:1.03/2.17/5.57,E1.fr:0.1/0.3/0.6,E1.Wn:2,E1.tp:3.14,E1.H:96,E5.lv:1.5/3/5,E5.fr:0.25/0.25/0.5,E5.tp:3,E5.H:96,E5.Wn:1,E2.lv:1.52/3.91/6.32,E2.fr:0.4/0.3/0.3,E2.Wn:2,E2.tp:2.25,E2.H:96,E2.st:0,E1.tp:4,E5.tp:3.5 ENGP=E1.lv:1.03/2.17/5.57,E1.fr:0.1/0.3/0.6,E1.Wn:2,E1.tp:3.14,E1.H:96,E5.lv:1.5/3/5,E5.fr:0.25/0.25/0.5,E5.tp:3,E5.H:96,E5.Wn:1,E2.lv:1.52/3.91/6.32,E2.fr:0.4/0.3/0.3,E2.Wn:2,E2.tp:2.25,E2.H:96,E2.st:0 ENGP=E1.lv:1.03/2.17/5.57,E1.fr:0.1/0.3/0.6,E1.Wn:2,E1.tp:3.14,E1.H:96,E5.lv:1.5/3/5,E5.fr:0.25/0.25/0.5,E5.tp:3,E5.H:96,E5.Wn:1,E2.lv:1.52/3.91/6.32,E2.fr:0.4/0.3/0.3,E2.Wn:2,E2.tp:2.25,E2.H:96,E2.st:0,E1.tp:4,E5.tp:3.5,E1.tp:3.75`
