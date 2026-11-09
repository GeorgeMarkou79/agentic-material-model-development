"""Deterministic runner of the agent loop (no LLM) (Claude, 2026-10-25).

  python agent_loop/loop_run.py full <NNN>            regenerate v4.533/Source from the patches, build TRIC_AL<NNN>,
                                                      unit tests (Atena, Markou, KP), isolation check (22 cases byte
                                                      equal), model set (agent_loop/model_hook.py applied), all runs in
                                                      parallel, OUT.neu fields, scores + physics checks + plots
                                                      -> agent_loop/iterations/<NNN>/ (status.json, report.json/md, png)
  python agent_loop/loop_run.py build <NNN>           developer check: regenerate + build + unit tests (no isolation, no runs)
  python agent_loop/loop_run.py one <NNN> <model>     developer check: one model of the set on build TRIC_AL<NNN>
  python agent_loop/loop_run.py models                regenerate the analytical slab models (tools/tric_slab_models.py)

Disk: run folders are reduced to curve/summary/logs/fields (+ the ReConAn Eye zip); OUT.neu files are deleted
after the fields are extracted; isolation run folders are deleted. Builds are pruned by loop_manager.py.
"""
import hashlib, importlib, json, os, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT / 'tools'))
import loop_neu, loop_checks

CFG = json.loads((HERE / 'config.json').read_text())
KEEP = ('_curve.csv', 'summary.json', 'stdout.log', '_ERROR_FILE.dat', '_TRIC3_RC_iterations.csv',
        '_TRIC3_RC_audit.csv', '_DisplacementControl_Diagnostics.dat', '_ReconanEye.zip')
TRACKED = ['tools/tric_kp_patch.py', 'tests/TRIC_KP.f90', 'agent_loop/model_hook.py']


def it_dir(n):
    d = HERE / 'iterations' / f'{int(n):03d}'; d.mkdir(parents=True, exist_ok=True); return d


def build_name(n):
    return f"{CFG['build_prefix']}{int(n):03d}"


def sh(cmd, log, timeout=None, cwd=ROOT):
    t = time.time()
    with open(log, 'ab') as f:
        f.write(f'\n$ {" ".join(map(str, cmd))}\n'.encode()); f.flush()
        try:
            p = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = 'timeout'
    return code, time.time() - t


def tail(path, n=60):
    try:
        return '\n'.join(Path(path).read_text(errors='replace').splitlines()[-n:])
    except OSError:
        return ''


def source_hash():
    h = hashlib.sha256()
    for p in TRACKED:
        q = ROOT / p
        h.update(p.encode()); h.update(q.read_bytes() if q.exists() else b'')
    return h.hexdigest()


def regenerate(log):
    """v4.533/Source and SOURCE_MANIFEST.json from the patch chain (tools/release_v4533.py)."""
    R = importlib.import_module('release_v4533')     # fresh process: imports the current patch modules
    man = ROOT / 'v4.533/SOURCE_MANIFEST.json'; M = json.loads(man.read_text()); recs = {r['to']: r for r in M['files']}
    changed = []
    for q in R.selected():
        name = R.RENAMES.get(q.name, q.name); tg = ROOT / 'v4.533/Source' / name; d = R.transformed(q)
        if tg.read_bytes() != d:
            tg.write_bytes(d); recs['Source/' + name]['sha256'] = hashlib.sha256(d).hexdigest(); changed.append(name)
    man.write_text(json.dumps(M, indent=2))
    with open(log, 'a') as f:
        f.write(f'regenerated Source: {changed}\n')
    code, _ = sh([PY, 'tools/release_v4533.py', 'check'], log)
    return code == 0


