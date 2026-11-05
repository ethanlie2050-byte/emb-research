# BTC+ETH v4 candidate (2026-11-05 afternoon) - research only, NOT deployed
Backtest 2022-01 .. 2026-10, real bot code via mock, calibration 14301.99. "Realistic" = cmp.py turnover drag.

| | realistic CAGR | realistic DD | realistic Calmar | in-candle worst | 2x costs Calmar | crash (>=15 min) |
|---|---|---|---|---|---|---|
| morning leader (nRBe15e3) | 69% | -16% | 4.3 | -28% | - | -54% |
| **v4 (p_e2z125)** | **95%** | **-11%** | **8.6** | **-27%** | **6.8** (b9x2, E2Z 1.11) | -63% |

Backtest train (2022-01..2024-06) 138% / -11%, test (2024-07..2026-10) 88% / -7%. Years +51% +96% +353% +167% +19%.
Nearby settings: E1 weight 0.9/1.3 -> 8.6/8.6, E5 weight 1.9/2.7 -> 8.4/8.8, E2 weight x1.4 -> 8.5,
E2 dip depth 1.0 -> 3.0 (cliff!), 1.11 -> 8.6, 1.25 -> 8.6, 1.4 -> 8.4, 1.6 -> 8.3 (so 1.25 = middle of the good zone).

What it is: E1 = 30m dip engine (new settings), E5 slot = old 15m E1 settings, E2 slot = 1h dip engine, E3 shorts,
trend core long-only (CORESW=0); E4, RB1 (breakout), euphoria, alt trend add-on off; E5 may overlap with E1/E2 (DIPG=E1,E2).

Env: `UNIVF=univ_top2.pkl DIS=E2,E4,E5,RB1 NOTREND=1 NOEUPH=1 QE1=1.7 QE5=1.8 CAP=3 DDT=0.12/0.5 ENGC=E5=ETH E3R=-0.07 E5Z=1.85 DIS=E4,RB1 PCAP=2.5/2 E1TF=30m E1ZW=10 E1ATRN=28 E1AREGN=2000 E1Z=1.92 E1AR=1.6 QE1=1.9 E5Z=1.75 E5AR=1.8 QE5=1.7 ENGC= ENGP=E1.lv:1.03/2.17/5.57,E1.fr:0.1/0.3/0.6,E1.Wn:2,E1.tp:3.14,E1.H:96,E5.lv:1.5/3/5,E5.fr:0.25/0.25/0.5,E5.tp:3,E5.H:96,E5.Wn:1,E2.lv:1.52/3.91/6.32,E2.fr:0.4/0.3/0.3,E2.Wn:2,E2.tp:2.25,E2.H:96,E2.st:0 WMUL=E1:1.1,E5:2.3,E2:1.6,E3:1.5 DIPG=E1,E2 CORESW=0 E2ZW=10 E2ATRN=48 E2AREGN=1000 E2Z=1.11 E2AR=1.64 QE2=1.9 E2QT=E1 E2Z=1.25`

Dropped today: E5 (old), RB1 breakout engine, bigger trend core, faster trend, disaster stops (8/12 ATR), early drawdown brakes,
crash-time size cut, volume-spike / wait-for-green-candle entries, trend filter, time-of-day filter, more coins.
