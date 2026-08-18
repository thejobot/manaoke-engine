#!/usr/bin/env python3
"""backlog.py — the song creator's issue backlog (stdlib only).

A known-issue tracker for the Manaoke builder. GENERAL by design: it holds any
kind of issue (a validator finding, a hand-noted TODO, a "this clip sounds off"
gripe), keyed by a stable id so imports never duplicate. The first issue-type
it seeds from is `validate_segmentation` (over-merged study cards), but nothing
here is segmentation-specific.

Store: builds/backlog.json — a flat JSON list of items:
  {id, created (epoch int), song, section, type, severity(low|med|high),
   title, detail, suggest(list|None), status(open|done|wontfix),
   source, notes}

id = short sha1 of (song|type|section|key) so re-importing the same finding
lands on the SAME item (idempotent upsert), never a duplicate.

Subcommands:
  add     --song S --type T --title "..." [--detail ..] [--severity med] [--section X]
  list    [--song S] [--status open] [--type T]
  resolve <id> [--status done|wontfix] [--note "..."]
  import-segmentation    upsert every validate_segmentation finding; a finding
                         that stopped appearing (song was fixed) auto-closes to
                         `done` ("resolved: no longer flagged"). Manual items and
                         non-segmentation items are never auto-touched.
  view    regenerate builder/backlog.html (self-contained read-only viewer).
"""
import argparse, hashlib, json, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_io          # flock + atomic writes — denmoku jobs and CLI runs
                         # touch backlog.json concurrently (backlog f8a1fe93)

HERE = Path(__file__).resolve().parent          # tools/songcraft
ROOT = HERE.parents[1]                           # repo root
STORE = HERE / 'builds' / 'backlog.json'
VIEW = HERE / 'builder' / 'backlog.html'
VALIDATOR = HERE / 'validate_segmentation.py'
# validate_segmentation needs fugashi + unidic, which live in the parler env.
PARLER_PY = '/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python'
SEG_SOURCE = 'validate_segmentation'
AUTO_RESOLVED = 'resolved: no longer flagged'
SEV = ('low', 'med', 'high')

# ── store I/O (locked + atomic — see state_io.py) ──────────────────────────
def load():
    if STORE.exists():
        return json.loads(state_io.locked_read(STORE))
    return []

def save(items):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    state_io.locked_write(STORE, json.dumps(items, ensure_ascii=False, indent=2) + '\n')

def stable_id(*parts):
    """Deterministic short id from the item's identity parts. Same parts in →
    same id out, so an upsert finds its existing row instead of duplicating."""
    key = '|'.join('' if p is None else str(p) for p in parts)
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]

def by_id(items):
    return {it['id']: it for it in items}

def add_note(it, note):
    if not note:
        return
    it['notes'] = (it.get('notes') + ' | ' + note) if it.get('notes') else note

# ── add ────────────────────────────────────────────────────────────────────
def cmd_add(a):
    items = load()
    # manual items key off (song, type, section, title) so a re-add of the same
    # thing is a no-op rather than a duplicate.
    iid = stable_id(a.song, a.type, a.section or '', a.title)
    idx = by_id(items)
    if iid in idx:
        print(f'exists: {iid} — {idx[iid]["title"]}  (no duplicate added)')
        return 0
    it = dict(id=iid, created=int(time.time()), song=a.song, section=a.section or '',
              type=a.type, severity=a.severity, title=a.title, detail=a.detail or '',
              suggest=None, status='open', source='manual', notes='')
    items.append(it)
    save(items)
    print(f'added {iid}  [{a.type}/{a.severity}]  {a.song}  {a.title}')
    return 0

# ── list ───────────────────────────────────────────────────────────────────
def cmd_list(a):
    items = load()
    rows = [it for it in items
            if (not a.song or it['song'] == a.song)
            and (not a.status or it['status'] == a.status)
            and (not a.type or it['type'] == a.type)]
    if not rows:
        print('(no matching backlog items)')
        return 0
    # newest-open first, then by song
    order = {'open': 0, 'wontfix': 1, 'done': 2}
    rows.sort(key=lambda it: (order.get(it['status'], 3), it['song'], it['section']))
    w = dict(id=8, st=7, sev=4, type=13, song=18, sec=8)
    hdr = (f'{"id":<{w["id"]}} {"status":<{w["st"]}} {"sev":<{w["sev"]}} '
           f'{"type":<{w["type"]}} {"song":<{w["song"]}} {"sec":<{w["sec"]}} title')
    print(hdr)
    print('-' * len(hdr))
    for it in rows:
        print(f'{it["id"]:<{w["id"]}} {it["status"]:<{w["st"]}} '
              f'{it["severity"]:<{w["sev"]}} {it["type"][:w["type"]]:<{w["type"]}} '
              f'{it["song"][:w["song"]]:<{w["song"]}} {it["section"][:w["sec"]]:<{w["sec"]}} '
              f'{it["title"]}')
    n = len(rows)
    opn = sum(1 for it in rows if it['status'] == 'open')
    print(f'\n{n} item(s) · {opn} open')
    return 0

