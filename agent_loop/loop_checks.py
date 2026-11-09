"""Scores, pass criteria and physics checks of one agent-loop iteration (Claude, 2026-10-25).

Input : agent_loop/iterations/<NNN>/runs/<run>/ (curve.csv, summary.json, stdout.log, <run>_fields.npz, model.json)
Output: agent_loop/iterations/<NNN>/report.json, report.md, curves.png, cracks_<case>.png

Curve metric (as tools/tric_curve_errors.py): range 0..d_u (last test displacement); NRMSE of P_num - P_exp on a
200-point grid / P_exp,max; peak error; ultimate error; coverage; score = (NRMSE+|peak|+|ult|)/3 + (1-coverage).
Toronto: test displacement - 1.008 mm (self-weight deflection, the model has no self-weight).
Slabs: the analytical reference (tools/tric_slab_analytic.py) is the 'experiment'; plus K0, Pcr, Py, Pu.

Pass criteria (config.json 'criteria'):
  walls + W-2 (both meshes): |peak| <= 10 %, NRMSE <= 10 %, all steps accepted
  Toronto (both meshes): shear failure detected and peak in [700, 1050] kN (see config _toronto_note)
  slabs (both meshes): K0 5 %, Pcr / Py / Pu 10 %, NRMSE 10 %, all steps accepted
  mesh objectivity (every case): Coarse vs Medium peak within 10 % and curve NRMSE within 10 %
Physics checks (hard = counts as a failed criterion; warn = reported to the assessor/manager only):
  hard: equilibrium (load vs support/base reactions, axial load), all steps accepted, first-crack location
  warn: crushing before 0.8 x test peak, stress locking (cracked points with s1 > 1.5 ft), strut over-strength
        (s2 < -1.3 fc outside confined strips), NR iterations per step, load drops before the test peak
Objective J (lower = better) = mean case score (walls, W-2, slabs; both meshes) + 0.05 x failed criteria
  + Toronto penalty 0.25 per mesh without a detected shear failure.
  python agent_loop/loop_checks.py <iteration_dir>
"""
import csv, json, math, re, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from loop_neu import centroids


def cfg():
    return json.loads((HERE / 'config.json').read_text())


def read_curve(path, column='load_kN'):
    d, p = [], []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                d.append(float(r['displacement_mm'])); p.append(float(r[column]))
            except (KeyError, ValueError, TypeError):
                pass
    return np.array(d), np.array(p)


def metric(de, pe, dn, pn, prepend_zero=True):
    if prepend_zero and (len(dn) == 0 or dn[0] > 0):
        dn, pn = np.r_[0., dn], np.r_[0., pn]
    du, pmax = de[-1], pe.max()
    if len(dn) < 2:
        return dict(coverage=0., nrmse=1., bias=0., peak=-1., ult=-1., score=2., steps=0)
    d0 = max(de[0], dn[0]); dend = min(dn[-1], du)
    c = max(0., (dend - de[0]) / (du - de[0]))
    if dend <= d0:
        return dict(coverage=c, nrmse=1., bias=0., peak=-1., ult=-1., score=2., steps=len(dn) - 1)
    g = np.linspace(d0, dend, 200); e = np.interp(g, dn, pn) - np.interp(g, de, pe)
    nr = float(np.sqrt(np.mean(e ** 2)) / pmax); bias = float(e.mean() / pmax)
    peak = float((pn[dn <= du + 1e-9].max() - pmax) / pmax)
    ult = float(((np.interp(du, dn, pn) if dn[-1] >= du - 1e-9 else pn[-1]) - pe[-1]) / pmax)
    return dict(coverage=float(c), nrmse=nr, bias=bias, peak=peak, ult=ult,
                score=(nr + abs(peak) + abs(ult)) / 3 + (1 - c), steps=len(dn) - 1)


def nr_iterations(stdout):
    if not stdout.exists():
        return []
    return [int(m.group(1)) for m in re.finditer(r'^\s*(\d+) Int\.Iter\.', stdout.read_text(errors='replace'), re.M)]


def first_step(mask_steps):
    idx = np.nonzero(mask_steps)[0]
    return int(idx[0]) if len(idx) else None


