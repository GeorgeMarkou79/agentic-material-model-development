# Autonomous agent loop for the RC TRIC Markou material

Set up 2026-10-25. Nothing has been run yet.

## What it does

Each night the driver runs iterations until the budget, the iteration limit or the stop time is reached, or until every pass criterion is met:

```
manager (Opus, read-only)        picks ONE hypothesis from the ledger + best report, writes developer instructions
developer (Opus, guarded writes) edits tools/tric_kp_patch.py (+ tests/TRIC_KP.f90, agent_loop/model_hook.py);
                                 may call build_and_unit_test (<= 4x) and run_one_model (<= 4x)
runner (deterministic Python)    regenerate Source -> build -> unit tests (Atena, Markou, KP) -> isolation 22/22
                                 -> 16 runs (4 Lefas walls, W-2, Toronto, 2 slabs; Coarse + Medium)
                                 -> curve scores + physics checks + crack maps -> report.md / report.json / png
assessor (Sonnet, independent)   sees only the diff, the report, the plots, the best report and the rulebook
driver                           accepts a new best only if J improves, the build/isolation/tests pass, the rule
                                 checks pass and the assessor finds no violation; otherwise restores the best files
```

The first iteration (000) is a baseline of the current files (Markou-6) without any LLM call. If the baseline does not build or run, the loop stops before spending anything.

## Files

| file | purpose |
|---|---|
| `RULEBOOK.md` | binding rules: fixed formulation, what may change, allowed extension types, anti-gaming rules, acceptance |
| `CONTEXT.md` | project history and lessons for the manager/developer |
| `config.json` | budget ($20/night), limits, models, cases, pass criteria, Toronto target range |
| `prompts/*.md` | system prompts of the manager, developer, assessor |
| `loop_manager.py` | driver (Agent SDK), budget cap, write guard, rule checks, ledger, housekeeping |
| `loop_run.py` | deterministic runner (build, tests, isolation, runs, collection) |
| `loop_checks.py` | metric, pass criteria, physics checks, plots |
| `loop_neu.py` | per-step element fields from OUT.neu (cracks, crushing, stresses, bar stresses) |
| `model_hook.py` | the only model-side change the developer may make (same card transformation for all models) |
| `LEDGER.md`, `ledger.jsonl`, `state.json`, `driver.log`, `NIGHT_REPORT_<date>.md` | written at run time |
| `iterations/<NNN>/` | per iteration: snapshot of the 3 tracked files, diff.patch, notes.md, agent transcripts (*.log, *.json), status.json, report.md/json, curves.png, cracks_<case>.png, runs/<model>/ |

Out-of-plane checks (new): `tools/tric_slab_analytic.py` (layered-section reference: cantilever 2.0 m with tip load, simply supported 3.0 m with mid-span load; K0, Pcr, Py, Pu and the P-δ curve) and `tools/tric_slab_models.py` (Markou layered-shell models, Coarse 0.25 m / Medium 0.125 m) -> `verification/TRIC_Slab/`.

## One-time set-up (Windows)

1. Install the Python packages (Python 3.10 or newer):
   `python -m pip install claude-agent-sdk numpy matplotlib`
   (the Agent SDK bundles the Claude Code CLI; no separate install).
2. API key from the Claude Console: either set it as a user environment variable
   `setx ANTHROPIC_API_KEY sk-ant-...` (open a new terminal afterwards), or put it on the first line of
   `agent_loop\api_key.txt` (private; the agents are blocked from reading that file).
3. Recommended: in the Console, set a spend limit on the key's workspace (e.g. $25/month above what you plan to spend). The $20/night cap of the driver uses the SDK's client-side cost estimate; the Console limit is the hard backstop.
4. Double-click `Run_AgentLoop_Check.cmd` (workspace root): it checks Python, the packages, the key, all model and reference files, the baseline build, the compiler and the free disk. It makes no API call and runs no analysis. It must end with `READY`.

## Running a night

Double-click `Run_AgentLoop_Night.cmd` (or `python agent_loop\loop_manager.py` from the workspace root). Leave the window open. It stops by itself at the first of: $20 spent (minus a $1.50 reserve), 15 iterations, 07:30, 5 consecutive iterations without improvement, all criteria pass, or the manager's stop decision. At the end the workspace files are those of the best iteration.

In the morning read `agent_loop\NIGHT_REPORT_<date>.md` and `agent_loop\LEDGER.md`; the best iteration's plots are in `agent_loop\iterations\<NNN>\`. The loop never edits `WORK_LOG.md` — record accepted results there (or ask Claude to).

To stop early: close the window (Ctrl+C). The files may then be those of the iteration in progress: run `python agent_loop\loop_manager.py --check` and restore with `python -c "import sys; sys.path.insert(0,'agent_loop'); import loop_manager as M; M.restore(M.load_state()['best']['iteration'])"`.

## Cost estimate

Per iteration about $2-4 (manager ~$0.3-1, developer ~$1.5-3 on Opus, assessor ~$0.2-0.5 on Sonnet); per-call caps $1 / $6 / $1. An iteration takes roughly 30-60 min (build + isolation + 16 runs, mostly < 1 min each, Toronto Medium ~3 min), so a night gives about 6-10 iterations within $20.

## Changing the set-up

- Budget, limits, models, criteria: `config.json`.
- Toronto target range: `config.json` `criteria.toronto_peak_kN` (default 700-1050 kN; see `_toronto_note`).
- Rules: `RULEBOOK.md` (the agents read it every call).
- To start over: delete `state.json`, `ledger.jsonl`, `LEDGER.md` and `iterations\` (the next night starts with a new baseline). If you change `tools/tric_kp_patch.py` yourself between nights, the driver notices and runs a new baseline first.
