"""Run real native NEU analyses in isolated directories, serial deterministically."""
from pathlib import Path
import argparse,json,os,subprocess,time,shutil

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=Path(json.loads((ROOT/'baseline-manifest.json').read_text())['archive']).parent

def executable(mode):
    built=ROOT/'native/build'/mode/'ReConAn.exe'
    if built.exists():return built
    return ROOT/'native/bin'/('ReConAn_reference.exe' if mode=='reference' else 'ReConAn.exe')

def stage_runtime(mode):
    # App-local Intel DLLs are essential: this PC has older Intel runtimes in
    # System32, which Windows searches before PATH. Never alter System32.
    import pefile
    folder=executable(mode).parent
    locations=[Path(r'C:\Program Files (x86)\Intel\oneAPI\compiler\2026.1\bin'),
               Path(r'C:\Program Files (x86)\Intel\oneAPI\mkl\2026.1\bin'),ORIGINAL]
    seen=set()
    def visit(path):
        if path.name.lower() in seen:return
        seen.add(path.name.lower())
        pe=pefile.PE(str(path),fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        for lib in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]):
            name=lib.dll.decode()
            candidate=next((p/name for p in locations if (p/name).is_file()),None)
            if candidate:
                target=folder/name
                if not target.exists() or target.stat().st_size!=candidate.stat().st_size:shutil.copy2(candidate,target)
                visit(target)
    visit(executable(mode))

def environment():
    env=os.environ.copy()
    # DLLs supplied with the original project plus the installed compiler runtime.
    env['PATH']=r'C:\Program Files (x86)\Intel\oneAPI\compiler\2026.1\bin;C:\Program Files (x86)\Intel\oneAPI\mkl\2026.1\bin'+';'+str(ORIGINAL)+';'+env['PATH']
    env.update(RECONAN_BATCH='1',OMP_THREAD_LIMIT='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',MKL_DYNAMIC='FALSE')
    return env

def run(source,mode,name=None,timeout=300):
    stage_runtime(mode)
    source=Path(source).resolve()
    folder=ROOT/'native/runs'/mode/(name or source.stem)
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/'model.NEU';target.write_bytes(source.read_bytes())
    start=time.monotonic()
    with (folder/'stdout.log').open('wb') as log:
        try:
            p=subprocess.run([str(executable(mode))],input=b'model.NEU\n',
                             stdout=log,stderr=subprocess.STDOUT,cwd=folder,env=environment(),timeout=timeout)
            code=p.returncode
        except subprocess.TimeoutExpired:
            code='timeout'
    out=(folder/'stdout.log').read_text(errors='replace')
    result={'source':str(source),'mode':mode,'case':name or source.stem,'returncode':code,
            'seconds':round(time.monotonic()-start,3),'complete':'RECONAN_ANALYSIS_COMPLETE' in out,
            'partial':'RECONAN_ANALYSIS_PARTIAL' in out,'folder':str(folder)}
    (folder/'run.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['reference','integrated'])
    p.add_argument('source');p.add_argument('--name');p.add_argument('--timeout',type=int,default=300)
    a=p.parse_args();run(a.source,a.mode,a.name,a.timeout)
