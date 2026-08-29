# matplotlib ships no annotations for the figure methods, so every call reads as partly
# unknown. same reason the polars modules carry a pragma
# pyright: reportUnknownMemberType=false
from pathlib import Path
from typing import Any

from pitadvisor.model.backtest import EVENTS, MODEL, Report, Scored

# the dashboard's plate palette, so a reliability curve and a pace trace read as one product
PLATE = "#0e0f11"
ENGRAVE = "#22262b"
STEEL = "#868d96"
LUME = "#ece6d6"
SPLIT = "#c8202a"
WET = "#6f97a8"
LUME_DIM = "#a49e90"
SERIES = {MODEL: SPLIT, "grid": LUME, "standings": WET, "last_race": LUME_DIM}
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
        f"reliability over {report.holdout} time-forward races, {report.paths} paths",
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
        f"{scored.name:<11} "
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
    beaten = [
        item.name
        for item in report.scored
        if item.name != MODEL and ours.log_loss.value < item.log_loss.value
    ]
    lost = [
        item.name
        for item in report.scored
        if item.name != MODEL and ours.log_loss.value >= item.log_loss.value
    ]
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
    lines.append("assumptions the simulation was run under")
    lines.extend(
        f"  {item.name:<28} {item.value:>12.3f}  {item.detail}" for item in report.assumptions
    )
    return "\n".join(lines) + "\n"


def write(report: Report, output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    written = [output / REPORT, output / SUMMARY]
    written[0].write_text(report.model_dump_json(indent=2))
    written[1].write_text(summarise(report))
    written.append(render(report, output))
    return written
