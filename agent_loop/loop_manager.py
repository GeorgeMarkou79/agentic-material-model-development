"""Driver of the autonomous agent loop: manager -> developer -> runner -> assessor (Claude, 2026-10-25).

  python agent_loop/loop_manager.py            run one night (stops at the budget, the iteration limit, the stop
                                               time, 'all pass', or the manager's decision)
  python agent_loop/loop_manager.py --check    dry check of the set-up (no API call, no build, no analysis)

Agents (Claude Agent SDK, Console API key): manager (plans, read-only), developer (edits only the allowed files,
tools build_and_unit_test / run_one_model), assessor (independent, reads only the iteration folder and the best
report). The runner (agent_loop/loop_run.py) is deterministic code. The driver enforces the budget, the write
guard, the rule checks, acceptance of a new best, restoring files, the ledger and the disk housekeeping.
API key: environment variable ANTHROPIC_API_KEY, or the first line of agent_loop/api_key.txt (keep it private).
"""
import asyncio, datetime, difflib, hashlib, json, os, re, shutil, subprocess, sys, time, traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CFG = json.loads((HERE / 'config.json').read_text())
PY = sys.executable
TRACKED = ['tools/tric_kp_patch.py', 'tests/TRIC_KP.f90', 'agent_loop/model_hook.py']
STATE = HERE / 'state.json'
LEDGER = HERE / 'ledger.jsonl'
GUARDED = ['tools', 'tests', 'agent_loop', 'v4.533/Models', 'verification/TRIC_Lefas/experiment',
           'verification/TRIC_Bench/experiment', 'verification/TRIC_Slab/experiment'] + \
          [s for spec in CFG['model_sets'].values() for s in spec['sources']]
GUARD_SKIP = ('agent_loop/iterations', 'agent_loop/state.json', 'agent_loop/ledger.jsonl', 'agent_loop/LEDGER.md',
              'agent_loop/NIGHT_REPORT', 'agent_loop/api_key.txt', 'agent_loop/driver.log', '__pycache__')
CRACK_LIMIT = re.compile(r'incriter|cracks?[ _-]*per[ _-]*iteration|admission|max_?new_?cracks|crack_?queue'
                         r'|n_?cr_?iter', re.I)
TEST_NAMES = re.compile(r'\bSW ?\d\d\b|toronto|lefas|cervenka|collins|slabcant|slabss|\bW-?2\b', re.I)


# ---------------------------------------------------------------- small helpers
def now():
    return datetime.datetime.now()


def log(msg):
    line = f'[{now():%Y-%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    with open(HERE / 'driver.log', 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def it_dir(n):
    d = HERE / 'iterations' / f'{int(n):03d}'; d.mkdir(parents=True, exist_ok=True); return d


def read(p, limit=None):
    try:
        t = Path(p).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    return t if limit is None or len(t) <= limit else t[:limit] + '\n...[truncated]'


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return dict(best=None, next_iteration=0, nights={}, consecutive_failures=0, closed_families=[])


def save_state(s):
    STATE.write_text(json.dumps(s, indent=2) + '\n')


