# Expert-guided agentic loop for material-model development in a finite element code

This repository contains the agentic development environment used in the paper

> G. Markou, C. Mourlas, *Expert-guided agentic AI for the development of nonlinear material models in finite
> element codes*, submitted to Computer Methods in Applied Mechanics and Engineering (2026).

In the paper, large-language-model (LLM) agents developed a smeared-crack concrete model (UCO-TRIC) for the layered
TRIC shell element of the finite element program Reconan FEA. They worked in an unsupervised inner loop, and a human
expert worked in an outer loop. This repository holds the inner loop: the driver, the deterministic runner, the
metrics and the score, the bit-for-bit isolation check, the agent prompts, the rulebook, the configuration and the
records of the actual runs.

## What is included

| Path | Content | Paper |
|---|---|---|
| `agent_loop/loop_manager.py` | Driver: manager, developer and assessor agents (Claude Agent SDK), budgets, write guard (PreToolUse hook), hash check of the protected folders, rule checks, acceptance rule, restoration of the best files, ledger | Sections 4.1, 4.4, 4.6 |
| `agent_loop/loop_run.py` | Deterministic runner: regenerate the sources, build, unit tests, isolation check, benchmark runs, collection of results | Section 4.3 |
| `agent_loop/loop_checks.py` | Curve metric (NRMSE, peak and end errors, coverage, score S), objective J, pass criteria, physics checks, plots | Section 4.3 |
| `agent_loop/loop_neu.py` | Reader of per-step element fields (cracks, crushing, stresses) | Section 4.3 |
| `agent_loop/model_hook.py` | The only model-side file the developer agent may change | Section 4.2 |
| `agent_loop/prompts/` | System prompts of the manager, developer and assessor agents | Section 4.1 |
| `agent_loop/RULEBOOK.md`, `CONTEXT.md`, `config.json` | Binding rules, project context given to the agents, budgets, models, cases and criteria | Section 4.2 |
| `agent_loop/README.md` | Set-up and running instructions of the original workspace | |
| `tools/tric_isolation_check.py`, `native_regression.py`, `native_run.py`, `verify_v4533.py` | Bit-for-bit isolation check: 22 non-RC analyses compared byte for byte with the original program | Sections 4.6, 5.2 |
| `tools/tric_curve_errors.py` | Curve errors of the wall benchmarks | Section 4.3 |
| `Run_AgentLoop_Check.cmd`, `Run_AgentLoop_Night.cmd` | Dry check of the set-up and launcher of one overnight run (Windows) | Section 4 |
| `records/ledger.jsonl`, `records/LEDGER.md` | Ledger of all iterations of the four runs: hypothesis, developer summary, verdict, objective, cost | Section 6.1, Figure 6 |
| `records/NIGHT_REPORT_2026-10-25.md`, `records/state.json` | Report and final state of the loop | Section 6.1 |

## What is not included

Reconan FEA is proprietary software of the authors. Its source code, the executable, the patch scripts that
generate the RC TRIC Fortran routines (including the UCO-TRIC material routines edited by the developer agent), the
material-point unit tests and the benchmark models are not part of this repository. The loop therefore cannot be run
as it is. It is published so that the workflow, the rules, the enforcement mechanisms, the scoring and the records
described in the paper can be inspected and adapted to other codes. The model itself is described in Section 3 of
the paper in enough detail to be reimplemented.

## Adapting the loop to another code

The code-specific parts are concentrated in `loop_run.py` (build, test and run commands, file locations),
`tools/` (isolation check against a reference executable), `loop_checks.py` (benchmark curves and criteria) and
`config.json` (cases, budgets, models). The driver, the write guard, the hash check of protected files and the
acceptance rule in `loop_manager.py` are independent of the finite element code.

Requirements of the original set-up: Windows, Python 3.10 or newer, `pip install claude-agent-sdk numpy matplotlib`,
an Anthropic API key in the environment variable `ANTHROPIC_API_KEY` or in `agent_loop/api_key.txt` (excluded by
`.gitignore`, never commit it), and the Intel oneAPI Fortran tool chain for the build of the program.

## Citation

Please cite the paper above. A citation entry will be added on publication.

## Licence

MIT licence, see `LICENSE`.
