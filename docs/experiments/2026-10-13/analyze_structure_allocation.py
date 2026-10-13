"""Describe recorded gross allocations; never infer safe counterfactual pins."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('audit', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = json.loads(args.audit.read_text(encoding='utf-8'))
    assert report['diagnostic_passed'] and not report['performance']
    args.output.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    summary = []
    for ax, row in zip(axes, report['rows']):
        values = row['allocation_summary']['reserve_vs_actual']
        known = [r for r in values if r['reserve'] is not None]
        nonzero = [r for r in values if r['allocated']]
        over = [r for r in known if r['reserve'] > row['allocation_summary']['usable_blocks']]
        zero = [r for r in values if r['scheduled_tokens'] == 0 and r['allocated']]
        summary.append(dict(case=row['case'], steps=len(values), known_steps=len(known),
            allocating_steps=len(nonzero), no_gross_allocation_steps=len(values)-len(nonzero),
            over_pool_steps=len(over), over_pool_allocating_steps=sum(r['allocated'] > 0 for r in over),
            zero_compute_allocation_steps=zero,
            scope='Descriptive within this instrumented run; excludes touch/free and future demand.'))
        ax.plot([r['step'] for r in known], [r['reserve'] for r in known],
                linewidth=0.9, color='#5267c5', label='Original reserve')
        ax.scatter([r['step'] for r in nonzero], [r['allocated'] for r in nonzero],
                   s=9, color='#bd5718', label='Gross new blocks (nonzero steps)')
        ax.axhline(row['allocation_summary']['usable_blocks'], linestyle='--', linewidth=1,
                   color='#555555', label='Usable physical pool')
        ax.set_title(row['case'], loc='left')
        ax.set_ylabel('Blocks')
        ax.grid(alpha=0.2)
    axes[0].legend(loc='upper right', fontsize=8)
    axes[1].set_xlabel('Scheduler step within independent diagnostic')
    fig.suptitle('Recorded reserve and gross allocation — not a safe reserve estimate')
    fig.tight_layout()
    fig.savefig(args.output/'shared-structure-allocation.png', dpi=160)
    plt.close(fig)
    (args.output/'shared-structure-allocation-analysis.json').write_text(
        json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
