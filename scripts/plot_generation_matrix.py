"""Standalone scientific figure of an already complete formal matrix."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('summary', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = json.loads(args.summary.read_text(encoding='utf-8'))
    if report['stage'] != 'formal' or not report['matrix_complete']:
        raise ValueError('Only complete independently audited formal matrices can be plotted')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    names = list(report['aggregates'])
    labels = {'native': 'Native', 'owned-immediate': 'Owned immediate',
              'h2p5-d64': 'Ledger h2.5 / d64', 'h5p0-d1': 'Ledger h5.0 / d1'}
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    colors = ['#506b8a', '#a07848', '#568f7b', '#826c9f']
    for ax, (metric, title, scale, unit) in zip(axes.flat[:3], (
            ('ttft_ms_p95', 'TTFT P95', 1000, 'seconds'),
            ('ready_to_end_ms_p95', 'Ready-to-end P95', 1000, 'seconds'),
            ('prefill_tokens', 'Computed prefill', 1000, 'thousand tokens'))):
        values = [report['aggregates'][n][metric]['mean'] / scale for n in names]
        errors = [(report['aggregates'][n][metric]['stdev'] or 0) / scale for n in names]
        ax.bar(range(len(names)), values, yerr=errors, capsize=4, color=colors)
        for x, name in enumerate(names):
            samples = report['aggregates'][name][metric]['individual']
            ax.scatter([x + offset for offset in (-.09, 0, .09)],
                       [v / scale for v in samples], s=16, color='#202020', zorder=4)
        for x, value in enumerate(values):
            ax.annotate(f'{value:.2f}', (x, value), xytext=(0, 9),
                        textcoords='offset points', ha='center', fontsize=9)
        ax.set_title(title)
        ax.set_ylabel(unit)
        ax.set_xticks(range(len(names)), [labels[n] for n in names], rotation=12)
        ax.grid(axis='y', alpha=.2)
        ax.set_axisbelow(True)
        ax.margins(y=.2)
    ax = axes.flat[3]
    for offset, direction, color in ((-.18, 'h2d_bytes', '#527998'), (.18, 'd2h_bytes', '#b28a55')):
        values = [report['aggregates'][n].get(direction, {}).get('mean', 0) / 2**30 for n in names]
        ax.bar([x + offset for x in range(len(names))], values, width=.36,
               label=direction[:3].upper(), color=color)
    ax.text(0, 0, 'N/A', ha='center', va='bottom', fontsize=9)
    ax.set_title('CPU / GPU staging traffic')
    ax.set_ylabel('GiB')
    ax.set_xticks(range(len(names)), [labels[n] for n in names], rotation=12)
    ax.legend(frameon=False)
    ax.grid(axis='y', alpha=.2)
    ax.set_axisbelow(True)
    fig.suptitle(report['signature']['scope'] + '\n3 repetitions; bars = mean, error bars = sample SD', fontsize=13)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180)
    plt.close(fig)


if __name__ == '__main__':
    main()
