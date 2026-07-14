#!/usr/bin/env python3
"""
Build an editable draw.io diagram of the *kitchensearch* program flow from
kitchensearch_flow_diagram.json, using Graphviz for a layered layout with routed
edges. Shares all layout/emit machinery with drawio_common.py (the single source
of truth for the Graphviz run, coordinate transform, and node/edge emission);
this script only defines kitchensearch's own stage bands and colours.

Stages (top -> bottom):
  E  launch   - kitchensearch.py entry + hotkey/tray daemon
  U  ui       - the Tk picker and its UI helpers
  C  core     - search / thumbnails / clipboard / license utilities
  S  semantic - the MiniLM split-search daemon (IPC server)
  T  story    - text -> emoji-story PNG
Data files float alongside as cylinders (grp "asset" bundled tarball contents,
"model" committed embedding/ONNX files, "state" runtime cache/config), and
external services (grp "ext") float as blue pills. None of these constrain the
band ranking, so the module bands stay clean.

Usage:  python build_kitchensearch_drawio.py [graph.json] [out.drawio]
Requires Graphviz 'dot' on PATH.
"""
import json, sys, os, re
from collections import Counter
from drawio_common import (run_dot, transforms, decl, emit_node, emit_edges,
                           wrap_mxfile, esc)

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), 'kitchensearch_flow_diagram.json')
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), 'kitchensearch_flow_diagram.drawio')

G = json.load(open(SRC))
edges = G['edges']

GITHUB_BASE = 'https://github.com/morganrivers/kitchensearch'
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def gh_link(path):
    """Return a GitHub URL for a real repo path, or '' when the 'file' field is
    empty, a template, or an in-memory reference. Directories map to /tree/main/,
    files to /blob/main/."""
    if not path:
        return ''
    if any(c in path for c in '{[\''):
        return ''
    trimmed = path.rstrip('/')
    kind = 'tree' if path.endswith('/') or '.' not in os.path.basename(trimmed) else 'blob'
    return f'{GITHUB_BASE}/{kind}/main/{trimmed}'


# Filename -> repo-relative path manifest for auto-linking edge labels. Only the
# directly-committed data tiers are scanned (the embedding .npy arrays and the
# ONNX model), plus the repo-root scripts. The ui_assets/ + fonts/ live inside
# app_assets.tar.gz and are NOT checked in individually, so they are deliberately
# excluded here: those nodes carry an explicit 'link' to the tarball instead.
FILE_LOCATIONS = {}
for base in ['ksapp/data/embeddings', 'ksapp/data/models/all-MiniLM-L6-v2-onnx', '.']:
    d = os.path.join(REPO_ROOT, base)
    if os.path.isdir(d):
        for f in os.listdir(d):
            rel = f if base == '.' else f'{base}/{f}'
            if os.path.isfile(os.path.join(REPO_ROOT, rel)):
                FILE_LOCATIONS.setdefault(f, rel)


def edge_link(label):
    """If an edge label names an actual committed repo data file, return its
    GitHub URL; else ''. Control-flow labels and pure variable names have no
    target."""
    if not label:
        return ''
    for m in re.finditer(r'([\w.-]+\.(npy|onnx|json|tsv|txt|png|pkl))', label):
        f = m.group(1)
        if f in FILE_LOCATIONS:
            return f'{GITHUB_BASE}/blob/main/{FILE_LOCATIONS[f]}'
    return ''


for n in G['nodes']:
    if n.get('link'):                       # explicit JSON override wins
        continue
    link = gh_link(n.get('file', ''))
    if link:
        n['link'] = link
for e in edges:
    if e.get('link'):
        continue
    link = edge_link(e.get('label', ''))
    if link:
        e['link'] = link

# ---------- node sizing (label-driven, so Graphviz reserves real footprint) --
def size(n):
    lines = n['lbl'].split('\n')
    maxc = max(len(x) for x in lines)
    w = max(90, int(maxc * 6.6) + 24) + (20 if n.get('robot') else 0)
    h = len(lines) * 15 + (26 if n['type'] == 'data' else 20)
    return w, h
