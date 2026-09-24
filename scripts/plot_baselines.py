"""Plot repeated-run latency and KV work; whiskers show observed min/max, not CI."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser();p.add_argument('summary',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();data=json.loads(a.summary.read_text())['aggregates']
    policies=['baseline','immediate','eviction','eviction-h5']
    colors=['#546e7a','#00796b','#1976d2','#ef6c00']
    fig,axes=plt.subplots(2,2,figsize=(12,7.5),layout='constrained')
    for col,workload in enumerate(['fits-gpu','exceeds-gpu']):
        rows=[next(r for r in data if r['workload']==workload and r['policy']==p) for p in policies]
        stats=[r['metrics']['reuse_ttft_ms_p95'] for r in rows]
        means=[s['mean'] for s in stats]
        axes[0,col].bar(policies,means,color=colors,yerr=[[s['mean']-s['min'] for s in stats],[s['max']-s['mean'] for s in stats]],capsize=4)
        axes[0,col].set_title(workload+' | reuse TTFT P95')
        axes[0,col].set_ylabel('milliseconds (mean of 3 run P95s)')
        axes[1,col].bar(policies,[r['metrics']['prefill_computed_tokens']['mean'] for r in rows],color=colors)
        axes[1,col].set_title('Prefill tokens actually computed')
        axes[1,col].set_ylabel('tokens per run')
        for ax in axes[:,col]:
            ax.tick_params(axis='x',rotation=15);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.suptitle('Qwen3-4B / RTX 4090 | fixed open-loop trace | 2 GiB GPU KV\nWhiskers: observed range across 3 runs; no confidence interval',fontsize=12)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(a.output,dpi=180)


if __name__=='__main__':main()
