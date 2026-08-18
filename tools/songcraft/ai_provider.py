#!/usr/bin/env python3
"""ai_provider — the BYOM seam (four-verbs 1.1; design: docs/byom-design.md).

One call routes a drafting request through the configured backend:

    from ai_provider import complete, HandoffRequested, save_draft
    try:
        text = complete('author_data', prompt, system=sys_prompt)
    except HandoffRequested as h:
        print(h.prompt)          # today's flow, byte-identical

Three backends behind that call:
  handoff  DEFAULT. Never returns — raises HandoffRequested carrying the
           fully-rendered prompt. Zero config, zero network, zero behavior
           change: a fresh clone behaves exactly as BUILDER.md documents.
  api      Anthropic Messages API, key from the repo .env
           (ANTHROPIC_API_KEY=). urllib only — no SDK install.
  local    Any OpenAI-compatible server (Ollama :11434, LM Studio :1234,
           llama-server :8080) via POST {base}/v1/chat/completions. The shim
           only CONNECTS — it never starts a server (the standing localhost
           policy: a self-started listener must live in tmux; that is the
           operator's job, not this module's).

Config precedence (byom-design.md §3):
  providers.json per-step entry -> providers.json "default" ->
  .env AI_PROVIDER -> handoff.
Any backend failure degrades to the handoff floor (HandoffRequested with a
note) — never a broken build state, never a silent pass.

The validators stay the gate either way: BYOM changes who DRAFTS, never who
APPROVES. Every draft is written to builds/<key>.ai_drafts/ BEFORE anything
touches content.json, so review/diff/rollback is a file operation.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILDS = HERE / 'builds'
ENV_PATH = ROOT / '.env'
PROVIDERS_PATH = HERE / 'providers.json'

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_API_MODEL = 'claude-opus-4-8'   # the teaching IS the product — don't
                                        # default to a cheaper drafter
DEFAULT_LOCAL_BASE = 'http://127.0.0.1:1234/v1'   # LM Studio
DEFAULT_LOCAL_MODEL = 'qwen3-30b-a3b'


class HandoffRequested(Exception):
    """The handoff backend's non-return: carries the fully-rendered prompt.
    The orchestrator catches it, prints the prompt, and stops at the gate —
    byte-identical to today's PROMPT: wall (with real values spliced in)."""

    def __init__(self, prompt, note=''):
        super().__init__(note or 'handoff')
        self.prompt = prompt
        self.note = note


class ProviderError(Exception):
    """A backend failed AFTER being explicitly selected. complete() catches
    this internally and degrades to HandoffRequested; it escapes only via
    complete(..., strict=True) (tests, cost probes)."""


def _read_env():
    """KEY=value pairs from the repo .env (gitignored; same file as
    GOOGLE_TTS_KEY). os.environ wins over the file."""
    conf = {}
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith('#') and '=' in ln:
                k, v = ln.split('=', 1)
                conf[k.strip()] = v.strip()
    conf.update({k: v for k, v in os.environ.items() if k.startswith(('AI_', 'ANTHROPIC_'))})
    return conf


def resolve(step):
    """The effective {provider, model, base_url, web_search} for one step.
    Precedence: providers.json[step] -> providers.json.default ->
    .env AI_PROVIDER -> handoff."""
    env = _read_env()
    layers = [{'provider': env.get('AI_PROVIDER', 'handoff'),
               'model': env.get('AI_MODEL', ''),
               'base_url': env.get('AI_LOCAL_BASE_URL', ''),
               'local_model': env.get('AI_LOCAL_MODEL', '')}]
    if PROVIDERS_PATH.exists():
        try:
            pj = json.loads(PROVIDERS_PATH.read_text())
            if isinstance(pj.get('default'), dict):
                layers.append(pj['default'])
            if isinstance(pj.get(step), dict):
                layers.append(pj[step])
        except Exception as e:
            # A corrupt config must not silently change WHO writes the
            # teaching. Fall to handoff loudly via the resolved provider.
            return {'provider': 'handoff', 'model': '', 'base_url': '',
                    'web_search': False,
                    'note': f'providers.json unreadable ({e}) — handoff'}
    out = {'provider': 'handoff', 'model': '', 'base_url': '', 'web_search': False}
    for layer in layers:
        for k in ('provider', 'model', 'base_url', 'web_search', 'local_model'):
            if layer.get(k) not in (None, ''):
                out[k] = layer[k]
    if out['provider'] == 'local':
        out['model'] = out.get('model') or out.get('local_model') or DEFAULT_LOCAL_MODEL
        out['base_url'] = out.get('base_url') or DEFAULT_LOCAL_BASE
    elif out['provider'] == 'api':
        out['model'] = out.get('model') or DEFAULT_API_MODEL
    return out


