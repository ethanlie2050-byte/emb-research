# narrow joint search around the current safe best (k_e5ethp2); seeded, reproducible
import random, sys
pre, seed, N, cf = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
random.seed(seed); chunks = [l.split() for l in open(cf)]
B = "UNIVF=univ_top2.pkl DIS=E2,E4 NOTREND=1 NOEUPH=1 CAP=3 ENGC=E5=ETH"
def u(a, b, d=2): return round(random.uniform(a, b), d)
for i in range(N):
    a = u(1.3, 1.7); m2 = u(1.8, 2.2); m3 = u(3.0, 3.7)
    f1 = u(0.2, 0.3); f2 = u(0.2, 0.3); f3 = round(1 - f1 - f2, 2)
    engp = (f"E1.lv:{a}/{round(a*m2,2)}/{round(a*m3,2)},E1.fr:{f1}/{f2}/{f3},E1.tp:{u(2.6,3.4)},E1.H:{random.choice([80,96,112])},"
            f"E5.tp:{u(1.4,1.9)},E5.H:{random.choice([40,48,56])},E3.tp:{u(1.5,2.0)},E3.st:{u(5,7)}")
    kv = [f"ENGP={engp}", f"QE1={u(1.6,1.85)}", f"QE5={u(1.7,1.95)}", f"E1Z={u(1.65,1.9)}", f"E5Z={u(1.85,2.15)}",
          f"DDT={u(0.10,0.14)}/{u(0.4,0.6)}", f"PCAP={u(1.8,2.4)}/2", f"CFGO=crsc:{u(0.4,0.65)},wcore:{u(0.3,0.6)}"]
    for s, e in chunks: print(f"{pre}_{i:03d} {s} {e} {B} {' '.join(kv)}")
