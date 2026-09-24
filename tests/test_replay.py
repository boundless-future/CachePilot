import asyncio
import hashlib
import sys
import unittest
from pathlib import Path

from aiohttp import web
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from replay_trace import replay,consume_sse
from generate_trace import validate_trace,generate
from analyze_replay import counters,percentile


def trace():
    events=[]
    for turn in range(2):
        prompt='x'*(turn+1)
        events.append(dict(event_id=turn,session_id='s',turn=turn,arrival_ms=0,
            think_ms=40,prompt=prompt,prompt_tokens=len(prompt),
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),max_tokens=1,seed=42))
    return dict(trace_id='test',events=events)


class TimingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def handler(request):
            import json
            payload=await request.json()
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
            await response.prepare(request)
            # Empty choices must not be mistaken for the first token.
            await response.write(b'data: {"choices":[]}\n\n')
            await asyncio.sleep(.04)
            message='data: '+json.dumps({'choices':[{'text':'hello'}]})+'\n\n'
            # Split across network writes; receiver must reconstruct the line.
            await response.write(message[:12].encode());await response.write(message[12:].encode())
            usage={'prompt_tokens':len(payload['prompt']),'completion_tokens':1}
            await response.write(('data: '+json.dumps({'choices':[],'usage':usage})+'\n\n').encode())
            await response.write(b'data: [DONE]\n\n');return response
        app=web.Application();app.router.add_post('/v1/completions',handler)
        self.runner=web.AppRunner(app);await self.runner.setup()
        self.site=web.TCPSite(self.runner,'127.0.0.1',0);await self.site.start()
        self.url='http://127.0.0.1:'+str(self.site._server.sockets[0].getsockname()[1])

    async def asyncTearDown(self): await self.runner.cleanup()

    async def test_closed_loop_respects_think_time(self):
        r=(await replay(trace(),self.url,'test','closed'))['results']
        self.assertTrue(all(x['error'] is None for x in r))
        self.assertGreaterEqual(r[1]['scheduled_ms']-r[0]['finished_ms'],39)
        self.assertGreater(r[0]['ttft_ms'],30)

    async def test_open_loop_records_client_queue(self):
        r=(await replay(trace(),self.url,'test','open',limit=1))['results']
        self.assertEqual(r[0]['scheduled_ms'],r[1]['scheduled_ms'])
        self.assertGreater(r[1]['client_queue_ms'],30)
        self.assertGreater(r[1]['ready_to_first_ms'],r[1]['ttft_ms']+30)

    async def test_truncated_stream_fails(self):
        async def broken(): yield b'data: {"choices":[]}\n'
        with self.assertRaises(RuntimeError): await consume_sse(broken(),lambda _:None)


class SchemaTests(unittest.TestCase):
    def test_generator_is_deterministic_and_append_only(self):
        class CharTokenizer:
            def encode(self,s,add_special_tokens=False): return list(map(ord,s))
            def decode(self,ids): return ''.join(map(chr,ids))
        a=generate(CharTokenizer(),3,4,64)
        self.assertEqual(a,generate(CharTokenizer(),3,4,64))
        validate_trace(a)
        self.assertEqual(len(a['events']),12)

    def test_detect_duplicate_turn(self):
        t=trace();t['events'].append(t['events'][0])
        with self.assertRaises(AssertionError):validate_trace(t)

    def test_detect_changed_prompt(self):
        t=trace();t['events'][1]['prompt']='changed'
        with self.assertRaises(AssertionError):validate_trace(t)

    def test_token_arrays_extend_or_branch_explicitly(self):
        import json
        t=trace()
        for e in t['events']:
            e['prompt']=[7]*(e['turn']+1)
            e['prompt_sha256']=hashlib.sha256(json.dumps(e['prompt'],separators=(',',':')).encode()).hexdigest()
        validate_trace(t)
        t['events'][1]['prompt']=[8,9]
        t['events'][1]['prompt_sha256']=hashlib.sha256(b'[8,9]').hexdigest()
        with self.assertRaises(AssertionError):validate_trace(t)
        t['append_only']=False
        validate_trace(t)

    def test_metrics_and_percentile(self):
        self.assertEqual(counters('# comment\nx{a="b"} 2\nx{a="c"} 3')['x'],5)
        self.assertEqual(percentile([10,20,30],50),20)


if __name__=='__main__': unittest.main()
