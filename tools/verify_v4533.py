"""Check a fresh v4.533 build against the retained native verification runs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

from native_regression import CASES, stable_bytes
from native_verify import CASES as RC_CASES
from release_v4533 import RELEASE, ROOT, check


def retained_bytes(name, data):
    data, excluded = stable_bytes(name, data)
    out = []
    for line in data.splitlines(keepends=True):
        # The old writer appends the output-set ID after the wall-clock prefix.
        # Retain that ID, rather than dropping this entire NEU comment line.
        match = re.fullmatch(rb'Date : [0-9 ]{10} Clock : [0-9]+:[0-9]+:([ 0-9]+)\r?\n?', line)
        if match:
            out.append(b'NEU output-set ID: ' + match[1] + b'\n')
            excluded.append('NEU wall-clock date/time prefix; output-set ID retained')
        else:
            out.append(line)
    return b''.join(out), sorted(set(excluded))


def compare(left, right):
    records = []
    def relevant(folder):
        return {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()
                and p.suffix.lower() in ['.neu', '.dat', '.zip', '.vtk', '.vtu', '.pvd', '.csv', '.bin']
                and p.name not in ['model.NEU', 'model_TIME.dat']}
    assert relevant(left) == relevant(right), (left.name, 'output inventory differs')
    for name in sorted(relevant(left)):
        a, b = left / name, right / name
        if a.suffix.lower() == '.zip':
            with zipfile.ZipFile(a) as x, zipfile.ZipFile(b) as y:
                assert set(x.namelist()) == set(y.namelist())
                for member in sorted(x.namelist()):
                    old, new = x.read(member), y.read(member)
                    assert old == new, (left.name, name, member)
                    records.append({'file': name + '/' + member, 'equal': True,
                                    'bytes': len(old), 'sha256': hashlib.sha256(old).hexdigest()})
        else:
            old, new = a.read_bytes(), b.read_bytes()
            x, excluded = retained_bytes(a.name, old)
            y, _ = retained_bytes(b.name, new)
            assert x == y, (left.name, name)
            records.append({'file': name, 'equal': True, 'whole_file_equal': old == new,
                            'bytes': len(x), 'sha256': hashlib.sha256(x).hexdigest(),
                            'excluded_duration_fields': excluded})
    return records


def runtime_environment():
    env = os.environ.copy()
    env.update(RECONAN_TRIC3_RC_DETAIL='1',  # tool runs keep the per-step point/bar/nodal CSVs (2026-10-27 switch)
               RECONAN_BATCH='1', OMP_THREAD_LIMIT='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               MKL_DYNAMIC='FALSE', PATH=r'C:\Program Files (x86)\Intel\oneAPI\mkl\2026.1\bin;C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0')
    return env


def check_existing(case, rc=False):
    name = case
    folder = RELEASE / 'Build/VerifiedRuns' / name
    actual = json.loads((folder / 'run.json').read_text())
    old_folder = ROOT / 'native/runs/integrated' / name
    previous = json.loads((old_folder / 'run.json').read_text())
    assert all(actual[k] == previous[k] for k in ['returncode', 'complete', 'partial']), name
    assert (old_folder / 'model.NEU').read_bytes() == (folder / 'model.NEU').read_bytes(), name
    error = old_folder / 'model_ERROR_FILE.dat'
    if rc and previous['complete'] and error.exists() and error.read_bytes().strip():
        # Two successful canonical RC runs retained error logs from earlier failed
        # development runs. Re-run the unchanged native executable in a clean folder
        # instead of excluding the stale artifact or comparing unlike inventories.
        clean = RELEASE / 'Build/FreshNativeReference' / name
        clean.mkdir(parents=True, exist_ok=True)
        if not (clean / 'run.json').exists():
            shutil.copy2(folder / 'model.NEU', clean / 'model.NEU')
            with (clean / 'stdout.log').open('wb') as log:
                proc = subprocess.run([str(ROOT / 'native/build/integrated/ReConAn.exe')], input=b'model.NEU\n',
                                      cwd=clean, stdout=log, stderr=subprocess.STDOUT,
                                      env=runtime_environment(), timeout=300)
            output = (clean / 'stdout.log').read_text(errors='replace')
            assert proc.returncode == previous['returncode'] and 'RECONAN_ANALYSIS_COMPLETE' in output, name
            (clean / 'run.json').write_text(json.dumps({'returncode': proc.returncode, 'complete': True}) + '\n')
        old_folder = clean
    records = compare(old_folder, folder)
    result = {'case': name, 'RC': rc, **actual, 'reference': str(old_folder.relative_to(ROOT)),
              'all_retained_bytes_equal': True, 'comparisons': records}
    print(f'PASS {name}: {len(records)} file/member byte comparisons', flush=True)
    return result


def run(case, rc=False):
    name = case
    source = ROOT / ('native/examples' if rc else 'native/regression-inputs') / (name + '.NEU')
    folder = RELEASE / 'Build/VerifiedRuns' / name
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, folder / 'model.NEU')
    env = runtime_environment()
    with (folder / 'stdout.log').open('wb') as log:
        proc = subprocess.run([str(RELEASE / 'Build/ReConAn_v4.533.exe')], input=b'model.NEU\n',
                              cwd=folder, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=300)
    output = (folder / 'stdout.log').read_text(errors='replace')
    old_folder = ROOT / 'native/runs/integrated' / name
    previous = json.loads((old_folder / 'run.json').read_text())
    complete = 'RECONAN_ANALYSIS_COMPLETE' in output
    partial = 'RECONAN_ANALYSIS_PARTIAL' in output
    assert proc.returncode == previous['returncode'], (name, proc.returncode, previous['returncode'])
    assert complete == previous['complete'] and partial == previous['partial'], name
    (folder / 'run.json').write_text(json.dumps({'returncode': proc.returncode, 'complete': complete,
                                                'partial': partial}) + '\n')
    return check_existing(name, rc)


def main():
    check()
    build = RELEASE / 'Build'
    assert len(list(build.glob('*.obj'))) == 81, 'Expected a fresh compilation of all 81 sources'
    manifest = json.loads((RELEASE / 'SOURCE_MANIFEST.json').read_text())
    objects = []
    for record in manifest['files']:
        old = ROOT / 'native/build/integrated' / (Path(record['from']).stem + '.obj')
        new = build / (Path(record['to']).stem + '.obj')
        objects.append({'file': new.name, 'sha256': hashlib.sha256(new.read_bytes()).hexdigest(),
                        'identical_to_prior_native_object': old.read_bytes() == new.read_bytes()})
    jobs = [(name, False) for _, name in CASES]
    jobs += [(name, True) for name in RC_CASES]
    jobs += [('RC_cutoff', True), ('RC_invalid_Gf', True)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(check_existing if '--check-only' in sys.argv else run, name, rc) for name, rc in jobs]
        results = [f.result() for f in futures]
    report = {'fresh_compiled_sources': 81, 'build_flags': '/O2 /Qopenmp /Qmkl:parallel /libs:dll /threads /fpp',
              'prior_native_comparison_cases': len(results), 'all_match': True,
              'file_member_comparisons': sum(len(r['comparisons']) for r in results),
              'retained_bytes_compared': sum(c['bytes'] for r in results for c in r['comparisons']),
              'executable_sha256': hashlib.sha256((build / 'ReConAn_v4.533.exe').read_bytes()).hexdigest(),
              'objects': objects,
              'cases': results,
              'limits': 'Same deterministic single-thread settings as the earlier native report. Explicit elapsed-time '
                        'fields, wall-clock date/time prefixes in NEU comments and ZIP timestamps excluded. NEU output-set '
                        'IDs retained. Untested branches and parallel schedules are outside this evidence.'}
    out = RELEASE / 'Verification/v4.533-results.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# v4.533 source organization verification', '',
             'All 81 source files were freshly compiled and linked using Intel ifx 2026.1,',
             'oneMKL 2026.1 and VS2026 tools. No existing objects were reused.', '',
             f'{sum(o["identical_to_prior_native_object"] for o in objects)} of the 81 freshly built objects are byte-identical',
             'to the corresponding objects in the previous native delivery.', '',
             'The flat folder contains exactly 81 Fortran files, with unique object names and',
             'exact agreement with the supplied project selection. There are no external',
             'Fortran INCLUDE files. Of these, 80 files have unchanged contents relative to',
             'the previous native delivery. The other file only inlines the original diagnostic declarations.', '',
             f'All {len(results)} repeat cases matched the earlier native runs: 22 legacy cases and',
             '8 RC verification cases, plus the accepted-step cutoff and invalid-input cases.',
             f'The comparison covered {report["file_member_comparisons"]} file/member payloads and',
             f'{report["retained_bytes_compared"]:,} retained bytes.', '',
             'Two complete canonical RC runs had stale error logs from earlier development',
             'failures. Their references were freshly rerun with the unchanged native executable',
             'in clean directories; no error-log contents were excluded from the comparison.', '',
             'Explicit elapsed-time fields, ZIP timestamps, and the wall-clock date/time prefixes',
             'in NEU comment labels were excluded. The NEU output-set identifiers appended to',
             'those labels were retained. Every numerical result and RC CSV/NEU field was compared byte for byte.',
             'All runs used deterministic single-thread settings and app-local compiler/GLUT runtimes,',
             'with installed oneMKL kernels on PATH. A preliminary Windows-only PATH test',
             'exposed the cracking eigensolver dependency on dynamically loaded MKL kernels;',
             'the supplied launcher and build guide include the MKL runtime search path.',
             'Tested eligibility rejections and',
             'partial cases retained the same outcomes; their inclusion does not mean they completed.', '',
             'See v4.533-results.json for case outcomes and hashes. The earlier native report',
             'describes the formulation tests and remaining validation limits. Untested material',
             'branches and parallel schedules are not covered by this byte-equality evidence.', '']
    (RELEASE / 'Verification/v4.533_REPORT.md').write_text('\n'.join(lines))
    print(f'PASS all {len(results)} cases, {report["file_member_comparisons"]} retained byte comparisons', flush=True)


if __name__ == '__main__':
    main()
