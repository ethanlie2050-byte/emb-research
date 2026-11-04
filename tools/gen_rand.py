# random joint search around the current best BTC/ETH config (seeded, reproducible)
import random, sys
random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 48
chunks = [l.split() for l in open(sys.argv[3])]
B = "UNIVF=univ_top2.pkl DIS=E2,E4 NOTREND=1 NOEUPH=1 CAP=3"
def u(a, b, d=2): return round(random.uniform(a, b), d)
for i in range(N):
    a = u(1.1, 1.8); m2 = u(1.8, 2.4); m3 = u(3.0, 4.0)
    f1 = u(0.15, 0.35); f2 = u(0.15, 0.35); f3 = round(1 - f1 - f2, 2)
    engp = (f"E1.lv:{a}/{round(a*m2,2)}/{round(a*m3,2)},E1.fr:{f1}/{f2}/{f3},E1.tp:{u(2.2,3.8)},E1.H:{random.choice([64,80,96,120,144])},"
            f"E5.tp:{u(1.2,2.2)},E5.H:{random.choice([32,48,64,80])},E3.tp:{u(1.3,2.3)},E3.st:{u(4.5,8)}")
    kv = [f"ENGP={engp}", f"QE1={u(1.5,2.0)}", f"QE5={u(1.6,2.1)}", f"E1Z={u(1.55,2.0)}", f"E5Z={u(1.75,2.4)}",
          f"DDT={u(0.08,0.15)}/{u(0.3,0.7)}", f"PCAP={u(1.75,3.0)}/2",
          f"CFGO=crsc:{u(0.3,0.8)},wcore:{u(0.0,0.7)}"]
    ps = random.choice(['', '0.08/24', '0.10/48', '0.12/48', '0.15/24'])
    if ps: kv.append(f"PSTOP={ps}")
    for s, e in chunks: print(f"r_{i:03d} {s} {e} {B} {' '.join(kv)}")
