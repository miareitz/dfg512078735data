#!/usr/bin/env python3
"""Parse RF26b LCS cluster logs into CSVs and gnuplot inputs.

The parser intentionally counts the actual ``injecting SDC`` lines in each log
instead of trusting the ``-m`` filename parameter. Missing 40-rank configurations
are emitted as explicit placeholder rows so downstream plots keep the full grid.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


CsvValue = str | int | float
Row = dict[str, CsvValue]


class Args(argparse.Namespace):
    log_dir: Path
    out_dir: Path | None

    def __init__(self) -> None:
        super().__init__()
        self.log_dir = Path(__file__).resolve().parent
        self.out_dir = None


LOG_NAME_RE = re.compile(
    r"^itoyorifutures-numNodes(?P<num_nodes>\d+)-"
    + r"numCores(?P<num_cores>\d+)-totalCores(?P<total_cores>\d+)-"
    + r"(?P<system>.+)-n(?P<input_size>\d+)-r(?P<repeats>\d+)-"
    + r"c(?P<check_enabled>\d+)-s(?P<sdc_rate>\d+\.\d+)-"
    + r"m(?P<requested_sdc>\d+)\.out\.(?P<job_id>\d+)$"
)

INT = r"([\d,]+)"
FLOAT = r"([\d.]+)"

SIMPLE_PATTERNS = {
    "processes": re.compile(r"# of processes:\s+(\d+)"),
    "input_size_runtime": re.compile(r"N \(Input size\):\s+(\d+)"),
    "repeats_runtime": re.compile(r"# of repeats:\s+(\d+)"),
    "check_enabled_runtime": re.compile(r"Check enabled:\s+(\d+)"),
    "max_injected_sdcs": re.compile(r"Max injected SDCs:\s+(\d+)"),
    "cutoff": re.compile(r"Cutoff:\s+(\d+)"),
    "first_tree_ns": re.compile(rf"First tree duration {INT} ns = {FLOAT} s"),
    "twin_tree_ns": re.compile(rf"Twin tree duration {INT} ns = {FLOAT} s"),
    "traversal_ns": re.compile(rf"Traversal duration {INT} ns = {FLOAT} s"),
    "third_tree_ns": re.compile(rf"Third tree duration {INT} ns = {FLOAT} s"),
    "process_time_ns": re.compile(rf"Process time {INT} ns = {FLOAT} s"),
    "first_tree_tasks": re.compile(rf"First tree TOTAL TASKS: {INT}"),
    "twin_tree_tasks": re.compile(rf"Twin tree TOTAL TASKS: {INT}"),
    "third_tree_tasks": re.compile(rf"Third tree TOTAL TASKS: {INT}"),
}

PHASE_RE = re.compile(
    rf"Traversal phase breakdown root_exec_once={INT} clear_analysis={INT} "
    + rf"seed_needed={INT} propagate_needed={INT} precompute_direct={INT} "
    + rf"materialize_marked={INT}"
)

REPLAY_FORK_STATS_RE = re.compile(
    r"Replay fork stats "
    + r"exact_skip=(\d+) source_skip=(\d+) parent_drop=(\d+) child_null=(\d+) "
    + r"child_invalid=(\d+) child_validation_miss=(\d+) replay_exec=(\d+) "
    + r"needed=(\d+) value_mismatch=(\d+) source_invalid=(\d+) "
    + r"local_mismatch=(\d+) infected=(\d+)"
)

REPLAY_DECISION_GLOBAL_RE = re.compile(
    r"Replay decision causes global "
    + r"skip\(exact_leaf=(\d+) exact_recursive=(\d+) source_leaf=(\d+) source_alias=(\d+)\) "
    + r"exec\(incorrect_child=(\d+) cheap_false=(\d+) cheap_true_no_skip=(\d+)\)"
)

REPLAY_PROFILE_RE = re.compile(
    rf"Replay fork profile total={INT} parent_validate={INT} child_resolve={INT} "
    + rf"child_validate={INT} decision={INT}"
)

SYNC_PROFILE_RE = re.compile(
    rf"Replay sync profile sync_calls={INT} sync_total={INT} sync_done={INT} "
    + rf"sync_copy={INT} resume_calls={INT} resume_total={INT} resume_copy={INT}"
)

VALUE_MATCH_RE = re.compile(
    rf"Exact-proof values_match .*?full={INT} equal={INT} unequal={INT} "
    + rf"fetch_bytes={INT} avg_fetch={INT} .*?ns={INT}"
)

OUTPUT_MATCH_RE = re.compile(
    rf"Output-match paths recursive\(bridge={INT} visible={INT}\).*?"
    + rf"values_taken={INT} true={INT} false={INT}"
)

SDC_RE = re.compile(
    r"injecting SDC at tile=\((\d+),(\d+)\) field=([A-Za-z_]+) target=(\d+)"
)


RUN_FIELDS = [
    "status",
    "is_placeholder",
    "filename",
    "job_id",
    "num_nodes",
    "num_cores",
    "total_cores",
    "system",
    "input_size",
    "repeats",
    "check_enabled",
    "sdc_rate",
    "requested_sdc",
    "actual_sdc",
    "max_injected_sdcs",
    "cutoff",
    "first_tree_ns",
    "first_tree_s",
    "twin_tree_ns",
    "twin_tree_s",
    "traversal_ns",
    "traversal_s",
    "third_tree_ns",
    "third_tree_s",
    "process_time_ns",
    "process_time_s",
    "overhead_vs_first_s",
    "recovery_overhead_s",
    "first_tree_tasks",
    "twin_tree_tasks",
    "third_tree_tasks",
    "traversal_root_exec_once_ns",
    "traversal_clear_analysis_ns",
    "traversal_seed_needed_ns",
    "traversal_propagate_needed_ns",
    "traversal_precompute_direct_ns",
    "traversal_materialize_marked_ns",
    "replay_profile_total_ns",
    "replay_profile_parent_validate_ns",
    "replay_profile_child_resolve_ns",
    "replay_profile_child_validate_ns",
    "replay_profile_decision_ns",
    "sync_calls",
    "sync_total_ns",
    "sync_done_ns",
    "sync_copy_ns",
    "resume_calls",
    "resume_total_ns",
    "resume_copy_ns",
    "values_match_full",
    "values_match_equal",
    "values_match_unequal",
    "values_match_fetch_bytes",
    "values_match_avg_fetch",
    "values_match_ns",
    "output_match_bridge",
    "output_match_visible",
    "output_match_values_taken",
    "output_match_values_true",
    "output_match_values_false",
    "fork_exact_skip",
    "fork_source_skip",
    "fork_parent_drop",
    "fork_child_null",
    "fork_child_invalid",
    "fork_child_validation_miss",
    "fork_replay_exec",
    "fork_needed",
    "fork_value_mismatch",
    "fork_source_invalid",
    "fork_local_mismatch",
    "fork_infected",
    "decision_exact_leaf",
    "decision_exact_recursive",
    "decision_source_leaf",
    "decision_source_alias",
    "decision_incorrect_child",
    "decision_cheap_false",
    "decision_cheap_true_no_skip",
    "sdc_sites",
]

AGG_FIELDS = [
    "status",
    "is_placeholder",
    "num_nodes",
    "total_cores",
    "sdc_rate",
    "requested_sdc",
    "actual_sdc",
    "runs_total",
    "runs_ok",
    "runs_incomplete",
    "runs_failed",
    "process_time_s_mean",
    "process_time_s_median",
    "process_time_s_min",
    "process_time_s_max",
    "process_time_s_stddev",
    "first_tree_s_mean",
    "twin_tree_s_mean",
    "traversal_s_mean",
    "third_tree_s_mean",
    "overhead_vs_first_s_mean",
    "recovery_overhead_s_mean",
    "actual_sdc_mean",
    "fork_replay_exec_mean",
    "fork_source_skip_mean",
    "fork_value_mismatch_mean",
    "fork_infected_mean",
]

SCALING_FIELDS = [
    "status",
    "is_placeholder",
    "sdc_rate",
    "requested_sdc",
    "actual_sdc",
    "total_cores",
    "num_nodes",
    "runs_ok",
    "process_time_s_mean",
    "process_time_s_median",
    "speedup_vs_80_mean",
    "efficiency_vs_80_mean",
    "first_tree_s_mean",
    "twin_tree_s_mean",
    "traversal_s_mean",
    "third_tree_s_mean",
    "overhead_vs_first_s_mean",
    "recovery_overhead_s_mean",
]


@dataclass
class ParsedRun:
    values: Row = field(default_factory=dict)

    def set_int(self, key: str, value: str) -> None:
        self.values[key] = int(value.replace(",", ""))

    def set_float_from_ns(self, seconds_key: str, ns_key: str) -> None:
        ns = self.values.get(ns_key)
        self.values[seconds_key] = float(ns) / 1_000_000_000 if isinstance(ns, int) else ""

    def row(self) -> Row:
        return {key: self.values.get(key, "") for key in RUN_FIELDS}


def parse_args() -> tuple[Path, Path | None]:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "log_dir",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory containing RF26b .out logs (default: script directory)",
    )
    _ = parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: <log_dir>/csv)",
    )
    args = parser.parse_args(namespace=Args())
    log_dir = args.log_dir
    out_dir = args.out_dir
    return log_dir, out_dir


def parse_int(value: str) -> int:
    return int(value.replace(",", ""))


def value_as_int(value: CsvValue) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if value:
        return int(value)
    raise ValueError("empty value cannot be converted to int")


def value_as_float(value: CsvValue) -> float:
    if isinstance(value, int | float):
        return float(value)
    if value:
        return float(value)
    raise ValueError("empty value cannot be converted to float")


def status_for(values: Row, text: str) -> str:
    if values.get("process_time_ns"):
        return "ok"
    if "Could not allocate memory" in text or "MPI_ERR" in text or "error" in text.lower():
        return "failed"
    return "incomplete"


def parse_log(path: Path) -> ParsedRun:
    match = LOG_NAME_RE.match(path.name)
    if not match:
        raise ValueError(f"unexpected RF26b filename: {path.name}")

    run = ParsedRun()
    run.values.update(
        {
            "is_placeholder": 0,
            "filename": path.name,
            "job_id": match.group("job_id"),
            "num_nodes": int(match.group("num_nodes")),
            "num_cores": int(match.group("num_cores")),
            "total_cores": int(match.group("total_cores")),
            "system": match.group("system"),
            "input_size": int(match.group("input_size")),
            "repeats": int(match.group("repeats")),
            "check_enabled": int(match.group("check_enabled")),
            "sdc_rate": match.group("sdc_rate"),
            "requested_sdc": int(match.group("requested_sdc")),
        }
    )

    text = path.read_text(errors="replace")
    sdc_sites: list[str] = []
    for line in text.splitlines():
        sdc_match = SDC_RE.search(line)
        if sdc_match:
            x, y, field_name, target = sdc_match.groups()
            sdc_sites.append(f"({x},{y}):{field_name}:{target}")

        for key, pattern in SIMPLE_PATTERNS.items():
            simple = pattern.search(line)
            if simple:
                value = simple.group(1)
                if key.endswith("_ns") or key.endswith("_tasks") or key in {
                    "processes",
                    "input_size_runtime",
                    "repeats_runtime",
                    "check_enabled_runtime",
                    "max_injected_sdcs",
                    "cutoff",
                }:
                    run.set_int(key, value)

        phase = PHASE_RE.search(line)
        if phase:
            keys = [
                "traversal_root_exec_once_ns",
                "traversal_clear_analysis_ns",
                "traversal_seed_needed_ns",
                "traversal_propagate_needed_ns",
                "traversal_precompute_direct_ns",
                "traversal_materialize_marked_ns",
            ]
            for key, value in zip(keys, phase.groups(), strict=True):
                run.set_int(key, value)

        profile = REPLAY_PROFILE_RE.search(line)
        if profile:
            keys = [
                "replay_profile_total_ns",
                "replay_profile_parent_validate_ns",
                "replay_profile_child_resolve_ns",
                "replay_profile_child_validate_ns",
                "replay_profile_decision_ns",
            ]
            for key, value in zip(keys, profile.groups(), strict=True):
                run.set_int(key, value)

        sync = SYNC_PROFILE_RE.search(line)
        if sync:
            keys = [
                "sync_calls",
                "sync_total_ns",
                "sync_done_ns",
                "sync_copy_ns",
                "resume_calls",
                "resume_total_ns",
                "resume_copy_ns",
            ]
            for key, value in zip(keys, sync.groups(), strict=True):
                run.set_int(key, value)

        value_match = VALUE_MATCH_RE.search(line)
        if value_match:
            keys = [
                "values_match_full",
                "values_match_equal",
                "values_match_unequal",
                "values_match_fetch_bytes",
                "values_match_avg_fetch",
                "values_match_ns",
            ]
            for key, value in zip(keys, value_match.groups(), strict=True):
                run.set_int(key, value)

        output_match = OUTPUT_MATCH_RE.search(line)
        if output_match:
            keys = [
                "output_match_bridge",
                "output_match_visible",
                "output_match_values_taken",
                "output_match_values_true",
                "output_match_values_false",
            ]
            for key, value in zip(keys, output_match.groups(), strict=True):
                run.set_int(key, value)

        fork_stats = REPLAY_FORK_STATS_RE.search(line)
        if fork_stats:
            keys = [
                "fork_exact_skip",
                "fork_source_skip",
                "fork_parent_drop",
                "fork_child_null",
                "fork_child_invalid",
                "fork_child_validation_miss",
                "fork_replay_exec",
                "fork_needed",
                "fork_value_mismatch",
                "fork_source_invalid",
                "fork_local_mismatch",
                "fork_infected",
            ]
            for key, value in zip(keys, fork_stats.groups(), strict=True):
                run.set_int(key, value)

        decision = REPLAY_DECISION_GLOBAL_RE.search(line)
        if decision:
            keys = [
                "decision_exact_leaf",
                "decision_exact_recursive",
                "decision_source_leaf",
                "decision_source_alias",
                "decision_incorrect_child",
                "decision_cheap_false",
                "decision_cheap_true_no_skip",
            ]
            for key, value in zip(keys, decision.groups(), strict=True):
                run.set_int(key, value)

    run.values["actual_sdc"] = len(sdc_sites)
    run.values["sdc_sites"] = ";".join(sdc_sites)
    run.values["status"] = status_for(run.values, text)

    runtime_aliases = {
        "input_size_runtime": "input_size",
        "repeats_runtime": "repeats",
        "check_enabled_runtime": "check_enabled",
        "processes": "total_cores",
    }
    for runtime_key, canonical_key in runtime_aliases.items():
        runtime_value = run.values.get(runtime_key)
        if isinstance(runtime_value, int) and runtime_value != run.values.get(canonical_key):
            raise ValueError(
                f"{path.name}: filename {canonical_key}={run.values.get(canonical_key)} "
                + f"but log {runtime_key}={runtime_value}"
            )

    for ns_key in [
        "first_tree_ns",
        "twin_tree_ns",
        "traversal_ns",
        "third_tree_ns",
        "process_time_ns",
    ]:
        run.set_float_from_ns(ns_key.removesuffix("_ns") + "_s", ns_key)

    first_s = run.values.get("first_tree_s")
    process_s = run.values.get("process_time_s")
    twin_s = run.values.get("twin_tree_s")
    traversal_s = run.values.get("traversal_s")
    third_s = run.values.get("third_tree_s")
    if isinstance(first_s, float) and isinstance(process_s, float):
        run.values["overhead_vs_first_s"] = process_s - first_s
    if isinstance(twin_s, float) and isinstance(traversal_s, float) and isinstance(third_s, float):
        run.values["recovery_overhead_s"] = twin_s + traversal_s + third_s

    return run


def numeric(values: Iterable[CsvValue]) -> list[float]:
    return [float(v) for v in values if isinstance(v, int | float)]


def mean(values: Iterable[CsvValue]) -> CsvValue:
    nums = numeric(values)
    return statistics.mean(nums) if nums else ""


def median(values: Iterable[CsvValue]) -> CsvValue:
    nums = numeric(values)
    return statistics.median(nums) if nums else ""


def minimum(values: Iterable[CsvValue]) -> CsvValue:
    nums = numeric(values)
    return min(nums) if nums else ""


def maximum(values: Iterable[CsvValue]) -> CsvValue:
    nums = numeric(values)
    return max(nums) if nums else ""


def stddev(values: Iterable[CsvValue]) -> CsvValue:
    nums = numeric(values)
    return statistics.stdev(nums) if len(nums) > 1 else ""


def make_placeholder(num_nodes: int, total_cores: int, sdc_rate: str, requested_sdc: int) -> Row:
    row: Row = {key: "" for key in RUN_FIELDS}
    row.update(
        {
            "status": "missing",
            "is_placeholder": 1,
            "filename": f"placeholder-totalCores{total_cores}-s{sdc_rate}-m{requested_sdc}",
            "job_id": "placeholder",
            "num_nodes": num_nodes,
            "num_cores": total_cores,
            "total_cores": total_cores,
            "system": "-._._examples_lcs.out",
            "input_size": 1048576,
            "repeats": 1,
            "check_enabled": 0,
            "sdc_rate": sdc_rate,
            "requested_sdc": requested_sdc,
            "actual_sdc": "",
            "max_injected_sdcs": requested_sdc,
            "cutoff": 512,
        }
    )
    return row


def add_missing_40_rank_placeholders(rows: list[Row]) -> list[Row]:
    complete_grid = {
        (row["sdc_rate"], row["requested_sdc"])
        for row in rows
        if row.get("total_cores") != 40 and row.get("sdc_rate") and row.get("requested_sdc")
    }
    existing_40 = {
        (row["sdc_rate"], row["requested_sdc"])
        for row in rows
        if row.get("total_cores") == 40 and row.get("sdc_rate") and row.get("requested_sdc")
    }
    placeholders = [
        make_placeholder(1, 40, str(rate), value_as_int(requested))
        for rate, requested in sorted(complete_grid, key=lambda item: (str(item[0]), value_as_int(item[1])))
        if (rate, requested) not in existing_40
    ]
    return rows + placeholders


def aggregate_rows(rows: list[Row]) -> list[Row]:
    groups: dict[tuple[CsvValue, ...], list[Row]] = {}
    for row in rows:
        key = (
            row["num_nodes"],
            row["total_cores"],
            row["sdc_rate"],
            row["requested_sdc"],
            row["actual_sdc"],
        )
        groups.setdefault(key, []).append(row)

    aggregates: list[Row] = []
    for key, group in sorted(groups.items(), key=lambda item: (value_as_int(item[0][1]), str(item[0][2]), value_as_int(item[0][3]), str(item[0][4]))):
        num_nodes, total_cores, sdc_rate, requested_sdc, actual_sdc = key
        status_counts = {status: sum(1 for row in group if row["status"] == status) for status in ["ok", "incomplete", "failed"]}
        placeholder = all(value_as_int(row.get("is_placeholder", 0)) == 1 for row in group)
        status = "missing" if placeholder else ("ok" if status_counts["ok"] else group[0]["status"])
        agg = {
            "status": status,
            "is_placeholder": 1 if placeholder else 0,
            "num_nodes": num_nodes,
            "total_cores": total_cores,
            "sdc_rate": sdc_rate,
            "requested_sdc": requested_sdc,
            "actual_sdc": actual_sdc,
            "runs_total": len(group),
            "runs_ok": status_counts["ok"],
            "runs_incomplete": status_counts["incomplete"],
            "runs_failed": status_counts["failed"],
            "process_time_s_mean": mean(row["process_time_s"] for row in group),
            "process_time_s_median": median(row["process_time_s"] for row in group),
            "process_time_s_min": minimum(row["process_time_s"] for row in group),
            "process_time_s_max": maximum(row["process_time_s"] for row in group),
            "process_time_s_stddev": stddev(row["process_time_s"] for row in group),
            "first_tree_s_mean": mean(row["first_tree_s"] for row in group),
            "twin_tree_s_mean": mean(row["twin_tree_s"] for row in group),
            "traversal_s_mean": mean(row["traversal_s"] for row in group),
            "third_tree_s_mean": mean(row["third_tree_s"] for row in group),
            "overhead_vs_first_s_mean": mean(row["overhead_vs_first_s"] for row in group),
            "recovery_overhead_s_mean": mean(row["recovery_overhead_s"] for row in group),
            "actual_sdc_mean": mean(row["actual_sdc"] for row in group),
            "fork_replay_exec_mean": mean(row["fork_replay_exec"] for row in group),
            "fork_source_skip_mean": mean(row["fork_source_skip"] for row in group),
            "fork_value_mismatch_mean": mean(row["fork_value_mismatch"] for row in group),
            "fork_infected_mean": mean(row["fork_infected"] for row in group),
        }
        aggregates.append(agg)
    return aggregates


def scaling_rows(aggregates: list[Row]) -> list[Row]:
    baselines: dict[tuple[CsvValue, CsvValue, CsvValue], float] = {}
    for row in aggregates:
        if row["total_cores"] == 80 and row["process_time_s_mean"] != "":
            baselines[(row["sdc_rate"], row["requested_sdc"], row["actual_sdc"])] = value_as_float(row["process_time_s_mean"])

    rows: list[Row] = []
    for row in aggregates:
        out = {key: row.get(key, "") for key in SCALING_FIELDS}
        baseline = baselines.get((row["sdc_rate"], row["requested_sdc"], row["actual_sdc"]))
        process_mean = row.get("process_time_s_mean")
        total_cores = row.get("total_cores")
        if baseline and isinstance(process_mean, int | float) and isinstance(total_cores, int):
            speedup = baseline / value_as_float(process_mean)
            out["speedup_vs_80_mean"] = speedup
            out["efficiency_vs_80_mean"] = speedup / (total_cores / 80)
        else:
            out["speedup_vs_80_mean"] = ""
            out["efficiency_vs_80_mean"] = ""
        rows.append(out)
    return rows


def write_csv(path: Path, fields: list[str], rows: list[Row]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_gnuplot_scripts(out_dir: Path) -> None:
    plot_dir = out_dir / "plots"
    plot_dir.mkdir(exist_ok=True)
    scripts = {
        "plot_process_time.gnuplot": """
