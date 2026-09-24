"""Linux GPU smoke suite; owns its services and stops them even on failure.

Run inside the cachepilot Conda environment. Produces functional evidence,
not a benchmark: one greedy prompt, repeated GPU hit, then CPU hit after
engine restart. EVICTION_AWARE is exercised under actual GPU KV pressure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("VLLM_PORT", "8000"))
API = f"http://127.0.0.1:{PORT}"
CACHE = "http://127.0.0.1:8080"
SENTENCE = "In a multi-turn assistant session, reusable key and value states can avoid recomputing the same context."
PROMPT = " ".join(f"Sentence {i}: {SENTENCE}" for i in range(60))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def counter(metrics, name):
    return sum(float(line.rsplit(" ", 1)[1]) for line in metrics.splitlines()
               if line.startswith(name + "{") or line.startswith(name + " "))


def get_text(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.text


class Service:
    def __init__(self, command, log):
        self.command, self.log = command, log
        self.process = self.handle = None

    def start(self, health, timeout=900):
        self.handle = self.log.open("w")
        self.process = subprocess.Popen(self.command, cwd=ROOT, stdout=self.handle,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Service exited: {self.log}")
            try:
                if requests.get(health, timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(1)
        raise TimeoutError(f"Service readiness timeout: {self.log}")

    def stop(self):
        if self.process:
            try:
                # Signal only the parent: vLLM forwards SIGTERM itself.
                # Signalling the group can send EngineCore a second SIGTERM
                # after it resets handlers during teardown, skipping IPC cleanup.
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)
            # Kill surviving children in this service's own process group.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.handle:
            self.handle.close()


def request(out, name, prompt=PROMPT):
    payload = dict(model=os.environ.get("MODEL_PATH", str(ROOT / "models/Qwen3-4B")), prompt=prompt,
                   temperature=0, seed=42, max_tokens=32, ignore_eos=True)
    start = time.monotonic()
    response = requests.post(API + "/v1/completions", json=payload, timeout=180)
    data = response.json()
    write_json(out / f"{name}.json", dict(payload=payload, response=data,
                http_status=response.status_code, elapsed_seconds=time.monotonic() - start))
    response.raise_for_status()
    return data


def snapshot(out, name, cache=True):
    vllm = get_text(API + "/metrics")
    (out / f"{name}-vllm.prom").write_text(vllm)
    if cache:
        metrics = get_text(CACHE + "/metrics")
        (out / f"{name}-lmcache.prom").write_text(metrics)
        write_json(out / f"{name}-status.json", json.loads(get_text(CACHE + "/status")))
    else:
        metrics = ""
    return vllm, metrics


def run_mode(mode, out, eager, expected):
    out.mkdir()
    command = ["bash", str(ROOT / "scripts/serve.sh"), mode]
    if eager:
        command.append("--enforce-eager")
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    engine = Service(command, out / "vllm-cold.log")
    result = dict(mode=mode, eager=eager, kv_cache_bytes=int(os.environ.get("KV_CACHE_BYTES", 2147483648)),
                  prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest())
    try:
        print(f"[{mode}] starting services", flush=True)
        if mode != "baseline":
            cache.start(CACHE + "/status")
        engine.start(API + "/health")
        cold = request(out, "cold")
        before_hot, _ = snapshot(out, "cold", mode != "baseline")
        hot = request(out, "hot")
        after_hot, _ = snapshot(out, "hot", mode != "baseline")
        text = cold["choices"][0]["text"]
        assert text == hot["choices"][0]["text"], "cold/hot output mismatch"
        if expected is not None:
            assert text == expected, "output differs from native vLLM baseline"
        result.update(prompt_tokens=cold["usage"]["prompt_tokens"], output_text=text,
                      gpu_hit_tokens=counter(after_hot, "vllm:prefix_cache_hits_total") -
                                     counter(before_hot, "vllm:prefix_cache_hits_total"))
        assert result["gpu_hit_tokens"] > 0, "no GPU prefix hit"
        if mode != "baseline":
            # Different first tokens prevent all pressure requests sharing a prefix.
            # 16 x ~1,550 tokens exceeds the fixed 2 GiB GPU KV pool (~14,500 tokens).
            pressure_count = 16 if mode == "eviction" else 2
            for i in range(pressure_count):
                request(out, f"pressure-{i:02d}", f"Workload-{i}: " + PROMPT)
            _, stored = snapshot(out, "stored")
            result["stores_completed"] = counter(stored, "lmcache_mp_num_finished_stores_total")
            assert result["stores_completed"] > 0, "CPU store not observed"
            print(f"[{mode}] stored; restarting vLLM to clear GPU cache", flush=True)
            engine.stop()
            deadline = time.monotonic() + 15
            while True:
                state = json.loads(get_text(CACHE + "/status"))
                if not state.get("registered_gpu_ids"):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Old engine GPU IPC registration was not released")
                time.sleep(0.5)
            write_json(out / "before-restart-status.json", state)
            engine = Service(command, out / "vllm-retrieve.log")
            engine.start(API + "/health")
            before_v, before_l = snapshot(out, "before-retrieve")
            restored = request(out, "retrieve")
            # Allow server-side transfer completion metric subscriber to catch up.
            deadline = time.monotonic() + 10
            while True:
                after_v, after_l = snapshot(out, "after-retrieve")
                retrieved = counter(after_l, "lmcache_mp_num_finished_retrieves_total") - counter(before_l, "lmcache_mp_num_finished_retrieves_total")
                if retrieved > 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
            result.update(retrieve_transfers=retrieved,
                external_hit_tokens=counter(after_v, "vllm:external_prefix_cache_hits_total") - counter(before_v, "vllm:external_prefix_cache_hits_total"),
                gpu_hit_tokens_after_restart=counter(after_v, "vllm:prefix_cache_hits_total") - counter(before_v, "vllm:prefix_cache_hits_total"))
            assert retrieved > 0 and result["external_hit_tokens"] >= 1536, "CPU prefix retrieve not proved"
            assert result["gpu_hit_tokens_after_restart"] == 0, "unexpected GPU cache hit after restart"
            assert restored["choices"][0]["text"] == text, "CPU retrieve output mismatch"
            repeat = request(out, "hot-after-retrieve")  # Wheel's request-finished failure path.
            assert repeat["choices"][0]["text"] == text, "hot-after-retrieve output mismatch"
        result["passed"] = True
        print(f"[{mode}] PASS", flush=True)
    except Exception as exc:
        result.update(passed=False, error=repr(exc))
        raise
    finally:
        engine.stop()
        if mode != "baseline" and cache.process and cache.process.poll() is None:
            try:
                write_json(out / "after-engine-stop-status.json", json.loads(get_text(CACHE + "/status")))
            except requests.RequestException:
                pass
        cache.stop()
        write_json(out / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", nargs="+", choices=["baseline", "immediate", "fifo", "eviction"],
                        default=["baseline", "immediate", "fifo", "eviction"])
    parser.add_argument("--eager", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Refuse to interfere with services started by the user.
    for port in (PORT, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied; stop its service before running the suite")
    args.output.mkdir(parents=True, exist_ok=False)
    results, expected = [], None
    for mode in args.modes:
        result = run_mode(mode, args.output / mode, args.eager, expected)
        if mode == "baseline":
            expected = result["output_text"]
        results.append(result)
        write_json(args.output / "summary.json", results)


if __name__ == "__main__":
    main()