# ── resolve ────────────────────────────────────────────────────────────────
def cmd_resolve(a):
    items = load()
    idx = by_id(items)
    it = idx.get(a.id)
    if not it:
        print(f'no item with id {a.id}', file=sys.stderr)
        return 1
    it['status'] = a.status
    add_note(it, a.note)
    save(items)
    try:                                   # lessons loop — never breaks resolve
        sys.path.insert(0, str(HERE)); import lessons
        lessons.journal('backlog', it.get('song', ''),
                        f'{a.id} → {a.status}: {it["title"]}',
                        detail=a.note, source='backlog resolve')
    except Exception:
        pass
    print(f'{a.id} → {a.status}' + (f'  ({a.note})' if a.note else ''))
    return 0

# ── import-segmentation ────────────────────────────────────────────────────
def _run_validator():
    """Shell out to validate_segmentation --all --json in the parler env."""
    py = PARLER_PY if Path(PARLER_PY).exists() else sys.executable
    try:
        out = subprocess.run([py, str(VALIDATOR), '--all', '--json'],
                             cwd=str(ROOT), capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f'validate_segmentation failed:\n{e.stderr or e.stdout}')
    return json.loads(out.stdout or '[]')

def cmd_import_segmentation(a):
    findings = _run_validator()
    items = load()
    idx = by_id(items)

    seen = set()          # ids present in the current findings
    added = updated = 0
    for f in findings:
        # section is part of the identity: odoriko's 置いてきた appears in BOTH
        # v1 and v2 — two real cards, two backlog rows, not one collapsed row.
        iid = stable_id(f['song'], 'segmentation', f.get('section', ''), f['jp'])
        seen.add(iid)
        parts = f.get('suggest') or []
        title = f'over-merged card: {f["jp"]} → {" │ ".join(parts)}'
        if iid in idx:
            it = idx[iid]
            it.update(title=title, detail=f.get('reason', ''), suggest=parts,
                      section=f.get('section', ''))
            # a finding we'd auto-closed is flagged again → it regressed; reopen.
            if it['status'] == 'done' and it.get('notes', '').endswith(AUTO_RESOLVED):
                it['status'] = 'open'
                add_note(it, 'reopened: flagged again')
            updated += 1
        else:
            it = dict(id=iid, created=int(time.time()), song=f['song'],
                      section=f.get('section', ''), type='segmentation',
                      severity='med', title=title, detail=f.get('reason', ''),
                      suggest=parts, status='open', source=SEG_SOURCE,
                      notes='')
            items.append(it)
            idx[iid] = it
            added += 1

    # auto-close: an OPEN segmentation item we imported before whose finding is
    # gone (song was fixed). Never touch manual items or other issue types, and
    # never reverse a human's done/wontfix.
    closed = 0
    for it in items:
        if it.get('source') == SEG_SOURCE and it['id'] not in seen and it['status'] == 'open':
            it['status'] = 'done'
            add_note(it, AUTO_RESOLVED)
            closed += 1

    save(items)
    total = len(findings)
    print(f'import-segmentation: {total} finding(s) → +{added} new, '
          f'{updated} refreshed, {closed} auto-closed.')
    # by-song breakdown of what's currently flagged
    bysong = {}
    for f in findings:
        bysong[f['song']] = bysong.get(f['song'], 0) + 1
    for s, n in sorted(bysong.items()):
        print(f'    {s}: {n}')
    return 0

# ── view (self-contained HTML) ─────────────────────────────────────────────
def cmd_view(a):
    items = load()
    html = _VIEW_TEMPLATE.replace('/*__DATA__*/', json.dumps(items, ensure_ascii=False))
    VIEW.parent.mkdir(parents=True, exist_ok=True)
    VIEW.write_text(html)
    print(f'wrote {VIEW}  ({len(items)} item(s))')
    return 0


