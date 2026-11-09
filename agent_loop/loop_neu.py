"""Compact per-step element fields from a ReConAn OUT.neu (agent loop, Claude 2026-10-25).

Reads the FEMAP output vectors written by the RC TRIC build (output set = accepted step):
  890001-890003 TOP GP Sxx, Syy, Txy (element local, kPa)    890004-890006 BOTTOM GP Sxx, Syy, Txy
  890007 maximum historical crack opening (m)                  890008/890009 bar family 1/2 mean stress (kPa)
  890010 KP largest crack count (3 = crushed)                  890011 KP largest shear stress on a crack plane
  890012 KP fraction of crushed points
and stores them as float32 arrays [step, element] in <run>_fields.npz (steps = output set ids, elements =
element ids in json order). Principal stresses of the TOP/BOTTOM points are added (s1top, s2top, s1bot, s2bot).
  python agent_loop/loop_neu.py <OUT.neu> <model.json> <out.npz>
"""
import json, re, sys
from pathlib import Path
import numpy as np

VECTORS = {890001: 'sxx_top', 890002: 'syy_top', 890003: 'txy_top', 890004: 'sxx_bot', 890005: 'syy_bot',
           890006: 'txy_bot', 890007: 'crack_open', 890008: 'bar1', 890009: 'bar2', 890010: 'cracks',
           890011: 'crack_shear', 890012: 'crushed_frac'}
HEADER = re.compile(r'^\s*(\d+),(\d+),(\d+),\s*$')


def extract(neu, meta_json, out=None):
    meta = json.loads(Path(meta_json).read_text())
    eids = [e['id'] for e in meta['elements']]; col = {e: i for i, e in enumerate(eids)}
    data = {}                                             # (set, vector) -> {element: value}
    with open(neu, errors='replace') as f:
        lines = iter(f)
        for line in lines:
            m = HEADER.match(line)
            if not m or int(m.group(2)) not in VECTORS:
                continue
            s, v = int(m.group(1)), int(m.group(2))
            for _ in range(6):                             # title, 3 value lines, 2 flag lines
                next(lines)
            vals = {}
            for row in lines:
                t = row.strip()
                if t.startswith('-1,'):
                    break
                a, b = t.rstrip(',').split(',')[:2]
                vals[int(a)] = float(b)
            data[(s, v)] = vals
    steps = sorted({s for s, v in data})
    arrays = {'steps': np.array(steps, dtype=np.int32), 'elements': np.array(eids, dtype=np.int32)}
    for v, name in VECTORS.items():
        a = np.zeros((len(steps), len(eids)), dtype=np.float32)
        for i, s in enumerate(steps):
            for e, x in data.get((s, v), {}).items():
                if e in col:
                    a[i, col[e]] = x
        arrays[name] = a
    for side in ('top', 'bot'):
        sx, sy, t = arrays['sxx_' + side], arrays['syy_' + side], arrays['txy_' + side]
        c, r = (sx + sy) / 2, np.sqrt(((sx - sy) / 2) ** 2 + t ** 2)
        arrays['s1' + side], arrays['s2' + side] = c + r, c - r
    if out:
        np.savez_compressed(out, **arrays)
    return arrays


def centroids(meta):
    nodes = {int(k): v for k, v in meta['nodes'].items()}
    return np.array([[sum(nodes[n][0] for n in e['nodes']) / 3, sum(nodes[n][1] for n in e['nodes']) / 3]
                     for e in meta['elements']])


if __name__ == '__main__':
    a = extract(sys.argv[1], sys.argv[2], sys.argv[3])
    print(sys.argv[3], len(a['steps']), 'steps', len(a['elements']), 'elements')
