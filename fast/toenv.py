import sys, json
P = json.loads(sys.argv[1])
assert P.get('tpf', 1) == 1 and P['st'] == 0
print(f"E1TF={P['tf']} E1ZW={P['zw']} E1ATRN={P['atrn']} E1AREGN={P['aregn']} E1Z={P['z']} E1AR={P['ar']} QE1={P['q']} "
      f"ENGP=E1.lv:{'/'.join(map(str, P['lv']))},E1.fr:{'/'.join(map(str, P['fr']))},E1.Wn:{P['Wn']},E1.tp:{P['tp']},E1.H:{P['H']}")