_VIEW_TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Manaoke · Backlog</title>
<style>
 @font-face{font-family:'DG';src:local('DotGothic16');}
 :root{
   --bg:#0c0b0f; --panel:#16141b; --panel2:#1d1a23; --rule:#2a2632;
   --ink:#efeae2; --dim:#9b93a6; --faint:#6a6376; --lcd-ink:#7dd6a8;
   --cli:#c9a6ff;
   /* severity */
   --sev-low:#6a6376; --sev-med:#fbbf24; --sev-high:#f0806a;
   /* status */
   --open:#7dd6ff; --done:#6ee7b7; --wontfix:#6a6376;
   --f-disp:'DotGothic16',ui-monospace,monospace;
   --f-body:ui-sans-serif,-apple-system,'Segoe UI',system-ui,sans-serif;
   --f-mono:ui-monospace,'SF Mono','JetBrains Mono',monospace;
 }
 *{box-sizing:border-box}
 html,body{margin:0}
 body{background:var(--bg);color:var(--ink);font-family:var(--f-body);font-size:15px;
   line-height:1.5;-webkit-font-smoothing:antialiased;padding:0 0 64px;
   background-image:radial-gradient(120% 80% at 50% -10%,#191622 0%,#0c0b0f 60%)}
 .wrap{max-width:720px;margin:0 auto;padding:0 16px}
 header.mast{padding:28px 0 14px;border-bottom:1px solid var(--rule);margin-bottom:16px}
 .kicker{font-family:var(--f-mono);font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--faint)}
 h1{font-family:var(--f-disp);font-weight:400;font-size:27px;margin:6px 0 6px}
 h1 .jp{color:var(--lcd-ink)}
 .dek{color:var(--dim);font-size:13.5px;max-width:60ch;margin:0}
 a.back{font-family:var(--f-mono);font-size:11px;color:var(--faint);text-decoration:none}
 a.back:hover{color:var(--ink)}
 .tally{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0 4px;font-family:var(--f-mono);
   font-size:11px;letter-spacing:.04em;color:var(--dim)}
 .tally b{color:var(--ink);font-weight:400}
 /* filter bar */
 .filters{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 20px}
 .filt{font-family:var(--f-mono);font-size:11px;letter-spacing:.05em;padding:6px 12px;border-radius:20px;
   border:1px solid var(--rule);background:#171420;color:var(--dim);cursor:pointer}
 .filt:hover{color:var(--ink);border-color:var(--faint)}
 .filt.on{color:var(--bg);background:var(--lcd-ink);border-color:var(--lcd-ink);font-weight:700}
 /* song group */
 .group{margin:0 0 22px}
 .group-hd{display:flex;gap:10px;align-items:baseline;padding:0 2px 8px;border-bottom:1px solid var(--rule);margin-bottom:12px}
 .group-hd .sn{font-family:var(--f-disp);font-size:17px;color:var(--ink)}
 .group-hd .ct{font-family:var(--f-mono);font-size:11px;color:var(--faint);margin-left:auto}
 /* item card */
 .item{background:var(--panel);border:1px solid var(--rule);border-radius:12px;
   padding:13px 15px;margin:0 0 11px}
 .item.done,.item.wontfix{opacity:.62}
 .item .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:7px}
 .badge{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;
   padding:2px 8px;border-radius:20px;border:1px solid currentColor;flex:none}
 .b-type{color:var(--cli)}
 .b-sev-low{color:var(--sev-low)} .b-sev-med{color:var(--sev-med)} .b-sev-high{color:var(--sev-high)}
 .b-open{color:var(--open)} .b-done{color:var(--done)} .b-wontfix{color:var(--wontfix)}
 .b-sec{color:var(--faint)}
 .item .id{font-family:var(--f-mono);font-size:10px;color:var(--faint);margin-left:auto}
 .item .ti{font-family:var(--f-disp);font-size:15.5px;color:var(--ink);line-height:1.35;margin:2px 0 4px}
 .item .de{color:#c3bccb;font-size:13px;line-height:1.5;margin:0 0 6px}
 .split{display:flex;gap:0;align-items:stretch;flex-wrap:wrap;margin:8px 0 2px}
 .split .lbl{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
   color:var(--faint);align-self:center;margin-right:9px}
 .chip{font-family:var(--f-disp);font-size:14px;color:var(--lcd-ink);background:#101318;
   border:1px solid #23303a;border-radius:7px;padding:4px 11px}
 .split .bar{align-self:center;color:var(--faint);font-size:13px;padding:0 8px}
 .item .notes{font-family:var(--f-mono);font-size:11px;color:var(--faint);margin-top:8px;
   background:#120f18;border-left:2px solid var(--rule);padding:6px 10px;border-radius:0 6px 6px 0;white-space:pre-wrap}
 .empty{color:var(--faint);text-align:center;padding:44px 0;font-family:var(--f-mono);font-size:13px}
 footer{color:var(--faint);font-family:var(--f-mono);font-size:11px;text-align:center;margin-top:30px;line-height:1.7}
 footer code{color:var(--dim)}
</style></head>
<body>
<div class="wrap">
 <header class="mast">
   <div class="kicker">学オケ · backlog</div>
   <h1>The <span class="jp">積み残し</span> — song creator backlog</h1>
   <p class="dek">Every known issue across the songs, in one place, so nothing gets
     lost between builds. Validator findings (over-merged study cards) seed it; hand-noted
     TODOs live here too. Read-only: status changes happen on the CLI
     (<code style="color:var(--dim)">backlog.py resolve &lt;id&gt;</code>), and fixing a song
     auto-closes its imported items on the next <code style="color:var(--dim)">import-segmentation</code>.</p>
   <p style="margin:8px 0 0"><a class="back" href="index.html">&larr; back to the denmoku</a></p>
   <div class="tally" id="tally"></div>
 </header>
 <div class="filters" id="filters"></div>
 <div id="list"></div>
 <footer>
   Generated by <code>backlog.py view</code> from <code>builds/backlog.json</code>.<br>
   Resolve on the CLI: <code>backlog.py resolve &lt;id&gt; --status done|wontfix --note "..."</code>.
 </footer>
</div>
<script>
const ITEMS = /*__DATA__*/;
let filter = 'open';
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function counts(){
  const c={open:0,done:0,wontfix:0,all:ITEMS.length};
  ITEMS.forEach(i=>{c[i.status]=(c[i.status]||0)+1});
  return c;
}
function badge(cls,txt){return '<span class="badge '+cls+'">'+esc(txt)+'</span>'}
function splitRow(parts){
  if(!parts||!parts.length) return '';
  const chips=parts.map(p=>'<span class="chip">'+esc(p)+'</span>').join('<span class="bar">│</span>');
  return '<div class="split"><span class="lbl">split</span>'+chips+'</div>';
}
function itemCard(it){
  return '<div class="item '+esc(it.status)+'">'+
    '<div class="top">'+
      badge('b-type','#'+it.type)+
      badge('b-sev-'+it.severity, it.severity)+
      badge('b-'+it.status, it.status)+
      (it.section?badge('b-sec', it.section):'')+
      '<span class="id">'+esc(it.id)+'</span></div>'+
    '<div class="ti">'+esc(it.title)+'</div>'+
    (it.detail?('<div class="de">'+esc(it.detail)+'</div>'):'')+
    splitRow(it.suggest)+
    (it.notes?('<div class="notes">'+esc(it.notes)+'</div>'):'')+
  '</div>';
}
function render(){
  const c=counts();
  document.getElementById('tally').innerHTML=
    '<span><b>'+c.open+'</b> open</span><span><b>'+(c.done||0)+'</b> done</span>'+
    '<span><b>'+(c.wontfix||0)+'</b> wontfix</span><span><b>'+c.all+'</b> total</span>';
  const opts=[['open','open'],['done','done'],['wontfix','wontfix'],['all','all']];
  document.getElementById('filters').innerHTML=opts.map(([k,l])=>
    '<button class="filt'+(filter===k?' on':'')+'" data-f="'+k+'">'+l+' ('+(k==='all'?c.all:(c[k]||0))+')</button>').join('');
  document.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>{filter=b.getAttribute('data-f');render()});
  const rows=ITEMS.filter(i=>filter==='all'||i.status===filter);
  const list=document.getElementById('list');
  if(!rows.length){list.innerHTML='<div class="empty">no '+esc(filter)+' items.</div>';return}
  // group by song, keep song order of first appearance
  const groups=[]; const seen={};
  rows.forEach(i=>{if(!(i.song in seen)){seen[i.song]=[];groups.push(i.song)}seen[i.song].push(i)});
  const sevRank={high:0,med:1,low:2};
  list.innerHTML=groups.map(song=>{
    const g=seen[song].slice().sort((a,b)=>(sevRank[a.severity]-sevRank[b.severity])||a.section.localeCompare(b.section));
    return '<div class="group"><div class="group-hd"><span class="sn">'+esc(song)+
      '</span><span class="ct">'+g.length+' item'+(g.length>1?'s':'')+'</span></div>'+
      g.map(itemCard).join('')+'</div>';
  }).join('');
}
render();
</script>
</body></html>"""


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='Manaoke song-creator backlog manager.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('add', help='add a manual backlog item')
    p.add_argument('--song', required=True)
    p.add_argument('--type', required=True, help='e.g. segmentation, pronunciation, timing, todo')
    p.add_argument('--title', required=True)
    p.add_argument('--detail', default='')
    p.add_argument('--severity', default='med', choices=SEV)
    p.add_argument('--section', default='')
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser('list', help='show items as a table')
    p.add_argument('--song')
    p.add_argument('--status', choices=('open', 'done', 'wontfix'))
    p.add_argument('--type')
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser('resolve', help="change an item's status / add a note")
    p.add_argument('id')
    p.add_argument('--status', default='done', choices=('open', 'done', 'wontfix'))
    p.add_argument('--note', default='')
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser('import-segmentation', help='seed/refresh from validate_segmentation')
    p.set_defaults(fn=cmd_import_segmentation)

    p = sub.add_parser('view', help='regenerate builder/backlog.html')
    p.set_defaults(fn=cmd_view)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