set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set key outside top center horizontal
set grid ytics
set xlabel 'MPI ranks'
set ylabel 'Mean process time [s]'
set logscale x 2
set output 'plots/rf26b_process_time.pdf'
plot for [rate in '0.000002 0.000006'] \\
     for [m in '1 2 3 4 5 6'] \\
     'rf26b_scaling.csv' using \\
     (strcol(3) eq rate && strcol(4) eq m && strcol(1) eq 'ok' ? $6 : 1/0):9 \\
     with linespoints title sprintf('s=%s, m=%s', rate, m)
""",
        "plot_phase_breakdown.gnuplot": """
set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set style data histograms
set style histogram rowstacked
set style fill solid border -1
set boxwidth 0.75
set grid ytics
set xlabel 'MPI ranks'
set ylabel 'Mean duration [s]'
set output 'plots/rf26b_phase_breakdown_s0.000002_m1.pdf'
set title 'RF26b LCS phase breakdown (s=0.000002, actual/requested SDC=1)'
plot 'rf26b_scaling.csv' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $13 : 1/0):xtic(6) title 'first', \\
     '' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $14 : 1/0) title 'twin', \\
     '' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $15 : 1/0) title 'traversal', \\
     '' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $16 : 1/0) title 'third'
""",
        "plot_recovery_overhead.gnuplot": """
