# broad joint search over ~30 settings + engine on/off, centred on the current safe best (k_e5ethp2). Seeded.
# config 0 of every file is the exact current best (control: checks reproducibility on that worker).
import random, sys
pre, seed, N, cf = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
random.seed(seed); chunks = [l.split() for l in open(cf)]
BEST = ("UNIVF=univ_top2.pkl DIS=E2,E4 NOTREND=1 NOEUPH=1 QE1=1.7 QE5=1.8 CAP=3 "
        "ENGP=E1.lv:1.5/3/5,E1.fr:0.25/0.25/0.5 DDT=0.12/0.5 ENGC=E5=ETH PCAP=2/2")
def u(a, b, d=2): return round(random.uniform(a, b), d)
def one():
    dis = ['E2', 'E4']
    if random.random() < 0.12: dis.append('E3')
    if random.random() < 0.10: dis.append('RB1')
    e5 = random.choices(['ETH', 'BOTH', 'OFF'], [0.6, 0.2, 0.2])[0]
    if e5 == 'OFF': dis.append('E5')
    a = u(1.1, 1.9); m2 = u(1.7, 2.4); m3 = u(2.8, 4.0)
    f1 = u(0.15, 0.35); f2 = u(0.15, 0.35); f3 = round(1 - f1 - f2, 2)
    engp = (f"E1.lv:{a}/{round(a*m2,2)}/{round(a*m3,2)},E1.fr:{f1}/{f2}/{f3},E1.tp:{u(2.2,3.8)},E1.H:{random.choice([64,80,96,112,128,144])},"
            f"E5.tp:{u(1.2,2.2)},E5.H:{random.choice([32,48,64,80])},E3.tp:{u(1.3,2.3)},E3.st:{u(4.5,8)}")
    kv = ["UNIVF=univ_top2.pkl", "NOTREND=1", "NOEUPH=1", f"DIS={','.join(dis)}", f"CAP={u(2,4,1)}", f"ENGP={engp}",
          f"QE1={u(1.5,2.0)}", f"QE5={u(1.6,2.1)}", f"E1Z={u(1.55,2.0)}", f"E5Z={u(1.75,2.3)}", f"E3R={u(-0.08,-0.04,3)}",
          f"DDT={u(0.08,0.16)}/{u(0.3,0.7)}", f"PCAP={u(1.6,3.0)}/2",
          f"WMUL=E1:{u(0.6,1.5)},E3:{u(0.5,1.5)},E5:{u(0.5,1.5)},RB1:{u(0.6,1.5)}",
          f"CFGO=crsc:{u(0.3,0.8)},wcore:{u(0.0,0.7)},kd:{u(18,28,1)}",
          f"RB1S={u(0.4,0.8)}", f"RB1H={random.choice([120,144,168,192,216])}", f"RB1D={u(0.02,0.04,3)}"]
    if e5 == 'ETH': kv.append("ENGC=E5=ETH")
    return ' '.join(kv)
for i in range(N):
    kv = BEST if i == 0 else one()
    for s, e in chunks: print(f"{pre}_{i:03d} {s} {e} {kv}")