def save_draft(key, step, text, meta=None):
    """Audit trail: builds/<key>.ai_drafts/<step>-<ts>.json. Returns the path.
    Drafts land here BEFORE anything merges into content.json — draft is
    never done; the owner's review flips the step."""
    d = BUILDS / f'{key}.ai_drafts'
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'{step}-{time.strftime("%Y%m%d-%H%M%S")}.json'
    p.write_text(json.dumps({'step': step, 'key': key, 'ts': time.time(),
                             'meta': meta or {}, 'text': text},
                            ensure_ascii=False, indent=2))
    return p


def _post_json(url, payload, headers, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=dict({'Content-Type': 'application/json'},
                                              **headers))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _api_complete(cfg, prompt, system, max_tokens):
    env = _read_env()
    api_key = env.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ProviderError('api backend selected but no ANTHROPIC_API_KEY in .env')
    payload = {'model': cfg['model'], 'max_tokens': max_tokens,
               'messages': [{'role': 'user', 'content': prompt}]}
    if system:
        payload['system'] = system
    if cfg.get('web_search'):
        payload['tools'] = [{'type': 'web_search_20260209', 'name': 'web_search'}]
    try:
        data = _post_json(API_URL, payload,
                          {'x-api-key': api_key, 'anthropic-version': API_VERSION})
    except urllib.error.HTTPError as e:
        raise ProviderError(f'Anthropic API {e.code}: {e.read().decode()[:300]}')
    except Exception as e:
        raise ProviderError(f'Anthropic API unreachable: {type(e).__name__}: {e}')
    parts = [b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text']
    text = ''.join(parts)
    if not text:
        raise ProviderError(f'Anthropic API returned no text (stop_reason='
                            f'{data.get("stop_reason")!r})')
    return text, {'model': data.get('model'), 'usage': data.get('usage', {})}


def _local_complete(cfg, prompt, system, max_tokens):
    base = cfg['base_url'].rstrip('/')
    url = base + ('/chat/completions' if base.endswith('/v1') else '/v1/chat/completions')
    msgs = ([{'role': 'system', 'content': system}] if system else []) \
        + [{'role': 'user', 'content': prompt}]
    payload = {'model': cfg['model'], 'messages': msgs, 'max_tokens': max_tokens,
               'stream': False}
    try:
        data = _post_json(url, payload, {'Authorization': 'Bearer local'})
    except urllib.error.HTTPError as e:
        raise ProviderError(f'local server {e.code} at {url}: {e.read().decode()[:300]}')
    except Exception as e:
        raise ProviderError(f'local server unreachable at {url}: '
                            f'{type(e).__name__}: {e}')
    try:
        text = data['choices'][0]['message']['content'] or ''
    except Exception:
        raise ProviderError(f'local server returned unexpected shape: '
                            f'{str(data)[:200]}')
    # strip a <think>...</think> preamble (qwen3-class reasoning models)
    text = re.sub(r'^\s*<think>.*?</think>\s*', '', text, flags=re.S)
    if not text.strip():
        raise ProviderError('local server returned empty text')
    return text, {'model': data.get('model', cfg['model']),
                  'usage': data.get('usage', {})}


def complete(step, prompt, *, system='', max_tokens=32000, key=None, strict=False):
    """Route one drafting request through the configured backend for `step`.
    Returns the model text. handoff never returns (HandoffRequested). Any
    api/local failure ALSO raises HandoffRequested (with the failure in
    .note) unless strict=True — the failure fallback is the handoff floor,
    never a broken state. When `key` is given, the draft (and its cost line)
    is saved to builds/<key>.ai_drafts/ before returning."""
    cfg = resolve(step)
    if cfg['provider'] == 'handoff':
        raise HandoffRequested(prompt, note=cfg.get('note', ''))
    try:
        if cfg['provider'] == 'api':
            text, meta = _api_complete(cfg, prompt, system, max_tokens)
        elif cfg['provider'] == 'local':
            text, meta = _local_complete(cfg, prompt, system, max_tokens)
        else:
            raise ProviderError(f'unknown provider {cfg["provider"]!r}')
    except ProviderError as e:
        if strict:
            raise
        raise HandoffRequested(prompt, note=f'{cfg["provider"]} backend failed '
                                            f'({e}) — degraded to handoff')
    meta['provider'] = cfg['provider']
    usage = meta.get('usage') or {}
    n_in = usage.get('input_tokens') or usage.get('prompt_tokens') or 0
    n_out = usage.get('output_tokens') or usage.get('completion_tokens') or 0
    meta['cost_line'] = (f'{step}: {cfg["provider"]}/{meta.get("model")} '
                         f'{n_in} in / {n_out} out tokens')
    if key:
        meta['draft_path'] = str(save_draft(key, step, text, meta))
    return text


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'resolve':
        for s in (sys.argv[2:] or ['author_data', 'podcast', 'line_tr_draft']):
            print(f'{s}: {resolve(s)}')
    else:
        print(__doc__)