set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set key outside top center horizontal
set grid ytics
set xlabel 'MPI ranks'
set ylabel 'Recovery overhead after first run [s]'
set logscale x 2
set output 'plots/rf26b_recovery_overhead.pdf'
plot for [rate in '0.000002 0.000006'] \\
     for [m in '1 2 3 4 5 6'] \\
     'rf26b_scaling.csv' using \\
     (strcol(3) eq rate && strcol(4) eq m && strcol(1) eq 'ok' ? $6 : 1/0):18 \\
     with linespoints title sprintf('s=%s, m=%s', rate, m)
""",
        "plot_speedup.gnuplot": """
set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set key outside top center horizontal
set grid ytics
set xlabel 'MPI ranks'
set ylabel 'Speedup relative to 80 ranks'
set logscale x 2
set output 'plots/rf26b_speedup_vs_80.pdf'
plot for [rate in '0.000002 0.000006'] \\
     for [m in '1 2 3 4 5 6'] \\
     'rf26b_scaling.csv' using \\
     (strcol(3) eq rate && strcol(4) eq m && strcol(1) eq 'ok' ? $6 : 1/0):11 \\
     with linespoints title sprintf('s=%s, m=%s', rate, m)
""",
        "plot_actual_vs_requested_sdc.gnuplot": """
