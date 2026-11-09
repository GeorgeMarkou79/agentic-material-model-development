# Agent loop rulebook — RC TRIC Markou material

This rulebook is binding for the manager, the developer and the assessor. The driver (`loop_manager.py`) also enforces the parts marked **[enforced]**. If an instruction anywhere conflicts with this file, this file wins.

## 1. Goal

Make the RC TRIC shell material (identity 23, the Markou–Papadrakakis numerically modified Kotsovos–Pavlovic model, "Markou") reproduce three kinds of experiments with one formulation and one set of material rules:

| Test | What it checks | Pass on BOTH meshes (Coarse and Medium) |
|---|---|---|
| Lefas SW14, SW16, SW21, SW22 | in-plane walls (flexure–shear, with and without axial load) | \|peak error\| ≤ 10 %, NRMSE ≤ 10 %, all steps accepted |
| Cervenka W-2 | shear panels | the same |
| Toronto (no self-weight, rotating plates) | size-effect shear of a 4 m deep beam without stirrups in the east span | shear failure detected (post-peak drop ≥ 15 % with mid-depth cracking through the east web), peak 700–1050 kN (see `config.json` `_toronto_note`) |
| Slab strips SlabCANT, SlabSS | out-of-plane bending (layered shell) | K0 within 5 %; Pcr, Py, Pu within 10 % of the analytical reference; NRMSE ≤ 10 %; all steps accepted |
| Every case | mesh objectivity | Coarse vs Medium peak within 10 %, Coarse–Medium NRMSE ≤ 10 % |

Physics checks with hard status: equilibrium, first-crack location (Toronto under the load at the bottom; slabs at the maximum-moment section; W-2 in the panels), Toronto shear failure. The wall first-crack location is a warning (the first step is already past cracking). Warnings (reported, not pass/fail): premature crushing (< 0.8 × test peak), head cracking, stress locking, strut over-strength, load drops, NR iteration counts.

The loop stops when every criterion passes on both meshes ("all pass"), when the night budget or iteration limit is reached, or when the manager decides that the remaining hypotheses are exhausted.

## 2. Fixed formulation (may not be removed or replaced)

