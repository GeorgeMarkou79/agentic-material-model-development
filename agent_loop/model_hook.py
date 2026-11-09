"""Model hook of the agent loop (developer-editable) (Claude, 2026-10-25).

The runner (agent_loop/loop_run.py) copies the base models of every test into the iteration and calls
transform_card() on the concrete card of every identity-23 (KP-MP) property of EVERY model. The card is the
list of property values 49..74 (26 numbers, card[i] = value 49 + i):
  card[0] fc (kPa)        card[1] ft/fc (SC14)       card[2] beta (SC13)     card[3] value 52 (Gf, unused = 0)
  card[4] value 53 (acceptance: 1 = native NR)       card[5..7] values 54-56 (56 = f_l tie confinement, kPa;
  set per property by tools/tric_kp_confinement_models.py, 0 outside the tied boundary strips)
  card[8] value 57 = SC(23) (0.5)                    card[9..12] values 58-61
  card[13..24] reinforcement (number of families + families)  - may NOT be changed
  card[25] identity 23                                         - may NOT be changed
Rules (RULEBOOK.md): the same transformation for every model - the function sees only the card, never the
model or test name; a new material parameter must have a physical definition and one value (or a formula of
fc / the existing card values) for all tests; reinforcement and identity are unchanged (the runner checks).
Default: identity (no change).
"""


def transform_card(card):
    return card