set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set grid ytics
set xlabel 'Requested max SDCs (-m)'
set ylabel 'Mean actual injected SDCs'
set output 'plots/rf26b_actual_vs_requested_sdc.pdf'
plot for [rate in '0.000002 0.000006'] \\
     'rf26b_aggregate.csv' using \\
     (strcol(5) eq rate && strcol(1) eq 'ok' ? $6 : 1/0):23 \\
     with points pt 7 title sprintf('s=%s', rate)
""",
    }

    for name, content in scripts.items():
        _ = (out_dir / name).write_text(content.strip() + "\n")


def validate(rows: list[Row], aggregates: list[Row], scaling: list[Row]) -> None:
    real_rows = [row for row in rows if value_as_int(row["is_placeholder"]) == 0]
    if not real_rows:
        raise RuntimeError("no RF26b logs parsed")
    if not any(row["status"] == "ok" for row in real_rows):
        raise RuntimeError("no complete RF26b runs parsed")

    expected_40_grid = {
        (row["sdc_rate"], row["requested_sdc"])
        for row in real_rows
        if row["total_cores"] != 40 and row["sdc_rate"] and row["requested_sdc"]
    }
    covered_40_grid = {
        (row["sdc_rate"], row["requested_sdc"])
        for row in rows
        if row["total_cores"] == 40 and row["sdc_rate"] and row["requested_sdc"]
    }
    missing_40_grid = expected_40_grid - covered_40_grid
    if missing_40_grid:
        raise RuntimeError(f"40-rank grid coverage missing: {sorted(missing_40_grid)}")

    plottable_metrics = [
        "first_tree_s",
        "twin_tree_s",
        "traversal_s",
        "third_tree_s",
        "process_time_s",
        "overhead_vs_first_s",
        "recovery_overhead_s",
    ]
    for row in rows:
        if value_as_int(row["is_placeholder"]) != 1:
            continue
        if row["status"] != "missing":
            raise RuntimeError(f"placeholder has non-missing status: {row['filename']}")
        nonblank_metrics = [metric for metric in plottable_metrics if row.get(metric) != ""]
        if nonblank_metrics:
            raise RuntimeError(f"placeholder has plottable metrics {nonblank_metrics}: {row['filename']}")

    for row in real_rows:
        requested = row["requested_sdc"]
        actual = row["actual_sdc"]
        if isinstance(requested, int) and isinstance(actual, int) and actual > requested:
            raise RuntimeError(f"{row['filename']}: actual SDC count {actual} exceeds requested {requested}")
        max_injected = row.get("max_injected_sdcs", "")
        if max_injected != "" and value_as_int(max_injected) != value_as_int(requested):
            raise RuntimeError(
                f"{row['filename']}: max_injected_sdcs={max_injected} "
                + f"does not match requested_sdc={requested}"
            )

    if not aggregates:
        raise RuntimeError("no aggregate rows generated")

    plottable_keys: set[tuple[CsvValue, CsvValue, CsvValue]] = set()
    duplicate_keys: set[tuple[CsvValue, CsvValue, CsvValue]] = set()
    for row in scaling:
        if row["status"] != "ok":
            continue
        key = (row["sdc_rate"], row["requested_sdc"], row["total_cores"])
        if key in plottable_keys:
            duplicate_keys.add(key)
        plottable_keys.add(key)
    if duplicate_keys:
        raise RuntimeError(f"duplicate plottable scaling rows: {sorted(duplicate_keys)}")


def main() -> None:
    log_dir_arg, out_dir_arg = parse_args()
    log_dir = log_dir_arg.resolve()
    out_dir = (out_dir_arg or log_dir / "csv").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    parsed = [parse_log(path) for path in sorted(log_dir.glob("*.out.*"))]
    run_rows = [run.row() for run in parsed]
    run_rows = add_missing_40_rank_placeholders(run_rows)
    run_rows.sort(key=lambda row: (value_as_int(row["total_cores"]), str(row["sdc_rate"]), value_as_int(row["requested_sdc"]), str(row["job_id"])))

    aggregates = aggregate_rows(run_rows)
    scaling = scaling_rows(aggregates)
    validate(run_rows, aggregates, scaling)

    write_csv(out_dir / "rf26b_runs.csv", RUN_FIELDS, run_rows)
    write_csv(out_dir / "rf26b_aggregate.csv", AGG_FIELDS, aggregates)
    write_csv(out_dir / "rf26b_scaling.csv", SCALING_FIELDS, scaling)
    write_gnuplot_scripts(out_dir)

    real_count = sum(1 for row in run_rows if value_as_int(row["is_placeholder"]) == 0)
    placeholder_count = sum(1 for row in run_rows if value_as_int(row["is_placeholder"]) == 1)
    ok_count = sum(1 for row in run_rows if row["status"] == "ok")
    incomplete_count = sum(1 for row in run_rows if row["status"] == "incomplete")
    failed_count = sum(1 for row in run_rows if row["status"] == "failed")
    print(f"Parsed {real_count} logs")
    print(f"Rows: ok={ok_count} incomplete={incomplete_count} failed={failed_count} placeholders={placeholder_count}")
    print(f"Wrote {out_dir / 'rf26b_runs.csv'}")
    print(f"Wrote {out_dir / 'rf26b_aggregate.csv'}")
    print(f"Wrote {out_dir / 'rf26b_scaling.csv'}")
    print(f"Wrote gnuplot scripts in {out_dir}")


if __name__ == "__main__":
    main()
