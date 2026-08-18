#!/usr/bin/env python3
"""kana_line_harness — the kana-line-integrity repro/regression harness.

Feeds line_reading_plan() the EXACT defect cases from the 2026-07-10 audit,
including Apple's real sub-word splits (休|みの日には|母|さん|と — the splits
that produced きゅーみのびにわははさんと on the live site), and asserts the
composed line readings. Run under the parler env (fugashi + pyopenjtalk):

  /opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python \
      tools/songcraft/kana_line_harness.py

Exit 0 = every case correct AND the keep-correct controls unchanged.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_to_data as c2d


def words(*texts):
    return [{'text': t, 'begin_ms': i * 1000, 'end_ms': (i + 1) * 1000}
            for i, t in enumerate(texts)]


def card(jp, kana, only_lines=None):
    return {'_jp': jp, '_kana': kana, 'only_lines': only_lines,
            '_only_keys': {c2d._norm_line_key(x) for x in (only_lines or [])}}


# (name, line_text, apple_token_split, cards, expected_joined_kana)
CASES = [
    # THE shipped defect: Apple's real sub-word split + the real inochi cards.
    ('inochi kaa-san', '休みの日には母さんと',
     words('休', 'みの日には', '母', 'さん', 'と'),
     [card('母さん', 'かあさん'), card('休み', 'やすみ'), card('日', 'ひ')],
     'やすみのひにはかあさんと'),
    # Okurigana doubling at a split boundary (汚|れた → けがれ+れた).
    ('yogoreta doubling', 'なんぼ汚れたアタシでも',
     words('なんぼ', '汚', 'れたアタシで', 'も'),
     [card('汚れた', 'よごれた')],
     'なんぼよごれたあたしでも'),
    # Rendaku spelling: literal text づ preserved (g2p can never emit づ).
    ('kizuita rendaku', '気付いた時は消えてしまうけど',
     words('気付いた', '時は', '消えて', 'しまうけど'),
     [card('気付いた', 'きずいた'), card('時', 'とき')],   # card folds equal -> orthographic づ kept
     'きづいたときはきえてしまうけど'),
    ('chikazuite', '近づいて', words('近づいて'), [],
     'ちかづいて'),
    ('tsuzukeyou', '守り続けよう', words('守り', '続けよう'),
     [card('守り続けよう', 'まもりつづけよう')],
     'まもりつづけよう'),
    # Context class: 点ける is ツケル (g2p said テンケル).
    ('tsukeru', '点けるように', words('点ける', 'ように'), [],
     'つけるように'),
    # Card wins over dictionary default (fugashi says あす; the card says あした).
    ('ashita card wins', '明日には笑えるやろか',
     words('明日には', '笑える', 'やろか'),
     [card('明日', 'あした'), card('笑える', 'わらえる')],
     'あしたにはわらえるやろか'),
    # Keep-correct controls.
    ('kazoete control', '数えて', words('数えて'), [], 'かぞえて'),
    ('inazuma control', '稲妻', words('稲妻'), [], 'いなずま'),
    ('shinya control', '深夜1時', words('深夜1時'), [], 'しんやいちじ'),
    ('umarekawari control', '生まれ変わったら何になろうかな',
     words('生まれ変わったら', '何に', 'なろうかな'),
     [card('生まれ変わったら', 'うまれかわったら'), card('何', 'なに')],
     'うまれかわったらなにになろうかな'),
]

# moraize() small-vowel cases: (input, expected morae)
MORAIZE = [
    ('うぇいゔ', ['うぇ', 'い', 'ゔ']),
    ('ウェイヴ', ['ウェ', 'イ', 'ヴ']),
    ('ふぁいと', ['ふぁ', 'い', 'と']),
    ('きゃー', ['きゃー']),
    ('かあさん', ['か', 'あ', 'さ', 'ん']),
]

# particle romaji: joined roms for the には window must say niwa, display には
PARTICLE = ('日には', words('日には'), [card('日', 'ひ')], 'ひには', ['hi', 'ni', 'wa'])


def main():
    if c2d.TAGGER is None:
        print('FAIL: fugashi unavailable — run under the parler env')
        return 1
    bad = 0
    for name, text, ws, cards, want in CASES:
        cards = sorted(cards, key=lambda w: -len(w['_jp']))
        planned = c2d.line_reading_plan(text, ws, cards)
        if planned is None:
            print(f'✗ {name}: plan is None')
            bad += 1
            continue
        plan, merged = planned
        got = ''.join(plan[i]['reading'] for i in range(len(ws)) if i in plan)
        mark = '✓' if got == want else '✗'
        if got != want:
            bad += 1
        print(f'{mark} {name}: {got!r}' + ('' if got == want else f'  (want {want!r})'))

    for inp, want in MORAIZE:
        got = c2d.moraize(inp)
        mark = '✓' if got == want else '✗'
        if got != want:
            bad += 1
        print(f'{mark} moraize {inp!r}: {got!r}' + ('' if got == want else f'  (want {want!r})'))

    text, ws, cards, want_kana, want_roms = PARTICLE
    plan, _merged = c2d.line_reading_plan(text, ws, cards)
    kts = c2d.timed_morae(text, 0, 1000, reading=plan[0]['reading'],
                          rom_hints=plan[0]['hints'])
    got_kana = ''.join(k['kana'] for k in kts)
    got_roms = [k['rom'] for k in kts]
    ok = got_kana == want_kana and got_roms == want_roms
    if not ok:
        bad += 1
    print(f'{"✓" if ok else "✗"} particle rom: kana={got_kana!r} roms={got_roms!r}'
          + ('' if ok else f'  (want {want_kana!r} / {want_roms!r})'))

    # rendaku romaji: づ mora romanizes zu, display stays づ
    r = c2d.mora_rom('づ')
    ok = r == 'zu'
    if not ok:
        bad += 1
    print(f'{"✓" if ok else "✗"} mora_rom づ -> {r!r} (want zu)')

    print(f'\n{"ALL GREEN" if not bad else str(bad) + " FAILURE(S)"}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