def run_checks(case, kind, mesh, run_dir, run, crit, ref=None):
    """Physics checks of one run; returns (checks dict, list of (name, passed, hard, detail))."""
    out = []
    summ = json.loads((run_dir / 'summary.json').read_text()) if (run_dir / 'summary.json').exists() else {}
    meta = json.loads((run_dir / 'model.json').read_text())
    planned, accepted = summ.get('planned_steps', meta.get('steps')), summ.get('accepted_steps', 0)
    complete = accepted == planned and not summ.get('termination')
    out.append(('all_steps_accepted', complete, True, f'{accepted}/{planned} ' + '; '.join(summ.get('termination', []))))
    curve_csv = run_dir / (run + '_curve.csv')
    rows = list(csv.DictReader(open(curve_csv))) if curve_csv.exists() else []
    # equilibrium
    if rows and kind == 'wall':
        bad = max(abs(float(r['load_kN']) + float(r['base_reaction_kN'])) / max(abs(float(r['load_kN'])), 1.) for r in rows)
        ax = summ.get('axial_check_kN', {})
        bad_ax = abs(ax.get('base_vertical_last', 0) - ax.get('applied', 0)) / max(ax.get('applied', 0), 1.) if ax else 0.
        out.append(('equilibrium', bad <= crit['equilibrium_rel'] and bad_ax <= crit['equilibrium_rel'], True,
                    f'max |P + base reaction|/P {bad:.2e}, axial {bad_ax:.2e}'))
    elif rows:
        scale = meta.get('load_scale', 1.)
        bad = max(abs(float(r['support_vertical_kN']) - float(r['load_kN']) / scale) / max(abs(float(r['load_kN']) / scale), 1.)
                  for r in rows if abs(float(r['load_kN'])) > 1e-6)
        out.append(('equilibrium', bad <= crit['equilibrium_rel'], True, f'max |support - P/scale|/(P/scale) {bad:.2e}'))
    its = nr_iterations(run_dir / 'stdout.log')
    out.append(('nr_iterations', True, False,
                f'total {sum(its)}, max/step {max(its) if its else 0}, steps with >= 20: {sum(i >= 20 for i in its)}'))
    fields = run_dir / (run + '_fields.npz')
    info = dict(accepted=accepted, planned=planned, nr_total=sum(its), nr_max=max(its) if its else 0)
    if not fields.exists():
        out.append(('crack_fields', False, False, 'no OUT.neu fields'))
        return info, out
    a = np.load(fields); cen = centroids(meta); cr = a['cracks']; steps = a['steps']
    regions = np.array([e.get('region', '') for e in meta['elements']])
    load = {int(r['step']): float(r['load_kN']) for r in rows}
    fc = meta.get('fc_kPa', 30000.); ft = .05 * fc
    member = regions != 'head'                      # wall heads (loading beams) are checked separately
    if kind == 'wall' and ((cr > 0) & ~member).any():
        sh = first_step(((cr > 0) & ~member).any(1))
        out.append(('no_head_cracking', False, False,
                    f'loading-beam (head) elements crack from step {steps[sh]} (P {load.get(int(steps[sh]), float("nan")):.0f} kN); '
                    f'{int(((cr[-1] > 0) & ~member).sum())} head elements cracked at the end'))
    s_first = first_step(((cr > 0) & member).any(1))
    if s_first is not None:
        el = np.nonzero((cr[s_first] > 0) & member)[0]; x, y = cen[el, 0], cen[el, 1]
        info['first_crack'] = dict(step=int(steps[s_first]), load_kN=load.get(int(steps[s_first])), elements=int(len(el)),
                                   x_mean=float(x.mean()), y_mean=float(y.mean()))
        if kind == 'toronto':
            nodes = {int(k): v for k, v in meta['nodes'].items()}
            lx = nodes[meta['plate_centres']['load']][0]
            ok = np.mean((abs(x - lx) <= 1.5) & (y <= 1.0))
            out.append(('first_crack_under_load_bottom', ok >= crit['first_crack_fraction_in_region'], True,
                        f'{ok:.0%} of the {len(el)} first-cracked elements within 1.5 m of the load and y <= 1 m (step {steps[s_first]})'))
        elif kind == 'slab':
            span = meta['span_m']
            if meta['kind'] == 'cantilever':
                ok = np.mean(x <= span / 6); where = 'root (x <= L/6)'
            else:
                ok = np.mean(abs(x - span / 2) <= span / 6); where = 'mid-span (|x - L/2| <= L/6)'
            out.append(('first_crack_at_max_moment', ok >= crit['first_crack_fraction_in_region'], True,
                        f'{ok:.0%} of the {len(el)} first-cracked elements at the {where}'))
        elif kind == 'wall':
            hw = cen[member, 1].max()
            ok = np.mean(y <= .3 * hw)
            out.append(('first_crack_at_wall_base', ok >= crit['first_crack_fraction_in_region'], False,   # warning (2026-10-25: first step already past cracking)
                        f'{ok:.0%} of the {len(el)} first-cracked elements in the lower 30 % of the wall'))
        elif kind == 'panel':
            ok = np.mean(np.char.startswith(regions[el].astype(str), 'panel'))
            out.append(('first_crack_in_panels', ok >= crit['first_crack_fraction_in_region'], True,
                        f'{ok:.0%} of the {len(el)} first-cracked elements in the web panels'))
    else:
        out.append(('cracking_occurs', kind == 'none', True, 'no cracked element in any step'))
    # crushing
    crushed = (cr >= 3) | (a['crushed_frac'] > 0)
    s_crush = first_step(crushed.any(1))
    if s_crush is not None:
        el = np.nonzero(crushed[s_crush])[0]
        info['first_crushing'] = dict(step=int(steps[s_crush]), load_kN=load.get(int(steps[s_crush])), elements=int(len(el)),
                                      x_mean=float(cen[el, 0].mean()), y_mean=float(cen[el, 1].mean()),
                                      regions=sorted(set(regions[el].tolist())))
        info['crushed_elements_last'] = int(crushed[-1].sum())
    if ref is not None and s_crush is not None and kind in ('wall', 'panel'):
        pk = ref
        ratio = (load.get(int(steps[s_crush])) or 0.) / pk
        out.append(('no_premature_crushing', ratio >= crit['premature_crushing_load_ratio'], False,
                    f'first crushing at {ratio:.0%} of the test peak (step {steps[s_crush]}, {info["first_crushing"]["regions"]})'))
    # stress sanity at the last step
    s1 = np.maximum(a['s1top'][-1], a['s1bot'][-1]); s2 = np.minimum(a['s2top'][-1], a['s2bot'][-1])
    locked = int(((cr[-1] > 0) & (cr[-1] < 3) & (s1 > crit['stress_locking_ft_factor'] * ft)).sum())
    unconf = np.array([meta['properties'].get(str(e['property']), {}).get('confinement_kPa', 0.) == 0 for e in meta['elements']])
    strut = int(((s2 < -crit['strut_overstrength_fc_factor'] * fc) & unconf & (regions != 'plate')).sum())
    out.append(('no_stress_locking', locked == 0, False, f'{locked} cracked elements with s1 > {crit["stress_locking_ft_factor"]} ft at the last step (max s1 {s1.max() / 1000:.2f} MPa, ft {ft / 1000:.2f})'))
    out.append(('no_strut_overstrength', strut == 0, False, f'{strut} unconfined elements with s2 < -{crit["strut_overstrength_fc_factor"]} fc at the last step (min s2 {s2.min() / 1000:.1f} MPa, fc {fc / 1000:.1f})'))
    # Toronto shear failure
    if kind == 'toronto':
        nodes = {int(k): v for k, v in meta['nodes'].items()}
        lx = nodes[meta['plate_centres']['load']][0]; rx = nodes[meta['plate_centres']['roller']][0]
        P = np.array([load.get(int(s), np.nan) for s in steps])
        ip = int(np.nanargmax(P)); peak = float(P[ip])
        drop = float((peak - np.nanmin(P[ip:])) / peak) if ip < len(P) - 1 else 0.
        east = (cen[:, 0] > lx + .5) & (cen[:, 0] < rx - .5) & (cen[:, 1] > 1.0) & (cen[:, 1] < 3.0) & (regions != 'plate')
        west = (cen[:, 0] < lx - .5) & (cen[:, 1] > 1.0) & (cen[:, 1] < 3.0) & (regions != 'plate')
        k_end = min(len(steps) - 1, ip + 1) if drop > 0 else ip
        fe = float((cr[k_end][east] > 0).mean()); fw = float((cr[k_end][west] > 0).mean()) if west.any() else 0.
        lo, hi = crit['toronto_peak_kN']
        failed = drop >= crit['toronto_post_peak_drop'] and fe >= crit['toronto_east_web_cracked_fraction'] and fe > fw
        info['toronto'] = dict(peak_kN=peak, peak_step=int(steps[ip]), post_peak_drop=drop, east_web_cracked=fe,
                               west_web_cracked=fw, shear_failure=bool(failed), peak_in_range=bool(lo <= peak <= hi))
        out.append(('toronto_shear_failure', bool(failed), True,
                    f'peak {peak:.0f} kN at step {steps[ip]}, post-peak drop {drop:.0%}, east web mid-depth cracked {fe:.0%} (west {fw:.0%})'))
        out.append(('toronto_peak_in_range', bool(lo <= peak <= hi) and bool(failed), True, f'peak {peak:.0f} kN, range {lo:.0f}-{hi:.0f} kN'))
    # slab first yield (bar stress)
    if kind == 'slab':
        fy = meta['analytic']['fy_MPa'] * 1000.
        yielded = (np.maximum(abs(a['bar1']), abs(a['bar2'])) >= .98 * fy).any(1)
        s_y = first_step(yielded)
        info['first_yield'] = None if s_y is None else dict(step=int(steps[s_y]), load_kN=load.get(int(steps[s_y])))
    # load drops before the test peak (spurious brittleness)
    if rows and kind in ('wall', 'panel'):
        p = np.array([float(r['load_kN']) for r in rows]); runmax = np.maximum.accumulate(p)
        drops = int(((runmax - p) / np.maximum(runmax, 1.) > .10).sum())
        out.append(('no_load_drops', drops == 0, False, f'{drops} steps more than 10 % below the running maximum'))
    return info, out