# Show each node's full repo path under the short name. Nodes without a backing
# file (runtime state, external services, tarball-only assets) keep lbl as-is.
for n in G['nodes']:
    if n.get('file'):
        n['lbl'] = n['lbl'] + '\n' + n['file']
for n in G['nodes']:
    if 'w' not in n:            # honour any explicit size
        n['w'], n['h'] = size(n)

# ---------- styles ----------------------------------------------------------
SCRIPT_BASE = 'rounded=1;whiteSpace=wrap;html=1;fontSize=11;'
SCRIPT_BY_GRP = {
    'E': 'fillColor=#dfe7ef;strokeColor=#6b7f96;fontColor=#1b2733;',   # launch   - slate
    'U': 'fillColor=#cfe8e6;strokeColor=#4f9b95;fontColor=#163a37;',   # ui       - teal
    'C': 'fillColor=#e6d6f2;strokeColor=#8a5fb0;fontColor=#3d2757;',   # core     - lavender
    'S': 'fillColor=#cfe6cf;strokeColor=#5a9367;fontColor=#1e3a24;',   # semantic - green
    'T': 'fillColor=#f6d9c0;strokeColor=#c17a3a;fontColor=#5a3410;',   # story    - peach
}
STYLE = {
    'ext':   'rounded=1;whiteSpace=wrap;html=1;arcSize=45;fillColor=#2f7dc4;strokeColor=#1c4f80;fontColor=#ffffff;fontSize=11;',
    'data':  'shape=cylinder;whiteSpace=wrap;html=1;fillColor=#e6ecf3;strokeColor=#8493a4;fontColor=#1b2733;fontSize=11;',
    'note':  'shape=note;whiteSpace=wrap;html=1;size=14;fillColor=#fdf3cf;strokeColor=#c9a227;fontColor=#5b4a00;fontSize=12;dashed=1;',
    'proxy': 'rounded=1;whiteSpace=wrap;html=1;fillColor=#eef2f7;strokeColor=#8493a4;fontColor=#5b6675;fontSize=9;dashed=1;',
}
def node_style(n):
    if n['type'] == 'script':
        return SCRIPT_BASE + SCRIPT_BY_GRP.get(n['grp'], SCRIPT_BY_GRP['C'])
    return STYLE[n['type']]

EBASE = 'edgeStyle=none;curved=1;html=1;endArrow=block;endFill=1;strokeColor=#9aa6b3;fontSize=9;fontColor=#5b6675;'
EK = {'handoff': 'strokeColor=#2e8b57;', 'loader': 'strokeColor=#3d72b4;dashed=1;',
      'cache': 'strokeColor=#b98a1e;dashed=1;', 'nav': 'strokeColor=#8493a4;dashed=1;',
      'proxy': 'strokeColor=#8493a4;dashed=1;'}

# stage banding (label + tag colour), ordered top -> bottom
BANDS = ['E', 'U', 'C', 'S', 'T']
STLAB = {'E': 'LAUNCH · entry + hotkey/tray daemon',
         'U': 'UI · Tk picker',
         'C': 'CORE · search, thumbnails, clipboard, license',
         'S': 'SEMANTIC · MiniLM split-search daemon (IPC)',
         'T': 'STORY · text → emoji PNG'}
STCOL = {'E': '#6b7f96', 'U': '#4f9b95', 'C': '#8a5fb0', 'S': '#5a9367', 'T': '#c17a3a'}

# ---------- layout via Graphviz --------------------------------------------
dot = ['digraph P{', 'rankdir=BT;', 'splines=polyline;', 'nodesep=0.45;', 'ranksep=0.85;',
       'node[shape=box,fixedsize=true];']
for n in G['nodes']:
    dot.append(decl(n))