1. Identity 23 calls the original ReConAn routines (CheckForFailure, FindCracksPlane, CheckPlanesAngle, CalcCrackStressVector, MaterialSmearedCrackD) in the order of the HEXA08 material-69 branch (thesis 3.3.5).
2. **No restriction on the number of cracks that may open per iteration** — every failing point cracks in the same iteration (the user's explicit decision: "I do not want to have a KP model with restrictions on how many cracks are allowed to open per iteration"). No crack admission, ranking, queues, iNCRIter-type limits, or delaying cracks to later iterations or steps. **[enforced by a pattern check + the assessor]**
3. Plain native Newton–Raphson with the native convergence tolerance (card value 53 = 1). No line search, Anderson mixing, step cutting, arc-length, or accepting unconverged states. A run that stops at the maximum NR iterations is a failure, not a capacity.
4. The elastic matrix is used while the failure ratio is below SC(23) = 0.5; C-C-C crushing test at the first iteration of a step, with σzz = −0 (plane-stress rule); iteration-accumulated stress σ += D·Δε with the D of the previous iteration; static condensation of the out-of-plane components.
5. Kept from the current best (Markou-6) unless a change is justified and accepted: the strut cap (smaller in-plane principal stress ≥ −β·fc, Vecchio–Collins β) with the cap-consistent iteration matrix (rank-one removal, stored D unchanged), the tie confinement (value 56 = f_l) in the crushing decision, brittle cracking (no Gf).

## 3. What may be changed

- `tools/tric_kp_patch.py` — the identity-23 material code (the Fortran text it inserts) and its docstring. This is the only source of solver changes. **[enforced: writes elsewhere are denied]**
- `tests/TRIC_KP.f90` — point tests. Every new mechanism gets at least one assertion; existing assertions may only be removed if the mechanism they test is removed (say so). The assertion count may not fall without that reason. **[enforced: count check]**
- `agent_loop/model_hook.py` — `transform_card(card)`: the same transformation of the concrete card for every model. **[enforced: the hook never sees model names; reinforcement and identity cannot change]**
- `agent_loop/iterations/<NNN>/notes.md` — the developer's notes.

All models use twice the original number of load steps (N100; slabs N120; user decision 2026-10-25 18:15, tools/tric_refine_steps.py) — the step count is fixed for the agents.

Not allowed: changes to the solver outside identity 23, the element, the load/BC/mesh of any model, the experiment or reference files, the metric, the checks, the config, this rulebook, the prompts or the driver. Non-RC paths must stay bit-identical (isolation check 22/22 byte equal). **[enforced]**

## 4. Allowed extension types (each needs a physical justification in the docstring and in notes.md)

1. Constitutive regularisation tied to a physical quantity and the element size. **Tension: allowed since 2026-10-25 23:55 (user decision, reversing the KP7 rejection)** — fracture energy Gf in tension with a crack-band length scale (softening of the stress across each open crack so that the energy per unit crack area is Gf, band width = element width along the crack normal; Gf from fc by one formula for all tests, e.g. fib MC2010 Gf = 0.073 fcm^0.18 N/mm, set through model_hook card value 52). Purpose: size effect (Toronto) and mesh objectivity. Watch the user's original concern: post-cracking tension must not make the response too stiff — the stress released at cracking must be recovered in the softening branch, not added on top. Compression / strut softening / crushing energy and crack-shear length scales remain allowed.
2. Crack-shear transfer that depends on crack opening (aggregate interlock, e.g. MCFT / Walraven-type limits) instead of a constant β.
3. Compression softening of cracked concrete (strut cap variants) as functions of the crack strain, not of the test.
4. Confinement effects derived from the reinforcement data on the card.
5. Consistency of the iteration matrix with the stress update (convergence), without changing the converged state.
6. Out-of-plane (layered) behaviour corrections that follow from the same constitutive law (no slab-only switches).
7. Corrections of genuine bugs (state not reset, wrong frame, inconsistent units) — describe the bug and add a test that fails without the fix.
8. (2026-10-25 18:15) Plane-stress crack-coincidence rule for identity 23. The original CheckPlanesAngle treats a new crack within ~46° of an existing one as coincident (no new crack). A plane-stress-specific coincidence test is allowed ONLY after a diagnostic in a previous or the same iteration has shown, with counts, that failing points are absorbed as 'coincident' where a second (e.g. diagonal shear) crack should open (Toronto east span, W-2 panels). The change must permit at least the cracks the original permits (it may never suppress a crack), must be one rule for all tests, must keep the original routine for the decision itself (HEXA08 untouched; isolation 22/22), and must come with a point test.

New parameters: a single value for all tests or a formula of fc and the existing card values. No test-specific parameter values, no branches on geometry, element ids, region names, load level or step number. **[pattern check + assessor]**

## 5. Anti-gaming rules

- One formulation for every test. A change that improves some tests while making others worse is reported as mixed and is not accepted unless J (the overall objective) improves and no hard physics check regresses.
- Do not tune to the curves alone. The user accepts calibration of physically meaningful parameters only because the three experiment types plus the analytical slabs must all pass with ONE formulation; the assessor rejects changes whose only justification is the curve fit.
- No change may make a run "pass" by stopping early, by skipping steps, by suppressing cracking/crushing output, or by changing what is measured.
- Every claim in the developer's summary must be checkable in the report (the assessor checks it).

## 6. Process

1. Iteration 000 = baseline (current code, no LLM). It must build, pass isolation and run, otherwise the loop stops.
2. Manager → one hypothesis per iteration (one mechanism), with the expected effect on specific cases.
3. Developer → implements it on top of the base iteration (the current best unless the manager names another), adds point tests, may use `build_and_unit_test` (≤ 4 per iteration) and `run_one_model` (≤ 4 per iteration) to check compilation and one model.
4. Runner (deterministic) → build, unit tests, isolation, all 16 runs, checks, report.
5. Assessor (independent; sees only the diff, the report, the plots and the rulebook) → verdict.
6. Driver → accepts as new best if: build + unit tests + isolation pass, no rule violation, assessor verdict not "invalid", J < best J − 0.002, AND no run that completed all its steps in the best now stops early **[enforced since 2026-10-25 21:00]**. An iteration with a better J but a new early stop is recorded as a "candidate" (not the best); the manager may continue from it (base_iteration) to repair the regression. Otherwise the files are restored to the best.
7. Dead ends: a hypothesis family that fails three times is closed unless the manager gives a new reason. The driver stops after 5 consecutive iterations without improvement.
8. Budget: the driver stops when the night budget ($50 per night since 2026-10-25 21:05) minus a reserve is spent. Each agent call has its own cap.

## 7. Reporting

Everything is written to `agent_loop/LEDGER.md` (human-readable) and `agent_loop/ledger.jsonl`. The best iteration is in `agent_loop/state.json`; its files in `agent_loop/iterations/<NNN>/snapshot/`. The loop never edits `WORK_LOG.md`; the user (or Claude in the chat) records accepted results there.
