# matplotlib ships no annotations for the figure methods, so every call reads as partly
# unknown. same reason the polars modules carry a pragma
# pyright: reportUnknownMemberType=false
from pathlib import Path
from typing import Any

from pitadvisor.model.backtest import EVENTS, MODEL, RAW, Comparison, Report, Scored

# the dashboard's plate palette, so a reliability curve and a pace trace read as one product
PLATE = "#0e0f11"
ENGRAVE = "#22262b"
STEEL = "#868d96"
LUME = "#ece6d6"
SPLIT = "#c8202a"
WET = "#6f97a8"
LUME_DIM = "#a49e90"
SERIES = {MODEL: SPLIT, RAW: STEEL, "grid": LUME, "standings": WET, "last_race": LUME_DIM}
FIGURE = "reliability.png"
SUMMARY = "summary.txt"
REPORT = "backtest.json"


class MissingPlotterError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("matplotlib is not installed: uv sync installs it in the dev group")


def render(report: Report, output: Path, figure: str = FIGURE) -> Path:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise MissingPlotterError from exc

    output.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, len(EVENTS), figsize=(13, 4.6), facecolor=PLATE)
    for panel, event in zip(axes, EVENTS, strict=True):
        _panel(panel, report, event)
    handles, labels = axes[0].get_legend_handles_labels()
    legend = fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=len(labels),
        frameon=False,
        fontsize=8,
    )
    for text in legend.get_texts():
        text.set_color(LUME_DIM)
    fig.suptitle(
        f"reliability over {len(report.per_race)} time-forward races, {report.paths} paths",
        color=LUME,
        fontsize=10,
    )
    fig.tight_layout(rect=(0.0, 0.07, 1.0, 0.95))
    path = output / figure
    fig.savefig(path, dpi=160, facecolor=PLATE)
    plt.close(fig)
    return path


def _panel(panel: Any, report: Report, event: str) -> None:
    panel.set_facecolor(PLATE)
    panel.plot([0, 1], [0, 1], color=ENGRAVE, linewidth=1, zorder=1)
    for scored in report.scored:
        curve = scored.curves.get(event, [])
        if not curve:
            continue
        forecast = [item.forecast for item in curve]
        observed = [item.observed for item in curve]
        panel.plot(
            forecast,
            observed,
            marker="o",
            markersize=3.5,
            linewidth=1.4 if scored.name == MODEL else 1.0,
            color=SERIES.get(scored.name, STEEL),
            label=scored.name,
            zorder=3 if scored.name == MODEL else 2,
        )
    panel.set_title(event, color=LUME, fontsize=9, loc="left")
    panel.set_xlabel("forecast", color=STEEL, fontsize=8)
    panel.set_ylabel("observed", color=STEEL, fontsize=8)
    panel.set_xlim(0, 1)
    panel.set_ylim(0, 1)
    panel.tick_params(colors=STEEL, labelsize=7)
    for edge in panel.spines.values():
        edge.set_color(ENGRAVE)


def _row(scored: Scored) -> str:
    return (
        f"{scored.name:<14} "
        f"log loss {scored.log_loss.value:6.4f} "
        f"[{scored.log_loss.low:6.4f}, {scored.log_loss.high:6.4f}]  "
        f"brier {scored.brier.value:6.4f} "
        f"[{scored.brier.low:6.4f}, {scored.brier.high:6.4f}]  "
        + "  ".join(f"ece {event} {scored.calibration[event]:.3f}" for event in EVENTS)
    )