def kp_unit_test(bname, log):
    """Compile tests/TRIC_KP.f90 against the build objects (as TRIC_Markou in tools/tric_round2.py) and run it."""
    r2 = importlib.import_module('tric_round2')
    b = ROOT / 'v4.533/Build' / bname
    shutil.copy2(ROOT / 'tests/TRIC_KP.f90', b / 'TRIC_KP.f90')
    (b / 'kp_link.rsp').write_text((b / 'test_link.rsp').read_text().replace('TRIC_Atena.obj', 'TRIC_KP.obj')
                                   .replace('TRIC_AtenaTests.exe', 'TRIC_KPTests.exe'))
    env_lines = [l for l in (b / 'build.cmd').read_text().splitlines() if 'vars.bat' in l]
    cmd = '\n'.join(['@echo off', 'setlocal'] + env_lines + ['cd /d "%~dp0"', 'set "TMP=%CD%\\tmp"', 'set "TEMP=%CD%\\tmp"',
                     f'ifx {r2.FLAGS} "TRIC_KP.f90" > kp_compile.log 2>&1', 'if errorlevel 1 exit /b 1',
                     'ifx @kp_link.rsp > kp_link.log 2>&1']) + '\n'
    (b / 'kp_test.cmd').write_text(cmd)
    code, _ = sh(['cmd', '/c', b / 'kp_test.cmd'], log, cwd=b)
    if code != 0:
        with open(log, 'a') as f:
            f.write(tail(b / 'kp_compile.log') + '\n' + tail(b / 'kp_link.log') + '\n')
        return False, 'KP test compile/link failed'
    from verify_v4533 import runtime_environment
    folder = b / 'kp_unit'; folder.mkdir(exist_ok=True)
    with open(folder / 'stdout.log', 'wb') as f:
        p = subprocess.run([str(b / 'TRIC_KPTests.exe')], cwd=folder, env=runtime_environment(), stdout=f,
                           stderr=subprocess.STDOUT, timeout=900)
    text = (folder / 'stdout.log').read_text(errors='replace')
    with open(log, 'a') as f:
        f.write(text)
    return p.returncode == 0 and 'PASS TRIC_KP' in text, text.strip().splitlines()[-1] if text.strip() else 'no output'


def build(n, log):
    """Regenerate + build + unit tests; skipped if the build exists for the same tracked sources."""
    bname = build_name(n); b = ROOT / 'v4.533/Build' / bname; h = source_hash()
    if (b / 'loop_hash.txt').exists() and (b / 'loop_hash.txt').read_text() == h and (b / 'ReConAn_v4.533.exe').exists():
        return dict(ok=True, build=bname, reused=True)
    if not regenerate(log):
        return dict(ok=False, build=bname, stage='release check', log=tail(log))
    code, t = sh([PY, 'tools/tric_round2.py', 'build', bname], log, timeout=3600)
    if code != 0:
        compile_log = '\n'.join(tail(b / f, 40) for f in ('compile.log', 'link.log', 'test_compile.log', 'markou_compile.log'))
        return dict(ok=False, build=bname, stage='build/unit tests (Atena, Markou)', log=tail(log, 80) + '\n' + compile_log)
    ok, msg = kp_unit_test(bname, log)
    if not ok:
        return dict(ok=False, build=bname, stage='KP unit test', log=msg + '\n' + tail(log, 60))
    (b / 'loop_hash.txt').write_text(h)
    return dict(ok=True, build=bname, minutes=round(t / 60, 1), kp_unit=msg)


FLAKY = 'mixed_hybrid_kinematic_linear'   # known intermittent init access violation (exit 157), also with the
                                          # unchanged baseline executable (tools/tric_rebar_failure.py retries it)


def isolation(bname, log):
    """Isolation check; repeated (max 2 more times) only when the sole difference is the known flaky crash."""
    total = 0.; attempts = []
    for attempt in range(3):
        code, t = sh([PY, 'tools/tric_isolation_check.py', bname], log, timeout=3600); total += t
        line = [l for l in tail(log, 40).splitlines() if l.startswith('ISOLATION')]
        attempts.append(line[-1] if line else 'no summary')
        if code == 0:
            break
        try:
            res = json.loads((ROOT / 'verification/TRIC_Isolation' / (bname + '.json')).read_text())
        except (OSError, ValueError):
            break
        bad = [r for r in res if not r['byte_equal']]
        if not (len(bad) == 1 and bad[0]['case'] == FLAKY and bad[0]['outcome']['returncode'] == 157):
            break
        with open(log, 'a') as f:
            f.write(f'isolation: only {FLAKY} crashed with 157 (known intermittent) - repeating\n')
    shutil.rmtree(ROOT / 'verification/TRIC_Isolation/runs' / bname, ignore_errors=True)
    return dict(ok=code == 0, summary=attempts[-1], attempts=attempts, minutes=round(total / 60, 1))


