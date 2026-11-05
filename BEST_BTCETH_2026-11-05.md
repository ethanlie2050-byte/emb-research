# BTC+ETH best versions after the overnight search (2026-11-05)
Backtest 2022-01 .. 2026-10, Binance 15m, real bot code via mock. Calibration 14301.99.

## Higher return (leader)  -- CAGR 160%, DD -16% EOD / -21% intraday, Calmar 9.8 (continuous 9.7), train/test Calmar 10.5/10.6, double costs 4.3, crash stress -86%
`UNIVF=univ_top2.pkl DIS=E2,E4 NOTREND=1 NOEUPH=1 QE1=1.7 QE5=1.8 CAP=3 DDT=0.12/0.5 ENGC=E5=ETH PCAP=2/2 ENGP=E1.lv:1.5/3/5,E1.fr:0.25/0.25/0.5,E5.tp:1.4 E3R=-0.07 PCAP=2.5/2 E5Z=1.85`

## Safer  -- CAGR 124%, DD -15% / -18%, Calmar 8.5 (continuous 8.4), double costs 3.8, crash stress -68%
`UNIVF=univ_top2.pkl DIS=E2,E4 NOTREND=1 NOEUPH=1 QE1=1.7 QE5=1.8 CAP=3 DDT=0.12/0.5 ENGC=E5=ETH PCAP=2/2 ENGP=E1.lv:1.5/3/5,E1.fr:0.25/0.25/0.5,E5.tp:1.4 E3R=-0.07 DDT=0.10/0.5`
