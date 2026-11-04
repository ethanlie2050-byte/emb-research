import os, sys, shutil, time, math, pickle
ROOT=os.path.dirname(os.path.abspath(__file__))
MODE=os.environ['MODE']; S=pd_S=os.environ['S']; E_=os.environ['E']
U=pickle.load(open(os.environ.get('UNIVF',os.path.join(ROOT,'univ.pkl')),'rb'))
import pandas as pd
S=pd.Timestamp(S); E_=pd.Timestamp(E_)
if MODE!='hind': os.environ['UNIV']=','.join(U[str(S.date())] if str(S.date()) in U else U[S.year])
sys.path.insert(0,os.path.join(ROOT,'bot'))
import numpy as np
import run_live
from run_live import Live, Logger
from feed import Feed, resample
from mock_bybit import MockREST
from signals import compute
from lib_data import P15, X1H, ADDON_EXTRA
T0=S-pd.Timedelta(os.environ.get('PRE','31D')); DS=T0-pd.Timedelta('250D')
OUT=os.path.join(ROOT,'out',f'{MODE}_{S.date()}'); shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT+'/data')
P=os.path.join(ROOT,'data')
COLS=['open','high','low','close','volume']
for c in P15+X1H+ADDON_EXTRA:
    d=pd.read_parquet(f'{P}/{c}.parquet').astype('float64')[COLS].astype('float64')
    if c=='MATIC': p2=pd.read_parquet(f'{P}/POL.parquet').astype('float64')[COLS].astype('float64'); d=pd.concat([d,p2[p2.index>d.index[-1]]])
    d=d[(d.index>=DS)&(d.index<E_+pd.Timedelta('2D'))&(d.volume!=0)]
    d.index=pd.DatetimeIndex(d.index).as_unit('ns'); d.index.name='t'
    if c in P15: d.to_pickle(f'{OUT}/data/{c}USDT_15m.pkl')
    else: resample(d,'1h').to_pickle(f'{OUT}/data/{c}USDT_1h.pkl')
F=Feed(lambda *a: [], OUT+'/data', start=str(DS.date()), log=print)
D=dict(m15={c:F.series[(c,'15m')] for c in P15}, h1={c:F.series[(c,'1h')] for c in X1H+ADDON_EXTRA})
for c in P15: D['h1'][c]=resample(D['m15'][c],'1h')
Sfull=compute(Feed.build(F, E_+pd.Timedelta('1D')))
CUR={}
class _Lazy:
    # behaves like v.iloc[:n]; the slice is only built when something other than len() is used
    __slots__=('f','n','_m')
    def __init__(s,f,n): s.f=f; s.n=n; s._m=None
    def __len__(s): return s.n
    def _mat(s):
        if s._m is None: s._m=s.f.iloc[:s.n]
        return s._m
    def __getattr__(s,a): return getattr(s._mat(),a)
    def __getitem__(s,k): return s._mat()[k]
_IDX={k:v.index for k,v in Sfull.items()}
def fast_compute(Dt):
    t=CUR['t']; out={}
    for k,v in Sfull.items():
        dur=pd.Timedelta({'E1':'15min','E5':'15min','RB1':'15min','RB5':'4h'}.get(k[0],'1h'))
        n=_IDX[k].searchsorted(t-dur,'right')
        out[k]=_Lazy(v,n) if os.environ.get('LAZY','1')=='1' else v.iloc[:n]
    return out
run_live.compute=fast_compute
if os.environ.get('FAST','1')=='1':
    import slots as _sl
    _orig=_sl.Slot.refresh; _ARR={}
    def _fast_refresh(self, S):
        k=(self.E,self.coin)
        if not getattr(self,'_fast',False) or k not in _ARR:
            _orig(self,S); self._fast=True
            f=Sfull[k]; a=dict(o=f.open.values,h=f.high.values,l=f.low.values,c=f.close.values,sig=f.sig.values.astype(bool),idx=f.index)
            if self.E.startswith('RB'): a['dist']=f.dist.values
            else:
                a.update(atr=f.atr.values,gate=f.gate.values.astype(bool),cf=f.cf.values.astype(bool),tgt=f.tgt.values,
                         gnext=f.gnext.values.astype(bool),cnext=f.cnext.values.astype(bool))
            a['sigall']=np.flatnonzero(a['sig']); _ARR[k]=a
        a=_ARR[k]; d=S[k]; n=len(d); self.d=d
        self.idx=a['idx'][:n]; self.o=a['o'][:n]; self.h=a['h'][:n]; self.l=a['l'][:n]; self.c=a['c'][:n]; self.sig=a['sig'][:n]
        if 'dist' in a: self.dist=a['dist'][:n]
        else:
            self.atr=a['atr'][:n]; self.gate=a['gate'][:n]; self.cf=a['cf'][:n]; self.tgt=a['tgt'][:n]; self.gnext=a['gnext'][:n]; self.cnext=a['cnext'][:n]
        sa=a['sigall']; self.sigidx=sa[:sa.searchsorted(n)]
    _sl.Slot.refresh=_fast_refresh
    run_live.Live.save=lambda self: None
    _ADV=run_live.adv_series(Feed.build(F, E_+pd.Timedelta('1D')))
    run_live.adv_series=lambda D: _ADV
    _DF=Feed.build(F, E_+pd.Timedelta('1D'))
    class _Dct:
        def __init__(s,d): s.d=d
        def to_dict(s): return s.d
    _VT=_Dct(run_live.vt_series(_DF).to_dict()); _RG=_Dct(run_live.regime_series(_DF).to_dict())
    run_live.vt_series=lambda D: _VT; run_live.regime_series=lambda D, *a, **k: _RG
    import addon as _ad
    _of=_ad.features; _FC={}
    def _feat(d):
        if not len(d): return _of(d)
        k=(d.index[0], float(d.close.values[0]))
        if k not in _FC:
            full=None
            for c,x in _FB['a4'].items():
                if len(x) and x.index[0]==k[0] and float(x.close.values[0])==k[1]: full=x; break
            if full is None: return _of(d)
            _FC[k]=_of(full)
        f=_FC[k]; return f.iloc[:len(d)]
    _ad.features=_feat
    class _B:
        def __init__(s,dct): s.d=dct
        def to_dict(s): return s.d
    _BU={}
    _obu=_ad.btc_up
    def _bup(b4):
        if 'x' not in _BU: _BU['x']=_B(_obu(_FB['a4']['BTC']).to_dict())
        return _BU['x']
    _ad.btc_up=_bup
