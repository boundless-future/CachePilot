"""Generate fixed, tokenized, append-only synthetic tool-session traces."""
import argparse
import hashlib
import json
from pathlib import Path


def validate_trace(trace):
    seen, previous = set(), {}
    for event in trace['events']:
        key = (event['session_id'], event['turn'])
        assert key not in seen, 'duplicate session turn'
        seen.add(key)
        assert event['arrival_ms'] >= 0 and event['max_tokens'] > 0
        assert event['prompt_tokens'] > 0
        prompt=event['prompt']
        raw=prompt.encode() if isinstance(prompt,str) else json.dumps(prompt,separators=(',',':')).encode()
        assert event['prompt_sha256'] == hashlib.sha256(raw).hexdigest()
        last = previous.get(event['session_id'])
        assert event['turn'] == (last['turn'] + 1 if last else 0)
        if last:
            if trace.get('append_only',True):
                assert prompt[:len(last['prompt'])] == last['prompt'], 'prefix must extend'
            assert event['arrival_ms'] >= last['arrival_ms']
        previous[event['session_id']] = event


def generate(tokenizer, sessions, turns, context_tokens, period_ms=1800, think_ms=100):
    events, prompts = [], {}
    for sid in range(sessions):
        prefix = f'Session {sid:04d}. You are debugging a repository. Evidence follows.\n'
        words = ' '.join(f'Module {i}: deterministic interface tests pass and tool output is cached.' for i in range(context_tokens))
        ids = tokenizer.encode(prefix + words, add_special_tokens=False)[:context_tokens]
        prompts[sid] = tokenizer.decode(ids)
    for turn in range(turns):
        for sid in range(sessions):
            if turn:
                prompts[sid] += (f'\nAssistant recorded action: inspect module {turn}.\n'
                                 f'Tool recorded result: module {turn} tests pass.\n')
            prompts[sid] += f'\nUser turn {turn}: explain the next debugging step briefly.\nAssistant:'
            prompt = prompts[sid]
            events.append(dict(event_id=len(events), session_id=f's{sid:04d}', turn=turn,
                arrival_ms=turn*period_ms + sid*20, think_ms=think_ms, prompt=prompt,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                prompt_tokens=len(tokenizer.encode(prompt, add_special_tokens=False)),
                max_tokens=48, temperature=0, seed=42))
    trace = dict(schema_version=2, trace_id=f'synthetic-s{sessions}-t{turns}-c{context_tokens}',
        semantics='Fixed recorded prompts; generated output never feeds the next turn.',
        events=events)
    validate_trace(trace)
    return trace


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--model', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--sessions', type=int, default=12)
    p.add_argument('--turns', type=int, default=4)
    p.add_argument('--context-tokens', type=int, default=2048)
    a=p.parse_args()
    from transformers import AutoTokenizer
    t=generate(AutoTokenizer.from_pretrained(a.model),a.sessions,a.turns,a.context_tokens)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(t,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__': main()
