#!/usr/bin/env python3
"""Render the Manaoke Song Page Builder dashboard as ONE self-contained HTML
file with the build state embedded (works offline on file:// — no server, no
fetch). Called by manaoke_build.write_dashboard(). Identity: the denmoku, the
karaoke songbook-remote you punch a song into and it runs the sequence.

Layout (2026-07-04 rework, per the owner): the main page is a SEARCHABLE GRID of album
art — tap a jacket to open that song's detail (with a BACK button). Detail has
two tabs: Build (the step sequence) and Gradient (the living-field lab, folded in
per-song — no longer a separate page/button). A "Main page" tile edits the
landing's own field; a "＋ New song" tile is the guided walkthrough. The gradient
editor keeps the preview PINNED (sticky) so you see the field change without
scrolling, and offers three push targets: this song, the motion dials globally
(dials only — never a global gradient), or the main page. The BACKLOG surface
(backlog.py / backlog.html, the study-card issue tracker) stays linked from the
library view."""
import json
from pathlib import Path


def render(all_states, lex=None, gl=None, dag=None, server=False):
    """all_states = the per-song build states (index.json). lex = the second
    data channel: {'words': the pronunciation lexicon, 'stale': per-song cheap
    staleness}. gl = the Gradient Lab state (manaoke_build._gradient_lab_state:
    per-song effective palette + recorded override + card accent, the landing
    'main' field, defaults, pale thresholds, fdur bases, motions). dag = the
    canonical STEPS sequence (for the New Song walkthrough, which has no
    build_state yet). server = SERVER_MODE (denmoku v2, builder/server.py):
    the same page, but the live JS turns on — /api/state polling with in-place
    patching, Run / Mark-done buttons, the job bar, the New Song search flow.
    file:// output (server=False) keeps the embedded-state behavior."""
    any_running = any(s.get('status') == 'running'
                      for b in (all_states or []) for s in (b.get('steps') or []))
    return (_TEMPLATE
            .replace('/*__STATE__*/', json.dumps(all_states, ensure_ascii=False))
            .replace('/*__LEX__*/', json.dumps(lex or {'words': {}, 'stale': {}}, ensure_ascii=False))
            .replace('/*__GL__*/', json.dumps(gl or {}, ensure_ascii=False))
            .replace('/*__DAG__*/', json.dumps(dag or [], ensure_ascii=False))
            .replace('/*__RUNNING__*/', 'true' if any_running else 'false')
            .replace('/*__SERVER__*/', 'true' if server else 'false'))


def render_server_html():
    """The dashboard with SERVER_MODE=true, gathered fresh from disk — called
    by builder/server.py per GET / (contract: docs/denmoku-v2-api.md). Mirrors
    exactly what manaoke_build.write_dashboard() embeds, via manaoke_build's
    own loaders (lazy import: manaoke_build imports this module lazily inside
    write_dashboard, so neither side may import the other at module level).
    Read-only — unlike write_dashboard it never rewrites build_state files."""
    import sys
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import manaoke_build as mb
    all_states = []
    for p in sorted(mb.BUILDS.glob('*.build_state.json')):
        try:
            all_states.append(mb._sync_steps(json.loads(p.read_text())))
        except Exception:
            pass                       # a mid-write file must never 500 the page
    lex = {'words': {}, 'stale': {}}
    try:
        lex['words'] = mb.load_lexicon_doc().get('words', {})
    except Exception:
        pass
    for st in all_states:
        try:
            lex['stale'][st['key']] = mb._cheap_stale(st)
        except Exception as e:
            lex['stale'][st['key']] = {'state': f'stale? ({type(e).__name__})',
                                       'reasons': [], 'cmd': '', 'warn': True}
    try:
        gl = mb._gradient_lab_state(all_states)
    except Exception:
        gl = {}
    dag = [{k: s.get(k) for k in ('key', 'title', 'owner', 'auto', 'blurb', 'cmd')}
           for s in mb.STEPS]
    return render(all_states, lex, gl, dag, server=True)


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Manaoke · Denmoku — song page builder</title>
<!-- Add to Home Screen gives a real standalone app — own icon, no Safari
     chrome, no URL bar. The phone is a first-class Denmoku surface and it was
     opening as a bookmarked web page. The manifest/icon routes are server-only
     (builder/server.py); a file:// dashboard just gets the data: icon. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Denmoku">
<meta name="theme-color" content="#0c0b0f">
<meta name="color-scheme" content="dark">
<link rel="icon" href="data:,">
<script>if(location.protocol!=='file:'){
  document.write('<link rel="manifest" href="/denmoku.webmanifest">'+
    '<link rel="apple-touch-icon" href="/denmoku-icon.png">'+
    '<link rel="icon" href="/denmoku-icon.png">')}</script>