grp = {n['id']: n['grp'] for n in G['nodes']}
# Real (routed) edges. A floating data/ext node should not distort the clean
# module bands, but a *pure committed input* (a .npy / .onnx / the tarball, read
# by exactly one module and written by nobody) reads best nestled right beside
# that reader — so let its single loader edge constrain the rank. Only float
# (constraint=false) the edges out of nodes that a script writes (runtime state,
# extracted assets) or that fan out to several consumers; those are the ones
# whose rank pull would otherwise smear the bands.
writers = {e['to'] for e in edges if grp.get(e['from']) in BANDS}
outdeg = Counter(e['from'] for e in edges)
# A connector stub must sit next to the neighbour it references, and a floating
# node that no edge points into (a pure external source) would sink to the floor
# with nothing to rank it — so keep at least its edge to a banded consumer.
no_incoming = {e['from'] for e in edges} - {e['to'] for e in edges}
for e in edges:
    src, dst = e['from'], e['to']
    floating = grp.get(src) not in BANDS
    stub = 'proxy' in (grp.get(src), grp.get(dst))
    nonconstrain = floating and (src in writers or outdeg[src] > 1)
    if stub or (floating and src in no_incoming and grp.get(dst) in BANDS):
        nonconstrain = False
    attr = '[constraint=false]' if nonconstrain else ''
    dot.append(f'"{e["from"]}"->"{e["to"]}"{attr};')
# Force clean stage bands via one invisible funnel node per gap: every node of
# band k ranks above every node of band k+1.
band_ids = {g: [n['id'] for n in G['nodes'] if n['grp'] == g] for g in BANDS}
for i in range(len(BANDS) - 1):
    z = f'__z{i}'
    dot.append(f'"{z}"[style=invis,width=0.01,height=0.01,label=""];')
    for nid in band_ids[BANDS[i]]:
        dot.append(f'"{nid}"->"{z}"[style=invis];')
    for nid in band_ids[BANDS[i + 1]]:
        dot.append(f'"{z}"->"{nid}"[style=invis];')
dot.append('}')
pos, H, edgepts = run_dot(dot)
X, Y = transforms(H)

bg = []   # stage tags (behind)
mid = []  # edges
fg = []   # nodes (front)

# stage tags as a left-margin legend column, one per band at that band's centre
# height. Centred-above tags collide with the floating data cylinders that
# interleave the module bands, so the labels live in their own left gutter where
# nothing else is drawn.
all_left = min(X(pos[n['id']][0]) - n['w'] / 2 for n in G['nodes'] if n['id'] in pos)
LAB_W = 250
lab_x = all_left - LAB_W - 60
for g in BANDS:
    ns = [n for n in G['nodes'] if n['grp'] == g and n['id'] in pos]
    if not ns:
        continue
    cy = sum(Y(pos[n['id']][1]) for n in ns) / len(ns)
    st = (f'rounded=1;whiteSpace=wrap;html=1;fillColor={STCOL[g]};strokeColor=none;'
          'fontColor=#ffffff;fontSize=13;fontStyle=1;opacity=92;align=left;spacingLeft=10;')
    bg.append(f'<mxCell id="lab_{g}" value="{esc(STLAB[g])}" style="{st}" vertex="1" parent="1">'
              f'<mxGeometry x="{lab_x:.0f}" y="{cy-16:.0f}" width="{LAB_W}" height="32" as="geometry"/></mxCell>')

# nodes + edges
for n in G['nodes']:
    if n['id'] in pos:
        fg.append(emit_node(n, node_style(n), pos, X, Y))
mid = emit_edges(edges, edgepts, X, Y, EBASE, EK)

xml = wrap_mxfile(bg + mid + fg, 'kitchensearch flow', 'kitchensearch-flow')
open(OUT, 'w').write(xml)
print('wrote %s: nodes=%d edges=%d bytes=%d' % (OUT, len(pos), len(edges), len(xml)))
