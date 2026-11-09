"""Compare actual native result bytes, without numeric tolerance or rounding."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,zipfile,re,sys
from native_run import ROOT,ORIGINAL,run,stage_runtime

CASES=[
 ('models/HEXA8_Gravity_Z.NEU','HEXA8_Gravity_Z'),
 ('models/HEXA8_Gravity_Warped_XYZ.NEU','HEXA8_warped'),
 ('models/HEXA20_Gravity_Z.NEU','HEXA20_Gravity_Z'),
 ('models/TRIC_ONLY_Gravity_Z_n16.NEU','TRIC_elastic_Z'),
 ('models/TRIC_ONLY_Gravity_X_n16.NEU','TRIC_elastic_X'),
 ('models/TRIC3_J2_CyclicDispl_1Elem_10L.NEU','TRIC_J2_cyclic_1'),
 ('models/TRIC3_J2_CyclicDispl_32Elem_10L.NEU','TRIC_J2_cyclic_32'),
 ('models/BEC_Z_n32.NEU','BEC_Z'),
 ('models/NBCFB_Z_n32.NEU','NBCFB_Z'),
 ('NBCFB_V_FixedSupported24ElementsLIN.NEU','NBCFBV_linear'),
 ('NBCFB_V_FixedSupported24Elements.NEU','NBCFBV_nonlinear'),
 ('FrameNBCFBV_TRIC_LIN.NEU','mixed_TRIC_frame_linear'),
 ('FrameDynamicTRICandNBCFB_100stepsLIN.NEU','mixed_dynamic_linear'),
 ('models/Beam2H8forceContDistributed_10LI.neu','H8_RC_nonlinear'),
 ('models/Beam2H20forceContDistributed_10LI.neu','H20_RC_nonlinear'),
 ('models/20NodedSSBeamRCForce_Material7.NEU','H20_material7'),
 ('3TypesofElementwithkinematicTEST_latestLIN.neu','mixed_hybrid_kinematic_linear'),
 ('FrameDynamicTRICandNBCFB_100steps.NEU','mixed_dynamic_nonlinear'),
]
CASES += [(ROOT/'native/examples'/(name+'.NEU'),name) for name in
          ['HEXA8_warped_J2LIN','HEXA20_J2LIN','TRIC_J2LIN_Z','TRIC_J2LIN_X']]

def stable_bytes(name,data):
    lines=data.splitlines(keepends=True);out=[];excluded=[]
    for line in lines:
        # Explicit duration fields only; counters, residuals and material data
        # retain their exact original bytes. Never round numeric FE results.
        if b'seconds:' in line.lower() or (b'elapsed' in line.lower() and b'(s)' in line) or b'wall interval (s):' in line.lower():
            excluded.append(line.decode(errors='replace').strip());continue
        if name.endswith('_TRIC3_Displacement_Performance.dat') and re.match(rb'^\s*\d+\s+\d+\s+[0-9.]+\s+\d+\s+[-+0-9.E]+\s*$',line):
            fields=line.split();out.append(b' '.join(fields[:2]+fields[3:])+b'\n')
            excluded.append('STEP_SECONDS column');continue
        out.append(line)
    return b''.join(out),sorted(set(excluded))

def compare(name):
    left=ROOT/'native/runs/reference'/name;right=ROOT/'native/runs/integrated'/name
    records=[]
    for path in left.rglob('*'):
        if not path.is_file():continue
        if path.suffix.lower() not in ['.neu','.dat','.zip','.vtk','.vtu','.pvd','.csv','.bin']:continue
        if path.name in ['model.NEU','model_TIME.dat']:continue
        label=path.relative_to(left).as_posix();other=right/path.relative_to(left)
        if not other.exists():records.append({'file':label,'equal':False,'missing':True});continue
        if path.suffix.lower()=='.zip':
            with zipfile.ZipFile(path) as a,zipfile.ZipFile(other) as b:
                for member in sorted(set(a.namelist())|set(b.namelist())):
                    x=a.read(member) if member in a.namelist() else b''
                    y=b.read(member) if member in b.namelist() else b''
                    records.append({'file':label+'/'+member,'equal':x==y,'bytes':len(x),
                                    'sha256':hashlib.sha256(x).hexdigest()})
        else:
            x=path.read_bytes();y=other.read_bytes()
            sx,excluded=stable_bytes(path.name,x);sy,_=stable_bytes(path.name,y)
            records.append({'file':label,'equal':sx==sy,'whole_file_equal':x==y,'bytes':len(x),
                            'sha256':hashlib.sha256(x).hexdigest(),'excluded_duration_fields':excluded})
    runs=[json.loads((p/'run.json').read_text()) for p in (left,right)]
    return {'case':name,'complete':all(p['complete'] and p['returncode']==0 for p in runs),
            'partial':any(p['partial'] for p in runs),'comparisons':records,
            'all_equal':all(p['equal'] for p in records),'runs':runs}

def main():
    for mode in ['reference','integrated']:stage_runtime(mode)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks=[pool.submit(run,(ROOT/'native/regression-inputs'/(name+'.NEU')) if (ROOT/'native/regression-inputs'/(name+'.NEU')).exists() else ORIGINAL/file,mode,name,300) for file,name in CASES for mode in ['reference','integrated']
               if '--compare-only' not in sys.argv and ('--new-only' not in sys.argv or not (ROOT/'native/runs'/mode/name/'run.json').exists())]
        for t in tasks:t.result()
    records=[compare(name) for _,name in CASES]
    (ROOT/'native/verification').mkdir(exist_ok=True)
    (ROOT/'native/verification/legacy-results.json').write_text(json.dumps(records,indent=2)+'\n')
    print('Comparable cases',len(records),'all byte equal',sum(r['all_equal'] for r in records),flush=True)
    for r in records:
        if not r['all_equal']:print(r['case'],[p['file'] for p in r['comparisons'] if not p['equal']],flush=True)

if __name__=='__main__':main()