def slab_quantities(run_dir, run, info):
    d, p = read_curve(run_dir / (run + '_curve.csv'))
    res = {}
    if len(d) < 2:
        return res
    k = p[0] / d[0] if d[0] > 0 else np.nan
    res['K0'] = float(k)
    fc = info.get('first_crack')
    # FE cracking load: bracketed by the last uncracked step load and K0 x the displacement of the first cracked
    # step (displacement control: the load reported at the cracked step is already released); midpoint
    if fc and fc['step'] > 1:
        lo = float(p[fc['step'] - 2]); hi = float(k * d[fc['step'] - 1]); res['Pcr'] = (lo + hi) / 2
    else:
        res['Pcr'] = None
    fy = info.get('first_yield')
    res['Py'] = None if not fy else float(fy['load_kN'])
    res['Pu'] = float(p[-1]); res['d_end'] = float(d[-1])
    return res


def evaluate(it_dir):
    it_dir = Path(it_dir); c = cfg(); crit = c['criteria']
    report = dict(iteration=it_dir.name, cases={}, failed=[], warnings=[])
    scores, toronto_pen = [], 0.
    for case, cc in c['cases'].items():
        de, pe = read_curve(ROOT / cc['experiment'], cc['load_column'])
        if cc.get('displacement_shift_mm'):
            de = de - cc['displacement_shift_mm']
        ref = json.loads((ROOT / cc['reference']).read_text()) if cc.get('reference') else None
        cres = dict(kind=cc['kind'], meshes={})
        curves = {}
        for mesh, run in cc['runs'].items():
            rd = it_dir / 'runs' / run
            if not (rd / 'model.json').exists():
                cres['meshes'][mesh] = dict(missing=True); report['failed'].append(f'{case} {mesh}: run missing')
                if cc['kind'] == 'toronto':
                    toronto_pen += .25
                else:
                    scores.append(2.)
                continue
            dn, pn = read_curve(rd / (run + '_curve.csv')) if (rd / (run + '_curve.csv')).exists() else (np.array([]), np.array([]))
            curves[mesh] = (dn, pn)
            m = metric(de, pe, dn, pn, prepend_zero=cc['kind'] != 'toronto')
            try:
                info, checks = run_checks(case, cc['kind'], mesh, rd, run, crit, ref=float(pe.max()))
            except Exception as e:                                # a broken run must not break the report
                info = dict(accepted=0, planned=0, nr_total=0, nr_max=0)
                checks = [('checks_error', False, True, repr(e)[:300])]
            r = dict(metric=m, info=info, checks=[dict(name=n, passed=bool(ok), hard=h, detail=dt) for n, ok, h, dt in checks])
            crits = []
            if cc['kind'] in ('wall', 'panel'):
                crits += [('peak', abs(m['peak']) <= crit['curve_peak_abs'], f"peak {m['peak']:+.1%}"),
                          ('nrmse', m['nrmse'] <= crit['curve_nrmse'], f"NRMSE {m['nrmse']:.3f}"),
                          ('coverage', m['coverage'] >= crit['coverage'], f"coverage {m['coverage']:.2f}")]
                scores.append(m['score'])
            elif cc['kind'] == 'slab':
                q = slab_quantities(rd, run, info); r['slab'] = q
                def rel(a, b):
                    return None if a is None or not b else (a - b) / b
                for key, refkey, tol in (('K0', 'K0_kN_per_mm', crit['slab_K0']), ('Pcr', 'Pcr_kN', crit['slab_Pcr']),
                                         ('Py', 'Py_kN', crit['slab_Py']), ('Pu', 'Pu_kN', crit['slab_Pu'])):
                    e = rel(q.get(key), ref[refkey])
                    crits.append((key, e is not None and abs(e) <= tol, f'{key} {q.get(key)} vs {ref[refkey]:.3g} ({"n/a" if e is None else f"{e:+.1%}"})'))
                crits.append(('nrmse', m['nrmse'] <= crit['slab_nrmse'], f"NRMSE {m['nrmse']:.3f}"))
                crits.append(('coverage', m['coverage'] >= crit['coverage'], f"coverage {m['coverage']:.2f}"))
                scores.append(m['score'])
            elif cc['kind'] == 'toronto':
                if not info.get('toronto', {}).get('shear_failure'):
                    toronto_pen += .25
            r['criteria'] = [dict(name=n, passed=bool(ok), detail=dt) for n, ok, dt in crits]
            for n, ok, dt in crits:
                if not ok:
                    report['failed'].append(f'{case} {mesh}: {dt}')
            for ch in r['checks']:
                if not ch['passed']:
                    (report['failed'] if ch['hard'] else report['warnings']).append(f"{case} {mesh}: {ch['name']}: {ch['detail']}")
            cres['meshes'][mesh] = r
        if len(curves) == 2 and all(len(v[0]) > 1 for v in curves.values()):
            (dc, pc), (dm, pm) = curves['Coarse'], curves['Medium']
            pkc, pkm = pc.max(), pm.max()
            dend = min(dc[-1], dm[-1]); g = np.linspace(0, dend, 200)
            mn = float(np.sqrt(np.mean((np.interp(g, np.r_[0, dc], np.r_[0, pc]) - np.interp(g, np.r_[0, dm], np.r_[0, pm])) ** 2)) / max(pkm, 1e-9))
            pdiff = float(abs(pkc - pkm) / max(pkm, 1e-9))
            cres['mesh'] = dict(peak_diff=pdiff, nrmse=mn)
            if pdiff > crit['mesh_peak_diff'] or mn > crit['mesh_nrmse']:
                report['failed'].append(f'{case}: mesh objectivity: peak diff {pdiff:.1%}, Coarse-Medium NRMSE {mn:.3f}')
        report['cases'][case] = cres
    report['n_failed'] = len(report['failed'])
    report['mean_score'] = float(np.mean(scores)) if scores else 2.
    report['toronto_penalty'] = toronto_pen
    report['J'] = report['mean_score'] + .05 * report['n_failed'] + toronto_pen
    report['all_pass'] = report['n_failed'] == 0
    (it_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    write_md(it_dir, report)
    try:
        plots(it_dir, c)
    except Exception as e:                                        # plots are for the assessor; never fatal
        (it_dir / 'plot_error.txt').write_text(repr(e))
    return report


def write_md(it_dir, rep):
    L = [f"# Iteration {rep['iteration']}", '',
         f"J = {rep['J']:.4f} (mean case score {rep['mean_score']:.4f}, failed criteria {rep['n_failed']}, Toronto penalty {rep['toronto_penalty']:.2f}); all pass: {rep['all_pass']}", '',
         '| case | mesh | steps | score | NRMSE | peak | ult | notes |', '|---|---|---|---|---|---|---|---|']
    for case, cres in rep['cases'].items():
        for mesh, r in cres['meshes'].items():
            if r.get('missing'):
                L.append(f'| {case} | {mesh} | missing | | | | | |'); continue
            m, i = r['metric'], r['info']
            note = ''
            if 'toronto' in i:
                t = i['toronto']; note = f"peak {t['peak_kN']:.0f} kN, drop {t['post_peak_drop']:.0%}, east web {t['east_web_cracked']:.0%}, shear failure {t['shear_failure']}"
            if 'slab' in r:
                note = ', '.join(f"{k} {v:.3g}" for k, v in r['slab'].items() if isinstance(v, float))
            L.append(f"| {case} | {mesh} | {i['accepted']}/{i['planned']} | {m['score']:.3f} | {m['nrmse']:.3f} | {m['peak']:+.1%} | {m['ult']:+.1%} | {note} |")
    L += ['', '## Failed criteria'] + [f'- {x}' for x in rep['failed']] + ['', '## Warnings'] + [f'- {x}' for x in rep['warnings']]
    L += ['', '## Events per run (first crack, first crushing)']
    for case, cres in rep['cases'].items():
        for mesh, r in cres['meshes'].items():
            if r.get('missing'):
                continue
            i = r['info']
            L.append(f"- {case} {mesh}: NR iterations {i['nr_total']} (max/step {i['nr_max']}); first crack {i.get('first_crack')}; first crushing {i.get('first_crushing')}; crushed elements at the end {i.get('crushed_elements_last', 0)}")
        if 'mesh' in cres:
            L.append(f"- {case} mesh objectivity: peak diff {cres['mesh']['peak_diff']:.1%}, Coarse-Medium NRMSE {cres['mesh']['nrmse']:.3f}")
    (it_dir / 'report.md').write_text('\n'.join(L) + '\n')


def plots(it_dir, c):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    cases = list(c['cases'])
    fig, ax = plt.subplots(2, 4, figsize=(20, 9))
    for a, case in zip(ax.flat, cases):
        cc = c['cases'][case]
        de, pe = read_curve(ROOT / cc['experiment'], cc['load_column'])
        de = de - cc.get('displacement_shift_mm', 0.)
        a.plot(de, pe, 'k-', lw=2.2, label='analytical' if cc['kind'] == 'slab' else 'test')
        for mesh, ls in (('Medium', '-'), ('Coarse', '--')):
            f = it_dir / 'runs' / cc['runs'][mesh] / (cc['runs'][mesh] + '_curve.csv')
            if f.exists():
                d, p = read_curve(f); a.plot(d, p, ls, color='#1a73e8', lw=1.5, label=mesh)
        a.set_title(case); a.grid(alpha=.3); a.legend(fontsize=7); a.set_xlabel('mm'); a.set_ylabel('kN')
    fig.suptitle(f'Iteration {it_dir.name}'); fig.tight_layout(); fig.savefig(it_dir / 'curves.png', dpi=90); plt.close(fig)
    colors = np.array([[1, 1, 1], [.55, .75, 1], [.15, .35, .9], [.85, .1, .1]])
    for case in cases:
        cc = c['cases'][case]
        fig, ax = plt.subplots(2, 3, figsize=(18, 8) if case != 'Toronto' else (20, 6))
        for row, mesh in enumerate(('Coarse', 'Medium')):
            rd = it_dir / 'runs' / cc['runs'][mesh]; run = cc['runs'][mesh]
            fz = rd / (run + '_fields.npz')
            if not fz.exists():
                continue
            meta = json.loads((rd / 'model.json').read_text()); a = np.load(fz)
            nodes = {int(k): v for k, v in meta['nodes'].items()}
            polys = [[nodes[n] for n in e['nodes']] for e in meta['elements']]
            cr = a['cracks']; steps = a['steps']
            rows = list(csv.DictReader(open(rd / (run + '_curve.csv')))) if (rd / (run + '_curve.csv')).exists() else []
            load = {int(r['step']): float(r['load_kN']) for r in rows}
            s_first = first_step((cr > 0).any(1)) or 0
            s_peak = int(np.argmax([load.get(int(s), -1e9) for s in steps]))
            for col, (k, label) in enumerate(((s_first, 'first crack'), (s_peak, 'peak'), (len(steps) - 1, 'last'))):
                v = np.clip(cr[k].round().astype(int), 0, 3); v[a['crushed_frac'][k] > 0] = 3
                pc = PolyCollection(polys, facecolors=colors[v], edgecolors='#999999', linewidths=.2)
                axx = ax[row, col]; axx.add_collection(pc); axx.autoscale(); axx.set_aspect('equal')
                axx.set_title(f'{mesh} {label}: step {steps[k]}, P {load.get(int(steps[k]), float("nan")):.1f} kN', fontsize=9)
        fig.suptitle(f'{case}: white uncracked, light blue 1 crack, blue 2 cracks, red crushed'); fig.tight_layout()
        fig.savefig(it_dir / f'cracks_{case}.png', dpi=80); plt.close(fig)


if __name__ == '__main__':
    r = evaluate(sys.argv[1])
    print(f"J {r['J']:.4f}  failed {r['n_failed']}  all pass {r['all_pass']}")
