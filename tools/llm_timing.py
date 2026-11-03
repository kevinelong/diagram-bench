"""llm_timing.py - TTFT, decode and prefill rates for a local model, with the context it produced them at.

MACHINES-2026-11-02.md §3 asks for TTFT, decode tok/s, prefill tok/s and peak VRAM, reported *with* the context length
and the quantisation. Its canonical instrument is `llama-bench`, which needs a .gguf and a build of llama.cpp; this box
runs Ollama, so this reads the counters Ollama itself reports for a real streaming request:

  * TTFT is measured here, as the wall time to the first streamed chunk (prefill + first decode step).
  * decode tok/s and prefill tok/s come from the server's `eval_duration` / `prompt_eval_duration`, so they are not
    wall-clock guesses.
  * quantisation and parameter count come from `/api/show`.

Peak VRAM is sampled from nvidia-smi while the request runs, because "does it fit" is the question that decides which
models this card can carry at all.

    python tools/llm_timing.py --model qwen3-vl:4b --tokens 2048
"""
import argparse
import json
import subprocess
import threading
import time
import urllib.request

HOST = 'http://127.0.0.1:11434'
FILLER = ('The instrument records what the reader waits for: the click, the first token, the picture. '
          'A number without its context length and its quantisation is not comparable, so both are reported. ')


def _post(path, obj, timeout=600):
    req = urllib.request.Request(HOST + path, data=json.dumps(obj).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=timeout)


def model_facts(model):
    try:
        with _post('/api/show', {'model': model}) as r:
            info = json.load(r)
    except Exception as exc:
        return {'error': '%s: %s' % (type(exc).__name__, exc)}
    details = info.get('details') or {}
    return {'family': details.get('family'), 'parameters': details.get('parameter_size'),
            'quantization': details.get('quantization_level'), 'context_length': info.get('model_info', {}).get(
                '%s.context_length' % details.get('family', ''), None)}


def vram_sampler(stop, peak):
    while not stop.is_set():
        try:
            out = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            if out:
                peak[0] = max(peak[0], int(out.splitlines()[0]))
        except Exception:
            pass
        time.sleep(0.25)


def measure(model, tokens, num_predict=128, threads=None):
    prompt = (FILLER * ((tokens * 4) // len(FILLER) + 1))[:tokens * 4]
    stop, peak = threading.Event(), [0]
    threading.Thread(target=vram_sampler, args=(stop, peak), daemon=True).start()
    payload = {'model': model, 'prompt': prompt, 'stream': True,
               'options': {'num_predict': num_predict}}
    if threads:
        payload['options']['num_thread'] = threads
    t0 = time.time()
    ttft, final = None, {}
    try:
        with _post('/api/generate', payload) as r:
            for line in r:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if ttft is None and (chunk.get('response') or ''):
                    ttft = time.time() - t0
                if chunk.get('done'):
                    final = chunk
    finally:
        stop.set()
    wall = time.time() - t0
    pre_tok = final.get('prompt_eval_count') or 0
    pre_ns = final.get('prompt_eval_duration') or 0
    dec_tok = final.get('eval_count') or 0
    dec_ns = final.get('eval_duration') or 0
    return {
        'model': model,
        'facts': model_facts(model),
        'prompt_tokens': pre_tok,
        'generated_tokens': dec_tok,
        'ttft_s': round(ttft, 3) if ttft else None,
        'prefill_tok_s': round(pre_tok / (pre_ns / 1e9), 1) if pre_ns else None,
        'decode_tok_s': round(dec_tok / (dec_ns / 1e9), 1) if dec_ns else None,
        'wall_s': round(wall, 3),
        'peak_vram_mib': peak[0] or None,
        'threads': threads,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-vl:4b')
    ap.add_argument('--tokens', type=int, default=2048, help='prompt length to aim for, in tokens')
    ap.add_argument('--num-predict', type=int, default=128)
    ap.add_argument('--threads', type=int, default=None)
    a = ap.parse_args(argv)
    print(json.dumps(measure(a.model, a.tokens, a.num_predict, a.threads), indent=2))


if __name__ == '__main__':
    main()