def apply_hook(text):
    """Apply agent_loop/model_hook.transform_card to every identity-23 concrete card (values 49..74)."""
    hook = importlib.import_module('model_hook'); importlib.reload(hook)
    lines = text.split('\n'); i = 0; count = 0
    while i < len(lines):
        if lines[i].strip() == '402' and lines[i - 1].strip() == '-1':
            i += 1
            while lines[i].strip() != '-1':
                if lines[i].strip() == '190,':
                    vals = [t for l in lines[i + 1:i + 39] for t in l.strip().split(',') if t != '']
                    if len(vals) == 190 and float(vals[73]) == 23:
                        card = [float(v) for v in vals[48:74]]
                        new = [float(v) for v in hook.transform_card(list(card))]
                        assert len(new) == 26 and new[25] == 23 and new[13:25] == card[13:25], \
                            'model_hook may change concrete values only (not the reinforcement or the identity)'
                        if new != card:
                            vals[48:74] = [format(v, '.17g') for v in new]
                            for k in range(38):
                                lines[i + 1 + k] = ','.join(vals[5 * k:5 * k + 5]) + ','
                            count += 1
                    i += 39
                else:
                    i += 1
        i += 1
    return '\n'.join(lines), count


def prepare_models(n, only=None):
    """Copy the base models of every set into the iteration, with the model hook applied."""
    out = {}
    for s, spec in CFG['model_sets'].items():
        d = it_dir(n) / 'models' / s
        shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
        order = []
        for src in spec['sources']:
            src = ROOT / src
            for m in [l.strip() for l in (src / 'ORDER.txt').read_text().splitlines() if l.strip()]:
                if only and m != only:
                    continue
                text, changed = apply_hook((src / (m + '.NEU')).read_text())
                (d / (m + '.NEU')).write_text(text); shutil.copy2(src / (m + '.json'), d / (m + '.json'))
                order.append(m); out[m] = dict(set=s, hook_changed_cards=changed)
        (d / 'ORDER.txt').write_text('\n'.join(order) + '\n')
    return out


def run_sets(n, bname, models, tag, log, timeout_min=150):
    procs = []
    for s, spec in CFG['model_sets'].items():
        d = it_dir(n) / 'models' / s
        if not any(v['set'] == s for v in models.values()):
            continue
        f = open(it_dir(n) / f'run_{s}.log', 'wb')
        procs.append((s, subprocess.Popen([PY, spec['runner'], bname, tag, str(d), str(CFG['workers'][s])],
                                          cwd=ROOT, stdout=f, stderr=subprocess.STDOUT), f))
    t0 = time.time()
    for s, p, f in procs:
        try:
            p.wait(timeout=max(60, timeout_min * 60 - (time.time() - t0)))
        except subprocess.TimeoutExpired:
            p.kill(); f.write(b'\nKILLED: loop timeout\n')
        f.close()
    return round((time.time() - t0) / 60, 1)


def collect(n, models, tag):
    runs = it_dir(n) / 'runs'
    for m, v in models.items():
        src = ROOT / CFG['run_roots'][v['set']] / f'{m}_{tag}'
        dst = runs / m
        shutil.rmtree(dst, ignore_errors=True); dst.mkdir(parents=True)
        shutil.copy2(it_dir(n) / 'models' / v['set'] / (m + '.json'), dst / 'model.json')
        if not src.exists():
            continue
        for q in src.iterdir():
            if q.name.endswith(KEEP):
                shutil.copy2(q, dst / q.name)
        neu = src / f'{m}_OUT.neu'
        if neu.exists():
            try:
                loop_neu.extract(neu, dst / 'model.json', dst / f'{m}_fields.npz')
            except Exception as e:
                (dst / 'fields_error.txt').write_text(repr(e))
        shutil.rmtree(src, ignore_errors=True)