<style>
 @font-face{font-family:'DG';src:local('DotGothic16');}
 :root{
   --bg:#0c0b0f; --panel:#16141b; --panel2:#1d1a23; --rule:#2a2632;
   --ink:#efeae2; --dim:#9b93a6; --faint:#6a6376;
   --lcd:#101318; --lcd-ink:#7dd6a8;
   /* semantic ownership */
   --local:#6ee7b7;     /* the box runs it */
   --cli:#c9a6ff;       /* hand to Claude Code / a CLI */
   --external:#fbbf24;  /* a signed-in tab / a hosted service */
   --warn:#f0806a;
   /* status */
   --done:#6ee7b7; --blocked:#f0806a; --pending:#6a6376; --running:#7dd6ff;
   --f-disp:'DotGothic16',ui-monospace,monospace;
   --f-body:ui-sans-serif,-apple-system,'Segoe UI',system-ui,sans-serif;
   --f-mono:ui-monospace,'SF Mono','JetBrains Mono',monospace;
 }
 *{box-sizing:border-box}
 html,body{margin:0}
 body{background:var(--bg);color:var(--ink);font-family:var(--f-body);
   font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
   padding:0 0 64px;
   background-image:radial-gradient(120% 80% at 50% -10%,#191622 0%,#0c0b0f 60%);}
 .wrap{max-width:920px;margin:0 auto;padding:0 18px}
 a{color:inherit}
 /* masthead — the denmoku faceplate. Sticky and full-bleed (it sits OUTSIDE
    .wrap and carries its own inner wrap) so the bar spans the screen on a
    phone instead of leaving gutters for content to scroll through, and so the
    way back is always one thumb-reach away rather than a scroll to the top. */
 header.mast{position:relative;z-index:35;border-bottom:1px solid var(--rule);
   margin-bottom:20px;background:rgba(12,11,15,.82);
   -webkit-backdrop-filter:saturate(150%) blur(14px);backdrop-filter:saturate(150%) blur(14px);
   padding-top:env(safe-area-inset-top)}
 /* Sticky only where it earns its keep: the 52px title bar. The library's
    faceplate is ~290px tall — pinning THAT would eat a third of the phone
    screen for the whole scroll. */
 body[data-view="detail"] header.mast{position:sticky;top:0}
 /* keep .wrap's 18px gutters — a bare `padding:26px 0` here overrode them and
    put the masthead hard against the left edge of the screen */
 .mastin{position:relative;padding:26px 18px 16px}
 .refbtn{position:absolute;top:24px;right:18px;font-family:var(--f-mono);font-size:14px;line-height:1;
   color:var(--dim);background:var(--panel2);border:1px solid var(--rule);border-radius:9px;
   padding:8px 11px;cursor:pointer}
 .refbtn:hover{color:var(--ink);border-color:var(--faint)}
 .kicker{font-family:var(--f-mono);font-size:11px;letter-spacing:.22em;
   text-transform:uppercase;color:var(--faint)}
 h1{font-family:var(--f-disp);font-weight:400;font-size:28px;letter-spacing:.02em;
   margin:8px 0 4px;color:var(--ink)}
 h1 .jp{color:var(--lcd-ink)}
 .dek{color:var(--dim);font-size:13.5px;max-width:60ch;margin:6px 0 0}
 /* ── title bar (detail views) ───────────────────────────────────────────
    Inside a song, the branding is dead weight: it cost ~250px — a third of a
    phone screen — above every single build screen, and the way back was a
    fourth stacked row under it. Swap the whole faceplate for one 52px row. */
 body[data-view="detail"] .brand{display:none}
 body[data-view="detail"] .mastin{padding:6px 18px}
 body[data-view="detail"] .refbtn{top:50%;transform:translateY(-50%);padding:7px 10px;font-size:13px}
 .navbar{display:flex;align-items:center;gap:6px;min-height:40px;padding-right:52px}
 /* display:flex beats the UA's [hidden]{display:none}, so the back chevron sat
    on the library screen under the masthead. Say it louder than flex. */
 .navbar[hidden]{display:none}
 .navback{flex:none;width:38px;height:38px;margin-left:-9px;display:flex;align-items:center;
   justify-content:center;font-family:var(--f-disp);font-size:26px;line-height:1;
   color:var(--ink);background:transparent;border:0;border-radius:50%;cursor:pointer;
   -webkit-tap-highlight-color:transparent}
 .navback:hover{background:var(--panel2)}
 .navback:active{background:var(--rule)}
 .navt{min-width:0;display:flex;flex-direction:column;justify-content:center}
 .navt b{font-family:var(--f-disp);font-weight:400;font-size:16px;line-height:1.2;color:var(--ink);
   overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .navt span{font-family:var(--f-mono);font-size:10.5px;letter-spacing:.05em;color:var(--faint);
   overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 /* punch-in / search */
 .punch{display:flex;gap:10px;align-items:stretch;margin:18px 0 4px;flex-wrap:wrap}
 .punch .lcd{flex:1;min-width:220px;background:var(--lcd);border:1px solid #23303a;
   border-radius:9px;padding:11px 14px;box-shadow:inset 0 1px 0 #0006, inset 0 0 30px #0a1a1230}
 .punch .lcd label{font-family:var(--f-mono);font-size:10px;letter-spacing:.18em;
   text-transform:uppercase;color:#3f6b57;display:block;margin-bottom:4px}
 .punch .lcd input{width:100%;background:transparent;border:0;outline:0;
   font-family:var(--f-disp);font-size:19px;color:var(--lcd-ink);letter-spacing:.03em}
 .punch .lcd input::placeholder{color:#2f5445}
 /* one line instead of a lit dot on every jacket — a state everything shares
    is said once, up here, not eight times down there. */
 .allstale{margin:14px 0 4px;font-family:var(--f-mono);font-size:11px;
   letter-spacing:.04em;color:var(--blocked)}
 /* ── library grid: just the jackets ────────────────────────────────── */
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px;margin:6px 0 8px}
 .tile{position:relative;aspect-ratio:1;border-radius:13px;overflow:hidden;cursor:pointer;
   border:1px solid var(--rule);background:#131018;box-shadow:0 3px 14px #0007;
   transition:transform .12s, box-shadow .12s, border-color .12s}
 .tile:hover{transform:translateY(-2px);box-shadow:0 8px 24px #000a;border-color:#3b3547}
 .tile img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
 .tile .cap{position:absolute;left:0;right:0;bottom:0;padding:22px 11px 9px;
   background:linear-gradient(180deg,transparent,rgba(6,5,10,.86) 62%);}
 .tile .cap .jp{font-family:var(--f-disp);font-size:15px;color:#fff;line-height:1.15;
   text-shadow:0 1px 6px #000b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .tile .cap .ar{font-family:var(--f-mono);font-size:10px;color:#d6cfe0;letter-spacing:.03em;
   margin-top:2px;text-shadow:0 1px 4px #000b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .tile .pbar{position:absolute;left:0;right:0;bottom:0;height:4px;background:#0009}
 .tile .pbar > i{display:block;height:100%;background:linear-gradient(90deg,var(--local),#3fb98a)}
 .tile .sd{position:absolute;top:9px;right:9px;width:9px;height:9px;border-radius:50%;
   background:var(--done);box-shadow:0 0 0 2px #0007}
 .tile .sd.warn{background:var(--blocked)}
 .tile.special{display:flex;align-items:center;justify-content:center;flex-direction:column;
   gap:8px;background:linear-gradient(160deg,#1a1722,#131019);text-align:center}
 .tile.special .big{font-size:34px;color:var(--lcd-ink);font-family:var(--f-disp);line-height:1}
 .tile.special .lbl{font-family:var(--f-mono);font-size:11px;letter-spacing:.12em;
   text-transform:uppercase;color:var(--dim)}
 .tile.mainpg{position:relative;overflow:hidden}
 .tile.mainpg .mini{position:absolute;inset:0}
 .tile.mainpg .lbl2{position:absolute;left:0;right:0;bottom:0;padding:20px 10px 9px;
   font-family:var(--f-mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:#f0ecf6;
   background:linear-gradient(180deg,transparent,rgba(6,5,10,.8));text-align:center}
 /* ── detail chrome (the back chip moved into the title bar) ────────── */
 .dhead{display:flex;gap:15px;align-items:center;margin:0 0 14px}
 .dhead img{width:66px;height:66px;border-radius:10px;object-fit:cover;flex:none;box-shadow:0 3px 10px #0009}
 .dhead .t{flex:1;min-width:0}
 .dhead .t .jp{font-family:var(--f-disp);font-size:22px;color:var(--ink);line-height:1.12}
 .dhead .t .en{color:var(--dim);font-size:13px;margin-top:2px}
 .dhead .t .ar{color:var(--faint);font-size:11.5px;font-family:var(--f-mono);margin-top:3px;letter-spacing:.04em}
 @media(max-width:480px){
   header.mast h1{font-size:24px}
   .dhead{flex-wrap:wrap}
   .dhead img{width:54px;height:54px}
   .dhead .prog{width:100%;justify-content:flex-start;margin-top:2px}
   .bar{width:100px}
 }
 .seg{display:inline-flex;background:#100e16;border:1px solid var(--rule);border-radius:10px;
   padding:3px;gap:3px;margin:0 0 16px}
 .seg button{font-family:var(--f-mono);font-size:12px;letter-spacing:.06em;padding:7px 15px;border-radius:7px;
   border:0;background:transparent;color:var(--dim);cursor:pointer}
 .seg button.on{background:var(--panel2);color:var(--ink);box-shadow:inset 0 0 0 1px var(--rule)}
 /* progress row in detail */
 .prog{display:flex;align-items:center;gap:10px;flex:none}
 .prog .frac{font-family:var(--f-mono);font-size:12px;color:var(--dim)}
 .bar{width:120px;height:6px;border-radius:3px;background:#0c0a10;overflow:hidden;border:1px solid #241f2c}
 .bar > i{display:block;height:100%;background:linear-gradient(90deg,var(--local),#3fb98a)}
 /* steps */
 .steps{margin:2px 0 10px}
 .nextup{display:flex;align-items:center;gap:12px;margin:0 0 14px;padding:12px 14px;
   border:1px solid var(--rule);border-left:3px solid var(--acc);border-radius:8px;background:var(--panel)}
 .nextup.live{border-left-color:var(--run,#7ee0a0)}
 .nextup .nu-t{flex:1;display:flex;flex-direction:column;gap:2px}
 .nextup .nu-t b{font-size:13.5px}
 .nextup .nu-t span{font-size:11.5px;color:var(--faint)}
 .nextup .row{display:flex;gap:8px}
 .plumb{margin-top:8px}
 .plumb summary{cursor:pointer;font-family:var(--f-mono);font-size:10.5px;color:var(--faint);
   text-transform:uppercase;letter-spacing:.08em}
 .plumb[open] summary{margin-bottom:6px}
 .step{border-top:1px solid var(--rule)}
 .step.next{background:linear-gradient(90deg,#171a22,transparent)}
 .step-hd{display:flex;gap:12px;align-items:center;padding:12px 6px;cursor:pointer;transition:background .12s}
 .step-hd:hover{background:#1b1822}
 .chev{width:14px;color:var(--faint);transition:transform .15s;flex:none;font-size:12px}
 .step.open .chev{transform:rotate(90deg)}
 .st-dot{width:11px;height:11px;border-radius:50%;flex:none;background:var(--pending)}
 .st-dot.done{background:var(--done)}
 .st-dot.blocked,.st-dot.failed{background:var(--blocked);box-shadow:0 0 8px #f0806a66}
 /* your turn — not a fault. Hollow ring in the hand-off colour, no alarm. */
 .st-dot.waiting{background:transparent;box-shadow:inset 0 0 0 2.5px var(--cli)}
 .st-dot.running{background:var(--running);animation:pulse 1s infinite}
 @keyframes pulse{50%{opacity:.35}}
 .step-hd .nm{flex:1;min-width:0}
 .step-hd .nm .ti{font-size:14px;color:var(--ink)}
 .step-hd .nm .key{font-family:var(--f-mono);font-size:10.5px;color:var(--faint);letter-spacing:.06em}
 .nextpill{font-family:var(--f-mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;
   color:var(--lcd-ink);border:1px solid #2e4a3e;border-radius:10px;padding:2px 7px;flex:none}
 .optpill{font-family:var(--f-mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;
   color:var(--dim);border:1px dashed var(--rule);border-radius:10px;padding:2px 7px;flex:none}
 .owner{font-family:var(--f-mono);font-size:10px;letter-spacing:.09em;text-transform:uppercase;
   padding:3px 8px;border-radius:20px;border:1px solid currentColor;flex:none}
 .owner.local{color:var(--local)} .owner.cli{color:var(--cli)} .owner.external{color:var(--external)}
 .auto{font-family:var(--f-mono);font-size:9.5px;color:var(--faint);letter-spacing:.08em;flex:none}
 .step-bd{display:none;padding:2px 8px 16px 40px;color:var(--dim);font-size:13.5px}
 .step.open .step-bd{display:block}
 .step-bd p{margin:6px 0 12px;color:#c3bccb;line-height:1.55}
 .cmd{position:relative;background:#0a0910;border:1px solid #241f2c;border-radius:8px;
   padding:11px 40px 11px 12px;font-family:var(--f-mono);font-size:12px;color:#b8e6cf;
   white-space:pre-wrap;word-break:break-word;line-height:1.5}
 .cmd.cli{color:#d9c6ff} .cmd.external{color:#ffe1a3}
 .cmd .cp{position:absolute;top:7px;right:7px;background:#1c1926;border:1px solid var(--rule);
   color:var(--dim);border-radius:6px;font-size:10px;font-family:var(--f-mono);padding:4px 7px;cursor:pointer}
 .cmd .cp:hover{color:var(--ink)}
 .row{display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap}
 .ctl{font-family:var(--f-mono);font-size:11px;letter-spacing:.05em;padding:6px 11px;border-radius:7px;
   border:1px solid var(--rule);background:#171420;color:var(--dim);cursor:pointer}
 .ctl:hover{color:var(--ink);border-color:var(--faint)}
 .ctl.on{color:var(--bg);background:var(--local);border-color:var(--local);font-weight:700}
 .note{margin-top:10px;font-family:var(--f-mono);font-size:11px;color:var(--faint);
   white-space:pre-wrap;background:#120f18;border-left:2px solid var(--rule);padding:8px 10px;border-radius:0 6px 6px 0}
 .stchip{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;
   padding:2px 8px;border-radius:12px;border:1px solid var(--rule);color:var(--dim);flex:none}
 .stchip.ok{color:var(--done);border-color:#2e4a3e}
 .stchip.warn{color:var(--blocked);border-color:#5a3a33;cursor:pointer}
 .stchip.warn:hover{color:var(--ink)}
 /* ── gradient lab (inline, no-scroll: preview stays pinned) ─────────── */
 .glab{display:grid;grid-template-columns:1fr;gap:14px;align-items:start}
 @media(min-width:760px){.glab{grid-template-columns:minmax(300px,44%) 1fr}}
 /* sticky preview: opaque page bg + a little pad so the scrolling color rows
    pass cleanly BEHIND it on mobile (single-column) instead of bleeding through
    the transparent card-accent row */
 .glab-prev{position:sticky;top:8px;z-index:4;background:var(--bg);padding-bottom:10px}
 .prev{position:relative;height:300px;border-radius:14px;overflow:hidden;border:1px solid var(--rule);
   background:radial-gradient(140% 130% at 50% 42%, var(--base1) 0%, var(--base2) 55%, var(--base3) 100%)}
 .prev .mesh{position:absolute;inset:-22%;opacity:.62;will-change:transform,opacity;
   background:
     linear-gradient(160deg, rgba(var(--c1),.30) 0%, rgba(var(--c2),.24) 42%, rgba(var(--c3),.36) 100%),
     radial-gradient(ellipse 140% 100% at 16% 18%, rgba(var(--c1),.58), transparent 64%),
     radial-gradient(ellipse 160% 110% at 84% 26%, rgba(var(--c2),.50), transparent 60%),
     radial-gradient(ellipse 135% 98% at 38% 76%, rgba(var(--c1),.56), transparent 68%),
     radial-gradient(ellipse 150% 104% at 78% 82%, rgba(var(--c3),.54), transparent 66%),
     radial-gradient(ellipse 116% 86% at 50% 46%, rgba(var(--hi),.22), transparent 60%);
   animation:fieldDrift var(--fdur-drift) ease-in-out infinite, fieldBreath var(--fdur-breath) ease-in-out infinite}
 .prev[data-motion="orbit"] .mesh{animation-name:fieldOrbit,fieldBreath}
 .prev[data-motion="sway"]  .mesh{animation-name:fieldSway,fieldBreath}
 .prev[data-motion="pulse"] .mesh{animation-name:fieldPulse,fieldBreath}
 .prev .fb{position:absolute;border-radius:50%;filter:blur(42px);mix-blend-mode:screen;opacity:.62;will-change:transform}
 .prev .fb1{width:80%;height:72%;left:-14%;top:-12%;
   background:radial-gradient(circle at 50% 50%, rgba(var(--fb1),.95), rgba(var(--fb1),0) 60%);
   animation:fbDriftA var(--fdur-a) ease-in-out infinite}
 .prev .fb2{width:90%;height:84%;right:-16%;bottom:-14%;
   background:radial-gradient(circle at 50% 50%, rgba(var(--fb2),.82), rgba(var(--fb2),0) 60%);
   animation:fbDriftB var(--fdur-b) ease-in-out infinite}
 .prev .fb3{width:66%;height:60%;left:22%;top:24%;
   background:radial-gradient(circle at 50% 50%, rgba(var(--fb3),.9), rgba(var(--fb3),0) 62%);
   animation:fbDriftC var(--fdur-c) ease-in-out infinite}
 .prev .scrim{position:absolute;inset:0;
   background:radial-gradient(150% 108% at 50% 45%, rgba(5,3,9,.10), rgba(5,3,9,.30) 100%)}
 /* CRT / LD-screen overlay — the EXACT scanline+vignette the song pages ship
    (#livingField .crt-overlay). Off by default; toggle it to judge the field
    the way it actually looks behind the screen. */
 .prev .crt{position:absolute;inset:0;z-index:6;pointer-events:none;overflow:hidden;display:none}
 .prev.crt-on .crt{display:block}
 .prev .crt::before{content:'';position:absolute;inset:0;mix-blend-mode:multiply;
   background:repeating-linear-gradient(0deg, transparent 0 2px, rgba(0,0,0,.14) 2px 3px)}
 .prev .crt::after{content:'';position:absolute;inset:0;
   background:radial-gradient(ellipse at center, transparent 58%, rgba(0,0,0,.42) 100%)}
 .crt-toggle{position:absolute;top:10px;right:10px;z-index:7;font-family:var(--f-mono);font-size:10px;
   letter-spacing:.12em;padding:5px 11px;border-radius:20px;cursor:pointer;-webkit-backdrop-filter:blur(2px);
   border:1px solid rgba(255,255,255,.22);background:rgba(10,8,14,.5);color:#e9e4f0;backdrop-filter:blur(2px)}
 .crt-toggle.on{background:var(--lcd-ink);color:#0a0910;border-color:var(--lcd-ink);font-weight:700}
 /* source artwork — grab colors off the REAL cover at a size you can aim at */
 .artref img{width:100%;max-width:320px;aspect-ratio:1;object-fit:cover;border-radius:10px;display:block;
   margin:0 auto 8px;box-shadow:0 3px 12px #0009}
 .artref .hint{font-size:11.5px;color:var(--faint);text-align:center;line-height:1.4}
 .prev .lyric{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
   color:#fff;font-size:24px;font-weight:700;letter-spacing:.04em;text-align:center;padding:0 18px;
   text-shadow:0 1px 14px rgba(0,0,0,.35)}
 @keyframes fieldDrift{
   0%{transform:translate3d(calc(-8%*var(--amp)),calc(-7%*var(--amp)),0) scale(1.08) rotate(calc(-3.5deg*var(--amp)))}
   50%{transform:translate3d(calc(8%*var(--amp)),calc(7.5%*var(--amp)),0) scale(1.24) rotate(calc(3.5deg*var(--amp)))}
   100%{transform:translate3d(calc(-8%*var(--amp)),calc(-7%*var(--amp)),0) scale(1.08) rotate(calc(-3.5deg*var(--amp)))}}
 @keyframes fieldOrbit{
   0%{transform:translate3d(calc(-7%*var(--amp)),0,0) scale(1.12) rotate(0deg)}
   25%{transform:translate3d(0,calc(-6%*var(--amp)),0) scale(1.18) rotate(calc(1.5deg*var(--amp)))}
   50%{transform:translate3d(calc(7%*var(--amp)),0,0) scale(1.24) rotate(0deg)}
   75%{transform:translate3d(0,calc(6%*var(--amp)),0) scale(1.18) rotate(calc(-1.5deg*var(--amp)))}
   100%{transform:translate3d(calc(-7%*var(--amp)),0,0) scale(1.12) rotate(0deg)}}
 @keyframes fieldSway{
   0%{transform:translate3d(calc(-10%*var(--amp)),calc(-2%*var(--amp)),0) scale(1.14) rotate(calc(-2deg*var(--amp)))}
   50%{transform:translate3d(calc(10%*var(--amp)),calc(2%*var(--amp)),0) scale(1.2) rotate(calc(2deg*var(--amp)))}
   100%{transform:translate3d(calc(-10%*var(--amp)),calc(-2%*var(--amp)),0) scale(1.14) rotate(calc(-2deg*var(--amp)))}}
 @keyframes fieldPulse{
   0%{transform:translate3d(0,0,0) scale(1.08)}50%{transform:translate3d(0,0,0) scale(1.3)}100%{transform:translate3d(0,0,0) scale(1.08)}}
 @keyframes fieldBreath{0%{opacity:.5}50%{opacity:.74}100%{opacity:.5}}
 @keyframes fbDriftA{0%{transform:translate(calc(-14%*var(--amp)),calc(-18%*var(--amp)));opacity:.5}50%{transform:translate(calc(26%*var(--amp)),calc(34%*var(--amp)));opacity:.78}100%{transform:translate(calc(-14%*var(--amp)),calc(-18%*var(--amp)));opacity:.5}}
 @keyframes fbDriftB{0%{transform:translate(calc(18%*var(--amp)),calc(16%*var(--amp)));opacity:.74}50%{transform:translate(calc(-30%*var(--amp)),calc(-26%*var(--amp)));opacity:.48}100%{transform:translate(calc(18%*var(--amp)),calc(16%*var(--amp)));opacity:.74}}
 @keyframes fbDriftC{0%{transform:translate(calc(-12%*var(--amp)),calc(20%*var(--amp)));opacity:.52}50%{transform:translate(calc(24%*var(--amp)),calc(-30%*var(--amp)));opacity:.8}100%{transform:translate(calc(-12%*var(--amp)),calc(20%*var(--amp)));opacity:.52}}
 .glab-ctl > .pane:first-child{margin-top:0}
 section.pane{background:var(--panel);border:1px solid var(--rule);border-radius:14px;padding:13px 15px;margin:14px 0}
 .pane h2{font-family:var(--f-mono);font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;
   color:var(--faint);margin:0 0 9px;font-weight:400}
 .crow{display:flex;gap:9px;align-items:center;padding:8px 0;border-top:1px solid var(--rule)}
 .crow:first-of-type{border-top:0}
 .crow .sw{width:34px;height:28px;border-radius:6px;border:1px solid #0006;flex:none}
 .crow .clab{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}
 .crow .clab .cn{font-size:13px;color:var(--ink);line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .crow .clab .cc{font-family:var(--f-mono);font-size:10px;color:var(--faint);letter-spacing:.01em}
 .crow .src{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.05em;color:var(--faint);flex:none;
   border:1px solid var(--rule);border-radius:10px;padding:1px 7px}
 .crow .src.override{color:var(--cli);border-color:#4a3a66}
 .crow input[type=color]{width:32px;height:26px;border:0;background:none;padding:0;cursor:pointer;flex:none}
 .crow .eyed{font-family:var(--f-mono);font-size:10px;padding:5px 9px;border-radius:6px;flex:none;
   border:1px solid var(--rule);background:#171420;color:var(--dim);cursor:pointer}
 .crow .eyed:hover{color:var(--ink)}
 .crow .pale{font-family:var(--f-mono);font-size:9px;color:var(--warn);flex:none}
 .dial{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--rule);flex-wrap:wrap}
 .dial:first-of-type{border-top:0}
 .dial .nm{font-family:var(--f-mono);font-size:12px;width:48px;color:var(--dim)}
 .dial input[type=range]{flex:1;min-width:120px;accent-color:var(--lcd-ink)}
 .dial .val{font-family:var(--f-mono);font-size:12px;width:52px;text-align:right;color:var(--lcd-ink)}
 .mbtn{font-family:var(--f-mono);font-size:11px;letter-spacing:.05em;padding:6px 10px;border-radius:7px;
   border:1px solid var(--rule);background:#171420;color:var(--dim);cursor:pointer}
 .mbtn.on{color:var(--bg);background:var(--lcd-ink);border-color:var(--lcd-ink);font-weight:700}
 .accrow{display:flex;gap:10px;align-items:center}
 .accrow .sw{width:48px;height:28px;border-radius:6px;border:1px solid #0006}
 .accrow .hex{font-family:var(--f-mono);font-size:13px;color:var(--ink)}
 .accrow .note{font-size:11.5px;color:var(--faint)}
 /* push targets */
 .push{border-top:1px solid var(--rule);padding-top:12px;margin-top:4px}
 .push:first-child{border-top:0;padding-top:0;margin-top:0}
 .push .ph{display:flex;align-items:baseline;gap:8px;margin:0 0 7px;flex-wrap:wrap}
 .push .ph .pt{font-family:var(--f-mono);font-size:12px;letter-spacing:.04em;color:var(--ink)}
 .push .ph .pd{font-size:11px;color:var(--faint)}
 .pcmd{position:relative;background:#0a0910;border:1px solid #241f2c;border-radius:8px;
   padding:10px 66px 10px 11px;font-family:var(--f-mono);font-size:11.5px;color:#d9c6ff;
   white-space:pre-wrap;word-break:break-word;line-height:1.5;min-height:38px}
 .pcmd.dim{color:var(--faint)}
 .pcmd .cp{position:absolute;top:6px;right:6px;background:#1c1926;border:1px solid var(--rule);
   color:var(--dim);border-radius:6px;font-size:10px;font-family:var(--f-mono);padding:4px 9px;cursor:pointer}
 .pcmd .cp:hover{color:var(--ink)}
 .palewarn{font-family:var(--f-mono);font-size:11px;color:var(--warn);margin-top:9px;white-space:pre-wrap}
 /* ── new-song walkthrough ──────────────────────────────────────────── */
 .form{display:grid;grid-template-columns:1fr 1fr;gap:10px}
 .form .fld{display:flex;flex-direction:column;gap:4px}
 .form .fld.wide{grid-column:1/-1}
 .form label{font-family:var(--f-mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
 .form input{background:#0a0910;border:1px solid #241f2c;border-radius:7px;padding:9px 11px;
   font-family:var(--f-mono);font-size:12.5px;color:var(--ink);outline:0}
 .form input:focus{border-color:var(--faint)}
 .go{font-family:var(--f-mono);font-size:12px;letter-spacing:.06em;padding:9px 16px;border-radius:8px;
   border:1px solid var(--local);background:#12241d;color:var(--local);cursor:pointer;font-weight:700}
 .go:hover{background:#16382b}
 /* site nav (backlog/lexicon) */
 .sitebar{display:flex;gap:10px;flex-wrap:wrap;margin:24px 0 0}
 .sitebar a,.sitebar button{font-family:var(--f-mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
   padding:9px 15px;border-radius:9px;border:1px solid var(--rule);background:var(--panel);color:var(--dim);
   cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:7px}
 .sitebar a:hover,.sitebar button:hover{color:var(--ink);border-color:var(--faint)}
 details.lex{background:var(--panel);border:1px solid var(--rule);border-radius:14px;margin:14px 0 20px;padding:4px 18px 16px}
 details.lex summary{cursor:pointer;padding:14px 0 4px;font-family:var(--f-disp);font-size:19px;list-style:none}
 details.lex summary::-webkit-details-marker{display:none}
 details.lex summary::before{content:'▸ ';color:var(--faint);font-family:var(--f-mono);font-size:13px}
 details.lex[open] summary::before{content:'▾ '}
 .lex .dek{margin:0 0 12px}
 .lexrow{display:grid;grid-template-columns:minmax(64px,.7fr) minmax(64px,.7fr) 2.6fr 1fr;
   gap:10px;padding:8px 2px;border-top:1px solid var(--rule);font-size:13px;color:#c3bccb}
 .lexrow.lexhead{border-top:0;font-family:var(--f-mono);font-size:10px;letter-spacing:.14em;
   text-transform:uppercase;color:var(--faint)}
 .lexrow .lw{font-family:var(--f-disp);color:var(--lcd-ink)}
 .lexrow .la{font-family:var(--f-mono);font-size:11px;color:var(--faint)}
 .lex-add{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px solid var(--rule)}
 .lex-add input{flex:1;min-width:110px;background:#0a0910;border:1px solid #241f2c;
   border-radius:7px;padding:8px 10px;font-family:var(--f-mono);font-size:12px;color:var(--ink)}
 .lex-add input::placeholder{color:var(--faint)}
 /* ── server mode (denmoku v2): run buttons, job bar, new-song search ── */
 .ctl:focus-visible,.go:focus-visible,.mbtn:focus-visible,.seg button:focus-visible,
 .navback:focus-visible,.refbtn:focus-visible,.cand:focus-visible{outline:2px solid var(--lcd-ink);outline-offset:2px}
 .ctl.run{color:var(--local);border-color:#2e4a3e}
 .ctl.run:hover{color:var(--local);border-color:var(--local);background:#12241d}
 .ctl.stop{color:var(--warn);border-color:#5a3a33}
 .ctl.stop:hover{color:var(--warn);border-color:var(--warn)}
 .ctl:disabled,.go:disabled{opacity:.45;cursor:default;pointer-events:none}
 .segrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 16px}
 /* five tabs stopped fitting a 390px phone when Writing joined — the row
    scrolls sideways instead of cropping the last tab off the screen. It still
    LOOKED cropped: the fifth tab ended mid-glyph against the panel edge with
    nothing to say it kept going. The mask fades the overflowing edge so it
    reads as more-to-the-right, and snapping stops a swipe mid-tab. */
 .segrow .segwrap{position:relative;flex:0 1 auto;min-width:0}
 /* The fade goes on whichever side is actually hiding a tab — a fixed
    right-hand fade dimmed the ACTIVE tab once the strip had scrolled to the
    end, the one thing on the row that must never look greyed out.
    Painted as an OVERLAY, not as mask-image on the wrapper: a mask puts the
    scroller in its own composited layer and Chromium then rasterises it at
    scroll offset 0 while the DOM still reports the real scrollLeft — the tab
    strip measured correct and rendered wrong. Touching mask-image at runtime
    also zeroed the scroll outright. An absolutely-positioned gradient does the
    same job with nothing clever underneath it. */
 .segrow .segwrap::before,.segrow .segwrap::after{content:'';position:absolute;
   top:1px;bottom:1px;width:26px;pointer-events:none;opacity:0;transition:opacity .15s}
 .segrow .segwrap::before{left:1px;border-radius:9px 0 0 9px;
   background:linear-gradient(90deg,#100e16 25%,rgba(16,14,22,0))}
 .segrow .segwrap::after{right:1px;border-radius:0 9px 9px 0;
   background:linear-gradient(270deg,#100e16 25%,rgba(16,14,22,0))}
 .segrow .segwrap.can-l::before{opacity:1}
 .segrow .segwrap.can-r::after{opacity:1}
 .segrow .seg{display:flex;margin:0;width:max-content;max-width:100%;
   overflow-x:auto;-webkit-overflow-scrolling:touch;
   scrollbar-width:none;scroll-snap-type:x proximity;scroll-padding:3px}
 .segrow .seg::-webkit-scrollbar{display:none}
 .segrow .seg button{flex:none;scroll-snap-align:start}
 /* run-all belongs beside the tabs, not orphaned on its own line under them */
 .segrow > .ctl{flex:none}
 .bkbadge{font-family:var(--f-mono);font-size:10px;background:var(--lcd);color:var(--lcd-ink);
   border:1px solid #23303a;border-radius:10px;padding:1px 7px;margin-left:2px}
 /* the fixed bottom job bar — visible whenever a job runs or waits */
 #jobbar{position:fixed;left:0;right:0;bottom:0;z-index:40;display:none;
   background:#14111c;border-top:1px solid var(--rule);box-shadow:0 -8px 28px #000b;
   padding:9px 18px calc(9px + env(safe-area-inset-bottom))}
 #jobbar.show{display:block}
 body.jb-open{padding-bottom:250px}
 #jobbar .jb-head{display:flex;gap:11px;align-items:center;font-family:var(--f-mono);font-size:12px;
   max-width:920px;margin:0 auto;flex-wrap:wrap}
 .jb-dot{width:10px;height:10px;border-radius:50%;flex:none;background:var(--pending)}
 .jb-dot.running{background:var(--running);animation:pulse 1s infinite}
 .jb-dot.done{background:var(--done)}
 .jb-dot.error{background:var(--blocked)}
 .jb-dot.stopped{background:var(--external)}
 .jb-meta{color:var(--ink);letter-spacing:.03em}
 .jb-ela,.jb-q{color:var(--faint)}
 .jb-log{max-width:920px;margin:8px auto 0;background:#0a0910;border:1px solid #241f2c;border-radius:8px;
   padding:9px 11px;font-family:var(--f-mono);font-size:11px;line-height:1.45;color:#b8e6cf;
   max-height:192px;overflow:auto;white-space:pre-wrap;word-break:break-word}
 #jobbar.flash .jb-head{animation:jbflash .5s ease-in-out 2}
 @keyframes jbflash{50%{opacity:.3}}
 /* new-song candidate cards (server search flow) */
 .cand{display:flex;gap:12px;align-items:center;width:100%;text-align:left;font:inherit;color:inherit;
   padding:10px 12px;margin:9px 0;cursor:pointer;background:var(--panel2);
   border:1px solid var(--rule);border-radius:12px;transition:border-color .12s}
 .cand:hover{border-color:var(--faint)}
 .cand.on{border-color:var(--local)}
 .cand img{width:72px;height:72px;border-radius:8px;object-fit:cover;flex:none;box-shadow:0 2px 8px #0008}
 .cand .noart{width:72px;height:72px;border-radius:8px;background:#0a0910;border:1px solid #241f2c;flex:none}
 .cand .ci{flex:1;min-width:0}
 .cand .ct{font-family:var(--f-disp);font-size:15px;color:var(--ink);line-height:1.2;
   overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .cand .ca{font-size:12px;color:var(--dim);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .cand .cm{font-family:var(--f-mono);font-size:10.5px;color:var(--faint);margin-top:4px;
   display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 .srcpill{border:1px solid var(--rule);border-radius:10px;padding:1px 7px;letter-spacing:.05em}
 .srcpill.yes{color:var(--local);border-color:#2e4a3e}
 .srcpill.no{color:var(--faint)}
 .srcpill.unk{color:var(--dim);border-style:dashed}
 .ns-out{margin-top:10px;font-family:var(--f-mono);font-size:11px;color:var(--warn);white-space:pre-wrap}
 /* deep source-probe cards (add-song confirm: per-source timing quality) */
 .probepick{font-family:var(--f-mono);font-size:10.5px;color:var(--dim);margin:0 0 9px;letter-spacing:.02em}
 .probepick b{color:var(--local);font-weight:400}
 .probes{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:9px;margin:0 0 14px}
 .probe{background:var(--panel2);border:1px solid var(--rule);border-radius:10px;padding:9px 10px;min-width:0}
 .probe .ph{display:flex;gap:7px;align-items:center;justify-content:space-between;flex-wrap:wrap}
 .probe .pn{font-family:var(--f-mono);font-size:11px;letter-spacing:.06em;color:var(--ink)}
 .pbadge{font-family:var(--f-mono);font-size:9px;letter-spacing:.08em;border:1px solid var(--rule);
   border-radius:10px;padding:2px 7px;color:var(--faint);white-space:nowrap}
 .pbadge.word{color:var(--local);border-color:#2e4a3e}
 .pbadge.line{color:var(--external);border-color:#5a4a2e}
 .pbadge.text{color:var(--dim)}
 .pbadge.unk{color:var(--dim);border-style:dashed}
 .probe .pc{font-family:var(--f-mono);font-size:10px;color:var(--faint);margin-top:5px;line-height:1.4;
   overflow:hidden;text-overflow:ellipsis}
 .probe .plines{margin-top:6px;max-height:96px;overflow:auto;border-top:1px solid var(--rule);padding-top:5px;
   font-family:var(--f-disp);font-size:12px;line-height:1.55;color:var(--dim)}
 .probe .plines div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 /* youtube preview embed (confirm pane, server mode) */
 .ytembed{position:relative;width:100%;max-width:560px;aspect-ratio:16/9;border-radius:10px;
   overflow:hidden;border:1px solid var(--rule);background:#000}
 .ytembed iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
 .ytembed>div{position:absolute;inset:0}   /* the IFrame API replaces this div */
 /* refetch-lyrics two-tap confirm (Build view, lyrics step) */
 .refetch-warn{font-family:var(--f-mono);font-size:11px;color:var(--warn);background:#1c1216;
   border:1px solid #5a3a33;border-radius:8px;padding:9px 11px;margin-top:10px;line-height:1.5}
 .refetch-warn .row{margin-top:8px}
 /* bring-your-own sheet: the paste box reuses the confirm shell, so an
    import reads as the same kind of act as a refetch. Monospace because
    what goes in it is a timed sheet and the timestamps should line up. */
 .refetch-warn textarea{display:block;width:100%;box-sizing:border-box;margin-top:8px;
   font-family:var(--f-mono);font-size:11px;line-height:1.5;color:var(--ink);
   background:#120e11;border:1px solid #5a3a33;border-radius:6px;padding:7px 8px;resize:vertical}
 .refetch-warn textarea:focus{outline:none;border-color:var(--warn)}
 /* youtube auto-match (add-song confirm, server mode) */
 .ytbox{display:flex;flex-direction:column;gap:8px}
 .ytbox .cand{margin:0}
 .ytbox .cand img,.ytbox .cand .noart{width:86px;height:48px;border-radius:6px}
 .ytspin,.ytmiss{font-family:var(--f-mono);font-size:11.5px;color:var(--dim);padding:6px 2px}
 .ytspin::before{content:'◌ ';color:var(--lcd-ink);animation:pulse 1s infinite}
 .ytmiss{color:var(--warn)}
 .ytdelta{border:1px solid #2e4a3e;color:var(--local);border-radius:10px;padding:1px 7px}
 .ytdelta.warn{border-color:#5a3a33;color:var(--warn)}
 .ytbox #nsc-ytalt{align-self:flex-start}
 .ytmanual input{width:100%}
 /* a video the player refused — it can still be clicked, it just can't win */
 .ytbox .cand.dead{opacity:.55}
 .ytbox .cand.dead .ct{text-decoration:line-through}
 .ytwarn{font-family:var(--f-mono);font-size:11.5px;line-height:1.55;color:var(--warn);
   background:#1c1216;border:1px solid #5a3a33;border-radius:8px;padding:9px 11px}
 .ytnote{font-family:var(--f-mono);font-size:11.5px;line-height:1.55;color:var(--dim);padding:2px}
 /* ── where the song starts (add-song step 2) ───────────────────────────
    A waveform you can point at. The auto measurement is only ever a
    suggestion here; the marker is the answer, and it is draggable.      */
 .stwrap{margin:2px 0 0}
 .stwave{position:relative;height:92px;border:1px solid var(--rule);border-radius:8px;
   background:var(--lcd);overflow:hidden;touch-action:none;cursor:ew-resize;user-select:none}
 .stwave canvas{display:block;width:100%;height:100%}
 .stwave .dead{position:absolute;inset:0 auto 0 0;background:rgba(0,0,0,.42);
   border-right:0;pointer-events:none}
 .stwave .mk{position:absolute;top:0;bottom:0;width:2px;margin-left:-1px;
   background:var(--lcd-ink);box-shadow:0 0 0 1px rgba(0,0,0,.45);pointer-events:none}
 .stwave .mk::before{content:'';position:absolute;top:0;left:-6px;border:7px solid transparent;
   border-top-color:var(--lcd-ink);border-bottom:0}
 .stwave .auto{position:absolute;top:0;bottom:0;width:1px;margin-left:-.5px;
   background:var(--faint);pointer-events:none}
 .stwave .grab{position:absolute;top:0;bottom:0;width:26px;margin-left:-13px;cursor:ew-resize}
 .stbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:9px;
   font-family:var(--f-mono);font-size:11.5px;color:var(--dim)}
 .stbar b{color:var(--lcd-ink);font-weight:400;font-variant-numeric:tabular-nums}
 .stbar .tag{border:1px solid #2e4a3e;color:var(--local);border-radius:10px;padding:1px 7px}
 .stbar .tag.set{border-color:var(--lcd-ink);color:var(--lcd-ink)}
 .stnote{font-family:var(--f-mono);font-size:11px;color:var(--faint);margin-top:7px;line-height:1.5}
 .stmsg{font-family:var(--f-mono);font-size:11.5px;color:var(--dim);padding:8px 2px}
 .stmsg.warn{color:var(--warn)}
 .stmsg.work::before{content:'◌ ';color:var(--lcd-ink);animation:pulse 1s infinite}
 .stout{font-family:var(--f-mono);font-size:11.5px;color:var(--dim);margin-top:9px}
 .sgstart{margin-top:22px}
 /* ── cover + its colors (add-song step 2) ──────────────────────────────
    The cover is ALWAYS armed: one click sets the background. No arming a
    field first — that two-step is the thing this replaces.              */
 .cvwrap{display:grid;grid-template-columns:minmax(160px,240px) 1fr;gap:16px;align-items:start}
 .cvart{position:relative;border-radius:10px;overflow:hidden;border:1px solid var(--rule);
   cursor:crosshair;line-height:0;touch-action:none}
 .cvart img{width:100%;display:block;-webkit-user-drag:none;user-select:none}
 .cvart canvas{display:none}
 .cvloupe{position:absolute;width:46px;height:46px;margin:-56px 0 0 -23px;border-radius:50%;
   border:2px solid #fff;box-shadow:0 2px 10px rgba(0,0,0,.6);pointer-events:none;display:none}
 .cvart.live .cvloupe{display:block}
 .cvprev{height:168px;border-radius:10px}      /* the real .prev field, shorter */
 .cvsw{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
 .cvsw button{display:flex;align-items:center;gap:6px;font:inherit;font-family:var(--f-mono);
   font-size:10.5px;color:var(--dim);background:transparent;border:1px solid var(--rule);
   border-radius:20px;padding:3px 9px 3px 4px;cursor:pointer}
 .cvsw button.on{border-color:var(--lcd-ink);color:var(--lcd-ink)}
 .cvsw button i{width:16px;height:16px;border-radius:50%;border:1px solid rgba(0,0,0,.4)}
 .cvcard{display:flex;align-items:center;gap:9px;margin-top:10px;font-family:var(--f-mono);
   font-size:11px;color:var(--dim)}
 .cvcard i{width:26px;height:16px;border-radius:3px;border:1px solid rgba(0,0,0,.4)}
 @media(max-width:640px){.cvwrap{grid-template-columns:1fr}}
 /* remove a song — quiet, last, and never the first thing you can hit */
 .rmbox{margin:34px 0 8px;border-top:1px solid var(--rule);padding-top:12px}
 .rmbox summary{font-family:var(--f-mono);font-size:11px;letter-spacing:.05em;
   color:var(--faint);cursor:pointer;list-style:none}
 .rmbox summary::-webkit-details-marker{display:none}
 .rmbox summary::before{content:'▸ ';color:var(--faint)}
 .rmbox[open] summary::before{content:'▾ '}
 .rmbox summary:hover{color:var(--dim)}
 .rmbody{max-width:640px;margin-top:10px}
 .rmbody p{font-size:12.5px;color:var(--dim);line-height:1.6;margin:0 0 12px}
 .rmbody p b{color:var(--lcd-ink);font-weight:400}
 .rmrow{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
 .rmrow input{flex:1;min-width:200px}
 .ctl.rmgo{border-color:#5a3a33;color:var(--warn)}
 .ctl.rmgo:disabled{border-color:var(--rule);color:var(--faint)}
 .rmout{font-family:var(--f-mono);font-size:11.5px;color:var(--warn);
   white-space:pre-wrap;margin-top:9px}
 /* name and links, after the song exists (same quiet chrome as remove) */
 .idbox{margin-top:26px}
 .idbox .rmbody{max-width:720px}
 .idbox .rmout{color:var(--dim)}
 .idwarn{color:var(--warn)!important}
 /* timing tab (server mode) */
 .tm-head{position:sticky;top:0;z-index:6;display:flex;gap:12px;align-items:center;flex-wrap:wrap;
   background:var(--bg);padding:10px 2px;border-bottom:1px solid var(--rule)}
 .tm-med{font-family:var(--f-mono);font-size:12px;color:var(--dim)}
 .tm-med b{color:var(--lcd-ink);font-weight:400}
 .tm-cols,.tm-row{display:grid;grid-template-columns:56px minmax(0,1fr) 44px 42px minmax(88px,auto);
   gap:10px;align-items:center;padding:8px 6px}
 .tm-cols{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
   color:var(--faint);padding-top:14px}
 .tm-line{border-top:1px solid var(--rule)}
 .tm-row{cursor:pointer}
 .tm-row:hover{background:#1b1822}
 .tm-line.flag .tm-row{background:linear-gradient(90deg,rgba(240,128,106,.10),transparent);
   box-shadow:inset 2px 0 0 var(--blocked)}
 .tm-t{font-family:var(--f-mono);font-size:11.5px;color:var(--lcd-ink)}
 .tm-txt{font-size:13px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .tm-n{font-family:var(--f-mono);font-size:11px;color:var(--dim);text-align:right}
 .tm-n.hot{color:var(--blocked)}
 .tm-chips{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}
 .tm-chip{font-family:var(--f-mono);font-size:9px;letter-spacing:.06em;text-transform:uppercase;
   border:1px solid #5a3a33;color:var(--blocked);border-radius:10px;padding:1px 6px}
 .tm-chip.res{border-color:var(--rule);color:var(--dim);text-transform:none}
 .tm-x{display:none;padding:4px 6px 14px}
 .tm-line.open .tm-x{display:block}
 .tm-line.open .tm-row{background:#1b1822}
 .tm-foot{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px}
 /* waveform lane (drag-a-word editor) */
 .tm-wave{margin:6px 0 2px}
 .tm-wave canvas{display:block;width:100%;height:112px;background:var(--lcd);
   border:1px solid #23303a;border-radius:8px;touch-action:none;cursor:crosshair;
   -webkit-user-select:none;user-select:none}
 .tm-wvro{font-family:var(--f-mono);font-size:11px;color:var(--lcd-ink);min-width:96px}
 .tm-wvnote{font-family:var(--f-mono);font-size:11px;color:var(--faint);
   background:var(--lcd);border:1px dashed #23303a;border-radius:8px;padding:10px 12px;margin:6px 0 10px}
 .tm-chip.anch{border-color:#8a6d1e;color:var(--external);text-transform:none}
 /* hand-set marker: a small amber dot (the ⚓ emoji ate too much space) */
 .tm-pindot{display:inline-block;width:6px;height:6px;border-radius:50%;flex:none;
   background:#e8b04a;box-shadow:0 0 4px #e8b04a66}
 .tm-sec{display:inline-block;font-family:var(--f-mono);font-size:9px;letter-spacing:.05em;
   color:var(--dim);border:1px solid var(--rule);border-radius:4px;padding:0 4px;margin-right:7px;vertical-align:1px}
 .tm-state{display:flex;gap:7px;align-items:center;flex-wrap:wrap;padding:9px 2px 3px}
 .tm-schip{font-family:var(--f-mono);font-size:10px;letter-spacing:.02em;color:var(--faint);
   border:1px solid var(--rule);border-radius:11px;padding:2px 9px;white-space:nowrap}
 .tm-schip.done{color:var(--done);border-color:#2f5a45}
 .tm-schip.notyet{opacity:.45;border-style:dashed}
 .tm-schip.now{color:var(--lcd-ink);border-color:#2f6b52;background:rgba(125,214,168,.06)}
 .tm-sp{flex:1}
 .tm-prevlink{font-family:var(--f-mono);font-size:11px;color:var(--running);text-decoration:none;margin-right:4px}
 .tm-act{font-size:12px}
 .tm-alldone{font-family:var(--f-mono);font-size:11px;color:var(--done)}
 .tm-nopage{font-family:var(--f-mono);font-size:11px;color:var(--dim)}
 /* "these times aren't lined up with the video yet" */
 .tm-warn{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 10px;
   padding:9px 12px;border:1px solid var(--rule);border-left:3px solid var(--cli);
   border-radius:10px;background:var(--panel);font-size:12.5px;color:var(--dim)}
 .tm-warn.bad{border-left-color:var(--blocked)}
 .tm-warn span{flex:1;min-width:220px}
 /* the Timing tab before there is anything to time */
 .tmprep{border:1px solid var(--rule);border-radius:12px;background:var(--panel);
   padding:20px 22px;margin:10px 0 4px;max-width:560px}
 .tmprep h3{margin:0 0 12px;font-size:15px;font-weight:600}
 .tmprep-s{font-family:var(--f-mono);font-size:12px;color:var(--faint);padding:3px 0}
 .tmprep-s.on{color:var(--ink)}
 .tmprep-s.done{color:var(--done)}
 .tmprep p{margin:14px 0 0;font-size:12.5px;line-height:1.55;color:var(--dim)}
 .tmprep button{margin-top:14px}
 .tm-sharpen{font-family:var(--f-mono);font-size:10.5px}
 .tm-ovwrap{margin:8px 0 4px}
 .tm-ovwrap canvas{display:block;width:100%;height:52px;background:var(--lcd);
   border:1px solid #23303a;border-radius:8px;touch-action:none;cursor:pointer}
 .tm-zoom{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:8px 0 4px}
 .tm-zb{font-family:var(--f-mono);font-size:12px;min-width:30px;height:26px;padding:0 8px;
   background:var(--panel2);color:var(--ink);border:1px solid var(--rule);border-radius:6px;cursor:pointer}
 .tm-zb.wide{font-size:10.5px}
 .tm-zb:hover{border-color:var(--lcd-ink)}
 .tm-wvhelp{margin:2px 0 8px !important;font-size:10.5px}
 .tm-zb.tm-i{margin-left:auto;min-width:30px;font-size:14px;line-height:1}
 .tm-zb.tm-i.on{border-color:var(--lcd-ink);color:var(--lcd-ink)}
 .tm-help{background:var(--panel);border:1px solid var(--rule);border-radius:9px;padding:8px 10px;margin:4px 0 8px}
 .tm-hrow{display:grid;grid-template-columns:120px 1fr;gap:10px;padding:3px 0;font-size:12px;color:var(--dim);
   border-top:1px solid var(--rule)}
 .tm-hrow:first-child{border-top:0}
 .tm-hk{font-family:var(--f-mono);color:var(--lcd-ink);font-size:11px}
 .tm-focus{background:var(--panel);border:1px solid var(--rule);border-radius:10px;padding:10px 12px;margin:2px 0 10px}
 .tm-focus-h{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
 .tm-focus-jp{font-size:18px;color:var(--ink);font-weight:600}
 .tm-focus-win{font-family:var(--f-mono);font-size:11px;color:var(--lcd-ink)}
 .tm-study{border-top:1px solid var(--rule);padding:8px 0 2px}
 .tm-study:first-of-type{border-top:0}
 .tm-study.empty{color:var(--faint);font-size:12.5px;border-top:0}
 .tm-study-h b{font-size:15px;color:var(--ink);font-weight:600}
 .tm-rom{font-family:var(--f-mono);font-size:12px;color:var(--lcd-ink)}
 .tm-part{font-family:var(--f-mono);font-size:9px;color:var(--cli);border:1px solid #4a3d63;border-radius:4px;padding:0 4px}
 .tm-mean{font-size:13.5px;color:var(--dim);margin:2px 0 6px}
 .tm-study-b{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 .tm-take{font-family:var(--f-mono);font-size:10px;color:var(--dim);border:1px solid var(--rule);border-radius:9px;padding:1px 7px}
 .tm-take.good{color:var(--done);border-color:#2f5a45}
 .tm-take.alt{color:var(--external);border-color:#8a6d1e}
 .tm-take.none{color:var(--faint)}
 .tm-fplay,.tm-fix{font-size:11px !important}
 /* word tools: held part + edit / add / delete (the options that replaced the
    old word-chip strip under the wave) */
 .tm-wtools{display:flex;gap:7px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--rule);
   margin-top:9px;padding-top:9px}
 .tm-wtools .ctl{font-size:11px}
 .tm-wtools .ctl.arm{border-color:#e8b04a;color:#e8b04a}
 .tm-holdlab{font-family:var(--f-mono);font-size:11px;color:#e8b04a}
 .tm-wform{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;background:var(--lcd);
   border:1px solid #23303a;border-radius:9px;padding:9px 11px;margin-top:8px}
 .tm-wform label{display:flex;flex-direction:column;gap:3px;font-family:var(--f-mono);
   font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
 .tm-wform input,.tm-wform select{background:var(--panel2);color:var(--ink);border:1px solid var(--rule);
   border-radius:6px;padding:5px 8px;font-size:13px;font-family:var(--f-disp);min-width:90px}
 .tm-wform input:focus{outline:none;border-color:var(--local)}
 .tm-wform .wide{min-width:200px}
 /* inline karaoke reveal — just the open line, right above the timeline; its
    round ▶/⏸ plays the line, and the words light up in real time off the SAME
    playhead clock as ▶ this word, always matching the current (edited) timings. */
 .tm-reveal{display:flex;gap:11px;align-items:center;background:var(--lcd);border:1px solid #23303a;
   border-radius:10px;padding:11px 12px 9px;margin:2px 0 8px}
 .tm-rvplay{flex:none;width:40px;height:40px;border-radius:50%;cursor:pointer;font-size:15px;
   background:var(--panel2);color:var(--lcd-ink);border:1px solid #2e4a3e;line-height:1;
   display:flex;align-items:center;justify-content:center;padding:0 0 0 2px}
 .tm-rvplay:hover{border-color:var(--local)}
 .tm-reveal.playing .tm-rvplay{color:#0f1b16;background:var(--local);border-color:var(--local);padding:0}
 .tm-rv-main{flex:1;min-width:0}
 .tm-rv-line{position:relative;display:flex;gap:9px;align-items:flex-end;overflow-x:auto;overflow-y:hidden;
   padding-bottom:3px;-webkit-overflow-scrolling:touch;scrollbar-width:thin}
 .tm-rvw{flex:none;display:inline-flex;flex-direction:column;align-items:center;gap:2px;
   cursor:pointer;padding:1px 2px;border-radius:6px;scroll-margin:24px}
 .tm-rvw-top{display:flex;align-items:baseline;gap:2px}
 .tm-rvk{font-family:var(--f-disp);font-size:23px;line-height:1.15;white-space:nowrap;
   --lit:var(--ink);--unlit:var(--ink);--p:100%;
   background-image:linear-gradient(90deg,var(--lit) 0,var(--lit) var(--p),var(--unlit) var(--p),var(--unlit) 100%);
   -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent}
 .tm-reveal.playing .tm-rvk{--lit:var(--local);--unlit:#453f52;--p:0%}
 /* the held sung vowel (hold_ms): a wave mark that dances while it's sung */
 .tm-rvh{font-size:15px;color:var(--faint);opacity:.5;transform-origin:50% 80%}
 .tm-rvw.holding .tm-rvh{color:var(--local);opacity:1;animation:tmHoldDance .5s ease-in-out infinite alternate}
 .tm-rvw.past .tm-rvh{color:var(--local);opacity:.8}
 @keyframes tmHoldDance{from{transform:scaleY(.8) translateY(0)}to{transform:scaleY(1.25) translateY(-2px)}}
 .tm-rvr{font-family:var(--f-mono);font-size:10px;color:var(--faint);white-space:nowrap;letter-spacing:.01em}
 .tm-rvr b{font-weight:700;color:var(--lcd-ink)}
 .tm-reveal.playing .tm-rvw.past .tm-rvr,.tm-reveal.playing .tm-rvw.now .tm-rvr{color:var(--local)}
 .tm-reveal.playing .tm-rvw.past .tm-rvr b,.tm-reveal.playing .tm-rvw.now .tm-rvr b{color:var(--local)}
 .tm-rvw .tm-pindot{width:5px;height:5px;align-self:center}
 .tm-reveal:not(.playing) .tm-rvw.foc{background:rgba(255,210,63,.10);box-shadow:inset 0 0 0 1px #c79a2a}
 .tm-reveal:not(.playing) .tm-rvw.foc .tm-rvk{--lit:#ffd23f;--unlit:#ffd23f}
 .tm-rv-kana{font-family:var(--f-disp);font-size:12.5px;color:var(--dim);margin-top:7px;
   line-height:1.45;overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch;scrollbar-width:thin}
 .tm-rv-kana .lb{font-family:var(--f-mono);font-size:8.5px;letter-spacing:.13em;text-transform:uppercase;
   color:var(--faint);margin-right:7px}
 .tm-rv-empty{font-family:var(--f-mono);font-size:11px;color:var(--faint)}
 .tm-modal{position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.62);display:flex;align-items:center;justify-content:center;padding:24px}
 .tm-modal-card{background:var(--panel);border:1px solid var(--rule);border-radius:14px;padding:20px 22px;max-width:420px}
 .tm-modal-card h3{margin:0 0 8px;font-size:16px;color:var(--ink)}
 .tm-modal-card p{margin:0 0 16px;font-size:13.5px;color:var(--dim);line-height:1.5}
 .tm-modal-btns{display:flex;gap:10px;justify-content:flex-end}
 /* pre-deploy preview link (Build view, once assemble is done) */
 a.ctl{text-decoration:none;display:inline-block}
 .prevrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 14px}
 /* the out-of-date notice, with the button that fixes it */
 .stalerow{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:0 0 16px;
   background:#1c1216;border:1px solid #5a3a33;border-radius:10px;padding:11px 13px}
 .stalerow .sm{flex:1 1 380px;font-family:var(--f-mono);font-size:11.5px;
   line-height:1.55;color:var(--warn)}
 /* mid-rebuild: the notice becomes the progress, so the press has somewhere to
    land. A greyed-out button on an unchanged red wall reads as a dead button. */
 .stalerow.working{background:#121820;border-color:#2c3f4e}
 .stalerow.working .sm{color:var(--running)}
 .spin{display:inline-block;width:11px;height:11px;margin-right:8px;vertical-align:-1px;
   border:2px solid #2c3f4e;border-top-color:var(--running);border-radius:50%;
   animation:spin .8s linear infinite}
 @keyframes spin{to{transform:rotate(360deg)}}
 @media(prefers-reduced-motion:reduce){.spin{animation-duration:2.4s}}
 /* words tab (server mode) */
 .wd-sec{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
   color:var(--faint);padding:16px 2px 8px}
 .wd-grid{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start}
 .wd-w{display:inline-flex;align-items:center;gap:7px;padding:7px 11px;cursor:pointer;font:inherit;
   color:inherit;text-align:left;background:var(--panel2);border:1px solid var(--rule);border-radius:10px;
   transition:border-color .12s}
 .wd-w:hover{border-color:var(--faint)}
 .wd-w.on{border-color:var(--local)}
 .wd-w .sf{font-family:var(--f-disp);font-size:15px;color:var(--ink);line-height:1.15}
 .wd-w .kn{font-family:var(--f-mono);font-size:10px;color:var(--faint)}
 .wd-b{width:9px;height:9px;border-radius:50%;flex:none;border:1px solid transparent}
 .wd-b.cur{background:var(--local)}
 .wd-b.std{background:var(--faint)}
 .wd-b.alt{background:var(--running)}
 .wd-b.none{background:transparent;border-color:var(--faint)}
 .wd-pinmark{color:var(--lcd-ink);font-size:11px;flex:none}
 .wd-play{font-family:var(--f-mono);font-size:10px;color:var(--dim);border:1px solid var(--rule);
   border-radius:50%;width:22px;height:22px;display:inline-flex;align-items:center;
   justify-content:center;flex:none;cursor:pointer}
 .wd-play:hover{color:var(--ink);border-color:var(--faint)}
 .wd-play.off{cursor:default;color:var(--faint);border-style:dashed}
 .wd-play.off:hover{color:var(--faint);border-color:var(--rule)}
 .wd-x{flex-basis:100%;background:var(--panel);border:1px solid var(--rule);border-radius:12px;
   padding:12px 14px;margin:2px 0 6px}
 .wd-xh{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:0 0 10px}
 .wd-xh .sf{font-family:var(--f-disp);font-size:17px;color:var(--ink)}
 .wd-xh .kn{font-family:var(--f-mono);font-size:11px;color:var(--faint)}
 .wd-srct{font-family:var(--f-mono);font-size:10px;color:var(--dim);letter-spacing:.05em}
 .wd-c{display:flex;gap:10px;align-items:center;padding:8px 10px;margin:7px 0;cursor:pointer;
   background:var(--panel2);border:1px solid var(--rule);border-radius:10px;transition:border-color .12s}
 .wd-c:hover{border-color:var(--faint)}
 /* selected take: touch has no hover — the picked row must read at a glance */
 .wd-c.on{border-color:var(--local);background:#12241d;box-shadow:inset 0 0 0 1px var(--local)}
 .wd-c .cl{font-family:var(--f-mono);font-size:11.5px;color:var(--ink);line-height:1.4;
   flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .wd-ck{font-family:var(--f-mono);font-size:12px;color:var(--local);flex:none}
 .wd-ord{font-family:var(--f-mono);font-size:10px;color:var(--faint);letter-spacing:.04em;margin:0 0 2px}
 .wd-miss{font-family:var(--f-mono);font-size:11.5px;color:var(--dim);padding:4px 2px}
 .wd-pinlab{display:inline-flex;gap:7px;align-items:center;font-size:12px;color:var(--dim);cursor:pointer}
 .wd-pinlab input{accent-color:var(--lcd-ink)}
 /* the install row stays reachable while 7 takes scroll by (sticky in-drawer) */
 .wd-act{position:sticky;bottom:calc(8px + env(safe-area-inset-bottom));z-index:5;background:var(--panel);
   padding:8px 0 4px;box-shadow:0 -12px 14px -12px #000d}
 /* suspect surfacing — "needs your ear" (amber = worth a listen, not broken) */
 .wd-susdot{width:7px;height:7px;border-radius:50%;flex:none;background:var(--external);box-shadow:0 0 5px #fbbf2466}
 .wd-strip{border:1px solid #4a3d1e;border-radius:12px;padding:10px 12px;margin:12px 0 2px;
   background:linear-gradient(120deg,rgba(251,191,36,.07),transparent 70%)}
 .wd-strip .wd-strip-h{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
   color:var(--external);margin:0 0 9px}
 .wd-why{display:flex;gap:7px;align-items:flex-start;font-family:var(--f-mono);font-size:11px;color:var(--external);margin:-4px 0 8px}
 .wd-why .wd-susdot{margin-top:4px}
 .wd-lexr{font-family:var(--f-mono);font-size:11px;color:var(--lcd-ink);margin:-2px 0 8px}
 /* the no-hand-off route out of the one step the box can't run itself */
 .wr-alt{font-family:var(--f-mono);font-size:11.5px;line-height:1.6;color:var(--lcd-ink);
   background:var(--lcd);border:1px solid #23303a;border-radius:9px;padding:9px 11px;margin:0 0 10px}
 .wr-alt a{color:var(--ink);text-underline-offset:3px}
 /* writing tab — the study text a person types instead of handing off */
 .wr-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 14px}
 .wr-count{font-family:var(--f-mono);font-size:11.5px;color:var(--dim);flex:1 1 240px}
 .wr-count b{color:var(--ink);font-weight:400}
 .wr-count.clean b{color:var(--done)}
 .wr-seg{display:inline-flex;border:1px solid var(--rule);border-radius:9px;overflow:hidden}
 .wr-seg button{font-family:var(--f-mono);font-size:11px;padding:7px 11px;background:var(--panel2);
   color:var(--dim);border:0;border-right:1px solid var(--rule);cursor:pointer}
 .wr-seg button:last-child{border-right:0}
 .wr-seg button.on{background:var(--panel);color:var(--ink)}
 .wr-card{background:var(--panel);border:1px solid var(--rule);border-radius:12px;
   padding:12px 14px;margin:0 0 10px}
 .wr-card.todo{border-color:#5a4a33}
 .wr-jp{font-family:var(--f-disp);font-size:17px;color:var(--lcd-ink);line-height:1.3}
 .wr-sub{font-family:var(--f-mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;
   color:var(--faint);margin:2px 0 10px}
 .wr-f{margin:0 0 9px}
 .wr-f label{display:block;font-family:var(--f-mono);font-size:10px;letter-spacing:.1em;
   text-transform:uppercase;color:var(--faint);margin:0 0 4px}
 .wr-f label .need{color:var(--external);letter-spacing:0;text-transform:none;margin-left:6px}
 .wr-f .say{color:var(--dim);letter-spacing:0;text-transform:none;margin-left:6px}
 .wr-f textarea,.wr-f input{width:100%;background:var(--panel2);border:1px solid var(--rule);
   border-radius:8px;color:var(--ink);font-family:var(--f-body);font-size:14px;line-height:1.5;
   padding:8px 10px;resize:vertical}
 .wr-f textarea:focus,.wr-f input:focus{outline:0;border-color:var(--local)}
 .wr-f.bad textarea,.wr-f.bad input{border-color:var(--warn)}
 .wr-f.saved textarea,.wr-f.saved input{border-color:var(--done)}
 .wr-err{font-family:var(--f-mono);font-size:11px;line-height:1.5;color:var(--warn);margin:5px 0 0}
 .wr-note{font-family:var(--f-mono);font-size:11px;line-height:1.5;color:var(--external);margin:5px 0 0}
 .wr-grp{font-family:var(--f-mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
   color:var(--faint);padding:16px 2px 8px}
 .wr-tip{font-family:var(--f-mono);font-size:11.5px;line-height:1.6;color:var(--dim);
   background:var(--panel2);border:1px solid var(--rule);border-radius:10px;padding:10px 12px;margin:0 0 14px}
 /* 390px phone pass — declared AFTER the base rules so the override wins */
 @media(max-width:480px){
   .form{grid-template-columns:1fr}
   .tm-cols,.tm-row{grid-template-columns:44px minmax(0,1fr) 38px 34px minmax(58px,auto);gap:6px;padding:8px 2px}
 }
 .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);
   background:#201c2a;border:1px solid var(--rule);color:var(--ink);
   font-family:var(--f-mono);font-size:12px;padding:9px 16px;border-radius:9px;opacity:0;
   transition:.2s;pointer-events:none;max-width:88vw;text-align:center}
 .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
 .empty{color:var(--faint);text-align:center;padding:40px 0;font-family:var(--f-mono);font-size:13px}
 footer{color:var(--faint);font-family:var(--f-mono);font-size:11px;text-align:center;margin-top:30px;line-height:1.7}
</style></head>
<body>
<header class="mast">
 <div class="wrap mastin">
   <button class="refbtn" id="refbtn" title="refresh the dashboard (re-reads build progress)">↻</button>
   <div class="brand">
     <div class="kicker">学オケ · denmoku</div>
     <h1>The <span class="jp">出面器</span> — song page builder</h1>
   </div>
   <!-- inside a song the faceplate becomes a title bar: back, what you are
        looking at, refresh. One row that stays put while the steps scroll
        under it, instead of 250px of branding you have to scroll past on
        every screen before you can see the song. -->
   <div class="navbar" id="navbar" hidden>
     <button class="navback" id="navback" aria-label="back to the library">‹</button>
     <div class="navt"><b id="navtitle"></b><span id="navsub"></span></div>
   </div>
 </div>
</header>
<div class="wrap">
 <div id="app"></div>
 <div id="libextra"></div>
 <footer></footer>
</div>
<div class="toast" id="toast"></div>
<div id="jobbar" aria-live="polite">
 <div class="jb-head">
   <span class="jb-dot" id="jbdot"></span>
   <span class="jb-meta" id="jbmeta"></span>
   <span class="jb-ela" id="jbela"></span>
   <span class="jb-q" id="jbq"></span>
   <span style="flex:1"></span>
   <button class="ctl stop" id="jbstop">■ stop</button>
   <button class="ctl" id="jbdismiss" hidden>× dismiss</button>
 </div>
 <pre class="jb-log" id="jblog"></pre>
</div>
<script>
const BUILDS = /*__STATE__*/;
const LEX = /*__LEX__*/;
const GL = Object.assign({songs:[],main:null,defaults:{},pale:{main:0.68,hi:0.82},fdur:{},motions:['drift','orbit','sway','pulse']}, /*__GL__*/);
const DAG = /*__DAG__*/;
const ANY_RUNNING = /*__RUNNING__*/;   // any step status=='running' at render time
// denmoku v2 (builder/server.py): true = live localhost app — poll /api/state,
// real Run / Mark-done buttons, the job bar, the New Song search flow.
// false = the classic self-contained file:// dashboard (embedded state only).
const SERVER_MODE = /*__SERVER__*/;
const JOB={cur:null,offset:0,active:false,ended:false,dismissed:true};  // job-bar state (server mode)
const CF = ['c1','c2','c3','hi','fb1','fb2','fb3'];
const PLACE = {c1:'#6a3a4a',c2:'#4a2535',c3:'#2a1420',hi:'#8a5a6a',fb1:'#7a4a5a',fb2:'#6a4050',fb3:'#553040'};
// plain-English names for the field colors (owner: "just call it what it is").
// The tiny code stays as a sub-label so a composed `gradient set --c1 ...`
// command is still traceable back to the row.
const GNAME = {c1:'Main color',c2:'Second color',c3:'Deep color',hi:'Highlight',
               fb1:'Glow — top left',fb2:'Glow — bottom right',fb3:'Glow — center'};
const SRCNAME = {cover:'from art',override:'edited',landing:'from landing',default:'site default','—':'unset'};
const byKey = k => BUILDS.find(b=>b.key===k);
const glByKey = k => (GL.songs||[]).find(s=>s.key===k);
function $(id){return document.getElementById(id)}
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),1700)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
// build-state copy predates the language rule (no tool names / pipeline
// jargon in user-facing text) — soften it at render time; the state files
// themselves belong to the CLI and stay untouched.
function friendly(s){return String(s==null?'':s)
  .replace(/\bLyriCool\b/g,'the lyric sources').replace(/\bDAG\b/g,'build sequence')}
// Owner pill labels (classes keep the raw owner ids — only the copy softens).
// "LOCAL / HAND-OFF / EXTERNAL" and "AUTO / MANUAL" told you which internal
// bucket a step is in, which is not a thing anyone needs. What a person
// standing at this screen wants to know is who has to do the next thing.
const OWN_LBL={local:'the box',cli:'claude',external:'a website'};
const AUTO_LBL=a=>a?'runs itself':'needs you';
function copy(txt){navigator.clipboard&&navigator.clipboard.writeText(txt).then(()=>toast('copied'),()=>toast('copy failed'))}

/* build-step scratch (persisted-to-command, same contract as before) */
const LS='manaoke-builder-scratch';
let scratch={}; try{scratch=JSON.parse(localStorage.getItem(LS)||'{}')}catch(e){}
function saveScratch(){localStorage.setItem(LS,JSON.stringify(scratch))}
function stepStatus(key,s){
  if(SERVER_MODE)return s.status;      // the server's build_state IS the truth — no scratch shadow
  const sc=(scratch[key]||{})[s.key];if(sc&&sc.done)return 'done';return s.status}
// the steps on the road to shipping — optional ones (the podcast) are real work
// you can run, but they are not what "finished" means, so they never count
function roadSteps(b){return (b.steps||[]).filter(s=>!s.optional)}
function roadDone(b){return roadSteps(b).filter(s=>stepStatus(b.key,s)==='done').length}
function roadPct(b){const n=roadSteps(b).length;return n?Math.round(100*roadDone(b)/n):0}

/* ══ router ═════════════════════════════════════════════════════════ */
function route(){
  const h=location.hash.replace(/^#/,'');
  const [view,key,tab]=h.split('/');
  // leaving the Timing tab: silence the audition audio + tear down the preview
  // iframe so its player/gradient loop don't burn battery in the background
  if(typeof tmLeaving==='function'&&!(view==='song'&&tab==='timing'))tmLeaving();
  if(view==='song'&&byKey(key)) return renderSong(key, TABS.some(t=>t.id===tab)?tab:'build');
  if(view==='main') return renderMainPage();
  if(view==='new') return renderNew();
  return renderLibrary();
}
function go(hash){location.hash=hash}       // back-button + browser-back both work
/* The title bar's contents. Every detail screen used to print its own
   "← library" chip as the first thing in #app, so on a phone you scrolled
   past a masthead AND a back chip before reaching the song. One bar now, up
   in the sticky header, filled by whoever is on screen. */
function setNav(title,sub){
  const nb=$('navbar'); if(!nb)return;
  nb.hidden=!title;
  if(title){$('navtitle').textContent=title; $('navsub').textContent=sub||''}
}

/* ══ library grid ═══════════════════════════════════════════════════ */
function tileSong(b,shared){
  const m=b.meta||{}, steps=b.steps||[];
  const pct=roadPct(b);
  const st=staleOf(b);
  // A dot only when this song's state differs from the one most of them share.
  // Lit on every tile it means nothing; the shared state is said once, in
  // staleNote(), so the only dot on screen is the song that stands apart.
  const dot=(st&&st.state!==shared)?'<div class="sd warn" title="'+esc(st.state)+'"></div>':'';
  // The bar only while a build is actually running. As a step counter it lied:
  // a live, promoted song sits at 89% forever because deploy/promote never get
  // marked done — six identical near-full bars saying nothing true.
  const running=steps.some(s=>stepStatus(b.key,s)==='running');
  const bar=running?'<div class="pbar"><i style="width:'+pct+'%"></i></div>':'';
  const art=m.art?'<img src="'+esc(m.art)+'" alt="" loading="lazy" onerror="this.style.display=\'none\'">':'';
  return '<div class="tile" data-go="song/'+esc(b.key)+'" data-tile="'+esc(b.key)+'">'+art+dot+
    '<div class="cap"><div class="jp">'+esc(m.title_jp||b.key)+'</div>'+
      '<div class="ar">'+esc(m.artist_en||m.artist||'')+'</div></div>'+
    bar+'</div>';
}
function tileMain(){
  const M=GL.main; if(!M) return '';
  // a live mini living-field so the tile previews the landing's own gradient
  const v=f=>trip((M[f]||PLACE[f]));
  const style='--c1:'+v('c1')+';--c2:'+v('c2')+';--c3:'+v('c3')+';--hi:'+v('hi')+
    ';--fb1:'+v('fb1')+';--fb2:'+v('fb2')+';--fb3:'+v('fb3')+
    ';--base1:'+darken(M.c1||'#2a1830',.165,1.3)+';--base2:'+darken(M.c1||'#2a1830',.094,1.3)+';--base3:'+darken(M.c1||'#2a1830',.047,1.3)+
    ';--amp:1;--fdur-drift:20s;--fdur-breath:11s;--fdur-a:15s;--fdur-b:21s;--fdur-c:13s';
  return '<div class="tile mainpg" data-go="main">'+
    '<div class="prev mini" style="height:100%;border:0;border-radius:0;'+style+'">'+
      '<div class="mesh"></div><div class="fb fb1"></div><div class="fb fb2"></div><div class="fb fb3"></div><div class="scrim"></div></div>'+
    '<div class="lbl2">Main page field</div></div>';
}
function tileNew(){return '<div class="tile special" data-go="new"><div class="big">＋</div><div class="lbl">New song</div></div>'}
// only a REAL problem earns a chip — 'warn' is set by _cheap_stale (a song that
// was never assembled has no manifest by definition, and that isn't news)
/* STALE starts as the copy baked into this page at render time and is then
   kept LIVE off /api/stale. It used to be read straight out of that frozen
   const, which made the rebuild button look broken: you pressed it, the job
   ran a full minute, it finished green — and the red "built from an older
   version" banner and the STALE chip were still sitting there, because the
   only thing that could clear them was a full ↻ reload. The box has to be
   able to un-say what it said. */
const STALE = Object.assign({}, LEX.stale||{});
function staleOf(b){const st=STALE[b.key];return (st&&st.warn)?st:null}
/* Ask the box to re-measure. Not on the 2s poll — it re-hashes the template
   tree and walks each song's audio (~0.6s for the library) — so it runs at the
   moments staleness can actually have moved: boot, and the end of a job. */
let _staleBusy=false;
async function refreshStale(repaint){
  if(!SERVER_MODE||_staleBusy)return; _staleBusy=true;
  try{
    const r=await fetch('/api/stale'); if(!r.ok)return;
    const j=await r.json(), next=j.stale||{};
    let moved=false;
    for(const k in next){
      const a=STALE[k]||{}, n=next[k];
      if(a.state!==n.state||!!a.warn!==!!n.warn)moved=true;
      STALE[k]=n;
    }
    // repaint whatever is on screen with the truth. `repaint` forces it after a
    // rebuild even when nothing moved — otherwise a rebuild that FAILED would
    // leave its "rebuilding…" row spinning with no job behind it.
    if(moved||repaint)route();
  }catch(e){}
  finally{_staleBusy=false}
}
// The state most of the library shares — said once above the grid instead of
// as an identical dot on every jacket. Needs a real majority to count.
function sharedStale(list){
  const c={}; list.forEach(b=>{const s=staleOf(b); if(s)c[s.state]=(c[s.state]||0)+1});
  let best=null,n=0; for(const k in c){if(c[k]>n){best=k;n=c[k]}}
  return (n>1&&n>=Math.ceil(list.length/2))?{state:best,n:n}:null;
}
function staleNote(list){
  const m=sharedStale(list); if(!m)return '';
  // "behind the template" is the internal words for it. Nobody outside this
  // repo knows what the template is; what they need to know is that the page
  // on screen was built from an older recipe and can be rebuilt.
  const what=/^stale: template/.test(m.state)
    ? m.n+' songs were built from an older version of the song page — open one to rebuild it'
    : m.n+' songs: '+m.state;
  return '<div class="allstale">'+esc(what)+'</div>';
}
function gridTiles(list){const m=sharedStale(list);
  return list.map(b=>tileSong(b,m?m.state:null)).join('')+tileMain()+tileNew()}
function filterSongs(q){q=(q||'').trim().toLowerCase();return BUILDS.filter(b=>{
  if(!q)return true; const m=b.meta||{};
  return (b.key+' '+(m.title_jp||'')+' '+(m.title_en||'')+' '+(m.artist||'')+' '+(m.artist_en||'')).toLowerCase().includes(q)})}
function renderLibrary(){
  document.body.dataset.view='lib';
  setNav(null);
  const qv=sessionStorage.getItem('mb-q')||'';
  const songs=filterSongs(qv);
  $('app').innerHTML=
    // This box only narrows the songs already on this screen — it does NOT go
    // looking for new ones. It used to say "find a song" (the same words as
    // the New song screen's catalog search) with "odoriko" sitting in it like
    // a typed answer, so typing an artist you wanted to ADD emptied the grid
    // and looked broken. Say what it does.
    '<div class="punch"><div class="lcd"><label>narrow this list</label>'+
      '<input id="q" placeholder="type part of a title or artist" autocomplete="off" value="'+esc(qv)+'"></div></div>'+
    staleNote(songs)+
    (songs.length||!qv.trim()?'':'<div class="empty">no song matches “'+esc(qv)+'”. use ＋ New song to start it.</div>')+
    '<div class="grid">'+gridTiles(songs)+'</div>';
  $('libextra').innerHTML=siteBar()+lexPanel();
  paintBacklog();                      // the badge is re-created empty — refill it
  const qi=$('q');
  qi.addEventListener('input',()=>{sessionStorage.setItem('mb-q',qi.value);
    document.querySelector('.grid').innerHTML=gridTiles(filterSongs(qi.value)); bindGo()});
  bindGo(); bindLex();
}
function bindGo(){document.querySelectorAll('[data-go]').forEach(el=>el.onclick=()=>go(el.getAttribute('data-go')))}

/* ══ song detail (Build + Gradient tabs) ════════════════════════════ */
function detailHead(b){
  const m=b.meta||{};
  // optional steps (the podcast) are not on the road to shipping, so they are
  // not in the denominator — a finished song has to be able to read finished
  const road=roadSteps(b), done=roadDone(b), pct=roadPct(b);
  // Same policy as the library grid: only a REAL problem earns a chip (staleOf
  // filters on warn). Plus a guard the grid doesn't need — LEX is baked in at
  // page-render time, so after a song is removed and re-added under the same
  // key the chip still describes the OLD song. A song whose page has not been
  // assembled cannot be stale against a template it has never been cloned from,
  // and it flew a red "STALE: TEMPLATE" at 1/15 anyway (mariigoorudo,
  // 2026-07-29). Reload (↻) is what refreshes the chip's data.
  const st=staleOf(b);
  const asmDone=(b.steps||[]).some(s=>s.key==='assemble'&&stepStatus(b.key,s)==='done');
  const chip=(st&&asmDone)?('<span class="stchip warn"'+
    (st.cmd?(' data-cp="'+esc(st.cmd).replace(/"/g,'&quot;')+'"'):'')+'>'+esc(st.state)+'</span>'):'';
  return '<div class="dhead">'+(m.art?'<img src="'+esc(m.art)+'" alt="" onerror="this.remove()">':'')+
    '<div class="t"><div class="jp">'+esc(m.title_jp||b.key)+'</div>'+
      '<div class="en">'+esc(m.title_en||'')+'</div>'+
      // a band whose JP and EN names are the same string printed it twice —
      // "go!go!vanillas · go!go!vanillas" — and on a phone that doubled line
      // wrapped, pushing the tabs down for nothing
      '<div class="ar">'+esc(m.artist||'')+
        ((m.artist_en&&m.artist_en!==m.artist)?(' · '+esc(m.artist_en)):'')+
        ' · songs/'+esc(b.slug||'')+'</div></div>'+
    '<div class="prog">'+chip+'<span class="frac">'+done+'/'+road.length+'</span>'+
      '<span class="bar"><i style="width:'+pct+'%"></i></span></div></div>';
}
// the song-detail tab bar — data-driven so a new surface (Timing, Words, …)
// is one pushed entry: {id, label, mount(el, build)}. Server-only tabs are
// appended under SERVER_MODE; file:// keeps just Build + Gradient.
const TABS=[
  {id:'build',    label:'Build',    mount:(el,b)=>mountBuild(el,b)},
  {id:'gradient', label:'Gradient', mount:(el,b)=>mountGradient(el,{kind:'song',key:b.key})},
];
if(SERVER_MODE)TABS.push({id:'timing',  label:'Timing',  mount:(el,b)=>mountTiming(el,b)},
                         {id:'words',   label:'Words',   mount:(el,b)=>mountWords(el,b)},
                         {id:'writing', label:'Writing', mount:(el,b)=>mountWriting(el,b)});
function renderSong(key,tab){
  document.body.dataset.view='detail';
  const b=byKey(key);
  const seg='<div class="seg">'+TABS.map(t=>
    '<button data-tab="'+t.id+'" class="'+(tab===t.id?'on':'')+'">'+t.label+'</button>').join('')+'</div>';
  const runall=SERVER_MODE&&tab==='build'?
    '<button class="ctl run" data-runall'+(JOB.active?' disabled':'')+'>▶ run all (auto)</button>':'';
  const m=b.meta||{};
  setNav(m.title_jp||b.key,(m.artist_en||m.artist||'')+' · '+(TABS.find(t=>t.id===tab)||TABS[0]).label);
  $('app').innerHTML=detailHead(b)+
    '<div class="segrow"><div class="segwrap">'+seg+'</div>'+runall+'</div><div id="tabbody"></div>';
  $('libextra').innerHTML='';
  $('app').querySelectorAll('[data-tab]').forEach(x=>x.onclick=()=>go('song/'+key+'/'+x.getAttribute('data-tab')));
  // five tabs overflow a phone; the one you're ON must be the one you can see
  const onTab=$('app').querySelector('[data-tab].on'), tabBox=$('app').querySelector('.segrow .seg');
  if(onTab&&tabBox){
    const edges=()=>{const w=tabBox.parentNode, x=tabBox.scrollLeft;
      w.classList.toggle('can-l',x>2);
      w.classList.toggle('can-r',x < tabBox.scrollWidth-tabBox.clientWidth-2)};
    const centre=()=>{
      if(tabBox.scrollWidth>tabBox.clientWidth+1)
        tabBox.scrollLeft=Math.max(0,onTab.offsetLeft-(tabBox.clientWidth-onTab.offsetWidth)/2);
      edges()};
    tabBox.addEventListener('scroll',edges,{passive:true});
    addEventListener('resize',centre);
    // once now and once after layout settles — the tab body mounts after this
    // and web fonts land later still, either of which can resize the strip
    centre(); requestAnimationFrame(centre);
  }
  $('app').querySelectorAll('.stchip[data-cp]').forEach(c=>c.onclick=e=>{e.stopPropagation();copy(c.getAttribute('data-cp'))});
  const ra=$('app').querySelector('[data-runall]');
  if(ra)ra.onclick=async()=>{
    try{await api('/api/run',{key,step:null});toast('run all (auto) queued')}catch(err){}
    pollState(true)};
  (TABS.find(t=>t.id===tab)||TABS[0]).mount($('tabbody'),b);
}
function mountBuild(el,b){
  const steps=b.steps||[];
  // "next" follows the road to shipping and steps over the optional ones, the
  // same way the auto walk does — otherwise it parks on the podcast forever
  const nextIdx=steps.findIndex(s=>!s.optional&&stepStatus(b.key,s)!=='done');
  // pre-deploy preview (backlog 14531afd): once the page is assembled, open
  // it right off the box — songs/<slug>/ served with prod's asset routing.
  const asm=steps.find(s=>s.key==='assemble');
  const prev=(SERVER_MODE&&b.slug&&asm&&stepStatus(b.key,asm)==='done')?
    ('<div class="prevrow"><a class="ctl run" href="/preview/'+esc(b.slug)+'/" target="_blank" rel="noopener">▶ preview this build</a>'+
     '<span class="dek" style="margin:0;font-size:11px">the assembled page, served from this box — before any deploy</span></div>'):'';
  el.innerHTML=staleRow(b)+nextUp(b,steps,nextIdx)+prev+
    '<div class="steps">'+steps.map((s,i)=>stepRow(b,s,i===nextIdx)).join('')+'</div>'+
    startPane(b)+identPane(b)+removeBox(b);
  bindSteps(el,b);
  bindStart(b);
  bindIdent(el,b);
  bindRemove(el,b);
  bindStale(el,b);
  bindNextUp(el,b);
}
/* The red STALE: TEMPLATE chip said the page was out of date and stopped
   there. run all (auto) can't fix it — every step is already done, so the walk
   goes straight to the end and parks on deploy with the old page still on
   disk. Whatever the box reports, the box has to be able to do. */
function staleRow(b){
  const st=staleOf(b);
  const asmDone=(b.steps||[]).some(s=>s.key==='assemble'&&stepStatus(b.key,s)==='done');
  if(!st||!asmDone||!SERVER_MODE)return '';
  // "stale: template+audio" is the internal shorthand. Say which thing moved.
  const s=st.state||'', tmpl=/template/.test(s), aud=/audio/.test(s);
  const why=tmpl&&aud
      ? 'This page was built from an older version of the song page, and the '+
        'clips have changed since it was built.'
    :tmpl
      ? 'This page was built from an older version of the song page.'
    :aud
      ? 'The word clips have changed since this page was built, so it is still '+
        'pointing at the old ones.'
      : esc(s);
  // Already rebuilding? Then the notice IS the progress — it says so itself
  // instead of leaving an identical red wall with one greyed-out button, which
  // is indistinguishable from a press that did nothing.
  if(rebuilding(b.key))return staleWorking();
  return '<div class="stalerow"><div class="sm">'+
    why+' Rebuilding clones the current page and puts this song\x27s words back '+
    'into it — nothing you have written is lost.</div>'+
    '<button class="ctl run" data-rebuild="1">▶ rebuild this page</button></div>';
}
function rebuilding(key){
  return !!(JOB.cur&&!JOB.ended&&JOB.cur.key===key&&/^rebuild:/.test(JOB.cur.step||''));
}
function staleWorking(){
  return '<div class="stalerow working"><div class="sm"><span class="spin"></span>'+
    'Rebuilding this page — cloning the current one and putting this song\x27s '+
    'words back into it. It takes about a minute; the progress is at the bottom '+
    'of the screen.</div></div>';
}
function bindStale(el,b){
  const btn=el.querySelector('[data-rebuild]'); if(!btn)return;
  btn.onclick=async()=>{
    btn.disabled=true;
    // Say it on the row you just pressed, not only in a toast that fades. The
    // job bar is driven by the 2s poll, so without this the screen sat
    // unchanged for up to two seconds after the press — long enough on a phone
    // to read as a dead button and press it again.
    const row=btn.closest('.stalerow');
    if(row)row.outerHTML=staleWorking();
    try{await api('/api/rebuild',{key:b.key}); toast('rebuilding the page…'); pollState(true)}
    catch(e){toast((e&&e.message)||'the rebuild wouldn\x27t start'); route()}
  };
}
/* The "Next: …" banner's own buttons. Split out of mountBuild because
   patchSong RE-RENDERS the banner on every poll and has to rebind them: it
   used to patch the dots and the fraction but leave the banner untouched, so
   the biggest instruction on the page kept naming a step that had already
   finished — 4/15 done and still "Next: Timed lyric sheet · ▶ run this"
   (mariigoorudo, 2026-07-29). A stale instruction is worse than none. */
function bindNextUp(el,b){
  el.querySelectorAll('[data-nu-run]').forEach(nb=>{
    nb.onclick=async()=>{try{await api('/api/run',{key:b.key,step:nb.getAttribute('data-nu-run')||null});
      toast('queued')}
      // a swallowed failure looked exactly like a queued run that never started
      catch(err){toast('could not start it: '+((err&&err.message)||'no answer from the box'))}
      pollState(true)}});
  const no=el.querySelector('[data-nu-open]');
  if(no)no.onclick=()=>{const st=el.querySelector('.step[data-s="'+no.getAttribute('data-nu-open')+'"]');
    if(st){st.classList.add('open');st.scrollIntoView({behavior:'smooth',block:'center'})}};
}
/* The start point, for a song that already exists. Same pane as the New song
   screen (see ST) — the only difference is that here it saves.            */
function startPane(b){
  if(!SERVER_MODE)return'';
  const yt=(b.meta||{}).yt||'';
  if(!yt)return'';
  return '<section class="pane sgstart"><h2>where the song starts</h2>'+
    '<div id="sg-start"><div class="stmsg work">looking at the audio…</div></div></section>';
}
function bindStart(b){
  if(!SERVER_MODE)return;
  const m=b.meta||{}, yt=m.yt||''; if(!yt)return;
  stProbe(yt,{mode:'song',key:b.key,
    saved:(m.music_start_src==='manual'&&m.music_start_ms!=null)?m.music_start_ms:null});
}

/* The seven strings that say WHICH SONG this is. They were settable only on
   the New song screen, so a typo — or the blank English title the old box
   accepted — could be fixed only by hand-editing two JSON files, which is
   exactly what the last two walks had to do (mariigoorudo, 2026-07-29/30).
   Quiet like the remove box, with one exception: it opens ITSELF when the
   English title is missing, because nothing else on screen says so. What a
   blank one actually costs, measured on this song rather than assumed: the
   page ships data.json with an empty title_en (the English name and the share
   text lose it), and the study hand-off prompt calls the song by its key —
   "mariigoorudo" instead of "Marigold" (manaoke_build.py's <TITLE> fill-in).
   A blank artist (en) is worse and shows the same class of bug from the other
   side: that string IS in the page markup, so the retarget leaves the
   TEMPLATE's band in the clone and only the parity gate notices.
   Saving writes both files (see `identity` in manaoke_build.py).            */
function identPane(b){
  if(!SERVER_MODE)return'';
  const m=b.meta||{};
  const missing=!String(m.title_en||'').trim();
  const fld=(id,lbl,val,ph,wide)=>'<div class="fld'+(wide?' wide':'')+'"><label>'+lbl+'</label>'+
    '<input id="id-'+id+'" value="'+esc(val||'')+'" placeholder="'+esc(ph||'')+'" '+
    'autocomplete="off" spellcheck="false"></div>';
  return '<details class="rmbox idbox"'+(missing?' open':'')+'>'+
    '<summary>Name, artist and links'+
      (missing?' — the English title is missing':'')+'</summary>'+
    '<div class="rmbody"><p>What this song is called, and where its video, '+
    'album page and cover come from. Saving writes both files that hold these '+
    '— the build state and the content sheet — so they can\x27t drift apart. '+
    'If the page is already built, saving also puts the page rebuild, the '+
    'landing card and the final check back on the list, because the page has '+
    'these words baked into it.</p>'+
    (missing?'<p class="idwarn">This song has no English name right now. '+
      'The page ships without one, and the study hand-off calls the song '+
      '<b>'+esc(b.key)+'</b>. Nothing downstream can work it out — a '+
      'romanization of the Japanese title is not the English name.</p>':'')+
    '<div class="form">'+
      fld('title_jp','title (jp)',m.title_jp)+
      fld('title_en','title (en) — nothing can guess this',m.title_en,
          'the English name of the song')+
      fld('artist','artist',m.artist)+
      fld('artist_en','artist (en)',m.artist_en,'Aimyon')+
      fld('yt','youtube video id',m.yt,'0xSiBpUdW4E')+
      fld('apple','apple music url',m.apple,'',true)+
      fld('art','cover url (400x400)',m.art,'',true)+
    '</div>'+
    '<div class="rmrow" style="margin-top:12px">'+
      '<button class="ctl" id="idsave">Save</button>'+
      '<span class="dek" style="margin:0;font-size:11.5px">the page itself '+
        'changes when you run the steps this reopens</span></div>'+
    '<div class="rmout" id="idout"></div></div></details>';
}
function bindIdent(el,b){
  const go_=el.querySelector('#idsave'); if(!go_)return;
  const F=['title_jp','title_en','artist','artist_en','yt','apple','art'];
  go_.onclick=async()=>{
    const out=el.querySelector('#idout'), body={key:b.key};
    F.forEach(f=>{const i=el.querySelector('#id-'+f); if(i)body[f]=i.value.trim()});
    // the box refuses the blank here as well as in the CLI, so the answer
    // arrives without a round trip — and says WHY, not just "required"
    if(!body.title_en){
      // a refusal has to LOOK like a stop — the pane's own output line is
      // dimmed for the ordinary "here's what it wrote" case
      out.className='rmout idwarn';
      out.textContent='the English title can\x27t be blank — the page would '+
        'ship without an English name, and the study hand-off would call the '+
        'song '+b.key+'.';
      const i=el.querySelector('#id-title_en'); if(i)i.focus(); return}
    go_.disabled=true; const lbl=go_.textContent; go_.textContent='saving…';
    try{
      const j=await api('/api/identity',body);
      out.className='rmout';
      out.textContent=(j.output||'').trim()||'saved';
      toast('saved');
      // The name at the top of this screen is drawn once and the poll only
      // patches the counter and the bar — so a corrected title left the OLD
      // one sitting in the header, which reads as "it didn't save". Patch it
      // here from what was just written.
      const hd=document.querySelector('.dhead .t');
      if(hd){
        const jp=hd.querySelector('.jp'), en=hd.querySelector('.en'),
              ar=hd.querySelector('.ar');
        if(jp)jp.textContent=body.title_jp||b.key;
        if(en)en.textContent=body.title_en||'';
        if(ar)ar.textContent=(body.artist||'')+
          (body.artist_en?(' · '+body.artist_en):'')+' · songs/'+(b.slug||'');
      }
      const im=document.querySelector('.dhead img');
      if(im&&body.art&&im.getAttribute('src')!==body.art)im.setAttribute('src',body.art);
      await pollState(true);
    }catch(e){out.className='rmout idwarn';
      out.textContent=(e&&e.message)||'could not save it'}
    go_.disabled=false; go_.textContent=lbl;
  };
}

/* Taking a song back out. Deliberately quiet, deliberately last, and it does
   not delete: the box moves every file into builds/_trash/ so a mistake is an
   undo, not a loss. Typing the key is the confirmation — a plain OK button is
   too easy to hit on the wrong song.                                       */
function removeBox(b){
  if(!SERVER_MODE)return'';
  return '<details class="rmbox"><summary>Remove this song</summary>'+
    '<div class="rmbody"><p>Moves everything this song owns — its build files, '+
    'its page folder, its audio, its subset fonts and its three routing rules '+
    '— into <b>builds/_trash/</b>. Nothing is '+
    'deleted; moving the folder back undoes it (the routing rules are written '+
    'down in the trash too). If the song is live on the '+
    'landing page, the box refuses until it isn\x27t.</p>'+
    '<div class="rmrow"><input id="rmkey" placeholder="type '+esc(b.key)+' to confirm" '+
      'autocomplete="off" spellcheck="false">'+
    '<button class="ctl rmgo" id="rmgo" disabled>Remove '+esc(b.key)+'</button></div>'+
    '<div class="rmout" id="rmout"></div></div></details>';
}
function bindRemove(el,b){
  const i=el.querySelector('#rmkey'), go_=el.querySelector('#rmgo'); if(!i||!go_)return;
  i.oninput=()=>{go_.disabled=i.value.trim()!==b.key};
  go_.onclick=async()=>{
    go_.disabled=true; const lbl=go_.textContent; go_.textContent='removing…';
    try{
      const j=await api('/api/remove',{key:b.key,confirm:i.value.trim()});
      if(j.ok===false){el.querySelector('#rmout').textContent=j.output||j.error||'couldn\x27t remove it';
        go_.disabled=false; go_.textContent=lbl; return}
      toast(b.key+' removed — it\x27s in builds/_trash/');
      await pollState(true); go('');
    }catch(e){go_.disabled=false; go_.textContent=lbl}
  };
}
function nextUp(b,steps,nextIdx){
  // The one-line answer to "where is this build and what happens next?" —
  // server mode only; the plumbing below stays available but not forefront.
  if(!SERVER_MODE)return'';
  const running=steps.find(s=>stepStatus(b.key,s)==='running');
  if(running)return'<div class="nextup live"><span class="st-dot running"></span>'+
    '<div class="nu-t"><b>Now: '+esc(friendly(running.title))+'</b>'+
    '<span>running on the box — watch the bar below, or step away; it stops at the next hand-off.</span></div></div>';
  if(nextIdx<0)return'<div class="nextup done"><span class="st-dot done"></span>'+
    '<div class="nu-t"><b>All steps done.</b><span>This build is ready to publish.</span></div></div>';
  const s=steps[nextIdx];const own=s.owner;const dis=JOB.active?' disabled':'';
  let what,act='';
  if(s.auto){what='the box runs it';
    act='<button class="ctl run" data-nu-run="'+esc(s.key)+'"'+dis+'>▶ run this</button>'+
        '<button class="ctl" data-nu-run=""'+dis+'>▶ run everything it can</button>';}
  else if(own==='cli'){what='a hand-off step — Claude does this part; open it to see what to hand over';
    act='<button class="ctl" data-nu-open="'+esc(s.key)+'">show the hand-off</button>';}
  else{what='needs you — open it for the how-to, then mark it done';
    act='<button class="ctl" data-nu-open="'+esc(s.key)+'">show me</button>';}
  return'<div class="nextup"><span class="st-dot pending"></span>'+
    '<div class="nu-t"><b>Next: '+esc(friendly(s.title))+'</b><span>'+what+'</span></div>'+
    '<div class="row">'+act+'</div></div>';
}
/* A step's note, minus anything the step already shows above it. A hand-off
   step's output is "[cli] Hand this to …:" + the prompt verbatim, so the page
   printed the same prompt twice — once copyable, once not. Say it once. */
function noteText(s,cmd){
  const n=friendly(s.note||''); if(!n)return '';
  // compare with whitespace collapsed: the step's output re-wraps and indents
  // the prompt, so it is the same text without being the same string
  const norm=x=>x.replace(/\s+/g,' ').trim();
  const nn=norm(n), nc=norm(cmd||'');
  if(!nc||!nn.includes(nc))return n;
  const rest=norm(nn.split(nc).join(' '));
  // what's left is just the "hand this over" preamble → the prompt box said it
  return rest.replace(/[^a-z0-9]/gi,'').length<24?'':rest;
}
function stepRow(b,s,isNext){
  const stt=stepStatus(b.key,s);
  const own=((scratch[b.key]||{})[s.key]||{}).owner||s.owner;
  const cmd=fillCmd(s.cmd||'',b);
  const cmdCls=own==='cli'?'cli':own==='external'?'external':'';
  // server mode: real buttons — auto steps get ▶ run (POST /api/run), every
  // step gets mark-done/undo (POST /api/set). file:// keeps the scratch pad.
  const dis=JOB.active?' disabled':'';
  // refetch-lyrics (backlog c899c32e): a done lyrics step gets a small ↻
  // control with a MANDATORY two-tap inline warning — a re-fetch replaces
  // builds/<key>.lyrics.json wholesale, so manual timing edits are lost.
  const refetch=(SERVER_MODE&&s.key==='lyrics'&&stt==='done')?(
      '<button class="ctl" data-refetch'+dis+'>↻ refetch lyrics</button>'):'';
  const refetchWarn=refetch?(
      '<div class="refetch-warn" data-refetchwarn hidden>re-fetching replaces ALL line/word timings — '+
        'manual timing edits will be lost.'+
        '<div class="row"><button class="ctl stop" data-refetchgo'+dis+'>replace the timings</button>'+
        '<button class="ctl" data-refetchcancel>keep what I have</button></div></div>'):'';
  // bring-your-own sheet: the three network sources look a song up by
  // identity, so a song none of them has heard of has no way in at all.
  // Offered on the lyrics step whatever its status — the case that needs it
  // most is the one where the fetch found nothing, so it must not wait for
  // a done step the way ↻ refetch does.
  const byo=(SERVER_MODE&&s.key==='lyrics')?(
      '<button class="ctl" data-byo'+dis+'>⇥ import a sheet</button>'):'';
  const byoBox=byo?(
      '<div class="refetch-warn" data-byobox hidden>paste an .lrc, a TTML .xml, or a .json sheet — '+
        'the format is read from the text, not a file name. This replaces the timings you have.'+
        '<textarea data-byotext rows="6" spellcheck="false" '+
          'placeholder="[00:12.34]&#28468;&#35422;&#12398;&#34892;"></textarea>'+
        '<div class="row"><button class="ctl stop" data-byogo'+dis+'>import this sheet</button>'+
        '<button class="ctl" data-byocancel>cancel</button></div></div>'):'';
  const ctls=SERVER_MODE?(
      '<div class="row">'+
        (s.auto?'<button class="ctl run" data-run="'+esc(s.key)+'"'+dis+'>▶ run</button>':'')+
        '<button class="ctl'+(stt==='done'?' on':'')+'" data-setdone="'+esc(s.key)+'"'+dis+'>'+
          (stt==='done'?'↩ undo':'✓ mark done')+'</button>'+
        refetch+byo+
      '</div>'+refetchWarn+byoBox
    ):(
      '<div class="row">'+
        '<button class="ctl'+(stt==='done'?' on':'')+'" data-act="done">✓ done</button>'+
        '<button class="ctl" data-act="own" data-own="local">local</button>'+
        '<button class="ctl" data-act="own" data-own="cli">→ hand-off</button>'+
        '<button class="ctl" data-act="own" data-own="external">external</button>'+
      '</div>');
  return '<div class="step'+(isNext?' next':'')+'" data-s="'+esc(s.key)+'">'+
    '<div class="step-hd" data-toggle><span class="chev">▶</span>'+
      '<span class="st-dot '+stt+'"></span>'+
      '<span class="nm"><span class="ti">'+esc(friendly(s.title))+'</span> <span class="key">'+esc(s.key)+'</span></span>'+
      (isNext?'<span class="nextpill">next</span>':'')+
      // says out loud what the walk now does: steps over it, and doesn't count it
      (s.optional?'<span class="optpill" title="not needed to ship — run it when you want it">optional</span>':'')+
      '<span class="auto">'+AUTO_LBL(s.auto)+'</span>'+
      '<span class="owner '+own+'">'+(OWN_LBL[own]||own)+'</span></div>'+
    '<div class="step-bd"><p>'+esc(friendly(s.blurb))+'</p>'+
      // This is the ONE step on the road to shipping that the box can't run
      // itself. Handing the prompt to a model is the fast way; typing it is
      // the way that works with no account and no network. The second way was
      // invisible until it was named right here, next to the first.
      ((SERVER_MODE&&s.key==='author_data')?
        ('<div class="wr-alt">You can write this yourself instead of handing it '+
         'over — the <a href="#song/'+esc(b.key)+'/writing">Writing tab</a> has a '+
         'box for every line, card and section, and says which are still empty.</div>'):'')+
      // A hand-off step's command IS the thing you came here to do — hand it to
      // Claude Code. Burying it under "plumbing" (collapsed, sounds like
      // internals) meant the only copyable version of the prompt was hidden,
      // while the visible one, echoed in the note below, could only be
      // hand-selected. Hand-off steps show it open and say what it is; every
      // other step keeps the tidy collapsed plumbing.
      (cmd?(SERVER_MODE?
        (own==='cli'?
        ('<details class="plumb" open><summary>the prompt to hand over</summary>'+
         '<div class="cmd '+cmdCls+'">'+esc(cmd)+'<button class="cp" data-cp="'+esc(cmd).replace(/"/g,'&quot;')+'">copy</button></div></details>'):
        ('<details class="plumb"><summary>plumbing — the exact command</summary>'+
         '<div class="cmd '+cmdCls+'">'+esc(cmd)+'<button class="cp" data-cp="'+esc(cmd).replace(/"/g,'&quot;')+'">copy</button></div></details>')):
        ('<div class="cmd '+cmdCls+'">'+esc(cmd)+'<button class="cp" data-cp="'+esc(cmd).replace(/"/g,'&quot;')+'">copy</button></div>')):'')+
      ctls+((noteText(s,cmd))?('<div class="note">'+esc(noteText(s,cmd))+'</div>'):'')+
    '</div></div>';
}
function fillCmd(c,b){return c.replace(/<key>/g,b.key).replace(/<slug>/g,b.slug||b.key)
  .replace(/<TITLE>/g,(b.meta&&b.meta.title_en)||b.key).replace(/<ARTIST>/g,(b.meta&&b.meta.artist_en)||'')}
function bindSteps(root,b){
  root.querySelectorAll('[data-toggle]').forEach(h=>h.onclick=e=>{
    if(e.target.closest('.owner')||e.target.closest('.ctl')||e.target.closest('.cp'))return;
    h.parentElement.classList.toggle('open')});
  root.querySelectorAll('.cp').forEach(x=>x.onclick=e=>{e.stopPropagation();copy(x.getAttribute('data-cp'))});
  if(SERVER_MODE){
    root.querySelectorAll('[data-run]').forEach(btn=>btn.onclick=async e=>{e.stopPropagation();
      try{const j=await api('/api/run',{key:b.key,step:btn.getAttribute('data-run')});
        toast('queued · '+btn.getAttribute('data-run'))}catch(err){}
      pollState(true)});
    root.querySelectorAll('[data-setdone]').forEach(btn=>btn.onclick=async e=>{e.stopPropagation();
      const sk=btn.getAttribute('data-setdone');
      const bb=byKey(b.key)||b;                   // fresh state — BUILDS mutates in place
      const s=(bb.steps||[]).find(x=>x.key===sk)||{};
      const nv=stepStatus(bb.key,s)!=='done';
      try{await api('/api/set',{key:bb.key,step:sk,done:nv});
        toast(sk+(nv?' → done':' → pending'))}catch(err){}
      pollState(true)});
    // refetch-lyrics: tap 1 opens the inline warning, tap 2 queues the job
    const rWarn=()=>root.querySelector('[data-refetchwarn]');
    const rBtn=()=>root.querySelector('[data-refetch]');
    root.querySelectorAll('[data-refetch]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();
      const w=rWarn(); if(w)w.hidden=false; btn.hidden=true});
    root.querySelectorAll('[data-refetchcancel]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();
      const w=rWarn(); if(w)w.hidden=true; const rb=rBtn(); if(rb)rb.hidden=false});
    root.querySelectorAll('[data-refetchgo]').forEach(btn=>btn.onclick=async e=>{e.stopPropagation();
      try{await api('/api/refetch_lyrics',{key:b.key});
        toast('re-fetch queued — the timings will be replaced')}catch(err){}
      const w=rWarn(); if(w)w.hidden=true; const rb=rBtn(); if(rb)rb.hidden=false;
      pollState(true)});
    // bring-your-own sheet: same two-tap shape as refetch, because it lands
    // in the same place and costs the same timings.
    const yBox=()=>root.querySelector('[data-byobox]');
    const yBtn=()=>root.querySelector('[data-byo]');
    root.querySelectorAll('[data-byo]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();
      const w=yBox(); if(w)w.hidden=false; btn.hidden=true;
      const t=root.querySelector('[data-byotext]'); if(t)t.focus()});
    root.querySelectorAll('[data-byocancel]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();
      const w=yBox(); if(w)w.hidden=true; const yb=yBtn(); if(yb)yb.hidden=false});
    root.querySelectorAll('[data-byogo]').forEach(btn=>btn.onclick=async e=>{e.stopPropagation();
      const t=root.querySelector('[data-byotext]');
      const sheet=(t&&t.value||'').trim();
      if(!sheet){toast('paste a sheet first — nothing was sent');return}
      try{await api('/api/refetch_lyrics',{key:b.key,sheet});
        toast('importing your sheet — the timings will be replaced')}catch(err){}
      const w=yBox(); if(w)w.hidden=true; const yb=yBtn(); if(yb)yb.hidden=false;
      pollState(true)});
    return;
  }
  root.querySelectorAll('.ctl').forEach(btn=>btn.onclick=e=>{
    e.stopPropagation();
    const step=btn.closest('.step').getAttribute('data-s');
    scratch[b.key]=scratch[b.key]||{}; scratch[b.key][step]=scratch[b.key][step]||{};
    if(btn.getAttribute('data-act')==='done'){
      const nv=!scratch[b.key][step].done; scratch[b.key][step].done=nv; saveScratch();
      toast('persist:  manaoke_build.py set '+b.key+' '+step+(nv?' --done':' --status pending'));
    }else{const o=btn.getAttribute('data-own'); scratch[b.key][step].owner=o; saveScratch();
      toast('persist:  manaoke_build.py set '+b.key+' '+step+' --owner '+o)}
    mountBuild(root, b);
  });
}

/* ══ main-page gradient editor ══════════════════════════════════════ */
function renderMainPage(){
  document.body.dataset.view='detail';
  setNav('Main page','the landing living-field background');
  $('app').innerHTML=
    '<div class="dhead"><div class="t"><div class="jp">Main page</div>'+
    '<div class="en">the landing living-field background</div>'+
    '<div class="ar">index.html · one dark, breathing world behind the machine</div></div></div>'+
    '<div id="tabbody"></div>';
  $('libextra').innerHTML='';
  mountGradient($('tabbody'),{kind:'main'});
}

/* ══ new-song walkthrough ═══════════════════════════════════════════ */
const NF=['key','yt','title_jp','title_en','artist','artist_en','apple','art'];
function renderNew(){
  if(SERVER_MODE)return renderNewServer();   // real flow: search → pick → /api/init
  document.body.dataset.view='detail';
  const F=JSON.parse(sessionStorage.getItem('mb-new')||'{}');
  const fld=(id,lbl,ph,wide)=>'<div class="fld'+(wide?' wide':'')+'"><label>'+lbl+'</label>'+
    '<input id="nf-'+id+'" placeholder="'+esc(ph)+'" value="'+esc(F[id]||'')+'" autocomplete="off"></div>';
  setNav('New song','grab → time → teach → voice → deploy');
  $('app').innerHTML=
    '<div class="dhead"><div class="t"><div class="jp">New song</div>'+
    '<div class="en">grab → time → teach → voice → deploy — every step in order</div>'+
    '<div class="ar">fill the identity, then run each step in order (or “copy the whole run”)</div></div></div>'+
    '<section class="pane"><h2>1 · song identity → the create command</h2>'+
      '<div class="form">'+
        fld('key','key (slug stem)','shinunoga')+fld('yt','youtube id','wCTNa3ncksY')+
        fld('title_jp','title (jp)','死ぬのがいいわ')+fld('title_en','title (en)','I\'d Rather Die')+
        fld('artist','artist','藤井風')+fld('artist_en','artist (en)','Fujii Kaze')+
        fld('apple','apple music url (blank → LRCLIB path)','https://music.apple.com/...',true)+
        fld('art','artwork url (400x400; blank = iTunes lookup)','',true)+
      '</div>'+
      '<div class="row"><button class="go" id="nf-go">copy the create command</button>'+
        '<button class="ctl" id="nf-clear">clear</button></div>'+
      '<div class="pcmd dim" id="nf-cmd" style="margin-top:12px"><button class="cp" id="nf-cmdcp">copy</button><span id="nf-cmdtext">fill the key to compose the create command</span>'+
        '<span id="nf-cmdhint" class="idwarn" style="display:block;margin-top:6px"></span></div>'+
    '</section>'+
    '<section class="pane"><h2>2 · the sequence — run each in order</h2>'+
      '<p class="dek" style="margin:0 0 6px"><b style="color:var(--local)">local</b> steps the box runs; '+
        '<b style="color:var(--cli)">hand-off</b> steps you hand to Claude Code; <b style="color:var(--external)">external</b> '+
        'needs a signed-in tab. Tap a step for its command. “Copy the whole run” pastes every auto command for Claude Code to walk — the eventual “just hit go.”</p>'+
      '<div class="row" style="margin:2px 0 6px"><button class="ctl" id="wk-run">copy the whole run</button></div>'+
      '<div class="steps" id="wk-steps"></div>'+
    '</section>';
  $('libextra').innerHTML='';
  const readF=()=>{const o={};NF.forEach(id=>o[id]=($('nf-'+id).value||'').trim());return o};
  const fakeB=()=>{const f=readF();return {key:f.key||'<key>',slug:f.key||'<slug>',meta:{title_en:f.title_en,artist_en:f.artist_en}}};
  function composeInit(){
    const f=readF(); let c='python3 tools/songcraft/manaoke_build.py init '+(f.key||'<key>');
    const add=(flag,v)=>{if(v)c+=' --'+flag+' "'+v.replace(/"/g,'\\"')+'"'};
    add('title-jp',f.title_jp);add('title-en',f.title_en);add('artist',f.artist);add('artist-en',f.artist_en);
    add('yt',f.yt);add('apple',f.apple);add('art',f.art);
    $('nf-cmdtext').textContent=c; $('nf-cmd').classList.toggle('dim',!f.key);
    // init REFUSES without an English title (nothing downstream can guess it),
    // so say that here instead of letting the paste fail in the terminal
    $('nf-cmdhint').textContent=f.title_en?'':
      'add the English title — init refuses without it';
    return c;
  }
  NF.forEach(id=>$('nf-'+id).addEventListener('input',()=>{
    sessionStorage.setItem('mb-new',JSON.stringify(readF())); composeInit(); paintWk()}));
  $('nf-cmdcp').onclick=()=>copy(composeInit());   // the command, never the hint
  $('nf-go').onclick=()=>{copy(composeInit());toast('create command copied — run it, then walk the steps')};
  $('nf-clear').onclick=()=>{sessionStorage.removeItem('mb-new');renderNew()};
  function paintWk(){
    const b=fakeB();
    $('wk-steps').innerHTML=DAG.map((s,i)=>{
      const cmd=fillCmd(s.cmd||'',b);
      return '<div class="step" data-s="'+esc(s.key)+'"><div class="step-hd" data-toggle>'+
        '<span class="chev">▶</span><span class="st-dot pending"></span>'+
        '<span class="nm"><span class="ti">'+(i+1)+'. '+esc(friendly(s.title))+'</span> <span class="key">'+esc(s.key)+'</span></span>'+
        '<span class="auto">'+AUTO_LBL(s.auto)+'</span>'+
        '<span class="owner '+s.owner+'">'+(OWN_LBL[s.owner]||s.owner)+'</span></div>'+
        '<div class="step-bd"><p>'+esc(friendly(s.blurb))+'</p>'+
          (cmd?('<div class="cmd '+(s.owner==='cli'?'cli':s.owner==='external'?'external':'')+'">'+esc(cmd)+
            '<button class="cp" data-cp="'+esc(cmd).replace(/"/g,'&quot;')+'">copy</button></div>'):'')+'</div></div>';
    }).join('');
    $('wk-steps').querySelectorAll('[data-toggle]').forEach(h=>h.onclick=e=>{
      if(e.target.closest('.cp'))return; h.parentElement.classList.toggle('open')});
    $('wk-steps').querySelectorAll('.cp').forEach(x=>x.onclick=e=>{e.stopPropagation();copy(x.getAttribute('data-cp'))});
  }
  $('wk-run').onclick=()=>{
    const f=readF(); if(!f.key){toast('fill the key first');return}
    const b=fakeB();
    // auto/local runnable commands only; PROMPT/comment/external steps still need a human
    const runnable=DAG.filter(s=>s.cmd&&!/^\s*(PROMPT:|#|\()/.test(s.cmd)&&s.owner!=='external');
    const lines=[composeInit()].concat(runnable.map(s=>fillCmd(s.cmd,b).split('\n')[0]));
    copy(lines.join('\n'));
    toast('run copied — hand it to Claude Code (external + PROMPT steps still need you)');
  };
  composeInit(); paintWk();
}

/* ══ new-song search flow (server mode only) ════════════════════════
   type → GET /api/search (iTunes proxy + direct source probe, vendored
   lyric_sources) → candidate cards → pick → GET /api/probe (deep
   per-source granularity + preview) + prefilled confirm → /api/init.  */
const NS={results:[],picked:null,yt:'',ytc:[],ytBest:null,ytOpen:false,ytManual:'',probeSeq:0,
  ytBad:{},ytSeen:{},ytSkipped:'',   // videos the player refused to play in a page
  // (the start point lives in ST — one pane, shared with the song view)
  // the cover's colors: auto = what assemble would pick, cur = your overrides,
  // aim = which field the NEXT click on the cover sets (background by default,
  // so the common case is one click and no arming).
  pal:{art:'',auto:null,cur:{},aim:'c1',state:'',px:null}};
function mmss(ms){const s=Math.round((ms||0)/1000);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
function srcPill(name,v){return '<span class="srcpill '+(v===true?'yes':v===false?'no':'unk')+'">'+
  name+' '+(v===true?'✓':v===false?'—':'?')+'</span>'}
/* deep probe cards: WORD-LEVEL beats LINE-LEVEL beats TEXT-ONLY; '?' = the
   source couldn't be reached in budget (never shown as a hard "no").       */
const PSRC={apple:'Apple',netease:'NetEase',lrclib:'LRCLIB'};
function probeCard(src,r,pending){
  let badge;
  if(pending)badge='<span class="pbadge unk">? checking</span>';
  else if(!r||r.has==null)badge='<span class="pbadge unk">? unknown</span>';
  else if(!r.has)badge='<span class="pbadge">NONE</span>';
  else if(r.granularity==='word')badge='<span class="pbadge word">WORD-LEVEL</span>';
  else if(r.granularity==='line')badge='<span class="pbadge line">LINE-LEVEL</span>';
  else badge='<span class="pbadge text">TEXT-ONLY</span>';
  const bits=[];
  if(r&&r.has&&r.line_count)bits.push(r.line_count+' lines');
  if(r&&r.note)bits.push(esc(r.note));
  if(pending)bits.push('checking…');
  const prev=(r&&r.preview&&r.preview.length)?
    ('<div class="plines">'+r.preview.map(t=>'<div>'+esc(t)+'</div>').join('')+'</div>'):'';
  return '<div class="probe" data-psrc="'+src+'"><div class="ph"><span class="pn">'+(PSRC[src]||src)+'</span>'+
    badge+'</div>'+(bits.length?('<div class="pc">'+bits.join(' · ')+'</div>'):'')+prev+'</div>';
}
async function nsProbe(c){
  const seq=++NS.probeSeq;
  const paint=(j,pending)=>{
    const el=$('nsc-probe'); if(!el)return;
    el.innerHTML=['apple','netease','lrclib'].map(s=>
      probeCard(s,j?j[s]:(pending?null:{has:null,note:'probe failed — is the server up?'}),pending)).join('');
    const pk=$('nsc-pick'); if(!pk)return;
    if(pending){pk.textContent='checking each lyric source for this exact track — word-level beats line-level';return}
    // No auto pick is TWO different situations and they need different words.
    // Every source answered "no" = a real dead end, make the sheet by hand.
    // A source that was busy or too slow answered NOTHING — calling that a
    // dead end is the same lie the Throttled exception was added to stop
    // (a busy source is not an empty one). Say which, and say try again.
    const unk=['apple','netease','lrclib'].filter(s=>j&&j[s]&&j[s].has==null);
    pk.innerHTML=(j&&j.auto_pick)?
      ('lyrics will come from <b>'+(PSRC[j.auto_pick]||j.auto_pick)+'</b> (auto order: Apple → NetEase → LRCLIB)'):
      unk.length?
        (unk.map(s=>PSRC[s]||s).join(' and ')+' never answered — nothing found yet, but '+
         'that is not a no. Pick the track again in a minute to ask again.'):
      'no source can time this track automatically — the lyric sheet would need to be made by hand';
  };
  paint(null,true);
  let j=null;
  try{
    const r=await fetch('/api/probe?title='+encodeURIComponent(c.title||'')+
      '&artist='+encodeURIComponent(c.artist||'')+'&duration_ms='+(c.duration_ms||0)+
      '&apple_url='+encodeURIComponent(c.apple_url||'')+'&itunes_id='+(c.itunes_id||''));
    if(r.ok)j=await r.json();
  }catch(e){}
  if(seq!==NS.probeSeq)return;      // another candidate was picked meanwhile
  paint(j,false);
}
function ytId(v){v=(v||'').trim();
  const m=v.match(/(?:youtu\.be\/|[?&]v=|\/shorts\/|\/embed\/|\/live\/)([A-Za-z0-9_-]{11})/);
  if(m)return m[1];
  return /^[A-Za-z0-9_-]{11}$/.test(v)?v:'';
}
function renderNewServer(){
  document.body.dataset.view='detail';
  setNav('New song','search the catalog, pick the track, confirm');
  $('app').innerHTML=
    '<div class="dhead"><div class="t"><div class="jp">New song</div>'+
    '<div class="en">search the catalog, pick the track, confirm — the box adds the song</div>'+
    '<div class="ar">catalog search · lyric sources: Apple / NetEase / LRCLIB</div></div></div>'+
    '<section class="pane"><h2>1 · find the track</h2>'+
      '<div class="punch" style="margin:2px 0 4px"><div class="lcd"><label>find a song</label>'+
        '<input id="ns-q" placeholder="artist or title" autocomplete="off"></div>'+
        '<button class="go" id="ns-go">search</button></div>'+
      '<div id="ns-results"></div></section>'+
    '<div id="ns-confirm"></div>';
  $('libextra').innerHTML='';
  $('ns-q').addEventListener('keydown',e=>{if(e.key==='Enter')nsSearch()});
  $('ns-go').onclick=nsSearch;
  $('ns-q').focus();
}
async function nsSearch(){
  const q=($('ns-q').value||'').trim(); if(!q){toast('type a song or artist');return}
  $('ns-results').innerHTML='<div class="empty">searching…</div>';
  $('ns-confirm').innerHTML='';
  let res;
  try{const r=await fetch('/api/search?q='+encodeURIComponent(q));
    const j=await r.json(); if(!r.ok)throw new Error(j.error||r.status); res=j.results||[]}
  catch(e){$('ns-results').innerHTML='<div class="empty">search failed — is the server up?</div>';return}
  NS.results=res;
  if(!res.length){$('ns-results').innerHTML='<div class="empty">nothing found for “'+esc(q)+'”.</div>';return}
  $('ns-results').innerHTML=res.map((c,i)=>{
    const src=c.sources||{};
    return '<button class="cand" type="button" data-pick="'+i+'">'+
      (c.art400?('<img src="'+esc(c.art400)+'" alt="" loading="lazy" onerror="this.style.visibility=\x27hidden\x27">'):'<span class="noart"></span>')+
      '<div class="ci"><div class="ct">'+esc(c.title)+'</div>'+
      '<div class="ca">'+esc(c.artist)+(c.album?(' · '+esc(c.album)):'')+'</div>'+
      '<div class="cm"><span>'+mmss(c.duration_ms)+'</span>'+
        srcPill('NetEase',src?src.netease:null)+srcPill('LRCLIB',src?src.lrclib:null)+'</div></div></button>';
  }).join('');
  $('ns-results').querySelectorAll('[data-pick]').forEach(el=>el.onclick=()=>nsPick(+el.getAttribute('data-pick')));
}
function nsPick(i){
  const c=NS.results[i]; if(!c)return;
  NS.picked=c; NS.yt=''; NS.ytc=[]; NS.ytBest=null; NS.ytOpen=false; NS.ytManual='';
  NS.ytBad={}; NS.ytSeen={}; NS.ytSkipped='';   // per-track: what the player refused
  $('ns-results').querySelectorAll('.cand').forEach((el,j)=>el.classList.toggle('on',j===i));
  const fld=(id,lbl,val,ph,wide)=>'<div class="fld'+(wide?' wide':'')+'"><label>'+lbl+'</label>'+
    '<input id="nsc-'+id+'" value="'+esc(val||'')+'" placeholder="'+esc(ph||'')+'" autocomplete="off"></div>';
  $('ns-confirm').innerHTML='<section class="pane"><h2>2 · confirm — then the box adds it</h2>'+
    '<div class="probepick" id="nsc-pick"></div>'+
    '<div class="probes" id="nsc-probe"></div>'+
    '<div class="form">'+
      // "key (slug stem)" was the internal name for it. What it does, in the
      // words of someone who has never read the code: it is the word that ends
      // up in the song's web address.
      fld('key','name for the web address (a-z 0-9 -)',c.key_suggestion,
          'lowercase, no spaces')+
      fld('title_jp','title (jp)',c.title)+
      '<div class="fld wide"><label>youtube video — pick one to continue</label>'+
        '<div class="ytbox" id="nsc-ytbox"></div></div>'+
      // The English title is the one identity field nothing can guess (a
      // romanization of マリーゴールド is "mariigoorudo", not "Marigold"). Left
      // blank, data.json ships an empty title_en — the song has no English
      // name — and the author_data hand-off prompt calls it by its key
      // (manaoke_build.py's <TITLE> fill-in falls back to st['key']). Checked
      // 2026-07-30: it does NOT leave the template's English title in the
      // markup, which an earlier version of this comment claimed — that string
      // is nowhere in the page; artist (en) below is the field that does that.
      // The old placeholder was the literal word "Marigold" — for this song it
      // read as an answer already typed, so the field stayed empty. Required now.
      fld('title_en','title (en) — required, nothing can guess this','',
          'the English name of the song')+
      fld('artist','artist',c.artist)+
      // artist (en) is PREFILLED with the romanization: left blank, assemble
      // keeps the TEMPLATE's English artist in the clone (see
      // _artist_en_suggestion in server.py). Editable — it is a suggestion.
      // The placeholder used to be the literal word "Aimyon", which for a
      // YOASOBI song read as an answer already filled in — the same trap the
      // title (en) comment above records. Placeholders describe the field;
      // they never show one song's answer on another song's screen.
      fld('artist_en','artist (en)',c.artist_en_suggestion||'',
          'the artist\x27s name in English letters')+
      fld('apple','apple music url',c.apple_url,'',true)+
      fld('art','artwork url (400x400)',c.art400,'',true)+
    '</div></section>'+
    '<section class="pane"><h2>3 · where the song starts</h2>'+
      '<div id="nsc-start"><div class="stmsg">pick the video first — the start '
        +'point is measured off that video\x27s audio.</div></div></section>'+
    '<section class="pane"><h2>4 · the cover and its colors</h2>'+
      '<div id="nsc-cover"></div>'+
      '<div class="row" style="margin-top:16px"><button class="go" id="nsc-go" disabled>Add song</button>'+
        '<span class="dek" style="margin:0;font-size:11.5px">adds the song and starts building it — opens Timing, which fills in as the lines land</span></div>'+
      '<div class="ns-out" id="nsc-out"></div></section>';
  $('nsc-go').onclick=nsCreate;
  $('ns-confirm').scrollIntoView({behavior:'smooth',block:'start'});
  nsProbe(c);
  nsYtMatch(c);
  nsCoverInit(c.art400||'');
  const ai=$('nsc-art');
  if(ai)ai.addEventListener('change',()=>nsCoverInit((ai.value||'').trim()));
}

/* ══ where the song starts ═══════════════════════════════════════════
   The pipeline measures an onset (first sound above a threshold) and the
   player skips to it. That measurement can't know the difference between
   dead air and a quiet intro someone actually wants to hear, so here it is
   only a suggestion: the waveform is on screen and the marker is draggable.

   ONE pane, two homes. On the New song screen nothing is saved — whatever is
   showing when you hit Add song is what the song ships with. On a song that
   already exists the same pane gets Save, which writes the number and patches
   the lyric sheet, so a wrong start is a drag and a rebuild instead of a
   re-sync (that would cost a Demucs separation to move one number).        */
const ST={mode:'new',key:'',yt:'',doc:null,state:'',err:'',
          ms:null,        // what you dragged to (null = nothing dragged)
          saved:null,     // the point already set by hand, if any
          seq:0,timer:0,saving:false};
function stFmt(ms){const t=Math.max(0,ms||0),s=(t%60000)/1000;
  return Math.floor(t/60000)+':'+(s<10?'0':'')+s.toFixed(1)}
function stAuto(){return (ST.doc&&ST.doc.music_start_ms)||0}
function stBase(){return ST.saved!=null?ST.saved:stAuto()}   // what's in effect now
function stMs(){return ST.ms!=null?ST.ms:stBase()}
function stTouched(){return ST.ms!=null&&ST.ms!==stBase()}
// On the New song screen there is nothing to save yet — the number just rides
// along with Add song — so "not saved yet" would be a lie about a pending action.
function stTag(){return stTouched()?(ST.mode==='song'?'not saved yet':'you set this')
  :(ST.saved!=null?'you set this':'suggested')}
function stHost(){return $(ST.mode==='new'?'nsc-start':'sg-start')}
function stMounted(){return !!stHost()}
function stProbe(yt,opts){
  opts=opts||{};
  if(ST.timer){clearTimeout(ST.timer);ST.timer=0}
  const same=ST.yt===yt&&ST.mode===(opts.mode||'new')&&ST.key===(opts.key||'');
  if(!yt){ST.yt='';ST.doc=null;ST.state='';ST.ms=null;ST.seq++;stRender();return}
  // idempotent: the callers fire on every re-render, and a probe in flight for
  // this same video must not be restarted (that would drop a drag mid-edit).
  if(same&&(ST.state==='ready'||ST.state==='running'||ST.state==='error'))return;
  ST.mode=opts.mode||'new'; ST.key=opts.key||''; ST.saved=opts.saved!=null?opts.saved:null;
  ST.yt=yt; ST.doc=null; ST.err=''; ST.ms=null; ST.state='running'; ST.saving=false;
  const seq=++ST.seq;
  stRender();
  const read=async()=>{
    try{const r=await fetch('/api/startprobe/'+encodeURIComponent(yt));
      if(r.ok)return await r.json()}catch(e){}
    return null;
  };
  const settle=j=>{
    if(seq!==ST.seq)return true;
    if(j&&j.state==='ready'){ST.doc=j;ST.state='ready';stRender();return true}
    if(j&&j.state==='error'){ST.state='error';ST.err=j.error||'couldn\x27t read that video';
      stRender();return true}
    return false;
  };
  (async()=>{
    // ASK before working. A video this Mac has already measured comes back
    // instantly, and the box never gets told to do a download it doesn't need.
    if(settle(await read()))return;
    if(seq!==ST.seq)return;
    try{ await api('/api/startprobe',{yt}); }
    catch(e){
      if(seq!==ST.seq)return;
      ST.state='error'; ST.err=(e&&e.message)||'the box wouldn\x27t start the probe';
      stRender(); return;
    }
    const poll=async()=>{
      if(seq!==ST.seq)return;
      if(settle(await read()))return;
      ST.timer=setTimeout(poll,1500);
    };
    ST.timer=setTimeout(poll,1200);
  })();
}
/* yt-dlp's failures are written for whoever is reading a terminal: they name
   flags (--cookies-from-browser), link the project wiki, and run four lines
   long. On this screen that reads as the box breaking. Say the one thing the
   person can act on; keep anything unrecognized short. */
function plainYtErr(msg){
  const m=(msg||'')+'';
  if(/confirm your age|age-restrict/i.test(m))
    return 'YouTube age-restricts it, so nothing outside YouTube can play it.';
  if(/not a bot|sign in to confirm you/i.test(m))
    return 'YouTube blocked this Mac from reading it just now. Try again in a minute.';
  if(/private video/i.test(m))return 'that video is private.';
  if(/unavailable|removed|terminated/i.test(m))return 'that video is gone.';
  if(/geo|country|region/i.test(m))return 'that video isn\x27t available in this country.';
  return m.split(/[.\n]/)[0].slice(0,140);
}
function stRender(){
  const el=stHost(); if(!el)return;
  if(!ST.yt){el.innerHTML='<div class="stmsg">pick the video first — the start '+
    'point is measured off that video\x27s audio.</div>';return}
  if(ST.state==='running'){el.innerHTML='<div class="stmsg work">listening to the song… '+
    'the first time on this Mac it has to download the audio, which takes a moment. '+
    '(That download is the same one the sync step needs later, so nothing is wasted.)</div>';return}
  if(ST.state==='error'){el.innerHTML='<div class="stmsg warn">couldn\x27t read that video: '+
    esc(plainYtErr(ST.err))+'</div><div class="stnote">'+(ST.mode==='new'
      ?'You can still add the song — the sync step will measure the start point itself.'
      :'Nothing changed. The song keeps the start point it has.')+'</div>';return}
  const ms=stMs(), dur=(ST.doc&&ST.doc.duration_ms)||1;
  const song=ST.mode==='song';
  el.innerHTML=
    '<div class="stwrap"><div class="stwave" id="stw">'+
      '<canvas id="stwc"></canvas>'+
      '<div class="dead" id="stdead"></div>'+
      '<div class="auto" id="stauto"></div>'+
      '<div class="mk" id="stmk"></div>'+
      '<div class="grab" id="stgrab"></div>'+
    '</div>'+
    '<div class="stbar">starts at <b id="stnum">'+stFmt(ms)+'</b>'+
      '<span class="tag'+(ST.ms!=null||ST.saved!=null?' set':'')+'" id="sttag">'+stTag()+'</span>'+
      '<button class="ctl" id="sthear">hear it</button>'+
      // only offered once there is something to go back FROM (stPaint toggles
      // it live, so it appears the moment you drag)
      '<button class="ctl" id="stundo"'+(stTouched()?'':' hidden')+'>'+
        (song?'undo':'back to '+stFmt(stAuto()))+'</button>'+
      (song?'<button class="ctl run" id="stsave"'+(stTouched()?'':' hidden')+'>save this start</button>'+
            (ST.saved!=null?'<button class="ctl" id="stclear">let the box measure it</button>':'')
           :'')+
      '<span style="color:var(--faint)">song is '+stFmt(dur)+' long</span>'+
    '</div>'+
    '<div class="stnote">Drag the marker. Everything left of it is skipped — the '+
      'player starts here and the clock reads 0:00 here.'+
      (song?' Saving patches the lyric sheet; rebuild the page to ship it.':'')+
    '</div><div class="stout" id="stout"></div></div>';
  stDraw();
  stBind();
}
function stDraw(){
  const cv=$('stwc'),d=ST.doc; if(!cv||!d)return;
  const box=cv.parentElement.getBoundingClientRect();
  const dpr=Math.min(window.devicePixelRatio||1,2);
  const W=Math.max(1,Math.round(box.width)),H=Math.max(1,Math.round(box.height));
  cv.width=W*dpr; cv.height=H*dpr;
  const g=cv.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,W,H);
  const pk=d.peaks||[],n=pk.length; if(!n)return;
  const mid=H/2, amp=(H/2)-2;
  g.fillStyle=getComputedStyle(document.body).getPropertyValue('--lcd-ink').trim()||'#8fe3b0';
  g.globalAlpha=.75;
  for(let x=0;x<W;x++){
    const a=Math.floor(x*n/W), b=Math.max(a+1,Math.floor((x+1)*n/W));
    let lo=127,hi=-127;
    for(let i=a;i<b&&i<n;i++){if(pk[i][0]<lo)lo=pk[i][0];if(pk[i][1]>hi)hi=pk[i][1]}
    if(hi<lo)continue;
    const y0=mid-(hi/127)*amp, y1=mid-(lo/127)*amp;
    g.fillRect(x,y0,1,Math.max(1,y1-y0));
  }
  g.globalAlpha=1;
  stPaint();
}
function stPaint(){
  const d=ST.doc; if(!d)return;
  const w=$('stw'); if(!w)return;
  const dur=d.duration_ms||1, ms=stMs();
  const pct=x=>Math.max(0,Math.min(100,(x/dur)*100));
  const dead=$('stdead'),mk=$('stmk'),au=$('stauto'),gr=$('stgrab');
  if(dead)dead.style.width=pct(ms)+'%';
  if(mk)mk.style.left=pct(ms)+'%';
  if(gr)gr.style.left=pct(ms)+'%';
  if(au)au.style.left=pct(stAuto())+'%';
  const num=$('stnum'); if(num)num.textContent=stFmt(ms);
  const tag=$('sttag');
  if(tag){tag.textContent=stTag();
    tag.classList.toggle('set',ST.ms!=null||ST.saved!=null)}
  const ub=$('stundo'); if(ub)ub.hidden=!stTouched();
  const sb=$('stsave'); if(sb)sb.hidden=!stTouched();
}
function stBind(){
  const w=$('stw'),d=ST.doc; if(!w||!d)return;
  const dur=d.duration_ms||1;
  const at=e=>{const r=w.getBoundingClientRect();
    const x=((e.touches&&e.touches[0])||e).clientX-r.left;
    return Math.max(0,Math.min(dur,Math.round(x/Math.max(1,r.width)*dur)))};
  let drag=false;
  const move=e=>{if(!drag)return;e.preventDefault();ST.ms=at(e);stPaint()};
  const up=()=>{drag=false};
  w.addEventListener('pointerdown',e=>{drag=true;w.setPointerCapture&&w.setPointerCapture(e.pointerId);
    ST.ms=at(e);stPaint()});
  w.addEventListener('pointermove',move);
  w.addEventListener('pointerup',up); w.addEventListener('pointercancel',up);
  const hb=$('sthear'); if(hb)hb.onclick=()=>{
    const b=stMs();
    const a=new Audio('/api/startclip/'+encodeURIComponent(ST.yt)+'?b='+b+'&e='+(b+6000));
    a.play().catch(()=>toast('couldn\x27t play that — is the audio downloaded?'));
  };
  const ub=$('stundo'); if(ub)ub.onclick=()=>{ST.ms=null;stPaint()};
  const sb=$('stsave'); if(sb)sb.onclick=()=>stSave({ms:stMs()});
  const cb=$('stclear'); if(cb)cb.onclick=()=>stSave({auto:true});
  if(!window._stResize){window._stResize=true;
    window.addEventListener('resize',()=>{if($('stwc'))stDraw()})}
}
async function stSave(what){
  if(ST.saving)return; ST.saving=true;
  const out=$('stout'); if(out)out.textContent='saving…';
  try{
    const j=await api('/api/start',Object.assign({key:ST.key},what));
    ST.saved=what.auto?null:what.ms; ST.ms=null; ST.saving=false;
    stRender();
    const o=$('stout');
    if(o)o.textContent=what.auto
      ? 'back to automatic — the sync step will measure it again.'
      : 'saved. Rebuild the page to ship it: run the assemble step (or manaoke_build.py rebuild '+ST.key+').';
    pollState(true);
  }catch(e){
    ST.saving=false;
    const o=$('stout'); if(o)o.textContent=(e&&e.message)||'couldn\x27t save that';
  }
}
/* ══ 4 · the cover and its colors ════════════════════════════════════
   The cover is ALWAYS armed. Move over it to preview, click to set — no
   selecting a field first. The default target is the background, because
   that is the one anybody actually wants to change; the chips retarget the
   next click for the rare case. Auto picks are the same ones assemble
   derives, so leaving this alone changes nothing.                        */
const CVAIM=[['c1','background'],['c2','2nd color'],['c3','3rd color'],['hi','highlight']];
function cvCur(f){const P=NS.pal;return P.cur[f]||((P.auto||{})[f])||'#000000'}
function cvTouched(){return Object.keys(NS.pal.cur).length>0}
async function nsCoverInit(art){
  const P=NS.pal;
  P.art=art; P.auto=null; P.cur={}; P.aim='c1'; P.px=null; P.state=art?'loading':'none';
  nsCoverRender();
  if(!art)return;
  const mine=art;
  try{
    const r=await fetch('/api/palette?art='+encodeURIComponent(art));
    const j=await r.json();
    if(P.art!==mine)return;
    if(!r.ok)throw new Error(j.error||r.status);
    P.auto=j; P.state='ready';
  }catch(e){ if(P.art===mine){P.state='error'} }
  nsCoverRender();
}
function nsCoverRender(){
  const el=$('nsc-cover'); if(!el)return;
  const P=NS.pal;
  if(P.state==='none'){el.innerHTML='<div class="stmsg">no cover art url on this '+
    'track — paste one in the artwork field above and the colors follow.</div>';return}
  const swatches=CVAIM.map(([f,name])=>
    '<button type="button" data-aim="'+f+'"'+(P.aim===f?' class="on"':'')+'>'+
      '<i style="background:'+cvCur(f)+'"></i>'+esc(name)+
      (P.cur[f]?' ✓':'')+'</button>').join('');
  el.innerHTML='<div class="cvwrap">'+
    '<div><div class="cvart" id="cvart">'+
        '<img id="cvimg" src="/api/art?url='+encodeURIComponent(P.art)+'" alt="album cover">'+
        '<canvas id="cvcan"></canvas><div class="cvloupe" id="cvlp"></div>'+
      '</div>'+
      '<div class="stnote">Click the cover to set the '+
        '<b id="cvaimname">'+esc((CVAIM.find(a=>a[0]===P.aim)||CVAIM[0])[1])+'</b>.</div>'+
    '</div>'+
    '<div>'+
      '<div class="prev cvprev" id="cvprev"><div class="mesh"></div>'+
        '<div class="fb fb1"></div><div class="fb fb2"></div><div class="fb fb3"></div>'+
        '<div class="scrim"></div><div class="lyric">'+esc(($('nsc-title_jp')||{}).value||'')+'</div></div>'+
      '<div class="cvsw">'+swatches+'</div>'+
      '<div class="cvcard"><i id="cvcardsw"></i><span>library card accent · '+
        '<b id="cvcardhex"></b></span></div>'+
      (cvTouched()?'<div class="row" style="margin-top:10px">'+
        '<button class="ctl" id="cvreset">back to the auto picks</button></div>':'')+
    '</div></div>'+
    (P.state==='loading'?'<div class="stmsg work">reading the colors off the cover…</div>':'')+
    (P.state==='error'?'<div class="stmsg warn">couldn\x27t read colors off that '+
      'cover — the song still builds, assemble will try again.</div>':'');
  cvBind();
  cvPaint();
}
function cvPaint(){
  const p=$('cvprev'); if(!p)return;
  const P=NS.pal,st=p.style;
  ['c1','c2','c3','hi'].forEach(f=>st.setProperty('--'+f,trip(cvCur(f))));
  const fb=(P.cur.fb||(P.auto&&P.auto.fb)||[]);
  fb.forEach((h,i)=>st.setProperty('--fb'+(i+1),trip(h)));
  st.setProperty('--base1',darken(cvCur('c1'),.165,1.3));
  st.setProperty('--base2',darken(cvCur('c1'),.094,1.3));
  st.setProperty('--base3',darken(cvCur('c1'),.047,1.3));
  Object.entries(GL.fdur||{}).forEach(([n,b])=>st.setProperty('--fdur-'+n,fmt(b)+'s'));
  st.setProperty('--amp',1);
  // The library card accent is DERIVED from the background, exactly as the
  // build derives it — so picking the background picks the card too.
  const acc=P.cur.c1?cardAccent(P.cur.c1):((P.auto&&P.auto.card_accent)||cardAccent(cvCur('c1')));
  const sw=$('cvcardsw'),hx=$('cvcardhex');
  if(sw)sw.style.background=acc;
  if(hx)hx.textContent=acc;
  NS.pal.cardAccent=acc;
}
function cvBind(){
  const P=NS.pal, art=$('cvart'), img=$('cvimg'), can=$('cvcan'), lp=$('cvlp');
  $('nsc-cover').querySelectorAll('[data-aim]').forEach(b=>b.onclick=()=>{
    P.aim=b.getAttribute('data-aim'); nsCoverRender()});
  const rb=$('cvreset'); if(rb)rb.onclick=()=>{P.cur={};nsCoverRender()};
  if(!art||!img||!can)return;
  const sample=e=>{
    if(!P.px)return null;
    const r=img.getBoundingClientRect();
    const x=Math.floor((((e.touches&&e.touches[0])||e).clientX-r.left)/r.width*P.px.w);
    const y=Math.floor((((e.touches&&e.touches[0])||e).clientY-r.top)/r.height*P.px.h);
    if(x<0||y<0||x>=P.px.w||y>=P.px.h)return null;
    const i=(y*P.px.w+x)*4,d=P.px.data;
    return rgbhex([d[i],d[i+1],d[i+2]]);
  };
  const grab=()=>{
    // Same-origin via /api/art, so this canvas is never tainted.
    try{
      can.width=img.naturalWidth||img.width; can.height=img.naturalHeight||img.height;
      const g=can.getContext('2d',{willReadFrequently:true});
      g.drawImage(img,0,0,can.width,can.height);
      P.px={w:can.width,h:can.height,data:g.getImageData(0,0,can.width,can.height).data};
    }catch(e){P.px=null}
  };
  if(img.complete&&img.naturalWidth)grab(); else img.onload=grab;
  img.onerror=()=>{P.px=null};
  art.addEventListener('pointermove',e=>{
    const hex=sample(e); if(!hex){art.classList.remove('live');return}
    art.classList.add('live');
    const r=art.getBoundingClientRect();
    lp.style.left=(e.clientX-r.left)+'px'; lp.style.top=(e.clientY-r.top)+'px';
    lp.style.background=hex;
    cvPreview(hex);
  });
  art.addEventListener('pointerleave',()=>{art.classList.remove('live');cvPaint()});
  art.addEventListener('pointerdown',e=>{
    const hex=sample(e); if(!hex)return;
    e.preventDefault();
    P.cur[P.aim]=hex;                     // ONE click. No arming step.
    nsCoverRender();
  });
}
function cvPreview(hex){                   // hover-only: show it, don't commit it
  const p=$('cvprev'); if(!p)return;
  const P=NS.pal, keep=P.cur[P.aim];
  P.cur[P.aim]=hex; cvPaint();
  if(keep===undefined)delete P.cur[P.aim]; else P.cur[P.aim]=keep;
}
/* youtube auto-match: pick a track → GET /api/ytmatch → best-match card +
   "not this one?" alternates + a manual paste field as the last resort.
   Add song stays disabled until a video is chosen (NS.yt).                 */
function ytManualHTML(){
  return '<div class="ytmanual"><input id="nsm-yt" value="'+esc(NS.ytManual||'')+
    '" placeholder="or paste a YouTube link / 11-char id" autocomplete="off"></div>';
}
/* playable preview of the SELECTED video (backlog 19cceaa0) — hear the track
   before adding it. youtube-nocookie embed; the thumb+meta cards above stay
   the selection UI and the fallback when a video has embedding disabled.

   AND the check that a video can be embedded at all (2026-07-30). The song
   page is nothing but a YouTube embed with lyrics wrapped around it — it
   builds a YT.Player and drives it. So a video YouTube refuses to play inside
   another page (owner turned embedding off, or it is age-gated) makes a song
   page that never plays a note. Both of YOASOBI 夜に駆ける's official uploads
   are exactly that, and the box happily offered the blocked one as its best
   match and let it through to a finished build.

   Nothing off-page can tell you this: yt-dlp reports playable_in_embed:true
   for the blocked one, and oEmbed answers 200 for every video alive. The only
   thing that knows is the player itself, so ask it — mount the preview through
   the IFrame API and listen for onError. The preview is the same player the
   song page uses, so its verdict IS the page's verdict.                     */
var YTPL=null, YTPLID='';
function ytApi(cb){
  if(window.YT&&window.YT.Player)return cb();
  if(!window.__ytq){
    window.__ytq=[];
    const prev=window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady=function(){
      if(prev)try{prev()}catch(e){}
      const q=window.__ytq; window.__ytq=null; (q||[]).forEach(f=>{try{f()}catch(e){}});
    };
    const s=document.createElement('script');
    s.src='https://www.youtube.com/iframe_api'; document.head.appendChild(s);
  }
  if(window.__ytq)window.__ytq.push(cb); else cb();
}
// 101/150 = the owner disallows embedding (this is also what an age-gated
// video reports); 2 = not a video id; 5 = the browser can't play it;
// 100 = gone or private. All four end the same way for us.
const YTERR={2:'that isn\x27t a real video link',5:'this browser can\x27t play it',
  100:'that video is gone or private',
  101:'YouTube won\x27t let it play inside another page',
  150:'YouTube won\x27t let it play inside another page'};
function ytMount(id){
  if(YTPL){try{YTPL.destroy()}catch(e){} YTPL=null}
  YTPLID=id||'';
  if(!id)return;
  ytApi(()=>{
    const host=$('nsc-ytpl');
    if(!host||YTPLID!==id)return;                // moved on while the API loaded
    YTPL=new YT.Player(host,{videoId:id,host:'https://www.youtube-nocookie.com',
      playerVars:{rel:0},
      events:{
        // A blocked video still reports ready, then errors a beat later. Give
        // it that beat before calling it good.
        onReady:()=>setTimeout(()=>{if(YTPLID===id&&!NS.ytBad[id])ytVerdict(id,0)},2500),
        onError:e=>ytVerdict(id,e.data||101)}});
  });
}
function ytVerdict(id,code){
  if(NS.ytSeen[id]==='ok'&&!code)return;
  if(code){NS.ytSeen[id]='bad'; NS.ytBad[id]=YTERR[code]||'YouTube won\x27t play it here';}
  else NS.ytSeen[id]='ok';
  document.querySelectorAll('[data-ytpick="'+id+'"]').forEach(el=>
    el.classList.toggle('dead',!!NS.ytBad[id]));
  if(code&&id===NS.yt)ytNext(id); else {ytWarnPaint(); nsSyncGo()}
}
/* The blocked one was the box's own best guess, so don't hand the problem
   back as a dead end — move to the next candidate and say why. */
function ytNext(badId){
  const list=(NS.ytBest?[NS.ytBest]:[]).concat(
    NS.ytc.filter(v=>!NS.ytBest||v.id!==NS.ytBest.id));
  const next=list.find(v=>!NS.ytBad[v.id]);
  NS.ytSkipped=(NS.ytBad[badId]||'')+'';
  if(!next){NS.yt=''; ytWarnPaint(); nsSyncGo(); return}
  NS.yt=next.id; NS.ytManual=''; NS.ytOpen=true;   // open the list so the swap is visible
  nsYtRender();
}
function ytWarnHTML(){
  const bad=NS.yt?NS.ytBad[NS.yt]:null;
  if(bad)return '<div class="ytwarn">This video won\x27t work: '+esc(bad)+'. '+
    'The song page plays the video exactly this way, so the page would sit there empty. '+
    'Pick another one above, or paste a link you know plays.</div>';
  if(!NS.yt&&NS.ytSkipped)return '<div class="ytwarn">None of YouTube\x27s copies of this '+
    'song will play inside another page, so a song page built from any of them would sit '+
    'there empty. Paste a link you know plays, or choose a different song.</div>';
  if(NS.ytSkipped)return '<div class="ytnote">Skipped the closest match — '+
    esc(NS.ytSkipped)+'. This one plays.</div>';
  return '';
}
function ytWarnPaint(){const w=$('nsc-ytwarn'); if(w)w.innerHTML=ytWarnHTML()}
function ytEmbedHTML(){
  if(!NS.yt)return '';
  return '<div class="ytembed"><div id="nsc-ytpl"></div></div>';
}
function nsYtEmbedSync(){
  const em=$('nsc-ytembed'); if(!em)return;
  const want=NS.yt||'';
  if(em.getAttribute('data-yt')===want)return;   // same video — don't reload the player
  em.setAttribute('data-yt',want);
  em.innerHTML=ytEmbedHTML();
  ytMount(want);
  ytWarnPaint();
}
function ytEmbedBoxHTML(){return '<div id="nsc-ytembed" data-yt="'+esc(NS.yt||'')+'">'+
  ytEmbedHTML()+'</div><div id="nsc-ytwarn">'+ytWarnHTML()+'</div>'}
function ytCard(v,track,sel){
  const dd=(v.duration_ms||0)-(track.duration_ms||0);
  const off=Math.abs(dd)>10000;
  const chip=(track.duration_ms&&v.duration_ms)?('<span class="ytdelta'+(off?' warn':'')+'">'+
    (off?'⚠ ':'')+(dd>=0?'+':'−')+(Math.abs(dd)/1000).toFixed(1)+'s vs track</span>'):'';
  const bad=NS.ytBad[v.id];
  return '<button class="cand ytc'+(sel?' on':'')+(bad?' dead':'')+'" type="button" data-ytpick="'+esc(v.id)+'">'+
    (v.thumb?('<img src="'+esc(v.thumb)+'" alt="" loading="lazy" onerror="this.style.visibility=\x27hidden\x27">'):'<span class="noart"></span>')+
    '<div class="ci"><div class="ct">'+esc(v.title)+'</div>'+
    '<div class="ca">'+esc(v.channel||'')+'</div>'+
    '<div class="cm"><span>'+mmss(v.duration_ms)+'</span>'+chip+
      (bad?'<span class="ytdelta warn">⚠ won\x27t play in a page</span>':'')+'</div></div></button>';
}
async function nsYtMatch(c){
  const box=$('nsc-ytbox'); if(!box)return;
  box.innerHTML='<div class="ytspin">finding the video…</div>'+ytManualHTML();
  nsBindYt();
  let j=null;
  try{
    const r=await fetch('/api/ytmatch?title='+encodeURIComponent(c.title||'')+
      '&artist='+encodeURIComponent(c.artist||'')+'&duration_ms='+(c.duration_ms||0));
    if(!r.ok)throw new Error(r.status); j=await r.json();
  }catch(e){
    box.innerHTML='<div class="ytmiss">couldn\x27t search YouTube — paste the video link below.</div>'+ytManualHTML()+ytEmbedBoxHTML();
    nsBindYt(); return;
  }
  NS.ytc=(j&&j.candidates)||[];
  NS.ytBest=(j&&j.best)||null;
  if(NS.ytBest&&!NS.ytc.some(v=>v.id===NS.ytBest.id))NS.ytc.unshift(NS.ytBest);
  if(!NS.ytBest&&!NS.ytc.length){
    box.innerHTML='<div class="ytmiss">no video found — paste the video link below.</div>'+ytManualHTML()+ytEmbedBoxHTML();
    nsBindYt(); return;
  }
  NS.yt=NS.ytBest?NS.ytBest.id:'';   // best match starts chosen — "not this one?" to change
  nsYtRender();
}
function nsYtRender(){
  const box=$('nsc-ytbox'), c=NS.picked||{}, best=NS.ytBest; if(!box)return;
  const alts=NS.ytc.filter(v=>!best||v.id!==best.id);
  box.innerHTML=(best?ytCard(best,c,NS.yt===best.id):'')+
    (alts.length?('<button class="ctl" type="button" id="nsc-ytalt">'+
        (NS.ytOpen?'▾ fewer':'▸ not this one? '+alts.length+' more')+'</button>'+
      (NS.ytOpen?alts.map(v=>ytCard(v,c,NS.yt===v.id)).join(''):'')):'')+
    ytEmbedBoxHTML()+
    ytManualHTML();
  ytMount(NS.yt||'');       // the box was just rebuilt — the player with it
  nsBindYt();
}
function nsBindYt(){
  const box=$('nsc-ytbox'); if(!box)return;
  box.querySelectorAll('[data-ytpick]').forEach(el=>el.onclick=()=>{
    NS.yt=el.getAttribute('data-ytpick'); NS.ytManual='';
    const mi=$('nsm-yt'); if(mi)mi.value='';
    box.querySelectorAll('[data-ytpick]').forEach(x=>
      x.classList.toggle('on',x.getAttribute('data-ytpick')===NS.yt));
    nsYtEmbedSync(); nsSyncGo()});
  const alt=box.querySelector('#nsc-ytalt');
  if(alt)alt.onclick=()=>{NS.ytOpen=!NS.ytOpen;nsYtRender()};
  const mi=$('nsm-yt');
  if(mi)mi.oninput=()=>{
    NS.ytManual=mi.value;
    const id=ytId(mi.value), empty=!mi.value.trim();
    NS.yt=id||(empty&&NS.ytBest?NS.ytBest.id:'');   // garbage in the field = nothing chosen
    box.querySelectorAll('[data-ytpick]').forEach(x=>
      x.classList.toggle('on',!id&&x.getAttribute('data-ytpick')===NS.yt));
    nsYtEmbedSync(); nsSyncGo()};
  nsSyncGo();
}
function nsSyncGo(){const g=$('nsc-go');
  // A video the player has already refused is not a video you can build a song
  // on, so Add song stays off until there's one that plays.
  if(g)g.disabled=JOB.active||!NS.yt||!!NS.ytBad[NS.yt];
  // Every path that changes the video lands here — but so does the job-bar
  // poller, on EVERY view. Only touch the start pane when the New song screen
  // is the one on screen, or a poll would wipe the song view's pane.
  if($('nsc-start'))stProbe(NS.yt,{mode:'new'})}
async function nsCreate(){
  const v=id=>{const el=$('nsc-'+id);return el?(el.value||'').trim():''};
  const key=v('key');
  if(!/^[a-z0-9][a-z0-9-]*$/.test(key)){toast('key must be lowercase a-z 0-9 -');$('nsc-key').focus();return}
  const yt=NS.yt||ytId(NS.ytManual||'');
  if(!yt){toast('pick the video (or paste its link) first');const mi=$('nsm-yt');if(mi)mi.focus();return}
  // No English title = the template's English title stays in the clone, invisible
  // on screen and caught only later by the parity gate. Ask for it here.
  if(!v('title_en')){toast('type the English title — the page needs it');
    $('nsc-title_en').focus();return}
  const btn=$('nsc-go'); btn.disabled=true; $('nsc-out').textContent='';
  const label=btn.textContent; btn.textContent='adding the song…';
  try{
    // Only send what you actually changed. An untouched start point or an
    // untouched palette must leave the build byte-identical to before this
    // screen existed — the auto path stays the auto path.
    const body={key,title_jp:v('title_jp'),title_en:v('title_en'),
      artist:v('artist'),artist_en:v('artist_en'),yt,apple:v('apple'),art:v('art'),
      duration_ms:(NS.picked&&NS.picked.duration_ms)||0};
    if(stTouched())body.music_start_ms=stMs();
    if(cvTouched())body.design={gradient:Object.assign({},NS.pal.cur)};
    const j=await api('/api/init',body);
    if(j.ok===false){$('nsc-out').textContent=j.output||'couldn\x27t add the song';btn.disabled=false;btn.textContent=label;return}
    toast('song added — getting it ready');
    await pollState(true);            // pull the new build into BUILDS first
    // Straight to Timing, not to the checklist. The box is already fetching the
    // lyrics and aligning them; Timing is the room that fills up as it lands,
    // and it is where the work actually happens.
    go('song/'+key+'/timing');
  }catch(e){btn.disabled=false;btn.textContent=label}
}

/* ══ timing tab (server mode only) ══════════════════════════════════
   GET /api/timing/{key}: each sung line's begin/end vs the lyric source,
   pace + checks. Drags on the wave (edges/words/held-points) and the word
   tools post back; the box rewrites the timings and returns the fresh
   read model, which we patch in place.                                  */
const TM={key:null,data:null,open:null,busy:false,pendingFetch:false,readyTimer:0,
          peaks:null,peaksKey:null,lane:'vocals',
          view:null,           // {wb,span} — the open line's zoom window
          focus:null,          // focused word index within the open line
          holdArm:false,       // "mark the held part" armed → next wave tap sets it
          wform:null,          // open word form {kind:'edit'|'add', ...field values}
          helpOpen:false};     // the "what do these do" legend (mobile has no hover tooltips)
function tmss(ms){if(ms==null)return '—';const t=Math.max(0,ms),s=(t%60000)/1000;
  return Math.floor(t/60000)+':'+(s<10?'0':'')+s.toFixed(1)}
function signms(ms){return (ms>0?'+':ms<0?'−':'±')+Math.abs(Math.round(ms))+'ms'}
const MONO='ui-monospace,monospace';
function mountTiming(el,b){
  TM.key=b.key; TM.data=null; TM.open=null; TM.view=null; TM.focus=null;
  TM.holdArm=false; TM.wform=null; TM.pendingFetch=false;
  if(TM.readyTimer){clearTimeout(TM.readyTimer);TM.readyTimer=0}
  el.innerHTML='<div class="empty">reading the line timings…</div>';
  tmFetch();
  if(TM.peaksKey!==b.key){TM.peaks=null;TM.peaksKey=b.key;pkFetch(b.key)}
}
/* precomputed waveform peaks (builds/<key>.peaks.json via /api/peaks) —
   [min,max] int8 bins (10ms default, 2ms after "sharpen"), two lanes:
   vocals (demucs stem) + full mix */
async function pkFetch(key){
  let j=null;
  try{const r=await fetch('/api/peaks/'+encodeURIComponent(key));
    j=await r.json().catch(()=>null);
    if(!r.ok)j={error:(j&&j.error)||('no waveform data ('+r.status+')')}}
  catch(e){j={error:'couldn\x27t reach the box for the waveform'}}
  if(TM.peaksKey!==key)return;         // user moved to another song meanwhile
  TM.peaks=j;
  if(tmVisible()&&TM.data){wvDraw();ovDraw()}
}
const tmVisible=()=>location.hash==='#song/'+TM.key+'/timing';
/* There is nothing to edit until the lyric sheet lands. Adding a song now
   starts that work immediately (server: /api/init queues the prep walk), so
   this is a progress note, not a dead end — it keeps looking and fills itself
   in the moment the lines exist, minutes before the alignment finishes. */
function tmNotReady(){
  const el=$('tabbody'); if(!el)return;
  const b=byKey(TM.key)||{}, steps=b.steps||[];
  const sOf=k=>{const s=steps.find(x=>x.key===k);return s?stepStatus(TM.key,s):''};
  const working=JOB.active&&JOB.cur&&JOB.cur.key===TM.key;
  const line=(on,done,txt)=>'<div class="tmprep-s'+(done?' done':on?' on':'')+'">'+
    (done?'✓':on?'•':'·')+' '+txt+'</div>';
  const ly=sOf('lyrics')==='done', sy=sOf('whisper_sync')==='done';
  el.innerHTML='<div class="tmprep">'+
    (working?'<h3>Getting this song ready</h3>':'<h3>This song isn\x27t ready to work on yet</h3>')+
    line(working&&!ly,ly,'find the lyric sheet and its word timings')+
    line(working&&ly&&!sy,ly,'draw the waveform')+
    line(working&&ly&&!sy,sy,'line the words up with the singing')+
    '<p>'+(working?
      'The lines show up here as soon as the sheet lands — you can start reading them '+
      'while the alignment finishes. Watch the bar at the bottom, or walk away.':
      'Nothing is running for it. Start the work and this page fills itself in.')+'</p>'+
    (working?'':'<button class="ctl run" id="tmprepgo"'+(JOB.active?' disabled':'')+
      '>get it ready</button>')+'</div>';
  const go_=$('tmprepgo');
  if(go_)go_.onclick=async()=>{go_.disabled=true;
    try{await api('/api/run',{key:TM.key,step:null});toast('queued')}catch(e){go_.disabled=false}
    pollState(true)};
  if(TM.readyTimer)clearTimeout(TM.readyTimer);
  TM.readyTimer=setTimeout(()=>{if(tmVisible()&&!TM.data)tmFetch()},3000);
}
async function tmFetch(){
  const el=$('tabbody'); if(!el||!TM.key||!tmVisible())return;
  let j=null;
  try{const r=await fetch('/api/timing/'+encodeURIComponent(TM.key));
    if(!r.ok)throw new Error(r.status); j=await r.json()}
  catch(e){if(tmVisible())tmNotReady();return}
  if(!tmVisible())return;            // user moved on while we fetched
  if(TM.readyTimer){clearTimeout(TM.readyTimer);TM.readyTimer=0}
  TM.data=j; tmRender($('tabbody'));
  if(!TM.peaks||TM.peaks.error)pkFetch(TM.key);   // the wave may have landed since
}
// apply a fresh read model WITHOUT losing the open line / zoom / focus / scroll
// (the edit response carries it, so no extra round-trip). TM.view + TM.focus are
// module state, so a full tmRender preserves the studio — the old tmFetch nuke
// is gone. Scroll is captured/restored across the rebuild.
function tmApply(timing){
  if(!timing)return tmFetch();
  const se=document.scrollingElement||document.documentElement, sc=se?se.scrollTop:0;
  TM.data=timing;
  if(tmVisible())tmRender($('tabbody'));   // the reveal rebuilds from the fresh timings
  if(se)se.scrollTop=sc;
}
// raw check ids → words a person can act on (hover keeps the technical id)
const TMFLAG={'line>20s':'line runs very long','token>4s':'a word held too long',
  'mora-rate':'sung implausibly fast or slow','repeat-ratio>2.5':'same line, very different lengths'};

/* ── the state ladder: where is this song right now? ────────────────
   Four honest states derived from files + git (never from the lying
   build_state step flags). The FIRST unfinished one carries its action, so
   the いい trap — a fix that shows on this page but never reached the main
   page — is impossible to miss. Preview link ≠ manaoke.app (hard wall). */
function tmStateStrip(){
  const s=(TM.data&&TM.data.state)||null;
  if(!s)return '';
  // dim = this rung is not reachable yet. Without it the three shipping rungs
  // render exactly like the reached ones on a brand-new song, so the strip reads
  // as four claims ("page rebuilt", "on manaoke.app") instead of a path.
  const pill=(done,now,label,dim)=>'<span class="tm-schip'+(done?' done':now?' now':
    dim?' notyet':'')+'">'+(done?'✓ ':now?'• ':dim?'· ':'')+label+'</span>';
  // stage flags: edited (unsaved edits) is the negation of "page rebuilt"
  const rebuilt=s.built&&!s.edited;
  let actKey='', actLbl='';
  // A song whose page has never been assembled has no changes TO apply and
  // nothing to apply them to — offering "apply my changes to the page" there
  // was a button that could only fail. The honest answer is what's missing.
  if(!s.built){actKey='';actLbl='';}
  else if(!rebuilt){actKey='rebuild';actLbl='apply my changes to the page';}
  else if(!s.pushed){actKey='ship';actLbl='put it online (private link)';}
  else if(!s.promoted){actKey='promote';actLbl='put it on the main page';}
  const act=actKey?('<button class="ctl run tm-act" data-tmact="'+actKey+'"'+
    (JOB.active?' disabled':'')+'>'+esc(actLbl)+'</button>'):
    (s.built?'<span class="tm-alldone">✓ on the main page</span>':
     '<span class="tm-nopage">no page built yet — the study data comes first</span>');
  // the receipt for a human edit. lyrics.json alone is NOT one: the lyric fetch
  // writes it, so a brand-new song used to claim "last saved 10:21 PM" for work
  // nobody did. Only an edit sidecar or authored content counts.
  const saved=s.saved_at?('last saved '+
    new Date(s.saved_at*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})):
    // a scaffolded content.json is the machine skeleton, not somebody's work —
    // say which one it is instead of the flat "no edits yet"
    (s.scaffolded?'study skeleton ready — nothing written yet':'no edits yet');
  // two doors: this Mac's copy WITH the edits (always current — the box folds
  // pending edits in as it serves), and the private online preview link
  const editsLink=(s.preview_url&&s.built)?('<a class="tm-prevlink" href="'+esc(s.preview_url)+
    '" target="_blank" rel="noopener">open edits in preview ↗</a>'):'';
  const liveLink=(s.live_url&&s.pushed)?('<a class="tm-prevlink" href="'+esc(s.live_url)+
    '" target="_blank" rel="noopener">open in preview ↗</a>'):'';
  return '<div class="tm-state">'+
    pill(!!s.saved_at,false,saved)+
    // no page yet = the ladder hasn't started; marking a rung "now" would point
    // at a stage nothing can reach until the study data is authored
    pill(rebuilt,s.built&&!rebuilt,'page rebuilt',!s.built)+
    pill(s.pushed,rebuilt&&!s.pushed,'up at the preview link',!s.built)+
    pill(s.promoted,s.pushed&&!s.promoted,'on manaoke.app',!s.built)+
    '<span class="tm-sp"></span>'+editsLink+liveLink+act+
    '</div>';
}

/* Are these times lined up with THIS video, or are they still the lyric
   sheet's own guess? Until whisper_sync has run, every number in this table
   came from the source sheet — usable to read along with, but not something to
   trust to the millisecond, and nothing on the screen used to say so
   (mariigoorudo, 2026-07-28: the alignment refused, the table looked normal).  */
function tmAlignWarn(){
  // worse than "not lined up yet": the sheet is for another recording entirely
  const vw=(TM.data&&TM.data.variant_warning)||'';
  if(vw)return '<div class="tm-warn bad"><span>'+esc(vw)+
    ' — the lines below will not sit on this video. Refetch the lyrics on the Build tab '+
    'once the other sources answer.</span></div>';
  const b=byKey(TM.key); if(!b)return '';
  const s=(b.steps||[]).find(x=>x.key==='whisper_sync'); if(!s||s.status==='done')return '';
  const busy=JOB.active&&JOB.cur&&JOB.cur.key===TM.key;
  const bad=s.status==='blocked'||s.status==='failed';
  const msg=busy?'lining these up with the video now — the times will move when it lands':
    bad?'lining up with the video stopped partway — the Build tab says why':
    'these times came from the lyric sheet — nothing has lined them up with this video yet';
  return '<div class="tm-warn'+(bad?' bad':'')+'"><span>'+msg+'</span>'+
    (busy?'':'<button class="ctl run" data-tmalign'+(JOB.active?' disabled':'')+
      '>line them up with the video</button>')+'</div>';
}

function tmLine(L){
  const flagged=(L.flags||[]).length>0;
  const rate=L.mora_rate!=null?String(Math.round(L.mora_rate*10)/10):'—';
  const sec=L.section?('<span class="tm-sec" title="'+esc(L.section.name||'')+'">'+esc(L.section.short_name||L.section.name||'')+'</span>'):'';
  const chips=(L.flags||[]).map(f=>'<span class="tm-chip" title="'+esc(f)+'">'+esc(TMFLAG[f]||f)+'</span>')
    .concat(L.override==='line'?['<span class="tm-chip anch" title="this line window was set by hand — re-alignments keep it">hand-set</span>']:[])
    .concat(L.residual_ms!=null?['<span class="tm-chip res">'+signms(L.residual_ms)+'</span>']:[]).join('');
  return '<div class="tm-line'+(flagged?' flag':'')+(TM.open===L.i?' open':'')+'" data-i="'+L.i+'">'+
    '<div class="tm-row" data-tmrow>'+
      '<span class="tm-t">'+tmss(L.begin_ms)+'</span>'+
      '<span class="tm-txt">'+sec+esc(L.text)+'</span>'+
      '<span class="tm-n">'+(L.dur_ms!=null?((L.dur_ms/1000).toFixed(1)+'s'):'—')+'</span>'+
      '<span class="tm-n'+(flagged?' hot':'')+'">'+rate+'</span>'+
      '<span class="tm-chips">'+chips+'</span>'+
    '</div>'+
    '<div class="tm-x">'+(TM.open===L.i?tmExpand(L):'')+'</div></div>';
}
// mobile has no hover, so the title= tooltips never show — this is the tap-ⓘ
// version: a plain-language legend for every control on the line editor.
function tmHelpLegend(){
  const row=(k,v)=>'<div class="tm-hrow"><span class="tm-hk">'+k+'</span><span>'+v+'</span></div>';
  return '<div class="tm-help">'+
    row('▶ / ⏸','the round button by the karaoke line plays this line; tap again to pause, tap once more to keep going')+
    row('karaoke line','its words light up in time as the line (or a word) plays; the small text is romaji + the full kana reading. Tap a word to focus it.')+
    row('+ / –','zoom the waveform in and out')+
    row('fit word / fit line','frame the focused word, or the whole line')+
    row('voice only / full mix','show the isolated vocal or the full recording on the wave')+
    row('drag on the wave','a shaded edge resizes the line · a word marker moves the word · empty space slides')+
    row('▶ this word','hear the focused word — and watch it light up in the karaoke line')+
    row('▶ our recording','play our study clip for this word')+
    row('♪ held part','when a word\x27s last vowel is SUNG long (hodo…ooo), mark where the words end and the singing holds — tap ♪ then tap the wave. The 〜 dances while it\x27s sung.')+
    row('edit · add · delete','fix a word\x27s text or reading, slip in an ad-lib (hey), or drop a stray mark like 、 — the page text follows')+
    row('amber dot','a time you set by hand — kept through re-syncs')+
    row('sharpen waveform','rebuild the wave in finer detail for deep zoom')+
    '</div>';
}
function tmExpand(L){
  const adopt=(L.sources&&L.sources.lrclib)?'<button class="ctl run tm-adopt" data-tmadopt>⇤ match lyric source</button>':'';
  const wave=(TM.peaks&&TM.peaks.lanes)?
    ('<div class="tm-zoom">'+
       '<button class="tm-zb" data-tmzoom="out" title="zoom out">–</button>'+
       '<button class="tm-zb" data-tmzoom="in" title="zoom in">+</button>'+
       '<button class="tm-zb wide" data-tmzoom="word" title="frame the focused word">fit word</button>'+
       '<button class="tm-zb wide" data-tmzoom="line" title="frame the whole line">fit line</button>'+
       '<button class="tm-zb wide" data-tmlane title="switch between the isolated voice and the full recording">'+
         (TM.lane==='vocals'?'voice only':'full mix')+'</button>'+
       '<span class="tm-wvro" data-tmwvro></span>'+
       '<button class="tm-zb tm-i'+(TM.helpOpen?' on':'')+'" data-tmhelp title="what do these controls do?">ⓘ</button>'+
     '</div>'+
     (TM.helpOpen?tmHelpLegend():'')+
     '<div class="tm-wave"><canvas data-tmwv></canvas></div>'+
     '<div class="dek tm-wvhelp">'+(TM.holdArm
       ?'♪ tap the wave where the words end and the held vowel begins'
       :'tap the wave to hear from there · drag a word marker to move it · '+
        'drag a shaded edge to resize the line · drag empty space (or scroll) to slide · pinch/scroll-with-⌘ to zoom')+'</div>')
    :('<div class="tm-wvnote">'+esc((TM.peaks&&TM.peaks.error)||'reading the waveform…')+'</div>');
  const focusPanel=(TM.focus!=null&&L.words&&L.words[TM.focus])?tmFocus(L,TM.focus):'';
  return tmRevealHTML(L)+wave+focusPanel+
    (adopt?('<div class="tm-foot">'+adopt+'</div>'):'');
}
/* the inline karaoke reveal — the open line, right above the timeline. Each word
   is kanji (big) over its romaji; the words light up in real time off the shared
   playhead clock (tmRevealPaint), so the ▶/⏸ toggle and ▶ this word animate it
   against the CURRENT edited timings. The full kana reading sits underneath as kanji
   help. Rebuilt on every tmRender, so it always matches TM.data. */
function tmRevealHTML(L){
  const ws=L.words||[];
  const body=ws.length?ws.map((w,i)=>{
    // romaji: when one study word spans several tokens (かけ+てく share
    // "kaketeku"), the server sends rom_hl = THIS token's slice — bold it
    const rom=(w.study||[]).map(s=>{
      if(!s.rom)return '';
      const hl=s.rom_hl;
      if(hl&&hl.length===2&&hl[1]>hl[0])
        return esc(s.rom.slice(0,hl[0]))+'<b>'+esc(s.rom.slice(hl[0],hl[1]))+'</b>'+esc(s.rom.slice(hl[1]));
      return esc(s.rom);
    }).filter(Boolean).join(' ');
    const hold=(w.hold_ms!=null)?'<span class="tm-rvh" title="the vowel holds — it\x27s sung through '+tmss(w.end_ms)+'">〜</span>':'';
    const pin=w.override?'<i class="tm-pindot" title="hand-set"></i>':'';
    return '<span class="tm-rvw'+(TM.focus===i?' foc':'')+'" data-tmrvw="'+i+'" title="tap to focus this word">'+
      '<span class="tm-rvw-top"><span class="tm-rvk">'+esc(w.text)+'</span>'+hold+pin+'</span>'+
      '<span class="tm-rvr">'+rom+'</span></span>';
  }).join(''):'<span class="tm-rv-empty">no word timings on this line yet</span>';
  const kana=L.kana?('<div class="tm-rv-kana"><span class="lb">reads</span>'+esc(L.kana)+'</div>'):'';
  const playing=_tmAudio&&!_tmAudio.paused;
  return '<div class="tm-reveal'+(playing?' playing':'')+'" data-tmreveal>'+
    '<button class="tm-rvplay" data-tmrvplay title="play / pause this line">'+(playing?'⏸':'▶')+'</button>'+
    '<div class="tm-rv-main"><div class="tm-rv-line" data-tmrvline>'+body+'</div>'+kana+'</div></div>';
}
/* focus panel — one word up close: romaji, meaning, our recorded take(s). A
   timing token can hold more than one study word (ホラでも = ホラ + でも), so
   every contained study word is listed. Study coverage is partial by design;
   a token with none shows "no study word on file". */
function tmFocus(L,wi){
  const w=L.words[wi]; if(!w)return '';
  const study=w.study||[];
  const rows=study.length?study.map(s=>{
    const src=(s.provenance&&s.provenance.source)||'';
    const badge=s.clip_exists?('<span class="tm-take '+(src.indexOf('curated')===0||src.indexOf('nhk')===0?'good':src?'alt':'')+'">'+
      (src?esc(srcName(src)):'our take')+(s.pinned?' · pinned':'')+'</span>'):'<span class="tm-take none">no recording yet</span>';
    const play=s.clip_exists?('<button class="ctl tm-fplay" data-tmclip="'+esc(s.clip_url)+'">▶ our recording</button>'):'';
    return '<div class="tm-study">'+
      '<div class="tm-study-h"><b>'+esc(s.jp)+'</b> <span class="tm-rom">'+esc(s.rom)+'</span>'+
        (s.particle?' <span class="tm-part">particle</span>':'')+'</div>'+
      '<div class="tm-mean">'+esc(s.en||s.gloss||'')+'</div>'+
      '<div class="tm-study-b">'+badge+play+
        '<button class="ctl tm-fix" data-tmfix="'+esc(s.uid)+'" title="change the recording / tuning in the Words tab">fix the take →</button>'+
      '</div></div>';
  }).join(''):'<div class="tm-study empty">no study word on file for “'+esc(w.text)+'”. It still has its own timing — meaning + a recording live in the Words tab.</div>';
  // word tools: the held-part marker + the word-list edits (delete a stray 、,
  // add an ad-lib like hey, fix a token's text/reading)
  const hold=w.hold_ms!=null
    ?('<span class="tm-holdlab">♪ words end '+tmss(w.hold_ms)+' · vowel holds to '+tmss(w.end_ms)+'</span>'+
      '<button class="ctl" data-tmholdclear>un-mark</button>')
    :('<button class="ctl'+(TM.holdArm?' arm':'')+'" data-tmholdarm>'+
      (TM.holdArm?'now tap the wave…':'♪ mark the held part')+'</button>');
  const tools='<div class="tm-wtools">'+hold+
    '<span style="flex:1"></span>'+
    '<button class="ctl" data-tmwedit>✎ edit</button>'+
    '<button class="ctl" data-tmwadd>+ add a word</button>'+
    '<button class="ctl" data-tmwdel>× delete</button>'+
  '</div>';
  const f=TM.wform;
  let form='';
  if(f&&f.kind==='edit'){
    form='<div class="tm-wform" data-tmwform>'+
      '<label>word<input data-wf="text" value="'+esc(f.text!=null?f.text:w.text)+'"></label>'+
      '<label>its reading (kana)<input data-wf="reading" placeholder="auto" value="'+esc(f.reading!=null?f.reading:(w.kana||''))+'"></label>'+
      '<label class="wide-l">full line reading<input class="wide" data-wf="line_kana" placeholder="'+esc(L.kana||'—')+'" value="'+esc(f.line_kana!=null?f.line_kana:'')+'"></label>'+
      '<button class="ctl run" data-tmwsave>save</button>'+
      '<button class="ctl" data-tmwcancel>cancel</button></div>';
  }else if(f&&f.kind==='add'){
    form='<div class="tm-wform" data-tmwform>'+
      '<label>new word<input data-wf="text" placeholder="hey" value="'+esc(f.text||'')+'"></label>'+
      '<label>reading (kana, optional)<input data-wf="reading" value="'+esc(f.reading||'')+'"></label>'+
      '<label>where<select data-wf="where">'+
        '<option value="after"'+(f.where!=='before'?' selected':'')+'>after '+esc(w.text)+'</option>'+
        '<option value="before"'+(f.where==='before'?' selected':'')+'>before '+esc(w.text)+'</option>'+
      '</select></label>'+
      '<button class="ctl run" data-tmwsave>add it</button>'+
      '<button class="ctl" data-tmwcancel>cancel</button></div>';
  }
  return '<div class="tm-focus">'+
    '<div class="tm-focus-h"><span class="tm-focus-jp">'+esc(w.text)+'</span>'+
      '<span class="tm-focus-win">'+tmss(w.begin_ms)+' – '+tmss(w.end_ms)+'</span>'+
      '<span style="flex:1"></span>'+
      '<button class="ctl" data-tmwzoom title="zoom the waveform to this word">zoom to word</button>'+
      '<button class="ctl" data-tmplay="'+w.begin_ms+':'+w.end_ms+'">▶ this word</button>'+
    '</div>'+rows+tools+form+'</div>';
}
function srcName(s){s=s||'';
  if(s.indexOf('curated')===0)return 'curated';
  if(s.indexOf('nhk')===0)return 'NHK';
  if(s.indexOf('kokoro')===0)return 'standard voice';
  if(s.indexOf('qwen')===0||s.indexOf('aivis')===0||s.indexOf('google')===0)return 'fallback voice';
  return s.split('_')[0];}

/* ── the song's own audio (served once per song) — play a [begin,end] window,
   and clock the playhead off it while it runs. ── */
let _tmAudio=null,_tmClipKey=null,_tmClipBegin=0,_tmStudyAudio=null;
// Silence EVERY Timing-tab audio owner (the song clip AND a focus-panel study
// recording) and park the playhead + reveal. One stop path, so switching/closing
// a line or starting any new clip can never leave a stale clock or overlapping
// sound. Nulls _tmAudio so a play() interrupted mid-buffer rejects past the
// `_tmAudio!==a` guard (no false "couldn't play" toast on a stop tap).
function tmStopAll(){
  if(_tmAudio){try{_tmAudio.pause()}catch(e){} _tmAudio=null}
  if(_tmStudyAudio){try{_tmStudyAudio.pause()}catch(e){} _tmStudyAudio=null}
  tmPlayheadStop();
}
// Play the [bms,ems] window as a small FROM-THE-START clip (no seeking — iOS
// Safari can't seek a network WAV). The clip ends by itself, so there's no
// stop-watchdog; the playhead maps the clip's 0-based time back onto the song
// via _tmClipBegin, which also drives the inline karaoke reveal.
function tmPlay(bms,ems){
  const ckey=TM.key+':'+Math.round(bms)+':'+Math.round(ems);
  if(_tmAudio&&_tmClipKey===ckey&&!_tmAudio.paused){tmStopAll();return}  // same window re-tap = stop
  tmStopAll();                              // silence any other clip / recording first
  _tmClipKey=ckey; _tmClipBegin=bms;
  const a=new Audio('/api/songclip/'+encodeURIComponent(TM.key)+
    '?b='+Math.round(bms)+'&e='+Math.round(ems));
  _tmAudio=a;
  a.onerror=()=>{if(_tmAudio!==a)return;
    toast('the song audio for this one isn\x27t on this Mac yet');_tmAudio=null;tmPlayheadStop()};
  a.onended=()=>{if(_tmAudio===a){_tmAudio=null;_tmClipKey=null;tmPlayheadStop()}};
  a.play().then(()=>tmPlayheadStart()).catch(err=>{
    if(_tmAudio!==a||(err&&err.name==='AbortError'))return;   // superseded / stopped mid-buffer
    toast(err&&err.name==='NotAllowedError'
      ?'the phone blocked that tap — tap ▶ once more'
      :'couldn\x27t play the song audio')});
}
// the round ▶/⏸ by the karaoke line: play the whole open line; tap again to
// PAUSE (the clip keeps its place); tap once more to carry on. A pause parks
// the playhead loop by itself (it's gated on !paused).
function tmRevToggle(){
  const L=wvLine(); if(!L)return;
  if(_tmAudio&&!_tmAudio.paused){_tmAudio.pause();return}
  // resume ONLY a paused mid-flight clip of THIS line's window — a naturally
  // ended clip, or a word/overview clip, must not replay under the line button
  const lineKey=TM.key+':'+Math.round(L.begin_ms)+':'+Math.round(L.end_ms);
  if(_tmAudio&&_tmAudio.paused&&!_tmAudio.ended&&_tmClipKey===lineKey){
    const a=_tmAudio;
    a.play().then(()=>{if(_tmAudio===a)tmPlayheadStart()}).catch(err=>{
      if(_tmAudio!==a||(err&&err.name==='AbortError'))return;
      toast('the phone blocked that tap — tap ▶ once more')});
    return;
  }
  tmPlay(L.begin_ms,L.end_ms);
}
// play a single study clip directly (bypasses the song wav) — the focus panel.
// Tracked in _tmStudyAudio so the next play/stop can silence it (else two
// recordings, or a recording + the line clip, would overlap).
function tmClip(url){
  tmStopAll();
  const a=new Audio(url); _tmStudyAudio=a;
  a.play().catch(()=>{if(_tmStudyAudio===a)toast('couldn\x27t play that recording')});
}

/* ── the playhead: a moving cursor on the wave + overview while audio plays.
   rAF, gated on !paused (no idle timer — LESSONS). Re-queries the canvas each
   frame so a drawer re-render never strands a stale ref; auto-pans when the
   cursor leaves the zoom window. ── */
let _tmRaf=null;
function tmPlayheadStart(){if(_tmRaf)return;const loop=()=>{
  if(!_tmAudio||_tmAudio.paused){_tmRaf=null;wvDraw();ovDraw();tmRevealIdle();return}
  const ms=_tmClipBegin+_tmAudio.currentTime*1000;      // clip time → absolute song ms
  if(TM.view&&!WVDRAG.mode){                          // keep the cursor in view
    const {wb,span}=TM.view;
    if(ms<wb+span*0.04||ms>wb+span*0.96){TM.view={wb:Math.max(0,ms-span*0.4),span};}
  }
  wvDraw(ms);ovDraw(ms);tmRevealPaint(ms);_tmRaf=requestAnimationFrame(loop);
};_tmRaf=requestAnimationFrame(loop)}
function tmPlayheadStop(){if(_tmRaf){cancelAnimationFrame(_tmRaf);_tmRaf=null}
  wvDraw();ovDraw();tmRevealIdle()}

/* ── the inline karaoke reveal, painted off the playhead's absolute song-ms.
   Each open-line word is past / now / future; the CURRENT word wipes left→right
   (--p 0→100%) for a real karaoke fill. Reads word times from TM.data live, so
   it always tracks the edited timings. Idle = every word fully lit (readable). ── */
let _tmRvNow=-1;
function tmRevealPaint(ms){
  const rev=document.querySelector('[data-tmreveal]'); if(!rev)return;
  const L=wvLine(); if(!L||!L.words)return;
  rev.classList.add('playing');
  const pb=rev.querySelector('[data-tmrvplay]'); if(pb)pb.textContent='⏸';
  const line=rev.querySelector('[data-tmrvline]');
  rev.querySelectorAll('[data-tmrvw]').forEach(sp=>{
    const wi=+sp.getAttribute('data-tmrvw'), w=L.words[wi]; if(!w)return;
    // a held word: the LEXICAL wipe completes at hold_ms, then the 〜 dances
    // (class .holding) while the vowel is sung out to end_ms
    const hold=(w.hold_ms!=null&&w.hold_ms>w.begin_ms&&w.hold_ms<w.end_ms)?w.hold_ms:null;
    let cls='future', p=0;
    if(ms>=w.end_ms){cls='past';p=100;}
    else if(ms>=w.begin_ms){
      const capEnd=hold||w.end_ms;
      p=Math.max(0,Math.min(100,(ms-w.begin_ms)/Math.max(1,capEnd-w.begin_ms)*100));
      cls=(hold&&ms>=hold)?'now holding':'now';
    }
    sp.className='tm-rvw '+cls;
    const k=sp.querySelector('.tm-rvk'); if(k)k.style.setProperty('--p',p.toFixed(1)+'%');
    if(cls.indexOf('now')===0&&line&&_tmRvNow!==wi){  // keep the current word centred (h-scroll only)
      _tmRvNow=wi; line.scrollLeft=sp.offsetLeft-line.clientWidth/2+sp.offsetWidth/2;}
  });
}
function tmRevealIdle(){
  const rev=document.querySelector('[data-tmreveal]'); if(!rev)return;
  rev.classList.remove('playing'); _tmRvNow=-1;
  const pb=rev.querySelector('[data-tmrvplay]'); if(pb)pb.textContent='▶';
  rev.querySelectorAll('[data-tmrvw]').forEach(sp=>{
    sp.className='tm-rvw'+(+sp.getAttribute('data-tmrvw')===TM.focus?' foc':'');
    const k=sp.querySelector('.tm-rvk'); if(k)k.style.removeProperty('--p');
  });
  tmRevealCenter();       // playback parked — bring the word being worked on back
}
// keep the FOCUSED word front and center whenever the reveal is idle — every
// re-render rebuilds the scroller at scrollLeft 0, so without this a word
// deep in a long line walks out of view on each saved nudge
function tmRevealCenter(){
  if(TM.focus==null)return;
  if(_tmAudio&&!_tmAudio.paused)return;   // the playing word owns the scroll
  const line=document.querySelector('[data-tmrvline]'); if(!line)return;
  const sp=line.querySelector('[data-tmrvw="'+TM.focus+'"]'); if(!sp)return;
  line.scrollLeft=sp.offsetLeft-line.clientWidth/2+sp.offsetWidth/2;
}

/* ── the full-song overview strip: mix-lane mini waveform + section bands +
   line ticks + the open window + the playhead. Click = hear from there;
   drag = slide the open line's zoom window along the song. ── */
function secHue(id){let h=0;for(const c of String(id||''))h=(h*31+c.charCodeAt(0))%360;return h}
function tmDuration(){return (TM.peaks&&TM.peaks.duration_ms)||(TM.data&&TM.data.duration_ms)||
  (TM.data&&TM.data.lines&&TM.data.lines.length?TM.data.lines[TM.data.lines.length-1].end_ms:0)||1}
function ovDraw(ph){
  const cv=document.querySelector('[data-tmov]');if(!cv)return;
  const dpr=window.devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;if(!W)return;
  if(cv.width!==Math.round(W*dpr)){cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr)}
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,W,H);
  const dur=tmDuration(),X=ms=>ms/dur*W,BAND=13;
  // section bands (runs of consecutive lines sharing a section)
  const lines=(TM.data&&TM.data.lines)||[];
  let i=0;
  while(i<lines.length){
    const sid=lines[i].section&&lines[i].section.id;let j=i;
    while(j+1<lines.length&&((lines[j+1].section&&lines[j+1].section.id)===sid))j++;
    if(sid){const x0=X(lines[i].begin_ms),x1=X(lines[j].end_ms);
      ctx.fillStyle='hsla('+secHue(sid)+',55%,55%,.30)';ctx.fillRect(x0,0,Math.max(1,x1-x0),BAND);
      ctx.fillStyle='hsla('+secHue(sid)+',60%,72%,.95)';ctx.font='9px '+MONO;ctx.textBaseline='middle';
      const lbl=(lines[i].section.short_name||'');if(x1-x0>14)ctx.fillText(lbl,x0+3,BAND/2);}
    i=j+1;
  }
  // mini waveform (mix lane, decimated)
  const lane=(TM.peaks&&TM.peaks.lanes&&(TM.peaks.lanes.mix||TM.peaks.lanes.vocals))||[];
  const bm=(TM.peaks&&TM.peaks.bin_ms)||10,mid=BAND+(H-BAND)/2,half=(H-BAND)/2-2;
  ctx.fillStyle='#33584a';
  for(let px=0;px<W;px++){
    const m0=px/W*dur,m1=(px+1)/W*dur;
    const a=Math.max(0,Math.floor(m0/bm)),bI=Math.min(lane.length,Math.max(a+1,Math.ceil(m1/bm)));
    let mn=0,mx=0;for(let k=a;k<bI;k++){const p=lane[k];if(p[0]<mn)mn=p[0];if(p[1]>mx)mx=p[1]}
    ctx.fillRect(px,mid-mx/127*half,1,Math.max(1,(mx-mn)/127*half));
  }
  // the open line's window
  const L=wvLine();
  if(L){ctx.strokeStyle='#7dd6a8';ctx.lineWidth=1.5;
    ctx.strokeRect(X(L.begin_ms),BAND+1,Math.max(2,X(L.end_ms)-X(L.begin_ms)),H-BAND-2)}
  // playhead
  if(ph!=null){const x=X(ph);ctx.fillStyle='#ffd23f';ctx.fillRect(x-1,0,2,H)}
}
function ovBind(){
  const cv=document.querySelector('[data-tmov]');if(!cv)return;
  const at=e=>{const r=cv.getBoundingClientRect();return Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*tmDuration()};
  let dragging=false,moved=false,startX=0;
  cv.onpointerdown=e=>{dragging=true;moved=false;startX=e.clientX;
    try{cv.setPointerCapture(e.pointerId)}catch(err){}
    const ms=at(e);if(TM.view){const sp=TM.view.span;TM.view={wb:Math.max(0,ms-sp/2),span:sp};wvClamp();wvDraw()}
    ovDraw();e.preventDefault()};
  cv.onpointermove=e=>{if(!dragging)return;
    if(Math.abs(e.clientX-startX)>4)moved=true;
    const ms=at(e);
    if(TM.view){const sp=TM.view.span;TM.view={wb:Math.max(0,ms-sp/2),span:sp};wvClamp();wvDraw()}ovDraw()};
  // a still TAP auditions from there; a drag just slides the window (no audio)
  cv.onpointerup=e=>{if(!dragging)return;dragging=false;
    if(!moved)tmPlay(Math.round(at(e)),tmDuration())};
  cv.onpointercancel=()=>{dragging=false};
}

/* ── the waveform lane: zoomable, pannable, with a playhead and a focused
   word. Zone-typed single-pointer (one model on trackpad + touch): rail edge =
   resize the line; near a marker = move that word; empty wave = pan (or tap =
   audition). Zoom via the buttons, ⌘/ctrl-scroll, or pinch. ── */
const WV={RAIL:26,HIT_W:22,HIT_E:26,PAD:1500,MINSPAN:250};
const WVDRAG={mode:null,wi:-1,ms:0,moved:false,startX:0,panWb:0};
const _tmPtr=new Map();
function wvLine(){return (TM.data&&TM.data.lines||[]).find(x=>x.i===TM.open)||null}
function wvInitView(L){
  const dur=tmDuration();
  const wb=Math.max(0,(L.begin_ms||0)-WV.PAD);
  const we=Math.min(dur,(L.end_ms||0)+WV.PAD);
  TM.view={wb,span:Math.max(we-wb,WV.MINSPAN)};
}
function wvClamp(){
  if(!TM.view)return;const dur=tmDuration();
  TM.view.span=Math.max(WV.MINSPAN,Math.min(TM.view.span,dur));
  TM.view.wb=Math.max(0,Math.min(TM.view.wb,dur-TM.view.span));
}
function wvWin(L){if(!TM.view)wvInitView(L);return [TM.view.wb,TM.view.wb+TM.view.span]}
function wvReadout(txt){const r=document.querySelector('[data-tmwvro]');if(r)r.textContent=txt}
function wvZoom(factor,centerMs){
  const L=wvLine();if(!L)return;if(!TM.view)wvInitView(L);
  const c=centerMs!=null?centerMs:(TM.view.wb+TM.view.span/2);
  const frac=(c-TM.view.wb)/TM.view.span;
  TM.view.span=TM.view.span*factor;
  TM.view.wb=c-frac*TM.view.span;
  wvClamp();wvDraw();
}
function wvDraw(ph){
  const cv=document.querySelector('[data-tmwv]'),L=wvLine();
  if(!cv||!L||!(TM.peaks&&TM.peaks.lanes))return;
  if(!TM.view)wvInitView(L);
  const dpr=window.devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;
  if(!W)return;
  if(cv.width!==Math.round(W*dpr)){cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr)}
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,W,H);
  const[wb,we]=wvWin(L),span=we-wb,X=ms=>(ms-wb)/span*W;
  let lb=L.begin_ms,le=L.end_ms;
  if(WVDRAG.mode==='b')lb=WVDRAG.ms; if(WVDRAG.mode==='e')le=WVDRAG.ms;
  ctx.fillStyle=L.override==='line'?'rgba(251,191,36,.09)':'rgba(125,214,168,.08)';
  ctx.fillRect(X(lb),0,Math.max(1,X(le)-X(lb)),H);
  ctx.fillStyle='rgba(255,255,255,.035)';ctx.fillRect(0,0,W,WV.RAIL);
  const lane=TM.peaks.lanes[TM.lane]||TM.peaks.lanes.mix||[],bm=TM.peaks.bin_ms||10;
  const mid=WV.RAIL+(H-WV.RAIL)/2,half=(H-WV.RAIL)/2-4;
  for(let px=0;px<W;px++){
    const m0=wb+px/W*span,m1=wb+(px+1)/W*span;
    const i0=Math.max(0,Math.floor(m0/bm)),i1=Math.min(lane.length,Math.max(i0+1,Math.ceil(m1/bm)));
    let mn=0,mx=0;
    for(let i=i0;i<i1;i++){const p=lane[i];if(p[0]<mn)mn=p[0];if(p[1]>mx)mx=p[1]}
    const inWin=m0>=lb&&m0<=le;
    ctx.fillStyle=inWin?'#5fae87':'#33584a';
    const y0=mid-mx/127*half,y1=mid-mn/127*half;
    ctx.fillRect(px,y0,1,Math.max(1,y1-y0));
  }
  const edge=(ms,drag)=>{const x=X(ms);
    ctx.fillStyle=drag?'#efeae2':'#7dd6a8';
    ctx.fillRect(x-1,0,2,H);
    ctx.fillRect(x-6,2,12,WV.RAIL-6);
    ctx.fillStyle='#101318';ctx.fillRect(x-1,5,2,WV.RAIL-12)};
  edge(lb,WVDRAG.mode==='b');edge(le,WVDRAG.mode==='e');
  // word markers only — the words themselves live in the karaoke line above
  // (tap one there to focus; its marker turns amber here)
  (L.words||[]).forEach((w,i)=>{
    const drag=WVDRAG.mode==='w'&&WVDRAG.wi===i;
    const ms=drag?WVDRAG.ms:w.begin_ms;
    if(ms<wb-1||ms>we+1)return;
    const x=X(ms),anch=!!w.override,foc=TM.focus===i;
    ctx.fillStyle=drag?'#efeae2':foc?'#ffd23f':anch?'#fbbf24':'#7dd6a8';
    ctx.fillRect(x-(drag||foc?1.5:0.75),WV.RAIL,drag||foc?3:1.5,H-WV.RAIL);
  });
  // held-vowel markers (♪): a notched line inside the word's window
  (L.words||[]).forEach((w,i)=>{
    const drag=WVDRAG.mode==='h'&&WVDRAG.wi===i;
    const hm=drag?WVDRAG.ms:w.hold_ms;
    if(hm==null||hm<wb-1||hm>we+1)return;
    const x=X(hm),foc=TM.focus===i;
    ctx.fillStyle=drag?'#efeae2':'#e8b04a';
    ctx.fillRect(x-(drag||foc?1.25:0.75),WV.RAIL,drag||foc?2.5:1.5,H-WV.RAIL);
    ctx.beginPath();                              // the ♪ notch
    ctx.moveTo(x,WV.RAIL+6);ctx.lineTo(x-4,WV.RAIL);ctx.lineTo(x+4,WV.RAIL);
    ctx.closePath();ctx.fill();
  });
  if(ph!=null&&ph>=wb&&ph<=we){const x=X(ph);ctx.fillStyle='#ffd23f';ctx.fillRect(x-1,0,2,H)}
  if(!WVDRAG.mode&&ph==null)wvReadout(tmss(wb)+' – '+tmss(we)+'  ·  '+((span/1000).toFixed(1))+'s wide');
}
function wvBind(){
  const cv=document.querySelector('[data-tmwv]'); if(!cv)return;
  WVDRAG.mode=null; _tmPtr.clear();
  if(TM.open!=null&&!TM.view){const L=wvLine();if(L)wvInitView(L)}
  wvDraw();
  cv.onpointerdown=e=>{
    const L=wvLine(); if(!L||TM.busy)return;
    _tmPtr.set(e.pointerId,e.clientX);
    if(_tmPtr.size===2){                 // second finger → abandon edit, pinch
      WVDRAG.mode='pinch';WVDRAG.pinch=_pinchState();return}
    const r=cv.getBoundingClientRect(),px=e.clientX-r.left,py=e.clientY-r.top;
    const[wb,we]=wvWin(L),span=we-wb,X=ms=>(ms-wb)/span*r.width;
    const at=()=>Math.round(wb+Math.max(0,Math.min(1,px/r.width))*span);
    WVDRAG.moved=false;WVDRAG.wi=-1;WVDRAG.startX=px;WVDRAG.panWb=wb;
    if(py<=WV.RAIL){
      if(Math.abs(px-X(L.begin_ms))<=WV.HIT_E){WVDRAG.mode='b';WVDRAG.ms=L.begin_ms}
      else if(Math.abs(px-X(L.end_ms))<=WV.HIT_E){WVDRAG.mode='e';WVDRAG.ms=L.end_ms}
      else{WVDRAG.mode='pan';WVDRAG.ms=at()}
    }else{
      // the focused word's ♪ hold marker wins the grab when it's closest
      const fw=(TM.focus!=null&&L.words)?L.words[TM.focus]:null;
      const hd=(fw&&fw.hold_ms!=null)?Math.abs(px-X(fw.hold_ms)):1e9;
      let best=-1,bd=WV.HIT_W+1;
      (L.words||[]).forEach((w,i)=>{const d=Math.abs(px-X(w.begin_ms));if(d<bd){bd=d;best=i}});
      if(hd<=WV.HIT_W&&hd<=bd){WVDRAG.mode='h';WVDRAG.wi=TM.focus;WVDRAG.ms=fw.hold_ms}
      else if(best>=0){WVDRAG.mode='w';WVDRAG.wi=best;WVDRAG.ms=L.words[best].begin_ms}
      else{WVDRAG.mode='pan';WVDRAG.ms=at()}
    }
    try{cv.setPointerCapture(e.pointerId)}catch(err){}
    e.preventDefault();wvDraw();
    if(WVDRAG.mode==='w')wvReadout(L.words[WVDRAG.wi].text+' · '+tmss(WVDRAG.ms));
    if(WVDRAG.mode==='h')wvReadout('♪ holds from '+tmss(WVDRAG.ms));
    if(WVDRAG.mode==='b'||WVDRAG.mode==='e')wvReadout((WVDRAG.mode==='b'?'starts ':'ends ')+tmss(WVDRAG.ms));
  };
  cv.onpointermove=e=>{
    if(!WVDRAG.mode)return;
    if(_tmPtr.has(e.pointerId))_tmPtr.set(e.pointerId,e.clientX);
    const L=wvLine(); if(!L)return;
    if(WVDRAG.mode==='pinch'){_pinchApply();return}
    const r=cv.getBoundingClientRect(),px=e.clientX-r.left;
    const[wb,we]=wvWin(L),span=we-wb;
    if(Math.abs(px-WVDRAG.startX)>4)WVDRAG.moved=true;
    if(WVDRAG.mode==='pan'){
      const dx=(px-WVDRAG.startX)/r.width*span;
      TM.view.wb=WVDRAG.panWb-dx;wvClamp();wvDraw();ovDraw();return}
    const ms=Math.round(wb+Math.max(0,Math.min(1,px/r.width))*span);
    WVDRAG.ms=ms;wvDraw();
    if(WVDRAG.mode==='w')wvReadout(L.words[WVDRAG.wi].text+' · '+tmss(ms));
    else if(WVDRAG.mode==='h')wvReadout('♪ holds from '+tmss(ms));
    else wvReadout((WVDRAG.mode==='b'?'starts ':'ends ')+tmss(ms));
  };
  cv.onpointerup=e=>{
    _tmPtr.delete(e.pointerId);
    const m=WVDRAG.mode,L=wvLine();
    if(m==='pinch'){
      if(_tmPtr.size){                     // one finger left → resume PAN from IT,
        const r=cv.getBoundingClientRect();  // re-seeding anchors so it doesn't jump
        WVDRAG.mode='pan';WVDRAG.moved=true; // (a continuation, not a fresh tap)
        WVDRAG.startX=[..._tmPtr.values()][0]-r.left;
        WVDRAG.panWb=TM.view?TM.view.wb:0;
      }else WVDRAG.mode=null;
      return}
    if(!m||!L){WVDRAG.mode=null;return}
    if(m==='pan'){
      WVDRAG.mode=null;
      if(!WVDRAG.moved){
        // armed "mark the held part": this tap places the ♪ inside the
        // focused word instead of auditioning
        if(TM.holdArm&&TM.focus!=null&&L.words&&L.words[TM.focus]){
          const w=L.words[TM.focus];
          if(WVDRAG.ms>w.begin_ms&&WVDRAG.ms<w.end_ms){TM.holdArm=false;tmHold(L,TM.focus,WVDRAG.ms);return}
          toast('tap inside “'+w.text+'” ('+tmss(w.begin_ms)+'–'+tmss(w.end_ms)+')');
          return;
        }
        tmPlay(WVDRAG.ms,wvWin(L)[1]);                  // a still tap = audition from here
      }
      wvDraw();return}
    if(m==='h'){
      const w=L.words[WVDRAG.wi];
      if(WVDRAG.moved&&w&&WVDRAG.ms!==w.hold_ms){tmHold(L,WVDRAG.wi,WVDRAG.ms);return}
      WVDRAG.mode=null;wvDraw();return;
    }
    if(m==='w'){
      const w=L.words[WVDRAG.wi];
      if(WVDRAG.moved&&WVDRAG.ms!==w.begin_ms){tmWordSet(L,WVDRAG.wi,WVDRAG.ms);return}
      WVDRAG.mode=null;
      // a focus CHANGE closes an open form + disarms ♪ — else text typed for
      // word A would save onto word B, and an armed hold would retarget
      if(TM.focus!==WVDRAG.wi){TM.holdArm=false;TM.wform=null}
      TM.focus=WVDRAG.wi;                                // a tap on a marker focuses it
      if(tmVisible())tmRender($('tabbody'));
      return;
    }
    if(WVDRAG.moved){tmSet(L,m==='b'?WVDRAG.ms:L.begin_ms,m==='e'?WVDRAG.ms:L.end_ms);return}
    WVDRAG.mode=null;wvDraw();
  };
  cv.onpointercancel=e=>{_tmPtr.delete(e.pointerId);WVDRAG.mode=null;wvDraw()};
  cv.onwheel=e=>{
    const L=wvLine();if(!L)return;
    if(e.ctrlKey||e.metaKey){                            // ⌘/pinch = zoom at cursor
      e.preventDefault();
      const r=cv.getBoundingClientRect(),[wb,we]=wvWin(L);
      const c=wb+Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*(we-wb);
      wvZoom(e.deltaY>0?1.15:0.87,c);ovDraw();
    }else if(Math.abs(e.deltaX)>Math.abs(e.deltaY)){     // horizontal scroll = pan
      e.preventDefault();
      const span=TM.view.span;TM.view.wb+=e.deltaX/cv.clientWidth*span;wvClamp();wvDraw();ovDraw();
    }                                                    // vertical scroll → page
  };
}
function _pinchState(){const xs=[..._tmPtr.values()];return {d:Math.abs(xs[0]-xs[1])||1,view:{...TM.view}}}
function _pinchApply(){const xs=[..._tmPtr.values()];if(xs.length<2)return;
  const d=Math.abs(xs[0]-xs[1])||1,p=WVDRAG.pinch;
  const cv=document.querySelector('[data-tmwv]');if(!cv)return;
  const r=cv.getBoundingClientRect(),cx=(xs[0]+xs[1])/2-r.left;
  const c=p.view.wb+Math.max(0,Math.min(1,cx/r.width))*p.view.span;
  const frac=(c-p.view.wb)/p.view.span;
  TM.view.span=Math.max(WV.MINSPAN,p.view.span*(p.d/d));
  TM.view.wb=c-frac*TM.view.span;wvClamp();wvDraw();ovDraw()}
window.addEventListener('resize',()=>{if(document.querySelector('[data-tmwv]')){wvDraw();ovDraw()}});

async function tmWordSet(L,wi,ms){
  if(TM.busy)return; TM.busy=true;
  wvReadout('saving…');
  try{const j=await api('/api/timing/word',{key:TM.key,line:L.i,word:wi,begin_ms:Math.round(ms)});
    const out=(j.output||'').trim().split('\n');
    toast(out[out.length-1]||'word moved');
    TM.busy=false; tmApply(j.timing); return;
  }catch(e){}
  TM.busy=false; tmFetch();
}
async function tmSet(L,nb,ne){
  if(TM.busy)return; TM.busy=true;
  try{const j=await api('/api/timing/set',{key:TM.key,line:L.i,begin_ms:Math.round(nb),end_ms:Math.round(ne)});
    TM.busy=false; tmApply(j.timing); return;
  }catch(e){}
  TM.busy=false; tmFetch();
}
// mark / move / clear the held-vowel point of one word (♪)
async function tmHold(L,wi,ms){
  if(TM.busy)return; TM.busy=true;
  wvReadout('saving…');
  try{const j=await api('/api/timing/hold',{key:TM.key,line:L.i,word:wi,at_ms:Math.round(ms)});
    toast('held part marked — the 〜 dances while it\x27s sung');
    TM.busy=false; tmApply(j.timing); return;
  }catch(e){}
  TM.busy=false; tmFetch();
}
async function tmHoldClear(L,wi){
  if(TM.busy)return; TM.busy=true;
  try{const j=await api('/api/timing/hold',{key:TM.key,line:L.i,word:wi,clear:true});
    TM.busy=false; tmApply(j.timing); return;
  }catch(e){}
  TM.busy=false; tmFetch();
}
// word-list edits: delete a token / add one / change text+reading. The box
// keeps line text ↔ tokens coherent and mirrors the page's content file.
async function tmWordOp(path,body,doneMsg){
  if(TM.busy)return; TM.busy=true;
  try{const j=await api(path,Object.assign({key:TM.key},body));
    const out=(j.output||'').trim().split('\n');
    toast(doneMsg||out[out.length-1]||'done');
    TM.wform=null; TM.pendingFetch=false; TM.busy=false; tmApply(j.timing); return;
  }catch(e){}                       // api() already toasted the box's reason
  TM.busy=false; tmFetch();
}


function tmRender(el){
  const d=TM.data||{}, lines=d.lines||[], med=d.median_delta_ms;
  el.innerHTML=
    tmStateStrip()+
    tmAlignWarn()+
    '<div class="tm-head">'+
      '<span class="tm-med">'+(med!=null?('vs lyric source · median <b>'+signms(med)+'</b>'):'no lyric-source match for this song')+'</span>'+
      '<span style="flex:1"></span>'+
      ((d.state&&d.state.has_corpus&&d.state.built)?
        '<button class="ctl tm-sharpen" data-tmsharpen'+(JOB.active||(TM.peaks&&TM.peaks.bin_ms<=2)?' disabled':'')+
          ' title="rebuild the waveform at a finer resolution so zoom-in stays sharp">sharpen waveform</button>':'')+
    '</div>'+
    (lines.length?
      ('<div class="tm-ovwrap"><canvas data-tmov></canvas></div>'+
       '<div class="tm-cols"><span>starts</span><span>line</span><span style="text-align:right">len</span>'+
        '<span style="text-align:right">pace</span><span style="text-align:right">checks</span></div>'+
       '<div class="tm-table">'+lines.map(tmLine).join('')+'</div>')
      :'<div class="empty">no timed lines yet — grab the lyrics first.</div>');
  el.querySelectorAll('[data-tmrow]').forEach(r=>r.onclick=e=>{
    if(e.target.closest('.ctl'))return;
    const i=+r.parentElement.getAttribute('data-i');
    const open=TM.open===i?null:i;
    TM.open=open; TM.view=null; TM.focus=null;          // fresh line → fresh zoom
    TM.holdArm=false; TM.wform=null;
    tmStopAll();      // any line change silences the current clip (its clock belongs to the OLD line)
    if(TM.pendingFetch)return tmRunPending();
    tmRender(el)});
  // state-ladder action (apply changes / put online / put on main page)
  el.querySelectorAll('[data-tmact]').forEach(btn=>btn.onclick=()=>tmAct(btn.getAttribute('data-tmact')));
  const sh=el.querySelector('[data-tmsharpen]');
  if(sh)sh.onclick=async()=>{try{await api('/api/peaks/build',{key:TM.key});toast('sharpening the waveform…')}catch(e){}pollState(true)};
  const al=el.querySelector('[data-tmalign]');
  if(al)al.onclick=async()=>{try{await api('/api/run',{key:TM.key,step:'whisper_sync'});
    toast('lining the words up with the singing — this takes a few minutes')}catch(e){}pollState(true)};
  el.querySelectorAll('[data-tmplay]').forEach(x=>x.onclick=e=>{e.stopPropagation();
    const[b,ee]=x.getAttribute('data-tmplay').split(':');tmPlay(+b,+ee)});
  el.querySelectorAll('[data-tmrvplay]').forEach(x=>x.onclick=e=>{e.stopPropagation();tmRevToggle()});
  el.querySelectorAll('[data-tmclip]').forEach(x=>x.onclick=e=>{e.stopPropagation();tmClip(x.getAttribute('data-tmclip'))});
  // tapping a word in the karaoke line focuses it
  el.querySelectorAll('[data-tmrvw]').forEach(x=>x.onclick=e=>{e.stopPropagation();
    const i=+x.getAttribute('data-tmrvw');TM.focus=(TM.focus===i?null:i);
    TM.holdArm=false;TM.wform=null;
    if(TM.pendingFetch)return tmRunPending();
    tmRender(el)});
  // word tools: held part + edit / add / delete
  const L0=wvLine();
  const ha=el.querySelector('[data-tmholdarm]');
  if(ha)ha.onclick=()=>{TM.holdArm=!TM.holdArm;tmRender(el)};
  const hc=el.querySelector('[data-tmholdclear]');
  if(hc)hc.onclick=()=>{if(L0&&TM.focus!=null)tmHoldClear(L0,TM.focus)};
  const we=el.querySelector('[data-tmwedit]');
  if(we)we.onclick=()=>{TM.wform=(TM.wform&&TM.wform.kind==='edit')?null:{kind:'edit'};TM.holdArm=false;tmRender(el)};
  const wa=el.querySelector('[data-tmwadd]');
  if(wa)wa.onclick=()=>{TM.wform=(TM.wform&&TM.wform.kind==='add')?null:{kind:'add',where:'after'};TM.holdArm=false;tmRender(el)};
  const wd=el.querySelector('[data-tmwdel]');
  if(wd)wd.onclick=()=>{
    if(!L0||TM.focus==null||!L0.words[TM.focus])return;
    const w=L0.words[TM.focus],li=L0.i,wi=TM.focus;
    tmConfirm('Delete “'+w.text+'”?',
      'It comes off this line — the page text too. Its sliver of time folds '+
      'into the word next to it.',
      'Delete it',()=>{TM.focus=null;tmWordOp('/api/timing/worddel',{line:li,word:wi},'“'+w.text+'” deleted')});
  };
  // the word form (edit / add): inputs keep their values in TM.wform so a
  // background poll re-render can't eat what's being typed
  el.querySelectorAll('[data-tmwform] [data-wf]').forEach(inp=>{
    inp.oninput=()=>{if(TM.wform)TM.wform[inp.getAttribute('data-wf')]=inp.value};
    inp.onchange=inp.oninput;
  });
  const ws=el.querySelector('[data-tmwsave]');
  if(ws)ws.onclick=()=>{
    const f=TM.wform; if(!f||!L0||TM.focus==null)return;
    const wi=TM.focus,li=L0.i;
    if(f.kind==='add'){
      const t=(f.text||'').trim(); if(!t){toast('type the new word first');return}
      const body={line:li,word:wi,text:t,where:f.where==='before'?'before':'after'};
      if((f.reading||'').trim())body.reading=f.reading.trim();
      tmWordOp('/api/timing/wordadd',body,'“'+t+'” added — drag its marker to fit');
    }else{
      const w=L0.words[wi],body={line:li,word:wi};
      const t=(f.text!=null?f.text:w.text).trim();
      if(t&&t!==w.text)body.text=t;
      const rd=(f.reading!=null?f.reading:(w.kana||'')).trim();
      if(rd!==(w.kana||''))body.reading=rd;
      if((f.line_kana||'').trim())body.line_kana=f.line_kana.trim();
      if(!body.text&&body.reading==null&&!body.line_kana){toast('nothing changed');return}
      tmWordOp('/api/timing/wordedit',body,'word updated');
    }
  };
  const wc=el.querySelector('[data-tmwcancel]');
  if(wc)wc.onclick=()=>{TM.wform=null;if(TM.pendingFetch)return tmRunPending();tmRender(el)};
  el.querySelectorAll('[data-tmzoom]').forEach(x=>x.onclick=()=>{
    const k=x.getAttribute('data-tmzoom'),L=wvLine();if(!L)return;
    if(k==='in')wvZoom(0.6);else if(k==='out')wvZoom(1.7);
    else if(k==='line')wvInitView(L),wvClamp(),wvDraw(),ovDraw();
    else if(k==='word'){const w=(TM.focus!=null&&L.words)?L.words[TM.focus]:null;
      if(w){const pad=Math.max(150,(w.end_ms-w.begin_ms)*0.6);TM.view={wb:Math.max(0,w.begin_ms-pad),span:(w.end_ms-w.begin_ms)+pad*2};wvClamp();wvDraw();ovDraw()}
      else toast('tap a word first, then “fit word”')}});
  const wz=el.querySelector('[data-tmwzoom]');
  if(wz)wz.onclick=()=>{const L=wvLine();if(!L||TM.focus==null)return;const w=L.words[TM.focus];
    const pad=Math.max(150,(w.end_ms-w.begin_ms)*0.6);TM.view={wb:Math.max(0,w.begin_ms-pad),span:(w.end_ms-w.begin_ms)+pad*2};wvClamp();wvDraw();ovDraw()};
  el.querySelectorAll('[data-tmfix]').forEach(x=>x.onclick=()=>{
    const uid=x.getAttribute('data-tmfix');
    WD.pendingFilter=uid;location.hash='#song/'+TM.key+'/words';});
  const ad=el.querySelector('[data-tmadopt]');
  if(ad)ad.onclick=async()=>{
    if(TM.busy)return; TM.busy=true;
    try{const j=await api('/api/timing/adopt',{key:TM.key,line:TM.open});toast('matched to the lyric source');TM.busy=false;tmApply(j.timing);return}catch(e){}
    TM.busy=false; tmFetch()};
  const ln=el.querySelector('[data-tmlane]');
  if(ln)ln.onclick=()=>{TM.lane=TM.lane==='vocals'?'mix':'vocals';tmRender(el)};
  const hp=el.querySelector('[data-tmhelp]');
  if(hp)hp.onclick=()=>{TM.helpOpen=!TM.helpOpen;tmRender(el)};
  wvBind(); ovBind();
  // draw the overview now (peaks/data may already be in hand) AND next frame
  // (once the canvas has real layout width) — pkFetch redraws again on arrival.
  // The focused reveal word re-centers the same way (fresh DOM starts at 0).
  ovDraw(); tmRevealCenter();
  requestAnimationFrame(()=>{ovDraw();wvDraw();tmRevealCenter()});
  // if audio is mid-play, keep the karaoke reveal lit after this rebuild (the DOM
  // is fresh, so reset the centering guard to re-scroll to the current word)
  if(_tmAudio&&!_tmAudio.paused){_tmRvNow=-1;tmRevealPaint(_tmClipBegin+_tmAudio.currentTime*1000)}
}
async function tmAct(kind){
  if(kind==='rebuild'){
    try{await api('/api/run',{key:TM.key,step:'assemble'});toast('applying your changes to the page…')}catch(e){}
    return pollState(true)}
  if(kind==='ship'){
    try{await api('/api/ship',{key:TM.key});toast('putting it online…')}catch(e){}
    return pollState(true)}
  if(kind==='promote'){
    tmConfirm('Put this on the main page?',
      'This repoints manaoke.app at this version and pushes it live for everyone. '+
      'The private preview link keeps working too.',
      'Put it on manaoke.app',async()=>{
        try{await api('/api/promote',{key:TM.key});toast('putting it on the main page…')}catch(e){}
        pollState(true)});
  }
}
// a small in-page confirm (never a native prompt — it freezes the page)
function tmConfirm(title,body,okLabel,onOk){
  const ov=document.createElement('div');ov.className='tm-modal';
  ov.innerHTML='<div class="tm-modal-card"><h3>'+esc(title)+'</h3><p>'+esc(body)+'</p>'+
    '<div class="tm-modal-btns"><button class="ctl" data-cancel>cancel</button>'+
    '<button class="ctl run" data-ok>'+esc(okLabel)+'</button></div></div>';
  document.body.appendChild(ov);
  ov.querySelector('[data-cancel]').onclick=()=>ov.remove();
  ov.onclick=e=>{if(e.target===ov)ov.remove()};
  ov.querySelector('[data-ok]').onclick=()=>{ov.remove();onOk()};
}
// called when a timing-relevant job ends (rebuild/ship/promote/sharpen).
// jobKey = the FINISHED job's song — ignore jobs for a song you're not viewing,
// so a background build of B never re-renders the A tab you're editing.
function tmOnJobEnd(step,jobKey){
  if(jobKey&&jobKey!==TM.key)return;
  if(!tmVisible())return;
  // 'all' = a walk that may have rewritten BOTH the wave and the timings
  // (the prep walk and run-all both rebuild peaks now) — refresh each.
  if(step==='peaks'||step==='all'){TM.peaks=null;pkFetch(TM.key);if(step==='peaks')return}
  // never yank a form out from under a typing thumb (the innerHTML rebuild
  // kills input focus + IME composition) — refresh when the form closes
  if(TM.wform){TM.pendingFetch=true;return}
  tmFetch();                              // state + timings may have changed
}
// a refresh parked while a word form was open — run it now (form closed)
function tmRunPending(){if(TM.pendingFetch){TM.pendingFetch=false;tmFetch()}}
// leaving the tab: silence every audio owner + park the playhead/reveal
function tmLeaving(){ tmStopAll(); }

/* ══ words tab (server mode only) ═══════════════════════════════════
   GET /api/words/{key}: every study-word clip and who's speaking it.
   Tap a word → hear the current take, hunt better ones (find better
   takes → /api/word/audition), install the keeper (/api/word/push —
   the box levels it and listens back; the job bar shows progress).   */
const WD={key:null,data:null,open:null,openCtx:'grid',cands:null,sel:null,pin:false,busy:false,bust:0,filter:'',pendingFilter:null};
const wdVisible=()=>location.hash==='#song/'+WD.key+'/words';
// who recorded the clip → the badge dot + its plain-English name
function wdSrcCls(w){
  if(!w.exists)return 'none';
  const s=(w.provenance&&w.provenance.source)||'';
  if(s.indexOf('curated')===0)return 'cur';
  if(s.indexOf('kokoro')===0)return 'std';
  if(s.indexOf('qwen')===0||s.indexOf('aivis')===0)return 'alt';
  return 'none';
}
const WDSRC={cur:'a human recording',std:'the standard voice',alt:'an alternate voice',none:'no clip yet'};
function mountWords(el,b){
  WD.key=b.key; WD.data=null; WD.open=null; WD.openCtx='grid'; WD.cands=null; WD.sel=null;
  WD.bust=Date.now();
  WD.filter=WD.pendingFilter||''; WD.pendingFilter=null;  // honor a "fix the take →" deep-link
  el.innerHTML='<div class="empty">reading the word clips…</div>';
  wdFetch();
}
async function wdFetch(){
  const el=$('tabbody'); if(!el||!WD.key||!wdVisible())return;
  let j=null;
  try{const r=await fetch('/api/words/'+encodeURIComponent(WD.key));
    if(!r.ok)throw new Error(r.status); j=await r.json()}
  catch(e){if(wdVisible())el.innerHTML='<div class="empty">couldn\x27t load the words for this song yet.</div>';return}
  if(!wdVisible())return;            // user moved on while we fetched
  WD.data=j; wdRender($('tabbody'));
}
let _wdAudio=null;
function wdPlay(url){
  if(_wdAudio){_wdAudio.pause();_wdAudio=null}
  _wdAudio=new Audio(url);
  _wdAudio.play().catch(()=>toast('couldn\x27t play that clip'));
}
const wdUrl=w=>w.file+'?t='+WD.bust;   // ?t= busts the old take after a push
// filter fold: NFKC flattens full-width romaji, lowercase for case-blindness
function wdFold(s){try{s=String(s==null?'':s).normalize('NFKC')}catch(e){s=String(s==null?'':s)}return s.toLowerCase()}
// ctx = 'grid'|'strip': a suspect word's chip lives in BOTH places, but its
// drawer renders only where it was opened from (one drawer, no dup ids).
function wdChip(w,ctx){
  const cls=wdSrcCls(w);
  const src=(w.provenance&&w.provenance.source)||'';
  return '<button class="wd-w'+(WD.open===w.uid?' on':'')+'" type="button" data-wd="'+esc(w.uid)+'"'+
    ' data-wdctx="'+ctx+'" data-q="'+esc(wdFold(w.surface+' '+(w.kana||'')+' '+(w.rom||'')+' '+w.uid))+'"'+
    ' data-src="'+esc(wdFold(src))+'" data-sus="'+(w.suspect?'1':'0')+'">'+
    '<span class="wd-b '+cls+'" title="'+WDSRC[cls]+'"></span>'+
    (w.suspect?'<span class="wd-susdot" title="'+esc(w.suspect_why||'worth a listen')+'"></span>':'')+
    '<span class="sf">'+esc(w.surface)+'</span>'+
    (w.kana&&w.kana!==w.surface?'<span class="kn">'+esc(w.kana)+'</span>':'')+
    (w.pinned?'<span class="wd-pinmark" title="always uses this recording">⚑</span>':'')+
    (w.exists?'<span class="wd-play" role="button" data-wdplay="'+esc(wdUrl(w))+'" title="play">▶</span>':'')+
  '</button>'+(WD.open===w.uid&&(WD.openCtx||'grid')===ctx?wdDrawer(w):'');
}
function wdDrawer(w){
  const cls=wdSrcCls(w);
  let inner;
  if(WD.cands==null){
    inner='<button class="ctl run" data-wdaud'+(JOB.active?' disabled':'')+'>✦ find better takes</button>';
  }else if(WD.cands==='busy'){
    inner='<div class="ytspin">listening for takes…</div>';
  }else if(!WD.cands.length){
    inner='<div class="wd-miss">no other takes found — the current clip stays.</div>';
  }else{
    inner='<div class="wd-ord">human recordings first — tap a take, then install it</div>'+
      WD.cands.map((c,i)=>'<div class="wd-c'+(WD.sel===i?' on':'')+'" data-wdc="'+i+'">'+
        (c.url?'<span class="wd-play" role="button" data-wdplay="'+esc(c.url)+'" title="play">▶</span>'
              :'<span class="wd-play off">·</span>')+
        '<span class="cl">'+esc(c.label)+'</span>'+
        (WD.sel===i?'<span class="wd-ck">✓</span>':'')+'</div>').join('')+
      '<div class="row wd-act" style="margin-top:10px">'+
        '<button class="go" data-wduse'+((WD.sel==null||JOB.active)?' disabled':'')+'>use this one</button>'+
        '<label class="wd-pinlab"><input type="checkbox" id="wd-pin"'+(WD.pin?' checked':'')+'>'+
          ' always use for this word</label></div>';
  }
  return '<div class="wd-x">'+
    '<div class="wd-xh"><span class="sf">'+esc(w.surface)+'</span>'+
      (w.kana&&w.kana!==w.surface?'<span class="kn">'+esc(w.kana)+'</span>':'')+
      '<span class="wd-b '+cls+'"></span><span class="wd-srct">'+WDSRC[cls]+'</span>'+
      (w.exists?'<button class="ctl" data-wdplay="'+esc(wdUrl(w))+'">▶ play current</button>':'')+'</div>'+
    (w.suspect?'<div class="wd-why"><span class="wd-susdot"></span>'+esc(w.suspect_why||'worth a listen')+'</div>':'')+
    (w.lex_reason?'<div class="wd-lexr">⚑ pinned — '+esc(w.lex_reason)+'</div>':'')+
    inner+'</div>';
}
function wdRender(el){
  const d=WD.data||{}, words=d.words||[];
  const secs=[], bySec={};
  words.forEach(w=>{if(!bySec[w.sec]){bySec[w.sec]=[];secs.push(w.sec)} bySec[w.sec].push(w)});
  const sus=words.filter(w=>w.suspect);
  const strip=sus.length?('<div class="wd-strip"><div class="wd-strip-h">needs your ear · '+sus.length+
      ' word'+(sus.length===1?'':'s')+' worth a listen</div>'+
    '<div class="wd-grid">'+sus.map(w=>wdChip(w,'strip')).join('')+'</div></div>'):'';
  const filter='<div class="punch" style="margin:6px 0 0"><div class="lcd"><label>filter the words</label>'+
    '<input id="wd-filter" placeholder="type to narrow · #qwen · #suspect" autocomplete="off" value="'+esc(WD.filter||'')+'"></div></div>';
  el.innerHTML=words.length?
    filter+strip+
    secs.map(sec=>'<div class="wd-sec">part '+esc(sec)+'</div>'+
      '<div class="wd-grid">'+bySec[sec].map(w=>wdChip(w,'grid')).join('')+'</div>').join('')
    :'<div class="empty">no word clips for this song yet — run the voice steps first.</div>';
  wdBind(el);
  wdApplyFilter(el);
}
/* live chip filter — client-side only, hides non-matching chips in place.
   plain text = surface/kana/rom substring (folded); '#tok' = provenance
   source substring, with '#suspect' as the needs-your-ear shorthand. */
function wdApplyFilter(el){
  const q=wdFold((WD.filter||'').trim());
  el.querySelectorAll('.wd-w[data-wd]').forEach(ch=>{
    let show=true;
    if(q){
      if(q.charAt(0)==='#'){const t=q.slice(1);
        show=!t||(t==='suspect'?ch.getAttribute('data-sus')==='1'
                 :(ch.getAttribute('data-src')||'').indexOf(t)>=0)}
      else show=(ch.getAttribute('data-q')||'').indexOf(q)>=0;
    }
    ch.style.display=show?'':'none';
    const nx=ch.nextElementSibling;                       // the open drawer follows its chip
    if(nx&&nx.classList.contains('wd-x'))nx.style.display=show?'':'none';
  });
  el.querySelectorAll('.wd-sec').forEach(sec=>{           // drop emptied section labels
    const g=sec.nextElementSibling; if(!g||!g.classList.contains('wd-grid'))return;
    const any=Array.from(g.querySelectorAll('.wd-w[data-wd]')).some(c=>c.style.display!=='none');
    sec.style.display=any?'':'none'; g.style.display=any?'':'none';
  });
  const st=el.querySelector('.wd-strip');
  if(st){const any=Array.from(st.querySelectorAll('.wd-w[data-wd]')).some(c=>c.style.display!=='none');
    st.style.display=any?'':'none'}
}
function wdBind(el){
  const fi=el.querySelector('#wd-filter');
  if(fi)fi.oninput=()=>{WD.filter=fi.value;wdApplyFilter(el)};   // no re-render — focus survives typing
  el.querySelectorAll('[data-wd]').forEach(x=>x.onclick=e=>{
    if(e.target.closest('[data-wdplay]'))return;
    const uid=x.getAttribute('data-wd');
    const ctx=x.getAttribute('data-wdctx')||'grid';
    const same=WD.open===uid&&(WD.openCtx||'grid')===ctx;
    WD.open=same?null:uid; WD.openCtx=ctx; WD.cands=null; WD.sel=null;
    const w=(WD.data&&WD.data.words||[]).find(v=>v.uid===WD.open);
    WD.pin=!!(w&&w.pinned);
    wdRender(el)});
  el.querySelectorAll('[data-wdplay]').forEach(x=>x.onclick=e=>{e.stopPropagation();
    wdPlay(x.getAttribute('data-wdplay'))});
  const au=el.querySelector('[data-wdaud]');
  if(au)au.onclick=async()=>{
    const uid=WD.open; if(!uid||WD.busy)return;
    WD.busy=true; WD.cands='busy'; wdRender(el);
    let j=null;
    try{j=await api('/api/word/audition',{key:WD.key,uid})}
    catch(e){WD.busy=false; WD.cands=null;
      if(wdVisible()&&WD.open===uid)wdRender(el); return}
    WD.busy=false;
    if(!wdVisible()||WD.open!==uid)return;   // user moved on mid-hunt
    WD.cands=(j&&j.candidates)||[]; WD.sel=null; wdRender(el)};
  el.querySelectorAll('[data-wdc]').forEach(x=>x.onclick=e=>{
    if(e.target.closest('[data-wdplay]'))return;
    const i=+x.getAttribute('data-wdc');
    const c=(Array.isArray(WD.cands)?WD.cands:[])[i];
    if(!c||!c.url)return;                    // a no-clip note can't be installed
    WD.sel=WD.sel===i?null:i; wdRender(el)});
  const pin=el.querySelector('#wd-pin');
  if(pin)pin.onchange=()=>WD.pin=pin.checked;
  const use=el.querySelector('[data-wduse]');
  if(use)use.onclick=async()=>{
    const c=(Array.isArray(WD.cands)?WD.cands:[])[WD.sel];
    if(!c||!c.url)return;
    use.disabled=true;
    try{await api('/api/word/push',{key:WD.key,uid:WD.open,candidate:c.url,pin:!!WD.pin});
      toast('installing that take — watch the bar below')}
    catch(e){use.disabled=WD.sel==null; return}
    WD.open=null; WD.cands=null; WD.sel=null; wdRender(el);
    pollState(true)};
}
function wdOnJobEnd(ok){                     // a word push landed — say the truth
  if(ok===false){                           // read-back rolled it back → old take kept
    toast('that take didn\x27t pass the listen-check — kept the previous recording');
    return;                                 // nothing changed; don't cache-bust
  }
  WD.bust=Date.now();
  toast('recording updated — rebuild the page to put it on the page');
  if(wdVisible()&&$('tabbody'))wdFetch();
}

/* ══ writing tab — the study text, typed here instead of handed off ══
   "Author the study data" is the one step on the road to shipping that the
   box could not do: it printed a prompt to paste into a cloud model and
   waited. Everything the page teaches — what each line means, what each card
   means, what the voice says out loud — lives in builds/<key>.content.json
   and had no editor anywhere. This is that editor. content_edit.py owns the
   file; this only draws the boxes and posts what changed. */
let WR={key:'',data:null,group:'lines',onlyBlank:false,busy:false};
// mirror of content_edit.REQUIRED — which boxes count as "still empty"
const WR_REQ={word:['en','en_speak','context','gloss','rom'],
              section:['name','short_name','subtitle','description','speak_en'],
              line:['tr_en','explain']};
const wrVisible=()=>location.hash==='#song/'+WR.key+'/writing';
// Every box, in the order a person fills them. `say` marks the text a voice
// reads out loud (so the label can warn before the gate does); `opt` marks a
// box the page is fine without.
const WR_SECTION_F=[
  ['name','what this part is called','',''],
  ['short_name','short label (V1, CH)','',''],
  ['subtitle','the heading on the page','',''],
  ['description','one line about what happens here','',''],
  ['speak_en','the intro, read out loud','say',''],
  ['note','extra note','','opt']];
const WR_LINE_F=[
  ['tr_en','what this line means','',''],
  ['tr_full','the whole sentence, when it runs past this line','','opt'],
  ['explain','the explainer, read out loud','say','']];
const WR_WORD_F=[
  ['en','the meaning on the card','',''],
  ['en_speak','the meaning, read out loud','say',''],
  ['gloss','the short version','say',''],
  ['context','how it is used here, read out loud','say',''],
  ['hint','extra hint','','opt'],
  ['rom','reading in English letters','',''],
  ['jp_speak','what the Japanese voice reads','','']];
function mountWriting(el,b){
  WR.key=b.key; WR.data=null;
  el.innerHTML='<div class="empty">reading the study text…</div>';
  wrFetch();
}
async function wrFetch(){
  const el=$('tabbody'); if(!el||!WR.key||!wrVisible())return;
  let j=null;
  try{const r=await fetch('/api/content/'+encodeURIComponent(WR.key));
    j=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(j.error||r.status)}
  catch(e){if(wrVisible())el.innerHTML='<div class="empty">'+esc(e.message||
    'couldn\x27t read the study text yet.')+'</div>';return}
  if(!wrVisible())return;
  WR.data=j; wrRender($('tabbody'));
}
function wrRows(){
  const d=WR.data; if(!d)return [];
  const rows=WR.group==='sections'?d.sections:WR.group==='cards'?d.words:d.lines;
  return WR.onlyBlank?rows.filter(r=>r.blank&&r.blank.length):rows;
}
function wrField(kind,id,jp,f,val,blank){
  const [name,label,say,opt]=f;
  const need=blank&&blank.indexOf(name)>=0;
  // Every box is a textarea, because a single-line input CLIPS: the sentence
  // runs off the right edge with no scrollbar and no clue it is there, and
  // the writer sees "That's it — it's over, it's over. The lovers go thei".
  // Guessing which fields are short by name got tr_en wrong — a line
  // translation is a whole sentence — and would have got the next one wrong
  // too. A textarea sized to its text (wrGrow) can't hide anything.
  const box='<textarea rows="1" data-wr-in>'+esc(val||'')+'</textarea>';
  return '<div class="wr-f'+(need?' todo':'')+'" data-kind="'+kind+'" data-id="'+esc(String(id))+
    '" data-field="'+name+'" data-jp="'+esc(jp||'')+'">'+
    '<label>'+esc(label)+
      (say==='say'?'<span class="say">· spoken</span>':'')+
      (opt==='opt'?'<span class="say">· optional</span>':'')+
      (need?'<span class="need">· still empty</span>':'')+
    '</label>'+box+'<div class="wr-msg"></div></div>';
}
function wrRender(el){
  const d=WR.data; if(!d){el.innerHTML='<div class="empty">nothing to write yet.</div>';return}
  const n=d.blanks;
  const seg=[['sections','parts of the song'],['lines','lines'],['cards','word cards']]
    .map(([g,l])=>'<button data-wr-g="'+g+'" class="'+(WR.group===g?'on':'')+'">'+l+'</button>').join('');
  const head='<div class="wr-head">'+
    '<div class="wr-count'+(n?'':' clean')+'"><b>'+(n?n+(n===1?' box':' boxes')+' still empty':
      'every box is filled')+'</b> · this is the writing that teaches the song</div>'+
    '<div class="wr-seg">'+seg+'</div>'+
    '<button class="ctl" data-wr-only>'+(WR.onlyBlank?'show everything':'show what\x27s left')+'</button>'+
    '</div>';
  const tip='<div class="wr-tip">Type in a box; it saves when you click away. '+
    'A box marked <b>spoken</b> is read out loud, so it takes plain English only.</div>';
  const done=(!n&&!d.author_done)
    ?'<div class="prevrow"><button class="ctl run" data-wr-done>✓ the writing is finished</button></div>':'';
  let body='';
  const rows=wrRows();
  if(!rows.length){
    body='<div class="empty">'+(WR.onlyBlank?'nothing left empty here.':'nothing here.')+'</div>';
  }else if(WR.group==='sections'){
    body=rows.map(s=>'<div class="wr-card'+(s.blank.length?' todo':'')+'">'+
      '<div class="wr-jp">'+esc(s.name||s.id)+'</div><div class="wr-sub">'+esc(s.id)+'</div>'+
      WR_SECTION_F.map(f=>wrField('section',s.id,'',f,s[f[0]],s.blank)).join('')+'</div>').join('');
  }else if(WR.group==='cards'){
    let sec='';
    body=rows.map(w=>{
      const h=(w.section!==sec)?('<div class="wr-grp">'+esc(wrSecName(w.section))+'</div>'):'';
      sec=w.section;
      return h+'<div class="wr-card'+(w.blank.length?' todo':'')+'">'+
        '<div class="wr-jp">'+esc(w.jp)+'</div><div class="wr-sub">card '+w.i+'</div>'+
        WR_WORD_F.map(f=>wrField('word',w.i,w.jp,f,w[f[0]],w.blank)).join('')+'</div>';
    }).join('');
  }else{
    let sec='';
    body=rows.map(l=>{
      const h=(l.section!==sec)?('<div class="wr-grp">'+esc(wrSecName(l.section))+'</div>'):'';
      sec=l.section;
      return h+'<div class="wr-card'+(l.blank.length?' todo':'')+'">'+
        '<div class="wr-jp">'+esc(l.jp)+'</div><div class="wr-sub">line '+l.i+'</div>'+
        WR_LINE_F.map(f=>wrField('line',l.i,l.jp,f,l[f[0]],l.blank)).join('')+'</div>';
    }).join('');
  }
  el.innerHTML=head+tip+done+body;
  el.querySelectorAll('[data-wr-g]').forEach(b=>b.onclick=()=>{
    WR.group=b.getAttribute('data-wr-g'); wrRender(el)});
  const only=el.querySelector('[data-wr-only]');
  if(only)only.onclick=()=>{WR.onlyBlank=!WR.onlyBlank; wrRender(el)};
  const fin=el.querySelector('[data-wr-done]');
  if(fin)fin.onclick=async()=>{
    fin.disabled=true;
    try{await api('/api/set',{key:WR.key,step:'author_data',done:true,
      note:'written in the box (Writing tab)'});
      toast('marked done — the next step is Assemble the page')}
    catch(e){fin.disabled=false;return}
    pollState(true); wrFetch()};
  el.querySelectorAll('[data-wr-in]').forEach(inp=>{
    inp.dataset.was=inp.value;
    inp.onblur=()=>wrSave(inp);
    if(inp.tagName==='TEXTAREA'){        // show the whole sentence, not a slice
      wrGrow(inp); inp.oninput=()=>wrGrow(inp);
    }
  });
}
function wrSecName(id){const s=(WR.data&&WR.data.sections||[]).find(x=>x.id===id);
  return (s&&(s.name||s.short_name))||id||'—'}
function wrGrow(t){t.style.height='auto';t.style.height=(t.scrollHeight+2)+'px'}
async function wrSave(inp){
  const f=inp.closest('.wr-f'); if(!f)return;
  const was=inp.dataset.was==null?'':inp.dataset.was;
  if(inp.value===was)return;                       // nothing typed — don't churn
  const msg=f.querySelector('.wr-msg');
  f.classList.remove('bad','saved'); msg.innerHTML='';
  let r=null;
  try{
    r=await fetch('/api/content/save',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:WR.key,edits:[{
        kind:f.dataset.kind,
        id:f.dataset.kind==='section'?f.dataset.id:parseInt(f.dataset.id,10),
        jp:f.dataset.jp||undefined,
        field:f.dataset.field, value:inp.value}]})});
  }catch(e){
    f.classList.add('bad'); msg.innerHTML='<div class="wr-err">couldn\x27t reach the box — '+
      'your text is still in this window, don\x27t close it.</div>'; return;
  }
  const j=await r.json().catch(()=>({}));
  if(!r.ok){
    f.classList.add('bad');
    msg.innerHTML='<div class="wr-err">'+esc((j.errors&&j.errors[0])||j.error||
      'that didn\x27t save')+'</div>';
    return;                                        // the typed text stays put
  }
  inp.dataset.was=inp.value;
  f.classList.add('saved');
  // the "still empty" marker on this box (and the amber edge on its card) has
  // to answer to what was just typed — leaving it up after a box is filled
  // reads as "the save didn't take"
  const lab=f.querySelector('label'), need=lab&&lab.querySelector('.need');
  const wasNeeded=f.classList.contains('todo');
  const empty=!inp.value.trim();
  if(wasNeeded&&!empty){f.classList.remove('todo'); if(need)need.remove()}
  else if(!wasNeeded&&empty&&need===null&&WR_REQ[f.dataset.kind].indexOf(f.dataset.field)>=0){
    f.classList.add('todo');
    if(lab)lab.insertAdjacentHTML('beforeend','<span class="need">· still empty</span>');
  }
  const card=f.closest('.wr-card');
  if(card)card.classList.toggle('todo',!!card.querySelector('.wr-f.todo'));
  const notes=[].concat(j.warnings||[],
    (j.clips||[]).map(c=>c.kept
      ?('the recording of the old words was a real voice, so it was kept — re-cut '+c.rel+' yourself')
      :'the old recording was thrown away; the audio step will read the new words'),
    (j.reopened&&j.reopened.length)?['the page needs rebuilding now — '+j.reopened.length+
      ' step'+(j.reopened.length===1?'':'s')+' reopened']:[]);
  if(notes.length)msg.innerHTML=notes.map(t=>'<div class="wr-note">'+esc(t)+'</div>').join('');
  setTimeout(()=>f.classList.remove('saved'),1200);
  // the count in the header (and the ✓ button) is now wrong — refresh the
  // numbers without redrawing the boxes out from under the typist
  if(WR.data&&typeof j.blanks==='number'&&j.blanks!==WR.data.blanks){
    WR.data.blanks=j.blanks;
    const c=$('tabbody')&&$('tabbody').querySelector('.wr-count b');
    if(c){c.textContent=j.blanks?(j.blanks+(j.blanks===1?' box':' boxes')+' still empty')
      :'every box is filled';
      c.parentElement.classList.toggle('clean',!j.blanks)}
  }
}

/* ══ gradient lab engine (shared: song + main) ══════════════════════ */
let G=null;   // active gradient context
function hexrgb(h){h=String(h||'').replace('#','');return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)]}
function rgbhex(t){return '#'+t.map(x=>Math.max(0,Math.min(255,Math.round(x))).toString(16).padStart(2,'0')).join('')}
function trip(hex){return hexrgb(hex).join(',')}
function rgb2hsv(r,g,b){r/=255;g/=255;b/=255;const mx=Math.max(r,g,b),mn=Math.min(r,g,b),d=mx-mn;let h=0;
  if(d){if(mx===r)h=((g-b)/d)%6;else if(mx===g)h=(b-r)/d+2;else h=(r-g)/d+4;h/=6;if(h<0)h+=1}
  return [h, mx?d/mx:0, mx]}
function hsv2rgb(h,s,v){h=((h%1)+1)%1;s=Math.max(0,Math.min(1,s));v=Math.max(0,Math.min(1,v));
  const i=Math.floor(h*6),f=h*6-i,p=v*(1-s),q=v*(1-f*s),t=v*(1-(1-f)*s);
  const m=[[v,t,p],[q,v,p],[p,v,t],[p,q,v],[t,p,v],[v,p,q]][i%6];return m.map(x=>x*255)}
function darken(hex,vt,mul,cap){const[r,g,b]=hexrgb(hex);const[h,s]=rgb2hsv(r,g,b);
  return rgbhex(hsv2rgb(h,Math.min(cap===undefined?0.80:cap,s*mul),vt))}
function cardAccent(hex){const[r,g,b]=hexrgb(hex);const[h,s]=rgb2hsv(r,g,b);
  return rgbhex(hsv2rgb(h,s>0.06?Math.min(0.92,s*1.12+0.04):s,0.45))}
function paleLim(f){return f==='hi'?GL.pale.hi:GL.pale.main}
function isPale(f,hex){return Math.max(...hexrgb(hex))/255>paleLim(f)}
function fmt(x){return (Math.round(x*100)/100).toString()}

function mountGradient(el,target){
  const dials=target.kind==='song'?
    '<section class="pane"><h2>motion dials</h2>'+
      '<div class="dial"><span class="nm">speed</span><input type="range" id="gspeed" min="0.25" max="3" step="0.05"><span class="val" id="gspeedv"></span></div>'+
      '<div class="dial"><span class="nm">amp</span><input type="range" id="gamp" min="0" max="2" step="0.05"><span class="val" id="gampv"></span></div>'+
      '<div class="dial"><span class="nm">motion</span><span id="gmotions"></span></div></section>':
    '<section class="pane"><h2>drift speed</h2>'+
      '<div class="dial"><span class="nm">speed</span><input type="range" id="gspeed" min="0.25" max="3" step="0.05"><span class="val" id="gspeedv"></span></div></section>';
  const artPane=target.kind==='song'?
    '<section class="pane"><h2>source artwork · eyedrop your colors off the real cover</h2>'+
      '<div class="artref" id="gart"></div></section>':'';
  el.innerHTML=
    '<div class="glab"><div class="glab-prev"><div class="prev" id="gprev" data-motion="">'+
      '<div class="mesh"></div><div class="fb fb1"></div><div class="fb fb2"></div><div class="fb fb3"></div>'+
      '<div class="scrim"></div><div class="crt"></div><div class="lyric" id="glyric"></div>'+
      '<button class="crt-toggle" id="gcrt" title="toggle the CRT scanline overlay the song pages ship — see the field the way it actually looks">CRT</button></div>'+
      (target.kind==='song'?'<div class="accrow" style="margin-top:12px"><span class="sw" id="gcardsw"></span>'+
        '<span class="hex" id="gcardhex"></span><span class="note">landing card accent (from the main color)</span></div>':'')+
    '</div>'+
    '<div class="glab-ctl">'+
      artPane+
      '<section class="pane"><h2>colors · the main color drives the background + card accent</h2><div id="gcolors"></div></section>'+
      dials+
      '<section class="pane"><h2>push it</h2><div id="gpush"></div>'+
        '<div class="palewarn" id="gpalewarn" hidden></div>'+
        '<div class="row"><button class="ctl" id="greset">reset to current</button></div>'+
      '</section>'+
    '</div></div>';
  gInit(target);
}
function gInit(target){
  const src={}, cur={}, recorded={}, touched=new Set();
  const d=GL.defaults||{};
  let base={}, meta={};
  if(target.kind==='main'){
    const M=GL.main||{};
    CF.forEach(f=>{base[f]=M[f]||PLACE[f]; src[f]=M[f]?'landing':'—'});
    meta={title:'命短し恋せよ乙女', speed:M.speed||1};
  }else{
    const s=glByKey(target.key)||{}; const pg=s.page||{};
    Object.assign(recorded, s.design||{});
    CF.forEach(f=>{base[f]=pg[f]||PLACE[f]; src[f]=pg[f]?'cover':'—'});
    meta={title:s.title_jp||'命短し恋せよ乙女', speed:1};
  }
  CF.forEach(f=>cur[f]=base[f]);
  ['c1','c2','c3','hi'].forEach(f=>{ if(target.kind!=='main'&&d[f]){cur[f]=d[f];src[f]='default'} if(recorded[f]){cur[f]=recorded[f];src[f]='override'} });
  if(target.kind!=='main'&&d.fb) d.fb.forEach((h,i)=>{cur['fb'+(i+1)]=h;src['fb'+(i+1)]='default'});
  if(recorded.fb) recorded.fb.forEach((h,i)=>{cur['fb'+(i+1)]=h;src['fb'+(i+1)]='override'});
  cur.speed = recorded.speed!=null?recorded.speed:(target.kind!=='main'&&d.speed!=null?d.speed:meta.speed);
  cur.amp   = recorded.amp  !=null?recorded.amp  :(target.kind!=='main'&&d.amp  !=null?d.amp  :1);
  cur.motion= recorded.motion||(target.kind!=='main'&&d.motion)||'drift';
  G={target,cur,base,recorded,touched,src};
  $('glyric').textContent=meta.title;
  // source artwork (song only) — big enough to aim the eyedropper at
  if(target.kind==='song'&&$('gart')){
    const s=glByKey(target.key)||{};
    $('gart').innerHTML=s.art?('<img src="'+esc(s.art)+'" alt="album cover">'+
      '<div class="hint">Hit <b>⦿ pick</b> on any color, then click a spot on this cover.</div>')
      :'<div class="hint">no cover art for this song yet.</div>';
  }
  // CRT overlay toggle — remembered across songs this session
  const crtOn=sessionStorage.getItem('mb-crt')==='1';
  $('gprev').classList.toggle('crt-on',crtOn);
  const cb=$('gcrt'); if(cb){ cb.classList.toggle('on',crtOn);
    cb.onclick=()=>{const on=!$('gprev').classList.contains('crt-on');
      $('gprev').classList.toggle('crt-on',on); cb.classList.toggle('on',on);
      sessionStorage.setItem('mb-crt',on?'1':'0')}; }
  gRenderColors(); gPaint();
}
function emitted(){ // color fields the composed command will set (pale tracks THESE)
  const e=new Set();
  ['c1','c2','c3','hi'].forEach(f=>{if(G.touched.has(f)||G.recorded[f])e.add(f)});
  if(['fb1','fb2','fb3'].some(f=>G.touched.has(f))||G.recorded.fb){e.add('fb1');e.add('fb2');e.add('fb3')}
  return e;
}
function gColorRow(f){
  const pale=emitted().has(f)&&isPale(f,G.cur[f]);
  return '<div class="crow" data-f="'+f+'">'+
    '<span class="sw" style="background:'+G.cur[f]+'"></span>'+
    '<span class="clab"><span class="cn">'+GNAME[f]+'</span>'+
      '<span class="cc">'+f+' · '+G.cur[f]+'</span></span>'+
    '<span class="src '+(G.src[f]==='override'?'override':'')+'">'+esc(SRCNAME[G.src[f]]||G.src[f])+'</span>'+
    (pale?'<span class="pale">too pale</span>':'')+
    ('EyeDropper' in window?'<button class="eyed" data-eye="'+f+'">⦿ pick</button>':'<span class="eyed" style="cursor:default" title="EyeDropper needs Chrome/Edge">no picker</span>')+
    '<input type="color" value="'+G.cur[f]+'" data-col="'+f+'"></div>';
}
function gRenderColors(){
  $('gcolors').innerHTML=CF.map(gColorRow).join('');
  $('gcolors').querySelectorAll('[data-col]').forEach(i=>i.oninput=()=>gSet(i.getAttribute('data-col'),i.value));
  $('gcolors').querySelectorAll('[data-eye]').forEach(b=>b.onclick=()=>{
    new EyeDropper().open().then(r=>gSet(b.getAttribute('data-eye'),r.sRGBHex)).catch(()=>{})});
}
function gSet(f,hex){G.cur[f]=hex.toLowerCase();G.touched.add(f);G.src[f]='override';gRenderColors();gPaint()}
function gPaint(){
  const p=$('gprev'), st=p.style;
  CF.forEach(f=>st.setProperty('--'+f,trip(G.cur[f])));
  st.setProperty('--base1',darken(G.cur.c1,.165,1.3));
  st.setProperty('--base2',darken(G.cur.c1,.094,1.3));
  st.setProperty('--base3',darken(G.cur.c1,.047,1.3));
  Object.entries(GL.fdur||{}).forEach(([n,b])=>st.setProperty('--fdur-'+n,fmt(b/G.cur.speed)+'s'));
  st.setProperty('--amp',G.cur.amp);
  p.setAttribute('data-motion',G.cur.motion==='drift'?'':G.cur.motion);
  if($('gspeed')){$('gspeed').value=G.cur.speed; $('gspeedv').textContent=fmt(G.cur.speed)+'×'}
  if($('gamp')){$('gamp').value=G.cur.amp; $('gampv').textContent=fmt(G.cur.amp)}
  if($('gmotions')){
    $('gmotions').innerHTML=(GL.motions||[]).map(m=>'<button class="mbtn'+(G.cur.motion===m?' on':'')+'" data-m="'+m+'">'+m+'</button>').join(' ');
    $('gmotions').querySelectorAll('[data-m]').forEach(b=>b.onclick=()=>{G.cur.motion=b.getAttribute('data-m');G.touched.add('motion');gPaint()});
  }
  if($('gcardsw')){const card=cardAccent(G.cur.c1);$('gcardsw').style.background=card;$('gcardhex').textContent=card}
  gCompose();
}
function gCompose(){
  const t=G.target, colorFlags=[];
  ['c1','c2','c3','hi'].forEach(f=>{if(G.touched.has(f)||G.recorded[f])colorFlags.push('--'+f+' "'+G.cur[f]+'"')});
  if(['fb1','fb2','fb3'].some(f=>G.touched.has(f))||G.recorded.fb) colorFlags.push('--fb "'+G.cur.fb1+','+G.cur.fb2+','+G.cur.fb3+'"');
  const dialFlags=[];
  if(G.cur.speed!==1||G.recorded.speed!=null) dialFlags.push('--speed '+fmt(G.cur.speed));
  if(t.kind==='song'){
    if(G.cur.motion!=='drift'||G.recorded.motion) dialFlags.push('--motion '+G.cur.motion);
    if(G.cur.amp!==1||G.recorded.amp!=null) dialFlags.push('--amp '+fmt(G.cur.amp));
  }
  const B='python3 tools/songcraft/manaoke_build.py gradient set ', RB='  &&  python3 tools/songcraft/manaoke_build.py rebuild ';
  const pushes=[];
  if(t.kind==='song'){
    const songParts=colorFlags.concat(dialFlags);
    pushes.push({t:'push to this song', d:'colors + dials → this page',
      cmd:songParts.length?B+t.key+' '+songParts.join(' ')+RB+t.key:''});
    // motion dials globally — DIALS ONLY, never colors (there are no global gradients)
    pushes.push({t:'push motion dials globally', d:'speed / motion / amp only → every song',
      cmd:dialFlags.length?B+'--all '+dialFlags.join(' ')+RB+'--all':''});
    // this look → the landing field (colors + drift speed; landing motion is fixed drift)
    const mainParts=colorFlags.concat(G.cur.speed!==1?['--speed '+fmt(G.cur.speed)]:[]);
    pushes.push({t:'push to the main page', d:'this look → the landing background',
      cmd:mainParts.length?B+'--main '+mainParts.join(' '):''});
  }else{
    const mainParts=colorFlags.concat(G.cur.speed!==1||G.recorded.speed!=null?['--speed '+fmt(G.cur.speed)]:[]);
    pushes.push({t:'push to the main page', d:'colors + drift speed → the landing background',
      cmd:mainParts.length?B+'--main '+mainParts.join(' '):''});
  }
  $('gpush').innerHTML=pushes.map(p=>'<div class="push"><div class="ph"><span class="pt">'+p.t+'</span><span class="pd">'+p.d+'</span></div>'+
    '<div class="pcmd'+(p.cmd?'':' dim')+'">'+(p.cmd?('<button class="cp" data-cp="'+esc(p.cmd).replace(/"/g,'&quot;')+'">copy</button>'+esc(p.cmd)):'(no overrides yet — pick a color or move a dial)')+'</div></div>').join('');
  $('gpush').querySelectorAll('.cp').forEach(x=>x.onclick=()=>copy(x.getAttribute('data-cp')));
  const pale=CF.filter(f=>emitted().has(f)&&isPale(f,G.cur[f]));
  const pw=$('gpalewarn');
  if(pale.length){pw.hidden=false;pw.textContent='⚠ the CLI will REFUSE '+pale.map(f=>GNAME[f]||f).join(', ')+' — no whitish tones in the field (white lyrics sit on top). Deepen the tone.'}
  else pw.hidden=true;
}
document.addEventListener('input',e=>{
  if(!G)return;
  if(e.target.id==='gspeed'){G.cur.speed=parseFloat(e.target.value);G.touched.add('speed');gPaint()}
  if(e.target.id==='gamp'){G.cur.amp=parseFloat(e.target.value);G.touched.add('amp');gPaint()}
});
document.addEventListener('click',e=>{ if(e.target.id==='greset'&&G){gInit(G.target);toast('reset to recorded state')} });

/* ══ site nav + lexicon panel (library view only) ═══════════════════ */
/* The Backlog badge's open count. Kept in a variable because the count arrives
   with the poll but the badge is destroyed and re-created empty every time the
   library re-renders (removing a song, say) — and the only writer used to sit
   BEHIND pollState's "gen unchanged → return early" guard, so once the badge was
   re-created the number never came back for the rest of the session. Rendering
   reads the cache; the poll refreshes it. */
let BKOPEN=0;
function paintBacklog(){
  const bk=$('bkct'); if(!bk)return;
  bk.hidden=!BKOPEN; bk.textContent=BKOPEN||'';
}
function siteBar(){
  // no lexicon link here — the panel below is its own control; two buttons
  // for one panel, stacked, was the same thing said twice.
  return '<div class="sitebar"><a href="backlog.html">▤ Backlog'+
    (SERVER_MODE?' <span id="bkct" class="bkbadge" hidden></span>':'')+'</a></div>';
}
function lexPanel(){
  const words=LEX.words||{}; const keys=Object.keys(words);
  const rows=keys.length?('<div class="lexrow lexhead"><span>word</span><span>kana</span><span>reason</span><span>added</span></div>'+
    keys.map(k=>{const w=words[k];return '<div class="lexrow"><span class="lw">'+esc(w.surface||k)+'</span>'+
      '<span>'+esc(w.kana||k)+'</span><span>'+esc(w.reason||'')+'</span>'+
      '<span class="la">'+esc(w.added||'')+(w.fed_by?(' · '+esc(w.fed_by)):'')+'</span></div>'}).join('')):
    '<div class="empty">lexicon empty.</div>';
  return '<details class="lex"><summary>Pronunciation lexicon</summary>'+
    '<p class="dek">A word the whisper read-back caught mispronounced once is listed here and can never ship from TTS again — on any song.</p>'+
    rows+'<div class="lex-add">'+
      '<input id="lxw" placeholder="word (事)" autocomplete="off">'+
      '<input id="lxk" placeholder="kana (こと)" autocomplete="off">'+
      '<input id="lxc" placeholder="carrier phrase (optional)" autocomplete="off">'+
      '<input id="lxr" placeholder="reason" autocomplete="off">'+
      '<button class="ctl" id="lxgo">copy add command</button></div></details>';
}
function bindLex(){
  const g=$('lxgo'); if(!g)return;
  g.onclick=()=>{const v=id=>($(id).value||'').trim();const w=v('lxw');if(!w){toast('word required');return}
    let cmd='python3 tools/songcraft/manaoke_build.py lexicon add '+w+(v('lxk')?(' --kana '+v('lxk')):'');
    if(v('lxc'))cmd+=' --carrier "'+v('lxc').replace(/"/g,'\\"')+'"';
    if(v('lxr'))cmd+=' --reason "'+v('lxr').replace(/"/g,'\\"')+'"';
    copy(cmd)};
}

/* ══ server mode: live state, in-place patching, the job bar ═════════
   Poll /api/state every 2s (visible tab only). gen unchanged → nothing
   moves. gen changed → patch ONLY the dynamic bits (dots, notes, bars,
   badges) so open chevrons + scroll survive; the grid re-renders only
   when the build LIST itself changed (a new song). No full-page reload,
   ever. The job bar streams the active job's log via /api/log.          */
async function api(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){toast(j.error||('error '+r.status));throw new Error(j.error||r.status)}
  return j;
}
let lastGen=null;
async function pollState(force){
  if(!SERVER_MODE)return;
  if(!force&&document.visibilityState!=='visible')return;
  let st;
  try{const r=await fetch('/api/state'); if(!r.ok)return; st=await r.json()}
  catch(e){return}
  updateJobBar(st);
  BKOPEN=st.backlog_open||0; paintBacklog();
  if(!force&&st.gen===lastGen)return;
  lastGen=st.gen;
  applyState(st);
}
function applyState(st){
  const oldKeys=BUILDS.map(b=>b.key).join('|');
  BUILDS.length=0; (st.builds||[]).forEach(b=>BUILDS.push(b));   // mutate in place — byKey() stays live
  const h=location.hash.replace(/^#/,''); const view=h.split('/')[0], key=h.split('/')[1];
  if(view==='song'){const b=byKey(key); if(b)patchSong(b); return}
  if(!view){                                          // library grid
    const grid=document.querySelector('.grid'); if(!grid)return;
    if(BUILDS.map(b=>b.key).join('|')!==oldKeys){     // list changed (new song) → re-render the grid section
      grid.innerHTML=gridTiles(filterSongs(sessionStorage.getItem('mb-q')||'')); bindGo(); return}
    BUILDS.forEach(b=>{                               // otherwise: progress bars only
      const t=grid.querySelector('.tile[data-tile="'+CSS.escape(b.key)+'"]'); if(!t)return;
      const bar=t.querySelector('.pbar > i'); if(bar)bar.style.width=roadPct(b)+'%';
    });
  }
}
function patchSong(b){
  const steps=b.steps||[];
  const done=roadDone(b), pct=roadPct(b);
  const frac=document.querySelector('.dhead .frac');
  if(frac)frac.textContent=done+'/'+roadSteps(b).length;
  const bar=document.querySelector('.dhead .bar > i'); if(bar)bar.style.width=pct+'%';
  const nextIdx=steps.findIndex(s=>!s.optional&&stepStatus(b.key,s)!=='done');
  // the out-of-date notice tracks the job too, so a rebuild started from
  // ANOTHER screen (or another device on the tailnet) shows as in-progress
  // here rather than as a still-pressable button that would 409
  const sr=document.querySelector('#tabbody .stalerow');
  if(sr&&rebuilding(b.key)!==sr.classList.contains('working')){
    if(rebuilding(b.key))sr.outerHTML=staleWorking();
    else{const tmp=document.createElement('div'); tmp.innerHTML=staleRow(b);
      if(tmp.firstElementChild){sr.replaceWith(tmp.firstElementChild); bindStale($('tabbody'),b)}}
  }
  // the banner tracks the build too (see bindNextUp)
  const nu=document.querySelector('#tabbody .nextup');
  if(nu){const fresh=nextUp(b,steps,nextIdx);
    if(fresh){const tmp=document.createElement('div'); tmp.innerHTML=fresh;
      if(tmp.firstElementChild&&tmp.firstElementChild.outerHTML!==nu.outerHTML){
        nu.replaceWith(tmp.firstElementChild); bindNextUp($('tabbody'),b)}}}
  steps.forEach((s,i)=>{
    const row=document.querySelector('.step[data-s="'+CSS.escape(s.key)+'"]'); if(!row)return;
    const stt=stepStatus(b.key,s);
    const dot=row.querySelector('.st-dot'); if(dot)dot.className='st-dot '+stt;
    row.classList.toggle('next',i===nextIdx);
    const hd=row.querySelector('.step-hd');
    if(hd){
      const np=hd.querySelector('.nextpill');
      if(i===nextIdx&&!np)hd.querySelector('.nm').insertAdjacentHTML('afterend','<span class="nextpill">next</span>');
      else if(i!==nextIdx&&np)np.remove();
    }
    const bd=row.querySelector('.step-bd'); if(!bd)return;
    let nt=bd.querySelector('.note');
    const ntx=noteText(s,fillCmd(s.cmd||'',b));      // same de-duplication as stepRow
    if(ntx){
      if(!nt){bd.insertAdjacentHTML('beforeend','<div class="note"></div>');nt=bd.querySelector('.note')}
      nt.textContent=ntx;
    }else if(nt)nt.remove();
    const db=row.querySelector('[data-setdone]');
    if(db){db.classList.toggle('on',stt==='done');db.textContent=stt==='done'?'↩ undo':'✓ mark done'}
  });
}
function updateJobBar(st){
  const bar=$('jobbar'); if(!bar)return;
  const job=st.job, queue=st.queue||[];
  JOB.active=!!job||queue.length>0;
  // every action button sleeps while a job is active — Stop stays live
  // ONLY queue-triggering buttons sleep during a job. The timing precision
  // controls (zoom/pan/drag/word-focus) stay LIVE — they rely on the
  // server's per-key 409, not the global job flag, so editing never freezes
  // just because some other song is building.
  document.querySelectorAll('[data-run],[data-runall],[data-setdone],[data-tmact],[data-tmsharpen],[data-tmalign],[data-wdaud],[data-refetch],[data-refetchgo],[data-byo],[data-byogo]').forEach(x=>x.disabled=JOB.active);
  const wu=document.querySelector('[data-wduse]');
  if(wu)wu.disabled=JOB.active||WD.sel==null;   // use-this-one also needs a picked take
  nsSyncGo();          // Add song needs no-job AND a chosen video
  if(job){
    if(!JOB.cur||JOB.cur.id!==job.id){                // a new job took the bar
      JOB.cur=job; JOB.offset=0; JOB.ended=false; JOB.dismissed=false;
      $('jblog').textContent=''; $('jbdismiss').hidden=true; $('jbstop').hidden=false;
      bar.classList.remove('flash');
    }
    JOB.cur=job;
    $('jbmeta').textContent=job.key+' · '+(job.step||'auto (run all)')+' · '+job.state;
    $('jbdot').className='jb-dot '+job.state;
    $('jbq').textContent=queue.length?('+'+queue.length+' queued'):'';
    bar.classList.add('show'); document.body.classList.add('jb-open');
    pollLog();
  }else if(JOB.cur&&!JOB.ended&&!JOB.dismissed){
    pollLog(true);                                    // just ended — final chunk + real end state
  }else if((!JOB.cur||JOB.dismissed)&&!queue.length){
    bar.classList.remove('show'); document.body.classList.remove('jb-open');
  }
}
let _logBusy=false;
async function pollLog(final){
  if(!JOB.cur||_logBusy)return; _logBusy=true;
  try{
    const r=await fetch('/api/log?id='+encodeURIComponent(JOB.cur.id)+'&offset='+JOB.offset);
    if(r.ok){
      const j=await r.json(), pre=$('jblog');
      if(j.chunk){pre.textContent+=j.chunk; pre.scrollTop=pre.scrollHeight}
      JOB.offset=j.offset||JOB.offset;
      const end=['done','error','stopped'].indexOf(j.state)>=0?j.state:(final?'done':null);
      if(end){                                        // flash the end state, keep the log until dismissed
        JOB.ended=true;
        $('jbmeta').textContent=JOB.cur.key+' · '+(JOB.cur.step||'auto (run all)')+' · '+end+(j.rc!=null?(' · rc '+j.rc):'');
        $('jbdot').className='jb-dot '+end;
        $('jbela').textContent='';
        $('jbstop').hidden=true; $('jbdismiss').hidden=false;
        const bb=$('jobbar'); bb.classList.add('flash'); setTimeout(()=>bb.classList.remove('flash'),1300);
        const step=JOB.cur.step||'', ok=(end==='done'&&(j.rc==null||j.rc===0));
        if(/^word-push:/.test(step))wdOnJobEnd(ok);              // fresh take → refetch, honest badge
        // timing-relevant jobs → refresh the Timing tab's state ladder / waveform
        if(step==='peaks'||step==='assemble'||step===''||/^prep:|^ship:|^promote:/.test(step))
          tmOnJobEnd(step==='peaks'?'peaks':
                     (step===''||/^prep:/.test(step))?'all':'state', JOB.cur.key);
        // A finished job is the one moment staleness can have moved. Re-measure
        // and repaint, so "rebuild this page" actually clears the thing that
        // asked for it — this is what the button is FOR.
        refreshStale(/^rebuild:/.test(step)||step===''||step==='assemble');
      }
    }
  }catch(e){}
  _logBusy=false;
}
if(SERVER_MODE){
  $('jbstop').onclick=async()=>{if(!JOB.cur)return;
    try{await api('/api/stop',{job_id:JOB.cur.id});toast('stop signalled')}catch(e){}};
  $('jbdismiss').onclick=()=>{JOB.cur=null;JOB.ended=false;JOB.dismissed=true;JOB.offset=0;
    $('jblog').textContent='';$('jobbar').classList.remove('show');document.body.classList.remove('jb-open')};
  setInterval(pollState,2000);
  pollState(true);
  // The baked-in copy is as old as this page load; a long-lived tab (or a
  // standalone home-screen app that never gets closed) would otherwise be
  // reading a day-old measurement. Confirm it once at boot, then only at the
  // end of jobs — it is too heavy for the 2s poll.
  refreshStale();
  addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshStale()});
  setInterval(()=>{                                   // elapsed ticker — display only
    if(!JOB.cur||JOB.ended||!JOB.cur.started_at)return;
    const s=Math.max(0,Math.floor(Date.now()/1000-JOB.cur.started_at));
    $('jbela').textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0');
  },1000);
}

/* ══ boot + guarded soft-refresh ════════════════════════════════════ */
window.addEventListener('hashchange',route);
route();
// restore what the last reload (auto or ↻) saved: open step chevrons + scroll
try{
  const vs=JSON.parse(sessionStorage.getItem('mb-view')||'null');
  if(vs){sessionStorage.removeItem('mb-view');
    (vs.open||[]).forEach(k=>{const el=document.querySelector('.step[data-s="'+CSS.escape(k)+'"]');
      if(el)el.classList.add('open')});
    if(typeof vs.y==='number')window.scrollTo(0,vs.y)}
}catch(e){}
function saveViewState(){
  try{sessionStorage.setItem('mb-view',JSON.stringify({
    open:Array.from(document.querySelectorAll('.step.open')).map(el=>el.getAttribute('data-s')),
    y:window.scrollY}))}catch(e){}
}
$('refbtn').onclick=()=>{saveViewState();location.reload()};
$('navback').onclick=()=>go('');    // always the library, from any depth
// live tick ONLY while a step is actually running (ANY_RUNNING is baked in at
// render time; each reload re-evaluates it, so the loop dies with the run).
// Idle = completely static: no reload, no animation restarts, no art re-decode.
// Never reload while a text field is focused or the gradient editor has unsaved
// picks (would wipe them). View persists via #hash; chevrons/scroll via mb-view.
// Server mode never reloads — pollState patches in place instead.
if(!SERVER_MODE&&ANY_RUNNING)setInterval(()=>{
  const ae=document.activeElement;
  if(ae&&/^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName))return;
  if(G&&G.touched&&G.touched.size)return;
  if(location.hash.indexOf('gradient')>=0||location.hash.indexOf('main')>=0||location.hash.indexOf('new')>=0)return;
  saveViewState();
  location.reload();
},4000);
</script>
</body></html>"""