run_live.Feed.update=lambda self, now: True
# fast feed: resample everything once, then slice by time each step (same closed-bar rule as Feed.build)
_FB={}
def _prep(self):
    from lib_data import P15 as _P, X1H as _X, ADDON_EXTRA as _A
    m15={c:self.series[(c,'15m')] for c in _P}
    _FB['m15']=m15
    _FB['h1']={c:resample(d,'1h') for c,d in m15.items()}
    for c in _X: _FB['h1'][c]=self.series[(c,'1h')]
    _FB['a4']={c:resample(d,'4h') for c,d in m15.items()}
    _FB['x1']={c:self.series[(c,'1h')] for c in _X+_A}
    for c in _X+_A: _FB['a4'][c]=resample(_FB['x1'][c],'4h')
    _FB['X']=_X; _FB['A']=_A
def _cut(d, t): return d.iloc[:d.index.searchsorted(t,'left')]
def fast_build(self, now):
    if not _FB: _prep(self)
    m15={c:_cut(d, now-pd.Timedelta('15min')+pd.Timedelta('1ns')) for c,d in _FB['m15'].items()}
    f1=now.floor('1h'); f4=now.floor('4h')
    h1={c:_cut(d, f1) for c,d in _FB['h1'].items() if c not in _FB['X']}
    for c in _FB['X']: h1[c]=_cut(_FB['x1'][c], now-pd.Timedelta('1h')+pd.Timedelta('1ns'))
    a4={c:_cut(d, f4) for c,d in _FB['a4'].items()}
    return dict(m15=m15,h1=h1,btc4=a4['BTC'],eth4=a4['ETH'],a4=a4)
run_live.Feed.build=fast_build
class M(MockREST):
    def __init__(s,D,equity):
        super().__init__(D,equity)
        s.bars={c:('15m',D['m15'][c]) for c in P15}
        for c in X1H+ADDON_EXTRA: s.bars[c]=('1h',D['h1'][c])
    def __getattr__(s,n):
        if n.startswith('__'): raise AttributeError(n)
        return lambda *a,**k: {}
import bot as _botmod
for _c in P15+X1H+ADDON_EXTRA:
    _src=D['m15'][_c] if _c in D['m15'] else D['h1'][_c]
    _dc=_src.close.resample('1D').last().dropna(); _v=_dc.pct_change().rolling(30,min_periods=10).std(); _v.index=_v.index+pd.Timedelta('1D')
    _botmod.VOLS[_c]=_v.dropna()
mock=M(D,10000.0)
_botmod.EXPO_FN=lambda: {c: p*mock._last(c) for c,p in mock.pos.items() if p}; mock.now=T0+pd.Timedelta('5s')
L=Logger(OUT+'/logs',echo=False)
cfg=dict(state_dir=OUT+'/state',data_dir=OUT+'/data',log_dir=OUT+'/logs',core=True,demo=False,data_start=str(DS.date()))
live=Live(mock,cfg,L,clock=lambda: mock.now); live.startup_checks()
tw=time.time(); n=0
halt_until=None; nh=0
for t in pd.date_range(T0,E_,freq='15min'):
    if halt_until is not None:
        if t < halt_until:
            mock.now=t+pd.Timedelta('5s'); continue
        # the person restarts the bot one day after an emergency stop (fresh start, same account)
        shutil.rmtree(cfg['state_dir'], ignore_errors=True)
        try: os.remove(os.path.join(ROOT,'bot','HALT.txt'))
        except OSError: pass
        mock.now=t+pd.Timedelta('5s')
        live=Live(mock,cfg,L,clock=lambda: mock.now); live.startup_checks(); halt_until=None
        print('RESTART after emergency stop at', t, round(mock.equity(),2), flush=True)
    try:
        if t>T0:
            mock.now=t-pd.Timedelta('1s'); mock.advance(t-pd.Timedelta('15min'),'15m')
            if t.minute==0: mock.advance(t-pd.Timedelta('1h'),'1h')
            live.idle(mock.now,force=True)
        mock.now=t+pd.Timedelta('5s'); CUR['t']=t.floor('15min')
        live.boundary(mock.now)
        mock.now=t+pd.Timedelta('6s'); live.idle(mock.now,force=True)
    except run_live.EmergencyStop as ex:
        nh+=1; halt_until=t+pd.Timedelta('24h')
        print('EMERGENCY STOP at', t, round(mock.equity(),2), 'positions', {c:p for c,p in mock.pos.items() if p}, flush=True)
    n+=1
    if n%(96*int(os.environ.get('PD','1')))==0: print(t, round(mock.equity(),2), round(time.time()-tw), flush=True)
print('HALTS', nh); print('DONE', MODE, S.date(), 'equity', round(mock.equity(),2), 'wall', round(time.time()-tw), flush=True)