def guarded_hash():
    h = {}
    for g in GUARDED:
        base = ROOT / g
        files = [base] if base.is_file() else (base.rglob('*') if base.exists() else [])
        for p in files:
            rel = p.relative_to(ROOT).as_posix()
            if p.is_file() and rel not in TRACKED and not any(rel.startswith(s) or s in rel for s in GUARD_SKIP):
                h[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return h


def snapshot(n):
    d = it_dir(n) / 'snapshot'; d.mkdir(exist_ok=True)
    for t in TRACKED:
        shutil.copy2(ROOT / t, d / Path(t).name)


def restore(n):
    d = it_dir(n) / 'snapshot'
    for t in TRACKED:
        if (d / Path(t).name).exists():
            shutil.copy2(d / Path(t).name, ROOT / t)


def tracked_equal_snapshot(n):
    d = it_dir(n) / 'snapshot'
    return all((d / Path(t).name).exists() and (d / Path(t).name).read_bytes() == (ROOT / t).read_bytes() for t in TRACKED)


def diff_against(n_base):
    d = it_dir(n_base) / 'snapshot'; out = []
    for t in TRACKED:
        a = read(d / Path(t).name).splitlines(keepends=True); b = read(ROOT / t).splitlines(keepends=True)
        out += difflib.unified_diff(a, b, fromfile=f'best/{t}', tofile=f'new/{t}')
    return ''.join(out)


def assertion_count(text):
    return len(re.findall(r'call\s+check\s*\(', text, re.I))


def run_runner(args, timeout):
    p = subprocess.run([PY, str(HERE / 'loop_run.py')] + [str(a) for a in args], cwd=ROOT,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout[-20000:] + ('\n' + p.stderr[-6000:] if p.stderr.strip() else '')


def ledger_add(entry):
    with open(LEDGER, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')
    rows = [json.loads(l) for l in LEDGER.read_text(encoding='utf-8').splitlines() if l.strip()]
    L = ['# Agent loop ledger', '', 'J = mean case score + 0.05 x failed criteria + Toronto penalty (lower is better).', '',
         '| it | time | hypothesis | family | base | J | failed | verdict | accepted | best | cost $ |',
         '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        L.append(f"| {r.get('iteration')}{' (candidate)' if r.get('candidate') else ''} | {r.get('time', '')[5:16]} | {r.get('hypothesis_id', '')} | {r.get('family', '')} | "
                 f"{r.get('base', '')} | {r.get('J', '') if r.get('J') is None else round(r['J'], 4)} | {r.get('n_failed', '')} | "
                 f"{r.get('verdict', '')} | {r.get('accepted', '')} | {r.get('best_after', '')} | {r.get('cost_usd', 0):.2f} |")
    L += ['', '## Details', '']
    for r in rows:
        L.append(f"### {r.get('iteration')} {r.get('hypothesis_id', '')}")
        for k in ('hypothesis', 'outcome', 'developer_summary', 'assessor_findings', 'next_step_advice', 'problems'):
            if r.get(k):
                L.append(f"- **{k}**: {r[k] if isinstance(r[k], str) else json.dumps(r[k])}")
        L.append('')
    (HERE / 'LEDGER.md').write_text('\n'.join(L) + '\n', encoding='utf-8')


def prune_builds(state, current):
    keep = {CFG['baseline_build']}
    if state.get('best'):
        keep.add(state['best']['build'])
    ours = sorted(p for p in (ROOT / 'v4.533/Build').glob(CFG['build_prefix'] + '*') if p.is_dir())
    recent = {p.name for p in ours[-CFG['keep_builds']:]} | {f"{CFG['build_prefix']}{int(current):03d}"}
    for p in ours:
        if p.name not in keep and p.name not in recent:
            shutil.rmtree(p, ignore_errors=True); log(f'deleted build {p.name}')
    for d in (HERE / 'iterations').glob('*'):
        for sub in ('dev', 'models'):
            if (d / sub).exists() and d.name != f'{int(current):03d}':
                shutil.rmtree(d / sub, ignore_errors=True)


# ---------------------------------------------------------------- budget
class Budget:
    def __init__(self, state):
        self.state = state; self.key = f'{now():%Y-%m-%d}' if now().hour >= 12 else f'{now() - datetime.timedelta(days=1):%Y-%m-%d}'
        self.cap = float(CFG['budget_usd_per_night']); self.state['nights'].setdefault(self.key, 0.)

    @property
    def spent(self):
        return self.state['nights'][self.key]

    def remaining(self):
        return self.cap - self.spent

    def add(self, usd):
        self.state['nights'][self.key] = self.spent + float(usd); save_state(self.state)

    def can_afford(self, *roles):
        typical = {'manager': 1.0, 'developer': 3.0, 'assessor': 1.0}     # do not start an iteration that cannot finish
        need = sum(min(CFG['per_call_budget_usd'][r], typical[r]) for r in roles) + CFG['stop_reserve_usd']
        return self.remaining() >= need


# ---------------------------------------------------------------- agents
def api_key():
    k = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not k and (HERE / 'api_key.txt').exists():
        k = (HERE / 'api_key.txt').read_text().strip().splitlines()[0].strip()
    return k


SCHEMAS = {
    'manager': {'type': 'object', 'additionalProperties': False,
                'required': ['decision', 'reason', 'hypothesis_id', 'family', 'extension_type', 'hypothesis',
                             'base_iteration', 'developer_instructions', 'expected_effects', 'first_model_to_check'],
                'properties': {'decision': {'type': 'string', 'enum': ['develop', 'stop']}, 'reason': {'type': 'string'},
                               'hypothesis_id': {'type': 'string'}, 'family': {'type': 'string'},
                               'extension_type': {'type': 'integer'}, 'hypothesis': {'type': 'string'},
                               'base_iteration': {'type': 'string'}, 'developer_instructions': {'type': 'string'},
                               'expected_effects': {'type': 'string'}, 'first_model_to_check': {'type': 'string'}}},
    'developer': {'type': 'object', 'additionalProperties': False,
                  'required': ['implemented', 'summary', 'files_changed', 'physical_basis', 'new_parameters',
                               'unit_tests_added', 'build_result', 'run_one_model_results', 'caveats'],
                  'properties': {'implemented': {'type': 'boolean'}, 'summary': {'type': 'string'},
                                 'files_changed': {'type': 'array', 'items': {'type': 'string'}},
                                 'physical_basis': {'type': 'string'}, 'new_parameters': {'type': 'string'},
                                 'unit_tests_added': {'type': 'string'}, 'build_result': {'type': 'string'},
                                 'run_one_model_results': {'type': 'string'}, 'caveats': {'type': 'string'}}},
    'assessor': {'type': 'object', 'additionalProperties': False,
                 'required': ['verdict', 'rule_violations', 'gaming_suspected', 'physics_assessment', 'prediction_check',
                              'key_findings', 'next_step_advice'],
                 'properties': {'verdict': {'type': 'string', 'enum': ['improved', 'worse', 'mixed', 'invalid']},
                                'rule_violations': {'type': 'array', 'items': {'type': 'string'}},
                                'gaming_suspected': {'type': 'boolean'}, 'physics_assessment': {'type': 'string'},
                                'prediction_check': {'type': 'string'},
                                'key_findings': {'type': 'array', 'items': {'type': 'string'}},
                                'next_step_advice': {'type': 'string'}}},
}


def within(p, roots):
    try:
        rp = Path(p).resolve()
    except OSError:
        return False
    return any(rp == r or r in rp.parents for r in roots)


def guard_hook(cwd, write_files=(), read_roots=None):
    """PreToolUse hook: writes only to write_files; reads (if read_roots is given) only below read_roots."""
    writes = {Path(w).resolve() for w in write_files}
    roots = [Path(r).resolve() for r in read_roots] if read_roots else None

    async def hook(input_data, tool_use_id, context):
        name = input_data.get('tool_name', ''); ti = input_data.get('tool_input', {}) or {}
        def path_of(key):
            v = ti.get(key)
            return None if not v else (Path(v) if Path(v).is_absolute() else Path(cwd) / v)
        ok, why = True, ''
        if 'api_key' in json.dumps(ti).lower():
            ok, why = False, 'the API key file is private'
        elif name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
            p = path_of('file_path') or path_of('notebook_path')
            ok = p is not None and p.resolve() in writes
            why = f'writes are allowed only to: {", ".join(sorted(str(w) for w in writes))}'
        elif roots is not None and name in ('Read', 'Grep', 'Glob'):
            p = path_of('file_path') or path_of('path') or Path(cwd)
            pat = str(ti.get('pattern', '')) if name == 'Glob' else ''
            ok = within(p, roots) and '..' not in pat and not Path(pat).is_absolute()
            why = f'reads are allowed only below: {", ".join(str(r) for r in roots)}'
        elif name not in ('Read', 'Grep', 'Glob', 'Edit', 'Write', 'MultiEdit', 'StructuredOutput') \
                and not name.startswith('mcp__loop__'):          # StructuredOutput = the final JSON answer
            ok, why = False, f'tool {name} is not available in this loop'
        if ok:
            return {}
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                       'permissionDecisionReason': why}}
    return hook


async def call_agent(role, n, system, prompt, cwd, budget, tools, write_files=(), read_roots=None, mcp=None):
    from claude_agent_sdk import (query, ClaudeAgentOptions, HookMatcher, ResultMessage, AssistantMessage,
                                  TextBlock, ToolUseBlock)
    cap = min(CFG['per_call_budget_usd'][role], max(budget.remaining() - CFG['stop_reserve_usd'], .1))
    allowed = list(tools) + ([f'mcp__loop__{t}' for t in mcp[1]] if mcp else [])
    opts = dict(model=CFG['models'][role], system_prompt=system, tools=list(tools), allowed_tools=allowed,
                permission_mode='dontAsk', cwd=str(cwd), max_turns=CFG['max_turns'][role], max_budget_usd=cap,
                setting_sources=[], env={'ANTHROPIC_API_KEY': api_key()},
                output_format={'type': 'json_schema', 'schema': SCHEMAS[role]},
                hooks={'PreToolUse': [HookMatcher(matcher=None, hooks=[guard_hook(cwd, write_files, read_roots)])]},
                mcp_servers={'loop': mcp[0]} if mcp else {})
    if CFG.get('effort', {}).get(role):
        opts['effort'] = CFG['effort'][role]
    options = ClaudeAgentOptions(**opts)
    transcript = it_dir(n) / f'{role}.log'; cost = None; result = None; text = ''
    with open(transcript, 'a', encoding='utf-8') as tr:
        tr.write(f'\n===== {role} {now():%H:%M:%S} cap ${cap:.2f}\n{prompt}\n')
        try:
            async for m in query(prompt=prompt, options=options):
                if isinstance(m, AssistantMessage):
                    for b in m.content:
                        if isinstance(b, TextBlock):
                            tr.write(f'\n[text] {b.text}\n')
                        elif isinstance(b, ToolUseBlock):
                            tr.write(f'\n[tool] {b.name} {json.dumps(b.input)[:2000]}\n')
                elif isinstance(m, ResultMessage):
                    cost = m.total_cost_usd; result = m.structured_output; text = m.result or ''
                    tr.write(f'\n[result] {m.subtype} turns {m.num_turns} cost {cost} errors {m.errors}\n{text[:4000]}\n')
        except Exception as e:
            tr.write('\n[exception] ' + ''.join(traceback.format_exception(e))[-4000:] + '\n')
    if cost is None:
        cost = cap                                       # unknown cost: charge the full cap (conservative)
    budget.add(cost)
    if result is None and text:
        try:
            result = json.loads(text[text.find('{'):text.rfind('}') + 1])
        except ValueError:
            result = None
    log(f'{role} done: ${cost:.2f} (night ${budget.spent:.2f} of ${budget.cap:.0f})')
    return result, cost


def system_prompt(role):
    parts = [read(HERE / 'prompts' / f'{role}.md'), '\n\n# RULEBOOK\n\n' + read(HERE / 'RULEBOOK.md')]
    if role in ('manager', 'developer'):
        parts.append('\n\n# PROJECT CONTEXT\n\n' + read(HERE / 'CONTEXT.md'))
    return ''.join(parts)


def developer_tools(n, counters):
    from claude_agent_sdk import tool, create_sdk_mcp_server
    lim = CFG['developer_tool_limits']

    @tool('build_and_unit_test', 'Regenerate v4.533/Source from the patches, compile with ifx and run the Atena, Markou '
          'and KP unit tests. Returns JSON with ok and, on failure, the stage and the log tail.', {})
    async def build_and_unit_test(args):
        if counters['build'] >= lim['build_and_unit_test']:
            return {'content': [{'type': 'text', 'text': 'limit reached for this iteration'}]}
        counters['build'] += 1
        code, out = await asyncio.to_thread(run_runner, ['build', n], 3600)
        return {'content': [{'type': 'text', 'text': out[-12000:]}]}

    @tool('run_one_model', 'Run one model of the test set on the build of this iteration (call build_and_unit_test first). '
          'Returns summary, curve metric and the console tail.', {'model': str})
    async def run_one_model(args):
        if counters['one'] >= lim['run_one_model']:
            return {'content': [{'type': 'text', 'text': 'limit reached for this iteration'}]}
        counters['one'] += 1
        code, out = await asyncio.to_thread(run_runner, ['one', n, args.get('model', '')], 3600)
        return {'content': [{'type': 'text', 'text': out[-12000:]}]}

    server = create_sdk_mcp_server('loop', '1.0.0', [build_and_unit_test, run_one_model])
    return server, ['build_and_unit_test', 'run_one_model']


# ---------------------------------------------------------------- iteration steps
def baseline(state, n, label):
    log(f'iteration {n:03d}: baseline ({label}) - runner only')
    snapshot(n)
    try:
        run_runner(['full', n], 4 * 3600)
    except subprocess.TimeoutExpired:
        pass
    st = json.loads(read(it_dir(n) / 'status.json') or '{}')
    entry = dict(iteration=f'{n:03d}', time=f'{now():%Y-%m-%d %H:%M}', hypothesis_id='baseline', family='-', base='-',
                 hypothesis=label, J=st.get('J'), n_failed=st.get('n_failed'), verdict='-', cost_usd=0.)
    if not st.get('ok'):
        entry.update(accepted=False, best_after=None, problems=json.dumps({k: st.get(k) for k in ('build', 'isolation')})[:3000])
        ledger_add(entry); state['next_iteration'] = n + 1; save_state(state)
        return False
    state['best'] = dict(iteration=f'{n:03d}', J=st['J'], build=st['build']['build'], n_failed=st['n_failed'],
                         all_pass=st['all_pass'])
    entry.update(accepted=True, best_after=f'{n:03d}', outcome=f"J {st['J']:.4f}, failed {st['n_failed']}")
    ledger_add(entry); state['next_iteration'] = n + 1; save_state(state)
    return True


def manager_prompt(state, n, budget, left):
    best = state['best']; b = it_dir(best['iteration'])
    rows = [json.loads(l) for l in read(LEDGER).splitlines() if l.strip()]
    last = next((r for r in reversed(rows) if r.get('assessor')), None)
    return '\n'.join([
        f'Plan iteration {n:03d}. Budget left tonight: ${budget.remaining():.2f} of ${budget.cap:.0f}; iterations left tonight: {left}.',
        f"Current best: iteration {best['iteration']} (J {best['J']:.4f}, failed criteria {best['n_failed']}).",
        f"Closed hypothesis families: {state.get('closed_families') or 'none'}.",
        '', '## Ledger', read(HERE / 'LEDGER.md', 30000),
        '', f"## Report of the best iteration ({best['iteration']})", read(b / 'report.md', 30000),
        '', '## Last assessment', json.dumps(last.get('assessor') if last else None, indent=1)[:6000],
        '', f"Plots of the best iteration: agent_loop/iterations/{best['iteration']}/curves.png and cracks_<case>.png (Read shows images).",
        'Decide now.'])


def completed_runs(it):
    rep = json.loads(read(it_dir(it) / 'report.json') or '{}')
    out = {}
    for case, c in rep.get('cases', {}).items():
        for mesh, r in c.get('meshes', {}).items():
            i = r.get('info', {})
            out[f'{case} {mesh}'] = bool(i) and i.get('accepted') == i.get('planned') and i.get('planned', 0) > 0
    return out


def new_early_stops(best_it, n):
    """Runs that completed all steps in the best iteration but not in iteration n (RULEBOOK 5: no hard-check regression)."""
    b, c = completed_runs(best_it), completed_runs(n)
    return sorted(k for k, ok in b.items() if ok and not c.get(k, False))


def static_checks(n, base, dev):
    problems = []
    d = diff_against(base); (it_dir(n) / 'diff.patch').write_text(d, encoding='utf-8')
    added = '\n'.join(l[1:] for l in d.splitlines() if l.startswith('+') and not l.startswith('+++'))
    if CRACK_LIMIT.search(added):
        problems.append('crack-per-iteration limit pattern in added lines: ' + ', '.join(sorted(set(m.group(0) for m in CRACK_LIMIT.finditer(added))))[:300])
    names = sorted(set(m.group(0) for m in TEST_NAMES.finditer(added)))
    if names and TEST_NAMES.search(read(ROOT / 'agent_loop/model_hook.py')):
        problems.append('test names in model_hook.py: ' + ', '.join(names))
    a0 = assertion_count(read(it_dir(base) / 'snapshot' / 'TRIC_KP.f90')); a1 = assertion_count(read(ROOT / 'tests/TRIC_KP.f90'))
    if a1 < a0 and 'assertion' not in json.dumps(dev or {}).lower():
        problems.append(f'unit test assertions fell from {a0} to {a1} without a reason')
    return d, problems, names


async def iteration(state, n, budget, left):
    t0 = now(); entry = dict(iteration=f'{n:03d}', time=f'{t0:%Y-%m-%d %H:%M}'); costs = {}
    # 1. manager
    dec, costs['manager'] = await call_agent('manager', n, system_prompt('manager'), manager_prompt(state, n, budget, left),
                                             ROOT, budget, ['Read', 'Grep', 'Glob'], read_roots=[ROOT])
    (it_dir(n) / 'manager.json').write_text(json.dumps(dec, indent=2) if dec else 'null')
    if not dec:
        entry.update(hypothesis_id='-', outcome='manager gave no decision', cost_usd=sum(costs.values()))
        ledger_add(entry); return 'stop'
    entry.update(hypothesis_id=dec['hypothesis_id'], family=dec['family'], hypothesis=dec['hypothesis'],
                 expected=dec['expected_effects'], manager=dec)
    if dec['decision'] == 'stop':
        entry.update(outcome='manager stop: ' + dec['reason'], cost_usd=sum(costs.values())); ledger_add(entry)
        return 'stop'
    base = dec.get('base_iteration') or state['best']['iteration']
    if not base.isdigit() or not (it_dir(base) / 'snapshot').exists() or not json.loads(read(it_dir(base) / 'status.json') or '{}').get('ok'):
        base = state['best']['iteration']
    base = f'{int(base):03d}'; entry['base'] = base
    restore(base)
    # 2. developer
    before = guarded_hash(); counters = dict(build=0, one=0)
    notes = it_dir(n) / 'notes.md'
    prompt = '\n'.join([
        f'Iteration {n:03d}. The working files are those of iteration {base} (report: agent_loop/iterations/{base}/report.md).',
        f'Hypothesis {dec["hypothesis_id"]} (family {dec["family"]}, extension type {dec["extension_type"]}): {dec["hypothesis"]}',
        '', '## Manager instructions', dec['developer_instructions'],
        '', '## Expected effects (for your information)', dec['expected_effects'],
        f'First model to check with run_one_model: {dec["first_model_to_check"]}',
        f'Write your notes to {notes.relative_to(ROOT).as_posix()}.'])
    dev, costs['developer'] = await call_agent(
        'developer', n, system_prompt('developer'), prompt, ROOT, budget, ['Read', 'Grep', 'Glob', 'Edit', 'Write'],
        write_files=[ROOT / t for t in TRACKED] + [notes], mcp=developer_tools(n, counters))
    (it_dir(n) / 'developer.json').write_text(json.dumps(dev, indent=2) if dev else 'null')
    after = guarded_hash()
    touched = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    snapshot(n)
    d, problems, names = static_checks(n, base, dev)
    if touched:
        problems.append('files outside the allowed set changed: ' + ', '.join(touched[:20]))
    entry['developer_summary'] = (dev or {}).get('summary', 'no developer result')
    if not d.strip():
        entry.update(outcome='no change (developer: ' + ((dev or {}).get('caveats') or 'no result')[:500] + ')',
                     accepted=False, cost_usd=sum(costs.values()), costs=costs)
        ledger_add(entry); restore(state['best']['iteration']); return 'fail'
    if problems:
        entry.update(outcome='rejected by the rule checks', problems=problems, accepted=False, verdict='invalid',
                     cost_usd=sum(costs.values()), costs=costs)
        ledger_add(entry); restore(state['best']['iteration']); return 'fail'
    # 3. runner
    log(f'iteration {n:03d}: runner (build, isolation, {sum(1 for c in CFG["cases"].values() for _ in c["runs"])} runs)')
    try:
        run_runner(['full', n], 4 * 3600)
    except subprocess.TimeoutExpired:
        pass
    st = json.loads(read(it_dir(n) / 'status.json') or '{}')
    entry.update(J=st.get('J'), n_failed=st.get('n_failed'))
    if not st.get('ok'):
        entry.update(outcome='runner failed: ' + json.dumps({k: st.get(k) for k in ('build', 'isolation')})[:2500],
                     accepted=False, cost_usd=sum(costs.values()), costs=costs)
        ledger_add(entry); restore(state['best']['iteration']); return 'fail'
    # 4. assessor
    best_it = state['best']['iteration']
    files = ['report.md', 'report.json', 'diff.patch', 'curves.png'] + [f'cracks_{c}.png' for c in CFG['cases']]
    aprompt = '\n'.join([
        f'Assess iteration {n:03d} (folder agent_loop/iterations/{n:03d}: {", ".join(files)}, notes.md).',
        f'Reference = current best iteration {best_it}: agent_loop/iterations/{best_it}/report.md and its png files.',
        f'Overall objective J: this iteration {st["J"]:.4f}, best {state["best"]["J"]:.4f} (lower is better); failed criteria {st["n_failed"]} vs {state["best"]["n_failed"]}.',
        f'Test names found in the added lines (check that they are only comments/docstring): {names or "none"}.',
        '', '## Manager hypothesis and expected effects', dec['hypothesis'], dec['expected_effects'],
        '', '## Developer summary (claims to verify)', json.dumps(dev, indent=1)[:6000]])
    ver, costs['assessor'] = await call_agent(
        'assessor', n, system_prompt('assessor'), aprompt, it_dir(n), budget, ['Read', 'Grep', 'Glob'],
        read_roots=[it_dir(n), it_dir(best_it)])
    (it_dir(n) / 'assessor.json').write_text(json.dumps(ver, indent=2) if ver else 'null')
    verdict = (ver or {}).get('verdict', 'none')
    better = st['J'] < state['best']['J'] - 0.002
    new_stops = new_early_stops(best_it, n)
    accept = better and not new_stops and ver is not None and verdict != 'invalid' and not ver.get('gaming_suspected') \
        and not ver.get('rule_violations')
    if better and new_stops and verdict != 'invalid':
        entry['candidate'] = True       # better J but a run that completed in the best now stops: not a new best,
        entry['problems'] = ['new early stops: ' + ', '.join(new_stops)]   # the manager may build on it (base_iteration)
    entry.update(verdict=verdict, assessor=ver, accepted=accept, cost_usd=sum(costs.values()), costs=costs,
                 assessor_findings=(ver or {}).get('key_findings'), next_step_advice=(ver or {}).get('next_step_advice'),
                 outcome=f"J {st['J']:.4f} vs best {state['best']['J']:.4f}; failed {st['n_failed']}; all pass {st['all_pass']}")
    if accept:
        state['best'] = dict(iteration=f'{n:03d}', J=st['J'], build=st['build']['build'], n_failed=st['n_failed'],
                             all_pass=st['all_pass'])
    entry['best_after'] = state['best']['iteration']
    ledger_add(entry)
    restore(state['best']['iteration'])
    return 'accept' if accept else 'fail'


def deadline():
    h, m = map(int, CFG['stop_at_local_time'].split(':'))
    t = now().replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now() else t + datetime.timedelta(days=1)


async def night():
    state = load_state(); budget = Budget(state); stop_at = deadline()
    state['consecutive_failures'] = 0      # each launch gets its own run of 5 (the user may have changed the rules)
    log(f'night start: budget ${budget.cap:.0f} (spent ${budget.spent:.2f}), stop at {stop_at:%Y-%m-%d %H:%M}')
    n = state['next_iteration']
    if state.get('best') and (HERE / 'iterations' / f'{n:03d}').exists():
        # an iteration was interrupted (window closed): discard it and restore the best files
        d = HERE / 'iterations' / f'{n:03d}'; d.rename(d.with_name(f'{n:03d}_interrupted_{now():%H%M%S}'))
        restore(state['best']['iteration'])
        log(f'iteration {n:03d} was interrupted: folder renamed, files restored to the best ({state["best"]["iteration"]})')
    if not state.get('best'):
        if not baseline(state, n, 'current workspace files (best so far)'):
            log('baseline failed: see LEDGER.md and the iteration folder; stopping'); return 'baseline failed'
        n = state['next_iteration']
    elif not tracked_equal_snapshot(state['best']['iteration']):
        log('the tracked files differ from the best snapshot (changed outside the loop): new baseline')
        if not baseline(state, n, 'workspace changed outside the loop'):
            return 'baseline failed'
        n = state['next_iteration']
    done = 0; reason = ''
    while True:
        left = CFG['max_iterations_per_night'] - done
        if state['best'].get('all_pass'):
            reason = 'all criteria pass'; break
        if left <= 0:
            reason = 'iteration limit'; break
        if state['consecutive_failures'] >= CFG['stop_after_consecutive_failures']:
            reason = f"{state['consecutive_failures']} consecutive iterations without improvement"; break
        if now() >= stop_at - datetime.timedelta(minutes=60):
            reason = 'stop time'; break
        if not budget.can_afford('manager', 'developer', 'assessor'):
            reason = f'budget (spent ${budget.spent:.2f} of ${budget.cap:.0f})'; break
        log(f'iteration {n:03d}: start')
        try:
            r = await iteration(state, n, budget, left)
        except Exception as e:
            log('iteration error: ' + ''.join(traceback.format_exception(e))[-3000:])
            restore(state['best']['iteration']); r = 'fail'
        state['next_iteration'] = n + 1; done += 1
        if r == 'stop':
            reason = 'manager decision'; save_state(state); break
        state['consecutive_failures'] = 0 if r == 'accept' else state['consecutive_failures'] + 1
        save_state(state); prune_builds(state, n); n += 1
    restore(state['best']['iteration']); save_state(state)
    rep = HERE / f'NIGHT_REPORT_{budget.key}.md'
    rep.write_text('\n'.join([f'# Night {budget.key}', '', f'Stop reason: {reason}', f'Iterations run: {done}',
                              f"Best: iteration {state['best']['iteration']} (J {state['best']['J']:.4f}, failed {state['best']['n_failed']}, all pass {state['best']['all_pass']})",
                              f'Spent: ${budget.spent:.2f} of ${budget.cap:.0f} (client-side estimate; the Console usage page is authoritative)',
                              f"Workspace files restored to the best iteration ({state['best']['iteration']}).", '',
                              'See LEDGER.md for every iteration.']) + '\n')
    log(f'night end: {reason}; best {state["best"]["iteration"]} J {state["best"]["J"]:.4f}; spent ${budget.spent:.2f}')
    return reason


# ---------------------------------------------------------------- dry check
def check():
    ok = True
    def say(good, msg):
        nonlocal ok
        ok &= bool(good); print(('OK    ' if good else 'FAIL  ') + msg)
    say(sys.version_info >= (3, 10), f'Python {sys.version.split()[0]} (>= 3.10 for the Agent SDK)')
    try:
        import claude_agent_sdk
        say(True, f'claude-agent-sdk {getattr(claude_agent_sdk, "__version__", "?")} installed')
    except ImportError:
        say(False, 'claude-agent-sdk not installed: pip install claude-agent-sdk')
    try:
        import numpy, matplotlib
        say(True, 'numpy and matplotlib installed')
    except ImportError:
        say(False, 'numpy / matplotlib missing: pip install numpy matplotlib')
    k = api_key()
    say(k.startswith('sk-ant-'), 'API key found (' + ('environment' if os.environ.get('ANTHROPIC_API_KEY') else 'agent_loop/api_key.txt') + ')' if k else 'API key missing (ANTHROPIC_API_KEY or agent_loop/api_key.txt)')
    for t in TRACKED + ['tools/release_v4533.py', 'tools/tric_round2.py', 'tools/tric_isolation_check.py',
                        'tools/tric_bench.py', 'tools/tric_lefas_parallel.py']:
        say((ROOT / t).exists(), t)
    for s, spec in CFG['model_sets'].items():
        for src in spec['sources']:
            say((ROOT / src / 'ORDER.txt').exists(), f'model source {src}')
    for case, cc in CFG['cases'].items():
        say((ROOT / cc['experiment']).exists(), f'{case} reference {cc["experiment"]}')
        for mesh, run in cc['runs'].items():
            found = any((ROOT / src / (run + '.NEU')).exists() for spec in CFG['model_sets'].values() for src in spec['sources'])
            say(found, f'{case} {mesh} model {run}')
    say((ROOT / 'v4.533/Build' / CFG['baseline_build'] / 'ReConAn_v4.533.exe').exists(), f'baseline build {CFG["baseline_build"]}')
    say((ROOT / 'v4.533/Build/TRIC_BaseObjects').exists(), 'retained objects v4.533/Build/TRIC_BaseObjects')
    vars_bat = Path(r'C:\Program Files (x86)\Intel\oneAPI\compiler\2026.1\env\vars.bat')
    say(vars_bat.exists() or os.name != 'nt', f'Intel oneAPI {vars_bat}')
    free = shutil.disk_usage(ROOT).free / 1e9
    say(free > 10, f'free disk {free:.0f} GB (> 10 GB)')
    st = load_state()
    print(f"state: best {st.get('best')}, next iteration {st.get('next_iteration')}")
    print(f"config: ${CFG['budget_usd_per_night']}/night, max {CFG['max_iterations_per_night']} iterations, stop at {CFG['stop_at_local_time']}, models {CFG['models']}")
    print('READY' if ok else 'NOT READY')
    return ok


if __name__ == '__main__':
    if '--check' in sys.argv:
        sys.exit(0 if check() else 1)
    if not api_key():
        sys.exit('No API key: set ANTHROPIC_API_KEY or create agent_loop/api_key.txt')
    asyncio.run(night())
