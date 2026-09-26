"""Audit corrected signals against the independent diagnostic allocator ledger."""
import argparse
import json
from pathlib import Path
from analyze_decision_timeline import analyze as timeline


def audit(rows):
    pending = current = allocated = consumed = signals = 0
    step_actual = 0
    errors, corrections, idle_allocations = [], [], []
    corrected_by_step = {}
    for row in rows:
        event = row['event']
        if event == 'allocation':
            count = row['allocated_blocks']
            pending += count
            current += count
            allocated += count
        elif event == 'scheduled':
            step_actual = current
            if row['actual_allocated_blocks'] != current:
                errors.append(f"Step {row['step']}: scheduled allocation count differs")
            if row['tokens'] == 0 and current:
                idle_allocations.append(dict(step=row['step'],blocks=current))
            current = 0
        elif event == 'allocation_signal':
            signals += 1
            consumed += row['consumed_blocks']
            expected = dict(actual_step_blocks=step_actual, consumed_blocks=pending,
                            total_allocated=allocated, total_consumed=consumed)
            for key, value in expected.items():
                if row[key] != value:
                    errors.append(f"Step {row['step']}: {key}={row[key]}, expected {value}")
            corrected_by_step[row['step']] = row['consumed_blocks']
            if row['original_new_blocks'] != pending:
                corrections.append({k: row[k] for k in ['step','original_new_blocks',
                    'actual_step_blocks','consumed_blocks']})
            pending = 0
        elif event == 'drain':
            if row['new_blocks'] != corrected_by_step.get(row['step']):
                errors.append(f"Step {row['step']}: policy did not receive corrected signal")
    if not allocated or not signals:
        errors.append('No nonzero allocation or corrected drain evidence')
    return dict(passed=not errors,errors=errors,total_allocated=allocated,
                total_consumed=consumed,unconsumed_blocks=pending,
                corrected_drains=signals,changed_signals=corrections,
                zero_token_allocation_steps=idle_allocations)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('ledger',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.ledger.read_text().splitlines()]
    result = dict(signal_audit=audit(rows),timeline=timeline(rows))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['signal_audit'],indent=2))
    if not result['signal_audit']['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