def full(n):
    d = it_dir(n); log = d / 'runner.log'; status = dict(iteration=f'{int(n):03d}', started=time.strftime('%Y-%m-%d %H:%M:%S'))
    def save():
        (d / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
    b = build(n, log); status['build'] = b; save()
    if not b['ok']:
        status['ok'] = False; save(); return status
    iso = isolation(b['build'], log); status['isolation'] = iso; save()
    if not iso['ok']:
        status['ok'] = False; save(); return status
    models = prepare_models(n); status['models'] = models
    tag = f'al{int(n):03d}'
    status['run_minutes'] = run_sets(n, b['build'], models, tag, log); save()
    collect(n, models, tag)
    rep = loop_checks.evaluate(d)
    status.update(ok=True, J=rep['J'], n_failed=rep['n_failed'], all_pass=rep['all_pass'],
                  finished=time.strftime('%Y-%m-%d %H:%M:%S')); save()
    return status


def one(n, model):
    """Single model on the developer build; returns summary + curve metric (no fields, no plots)."""
    bname = build_name(n)
    if not (ROOT / 'v4.533/Build' / bname / 'ReConAn_v4.533.exe').exists():
        return dict(ok=False, error=f'build {bname} missing: call build_and_unit_test first')
    sub = f'dev_{model}'
    d = it_dir(n) / 'dev'; d.mkdir(exist_ok=True)
    models = {}
    for s, spec in CFG['model_sets'].items():
        md = d / 'models' / s; shutil.rmtree(md, ignore_errors=True); md.mkdir(parents=True)
        for src in spec['sources']:
            src = ROOT / src
            if (src / (model + '.NEU')).exists():
                text, _ = apply_hook((src / (model + '.NEU')).read_text())
                (md / (model + '.NEU')).write_text(text); shutil.copy2(src / (model + '.json'), md / (model + '.json'))
                (md / 'ORDER.txt').write_text(model + '\n'); models[model] = dict(set=s)
    if not models:
        return dict(ok=False, error=f'unknown model {model}')
    s = models[model]['set']; tag = f'al{int(n):03d}dev'
    log = d / f'{sub}.log'
    code, t = sh([PY, CFG['model_sets'][s]['runner'], bname, tag, d / 'models' / s, '1'], log, timeout=3600)
    src = ROOT / CFG['run_roots'][s] / f'{model}_{tag}'
    res = dict(ok=code == 0, minutes=round(t / 60, 1))
    if (src / 'summary.json').exists():
        res['summary'] = json.loads((src / 'summary.json').read_text())
    if (src / f'{model}_curve.csv').exists():
        shutil.copy2(src / f'{model}_curve.csv', d / f'{model}_curve.csv')
        for case, cc in CFG['cases'].items():
            for mesh, run in cc['runs'].items():
                if run == model:
                    de, pe = loop_checks.read_curve(ROOT / cc['experiment'], cc['load_column'])
                    de = de - cc.get('displacement_shift_mm', 0.)
                    dn, pn = loop_checks.read_curve(d / f'{model}_curve.csv')
                    res['metric'] = loop_checks.metric(de, pe, dn, pn, prepend_zero=cc['kind'] != 'toronto')
        res['curve_tail'] = tail(d / f'{model}_curve.csv', 12)
    res['stdout_tail'] = tail(src / 'stdout.log', 25)
    shutil.rmtree(src, ignore_errors=True)
    return res


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'full':
        print(json.dumps(full(sys.argv[2]), indent=2))
    elif mode == 'build':
        print(json.dumps(build(sys.argv[2], it_dir(sys.argv[2]) / 'dev_build.log'), indent=2))
    elif mode == 'one':
        print(json.dumps(one(sys.argv[2], sys.argv[3]), indent=2))
    elif mode == 'models':
        subprocess.run([PY, 'tools/tric_slab_models.py'], cwd=ROOT, check=True)
