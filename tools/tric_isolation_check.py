"""Isolation check for the RC TRIC changes (Claude, 2026-10-23).

Runs the 22 non-RC native regression cases (tools/native_regression.py CASES: HEXA8/20
elastic, gravity and J2; TRIC elastic and J2; BEC; NBCFB; NBCFB-V; mixed hybrid, static
and dynamic) with a v4.533 build and compares every result file byte for byte with the
original reference results (native/runs/reference, original ReConAn), using the same
rule as native_regression.stable_bytes (only explicit wall-clock duration fields are
excluded; no numeric tolerance, no rounding).

usage: python tools/tric_isolation_check.py <build>      e.g. TRIC_OOP
writes verification/TRIC_Isolation/<build>.json and prints one line per case.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json, subprocess, sys, time, zipfile
from native_run import ROOT, ORIGINAL, run as run_reference
from native_regression import CASES, stable_bytes
from verify_v4533 import runtime_environment

SUFFIXES = ['.neu', '.dat', '.zip', '.vtk', '.vtu', '.pvd', '.csv', '.bin']


def source(file, name):
    special = ROOT / 'native/regression-inputs' / (name + '.NEU')
    return special if special.exists() else ORIGINAL / file


def run_build(build, file, name):
    exe = ROOT / 'v4.533/Build' / build / 'ReConAn_v4.533.exe'
    folder = ROOT / 'verification/TRIC_Isolation/runs' / build / name
    folder.mkdir(parents=True, exist_ok=True)
    for q in folder.iterdir():
        if q.is_file():
            q.unlink()
    (folder / 'model.NEU').write_bytes(Path(source(file, name)).read_bytes())
    start = time.monotonic()
    with (folder / 'stdout.log').open('wb') as log:
        try:
            code = subprocess.run([str(exe)], input=b'model.NEU\n', stdout=log, stderr=subprocess.STDOUT,
                                  cwd=folder, env=runtime_environment(), timeout=600).returncode
        except subprocess.TimeoutExpired:
            code = 'timeout'
    out = (folder / 'stdout.log').read_text(errors='replace')
    result = {'returncode': code, 'seconds': round(time.monotonic() - start, 3),
              'complete': 'RECONAN_ANALYSIS_COMPLETE' in out, 'partial': 'RECONAN_ANALYSIS_PARTIAL' in out}
    (folder / 'run.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def outcome(folder):
    # returncode as stored by the reference runner; complete/partial from the console log
    out = (folder / 'stdout.log').read_text(errors='replace')
    run = folder / 'run.json'
    code = json.loads(run.read_text())['returncode'] if run.exists() else None
    return {'returncode': code, 'complete': 'RECONAN_ANALYSIS_COMPLETE' in out,
            'partial': 'RECONAN_ANALYSIS_PARTIAL' in out}


def compare(left, right):
    records = []
    for path in left.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES or path.name in ['model.NEU', 'model_TIME.dat']:
            continue
        label = path.relative_to(left).as_posix(); other = right / path.relative_to(left)
        if not other.exists():
            records.append({'file': label, 'equal': False, 'missing': True}); continue
        if path.suffix.lower() == '.zip':
            with zipfile.ZipFile(path) as a, zipfile.ZipFile(other) as b:
                for member in sorted(set(a.namelist()) | set(b.namelist())):
                    x = a.read(member) if member in a.namelist() else b''
                    y = b.read(member) if member in b.namelist() else b''
                    records.append({'file': label + '/' + member, 'equal': x == y})
        else:
            x, _ = stable_bytes(path.name, path.read_bytes()); y, _ = stable_bytes(path.name, other.read_bytes())
            records.append({'file': label, 'equal': x == y})
    return records


def main(build):
    reference = ROOT / 'native/runs/reference'
    missing = [(f, n) for f, n in CASES if not (reference / n / 'run.json').exists()]
    for f, n in missing:
        run_reference(source(f, n), 'reference', n, 300)
    if '--compare-only' in sys.argv:
        runs = {n: outcome(ROOT / 'verification/TRIC_Isolation/runs' / build / n) for _, n in CASES}
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            runs = dict(zip([n for _, n in CASES], pool.map(lambda c: run_build(build, *c), CASES)))
    results = []
    for _, n in CASES:
        # Same outcome as the original (some reference cases stop by design, e.g. a LIN
        # eligibility warning or a partial nonlinear run) and identical result bytes.
        expected = outcome(reference / n)
        actual = {k: runs[n][k] for k in ['returncode', 'complete', 'partial']}
        records = compare(reference / n, ROOT / 'verification/TRIC_Isolation/runs' / build / n)
        ok = actual == expected and bool(records) and all(r['equal'] for r in records)
        results.append({'case': n, 'byte_equal': ok, 'files': len(records), 'outcome': actual,
                        'reference_outcome': expected, 'different': [r['file'] for r in records if not r['equal']]})
        print(('EQUAL     ' if ok else 'DIFFERENT ') + n, len(records), 'files', expected,
              '' if ok else (actual, results[-1]['different'][:6]), flush=True)
    out = ROOT / 'verification/TRIC_Isolation' / (build + '.json')
    out.write_text(json.dumps(results, indent=2) + '\n')
    print('ISOLATION', build, sum(r['byte_equal'] for r in results), 'of', len(results), 'cases byte equal', flush=True)
    return all(r['byte_equal'] for r in results)


if __name__ == '__main__':
    sys.exit(0 if main(sys.argv[1]) else 1)  # add --compare-only to re-check existing runs
