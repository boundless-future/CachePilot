"""Post-hoc phase description of complete, independently audited reference matrices."""
import argparse
import json
from pathlib import Path
import statistics


def phase_for(row):
    if row["session_id"].startswith("pressure-") and row["turn"] == 0:
        return "pressure"
    if row["session_id"].startswith("s") and row["turn"] in range(5):
        return "revisit" if row["turn"] == 4 else "early"
    raise ValueError("Unrecognized staged event; refusing implicit phase assignment")


def phase_metrics(rows):
    groups = {phase: [r for r in rows if phase_for(r) == phase]
              for phase in ("early", "pressure", "revisit")}
    if {phase: len(group) for phase, group in groups.items()} != dict(early=48, pressure=8, revisit=12):
        raise ValueError("Expected the unchanged 48/8/12 staged request composition")
    return {phase: dict(requests=len(group), **{
        key + "_p95": statistics.quantiles([r[key] for r in group], n=100, method="inclusive")[94]
        for key in ("ttft_ms", "ready_to_first_ms", "ready_to_end_ms", "client_queue_ms")})
        for phase, group in groups.items()}


def collect(directory):
    from summarize_reference_matrix import collect as audit
    matrix = audit(directory)
    if not matrix["matrix_complete"] or matrix["stage"] != "formal":
        raise ValueError("Phase description requires a complete formal matrix")
    rows = []
    for cell in matrix["rows"]:
        data = json.loads((directory / cell["cell"] / "requests.json").read_text(encoding="utf-8"))
        rows.append(dict(cell=cell["cell"], case=cell["case"], repeat=cell["repeat"],
                         phases=phase_metrics(data["results"])))
    aggregates = {}
    for name in matrix["aggregates"]:
        group = [r for r in rows if r["case"] == name]
        aggregates[name] = {}
        for phase in group[0]["phases"]:
            aggregates[name][phase] = {}
            for key in group[0]["phases"][phase]:
                values = [r["phases"][phase][key] for r in group]
                aggregates[name][phase][key] = dict(individual=values,
                    mean=statistics.mean(values), stdev=statistics.stdev(values) if len(values) > 1 else None)
    return dict(matrix_complete=True, trace_sha256=matrix["signature"]["trace_sha256"],
        rows=rows, aggregates=aggregates,
        scope="Post-hoc descriptive phase split; no selection, significance, or DMA causality claim")


def plot(report, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    names = list(report["aggregates"])
    labels = ["Native", "Immediate", "Fixed 2.5/64", "Fixed 5.0/1"]
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.4))
    for axis, phase in zip(axes, ("early", "pressure", "revisit")):
        for i, name in enumerate(names):
            metric = report["aggregates"][name][phase]["ttft_ms_p95"]
            axis.scatter([i - .10, i, i + .10], metric["individual"], color="#4169A1", s=25)
            axis.hlines(metric["mean"], i-.25, i+.25, color="#C24B40", linewidth=2)
        axis.set_xticks(range(len(names)), labels, rotation=25, ha="right")
        axis.set_title(phase.capitalize())
        largest = max(max(report["aggregates"][name][phase]["ttft_ms_p95"]["individual"])
                      for name in names)
        axis.set_ylim(0, largest * 1.12)
        axis.grid(axis="y", alpha=.2)
    axes[0].set_ylabel("Client TTFT P95 (ms)")
    from run_arrival_matrix import BASE_TRACE_SHA
    workload = "Original arrivals" if report["trace_sha256"] == BASE_TRACE_SHA else "Compressed arrivals"
    fig.suptitle(workload + ": post-hoc phase description")
    fig.text(.5, .01, "Dots: three separate repeats. Red line: mean of P95. No confidence interval or significance claim.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0,.045,1,.94))
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plot", type=Path)
    args = parser.parse_args()
    result = collect(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.plot:
        plot(result, args.plot)
    print(json.dumps({"complete_cells": len(result["rows"]), "cases": list(result["aggregates"])}))


if __name__ == "__main__":
    main()
