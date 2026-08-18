#!/usr/bin/env python3
"""
lint_template.py — static gate: no audio playback site may bypass AUDIO_V.

Why this exists: song audio is served under stable names with immutable 1-year
caching. Replacing a clip's bytes in place therefore NEVER reaches devices —
only a URL change (the ?v=<AUDIO_V> param) does. Round 6 proved it (the old に
clip kept playing with age: 41243 at the CF edge); Round 7 found the word/pitch
card players constructing `new Audio(bareUrl)` and re-serving year-old Kokoro
clips even after the repo bytes were curated-human. This lint fails the build
if ANY audio playback site in the template is not routed through _withAudioV.

Rules (run on the TEMPLATE page; clones inherit by parity):
  R1  `const AUDIO_V = '...'` sentinel and _withAudioV() must exist.
  R2  every `new Audio(<arg>)`: arg must be empty, a data: URI literal,
      contain `_withAudioV(`, or be a bare identifier that was reassigned via
      `<ident> = _withAudioV(<ident>)` within the preceding 12 lines.
  R3  any `.src = <rhs>` whose rhs contains a quoted/template `audio/` path or
      PODCAST_URL must contain `_withAudioV(`.
  R4  _audioUrlFor (the card/pitch URL builder) must version its returns.

Usage: python3 lint_template.py <template_dir_or_index.html>   → exit 0/1
"""
import re, sys
from pathlib import Path


def lint(html):
    errs = []
    lines = html.splitlines()

    # R1
    if not re.search(r"const AUDIO_V = '[0-9a-f]*';", html):
        errs.append("R1: missing `const AUDIO_V = '...';` sentinel")
    if 'function _withAudioV(' not in html:
        errs.append('R1: missing _withAudioV()')

    # R2 (a bare identifier is OK if its enclosing function re-assigned it via
    # `x = _withAudioV(x)` earlier — playAudio/_drillClip wrap at entry)
    for i, ln in enumerate(lines):
        code = ln.split('//')[0]
        for m in re.finditer(r'new Audio\(([^)]*)\)', code):
            arg = m.group(1).strip()
            if arg == '' or arg.startswith(("'data:", '"data:', '`data:')):
                continue
            if '_withAudioV(' in arg:
                continue
            ident = re.fullmatch(r'[A-Za-z_$][\w$]*', arg)
            if ident:
                ctx = '\n'.join(lines[max(0, i - 40):i + 1])
                if re.search(rf'\b{re.escape(arg)}\s*=\s*_withAudioV\(\s*{re.escape(arg)}', ctx):
                    continue
            errs.append(f'R2 line {i+1}: unversioned playback `new Audio({arg})` '
                        f'— wrap with _withAudioV()')

    # R3
    for i, ln in enumerate(lines):
        m = re.search(r'\.src\s*=\s*(.+)$', ln)
        if not m:
            continue
        rhs = m.group(1)
        if re.search(r'''['"`][^'"`]*audio/''', rhs) or 'PODCAST_URL' in rhs:
            if '_withAudioV(' not in rhs:
                errs.append(f'R3 line {i+1}: unversioned .src assignment: {rhs.strip()[:80]}')

    # R4
    fn = re.search(r'function _audioUrlFor\([^)]*\)\s*\{(.*?)\n\}', html, re.S)
    if fn:
        body = fn.group(1)
        rets = [r for r in re.findall(r'return\s+(.+?);', body) if r.strip() != 'null']
        bad = [r for r in rets if '_withAudioV(' not in r]
        if bad:
            errs.append(f'R4: _audioUrlFor has unversioned return(s): {bad}')
    else:
        errs.append('R4: _audioUrlFor not found')
    return errs


def main():
    p = Path(sys.argv[1])
    if p.is_dir():
        p = p / 'index.html'
    errs = lint(p.read_text())
    if errs:
        print(f'✗ LINT: {p} — {len(errs)} audio-version gap(s):')
        for e in errs:
            print('   ' + e)
        return 1
    print(f'✓ LINT: {p} — every audio playback site is AUDIO_V-versioned.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