def summarise(report: Report) -> str:
    ours = next(item for item in report.scored if item.name == MODEL)
    lines = [
        f"pit advisor backtest, run {report.run_id}, "
        f"generated {report.generated_at:%Y-%m-%d %H:%M}",
        f"{ours.races} races held out from {report.from_season} onwards, "
        f"{ours.rows} driver-races, {report.paths} paths a race, seed {report.seed}",
        "",
        "multiclass over classified finishing position, intervals bootstrapped at the race",
        "level because drivers inside one race share a car, a strategy and a safety car",
        "",
    ]
    lines.extend(_row(scored) for scored in report.scored)
    lines.append("")
    others = [item for item in report.scored if item.name not in (MODEL, RAW)]
    beaten = [item.name for item in others if ours.log_loss.value < item.log_loss.value]
    lost = [item.name for item in others if ours.log_loss.value >= item.log_loss.value]
    lines.append(
        f"the simulation beats {', '.join(beaten) if beaten else 'no baseline'} on log loss"
        + (f" and loses to {', '.join(lost)}" if lost else "")
    )
    lines.append(
        "this holdout separates it from "
        + (", ".join(report.separated_from) if report.separated_from else "no baseline at all")
        + ", the rest are inside bootstrap noise"
    )
    lines.append("")
    lines.append("how much better than each baseline, resampled over the same races. an")
    lines.append("interval that straddles zero means the two are not separated by this holdout")
    for item in report.paired:
        lines.append(
            f"  vs {item.baseline:<11} log loss {item.log_loss_gain.value:+7.4f} "
            f"[{item.log_loss_gain.low:+7.4f}, {item.log_loss_gain.high:+7.4f}]  "
            f"brier {item.brier_gain.value:+7.4f} "
            f"[{item.brier_gain.low:+7.4f}, {item.brier_gain.high:+7.4f}]"
        )
    lines.append("")
    lines.append("as events: the place distribution summed to win, podium and points, scored with")
    lines.append("a binary log loss and brier. murphy splits the brier into rel - res + unc")
    for scored in report.scored:
        for event in EVENTS:
            item = scored.events.get(event)
            if item is None:
                continue
            split = item.murphy
            lines.append(
                f"  {scored.name:<15} {event:<7} log loss {item.log_loss.value:.4f} "
                f"[{item.log_loss.low:.4f}, {item.log_loss.high:.4f}]  brier "
                f"{item.brier.value:.4f} [{item.brier.low:.4f}, {item.brier.high:.4f}]  "
                f"rel {split.reliability:.4f} res {split.resolution:.4f} "
                f"unc {split.uncertainty:.4f}"
            )
    lines.append("")
    lines.append("event gains over each baseline, same races")
    for item in report.paired:
        for event, gain in item.events.items():
            lines.append(
                f"  vs {item.baseline:<11} {event:<7} log loss {gain.log_loss_gain.value:+7.4f} "
                f"[{gain.log_loss_gain.low:+7.4f}, {gain.log_loss_gain.high:+7.4f}]  brier "
                f"{gain.brier_gain.value:+7.4f} "
                f"[{gain.brier_gain.low:+7.4f}, {gain.brier_gain.high:+7.4f}]"
            )
    lines.append("")
    pool = report.pool
    lines.append(
        f"pooled as sim^{pool.sim_power:.2f} x grid^{pool.grid_power:.2f}, refitted before every "
        f"scored race on the races before it, {report.warmup} of them warm-up only. "
        f"before qualifying, sim^{report.pool_without_grid.sim_power:.2f}"
    )
    lines.append("")
    lines.append("assumptions the simulation was run under")
    lines.extend(
        f"  {item.name:<28} {item.value:>12.3f}  {item.detail}" for item in report.assumptions
    )
    return "\n".join(lines) + "\n"


def write(report: Report, output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    written = [output / REPORT, output / SUMMARY]
    written[0].write_text(report.model_dump_json(indent=2) + "\n")
    written[1].write_text(summarise(report))
    written.append(render(report, output))
    return written


def describe(comparison: Comparison) -> str:
    gain = comparison.log_loss_gain
    window = f"before {comparison.until}" if comparison.until else "to the latest race"
    lines = [
        f"{comparison.label or 'candidate'} against the model as it ships, run {comparison.run_id}",
        f"{comparison.races} races {window}, {comparison.warmup} warm-up, "
        f"{comparison.paths} paths, seed {comparison.seed}, same draws for both, "
        f"forecast at {comparison.stage}",
        f"candidate {comparison.candidate.model_dump_json()}",
        "",
        "gain is the base's loss minus the candidate's, positive means the candidate is better",
        f"  log loss {gain.value:+7.4f} [{gain.low:+7.4f}, {gain.high:+7.4f}]",
        f"  brier    {comparison.brier_gain.value:+7.4f} "
        f"[{comparison.brier_gain.low:+7.4f}, {comparison.brier_gain.high:+7.4f}]",
        f"  before the pool, log loss {comparison.raw_log_loss_gain.value:+7.4f} "
        f"[{comparison.raw_log_loss_gain.low:+7.4f}, {comparison.raw_log_loss_gain.high:+7.4f}]",
    ]
    for event, gain in comparison.events.items():
        lines.append(
            f"  {event:<7}  log loss {gain.log_loss_gain.value:+7.4f} "
            f"[{gain.log_loss_gain.low:+7.4f}, {gain.log_loss_gain.high:+7.4f}]  brier "
            f"{gain.brier_gain.value:+7.4f} "
            f"[{gain.brier_gain.low:+7.4f}, {gain.brier_gain.high:+7.4f}]"
        )
    for subset in comparison.subsets:
        item = subset.log_loss_gain
        lines.append(
            f"  {subset.name} ({subset.races} races) log loss {item.value:+7.4f} "
            f"[{item.low:+7.4f}, {item.high:+7.4f}]"
        )
    return "\n".join(lines) + "\n"
