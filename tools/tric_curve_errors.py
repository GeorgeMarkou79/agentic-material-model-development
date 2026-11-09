"""Error metrics of numerical load-displacement curves against the tests (Claude, 2026-10-25).
Range: 0 .. d_u = last displacement of the experimental curve.
  coverage c   = min(d_num_max, d_u) / d_u
  NRMSE        = sqrt(mean over the covered range of (P_num - P_exp)^2) / P_exp,max  (200-point grid, linear interpolation)
  bias         = mean(P_num - P_exp) / P_exp,max over the covered range (+ = too strong)
  peak error   = (max P_num on [0, d_u] - P_exp,max) / P_exp,max
  ult. error   = (P_num(d_u) - P_exp(d_u)) / P_exp,max; if the run stopped before d_u: P_num at its last point
  score        = (NRMSE + |peak error| + |ult. error|)/3 + (1 - c)    (0 = perfect; the coverage term charges a run
                 that stops early with the uncovered fraction of the test range)
"""
import csv, os, glob, numpy as np
K='/home/claude/kpg'; T='/tmp/claude-0/kpc'; BN='/home/claude/bench'
def rd(p,y='load_kN'):
    d=[];l=[]
    for r in csv.DictReader(open(p)):
        try: d.append(float(r['displacement_mm'])); l.append(float(r[y]))
        except: pass
    return np.array(d),np.array(l)
EXP={f'SW{n}':rd(f'{K}/SW{n}_experiment_digitized.csv','lateral_load_kN') for n in (14,16,21,22)}  # set from 2026-10-25: SW25 dropped (premature test failure), SW15 not in the set
EXP['W2']=rd(f'{K}/W2_experiment.csv'); EXP['Toronto']=rd(f'{K}/Toronto_experiment.csv')
def src(version,case,mesh):
    pre=f'Lefas_{case}' if case.startswith('SW') else case
    c={'KP':f'{K}/{pre}_TRIC_KP_{mesh}_N50_curve.csv','KP2':f'{K}/k2/{pre}_TRIC_KP_{mesh}_N50_curve.csv',
       'KP3':f'{T}/kp3/{pre}_TRIC_KP_{mesh}_N50_curve.csv','KP4':f'{T}/kp4/{pre}_TRIC_KP_{mesh}_N50_curve.csv',
       'KP5':f'{T}/kp5/{pre}_TRIC_KP_{mesh}_N50_curve.csv','KP6':f'{T}/kp6/{pre}_TRIC_KP_{mesh}_N50_curve.csv','KP5 rigid head':f'{T}/kp5rh/{pre}_TRIC_KPRH_{mesh}_N50_curve.csv',
       'Markou r9 brittle':(f'{K}/{pre}_TRIC_MARKOU_{mesh}_N50_G0_curve.csv' if case.startswith('SW') else f'{BN}/{pre}_TRIC_MARKOU_{mesh}_N50_G0_curve.csv')}[version]
    return c if os.path.exists(c) else None
def metric(case,f):
    de,pe=EXP[case]; du=de[-1]; pmax=pe.max()
    dn,pn=rd(f); dn=np.r_[0.,dn] if case!='Toronto' else dn; pn=np.r_[0.,pn] if case!='Toronto' else pn
    d0=max(de[0],dn[0]); dend=min(dn[-1],du); c=max(0.,(dend-de[0])/(du-de[0]))
    g=np.linspace(d0,dend,200); e=np.interp(g,dn,pn)-np.interp(g,de,pe)
    nr=np.sqrt(np.mean(e**2))/pmax; bias=e.mean()/pmax
    peak=(pn[dn<=du+1e-9].max()-pmax)/pmax
    ult=((np.interp(du,dn,pn) if dn[-1]>=du-1e-9 else pn[-1])-pe[-1])/pmax
    return dict(coverage=c,nrmse=nr,bias=bias,peak=peak,ult=ult,score=(nr+abs(peak)+abs(ult))/3+(1-c),steps=len(dn)-1)
rows=[]
for v in ['KP','KP2','KP3','KP4','KP5','KP6','KP5 rigid head','Markou r9 brittle']:
    for case in EXP:
        for mesh in ('Medium','Coarse'):
            f=src(v,case,mesh)
            if f: rows.append(dict(version=v,case=case,mesh=mesh,**metric(case,f)))
with open('/tmp/claude-0/err/curve_errors.csv','w',newline='') as h:
    w=csv.DictWriter(h,fieldnames=list(rows[0])); w.writeheader()
    for r in rows: w.writerow({k:(round(x,4) if isinstance(x,float) else x) for k,x in r.items()})
import collections
print('version | mesh | n | mean score | mean NRMSE | mean bias | mean |peak| | mean |ult| | mean coverage   (4 Lefas walls: SW14, SW16, SW21, SW22)')
for v in ['KP','KP2','KP3','KP4','KP5','KP6','KP5 rigid head','Markou r9 brittle']:
    for mesh in ('Medium','Coarse'):
        s=[r for r in rows if r['version']==v and r['mesh']==mesh and r['case'].startswith('SW')]
        if s: print(f"{v:18s} {mesh:6s} {len(s)} {np.mean([r['score'] for r in s]):.3f} {np.mean([r['nrmse'] for r in s]):.3f} {np.mean([r['bias'] for r in s]):+.3f} {np.mean([abs(r['peak']) for r in s]):.3f} {np.mean([abs(r['ult']) for r in s]):.3f} {np.mean([r['coverage'] for r in s]):.2f}")
print()
for r in rows:
    print(f"{r['version']:18s} {r['case']:8s} {r['mesh']:6s} score {r['score']:.3f} nrmse {r['nrmse']:.3f} bias {r['bias']:+.3f} peak {r['peak']:+.3f} ult {r['ult']:+.3f} cov {r['coverage']:.2f}")
