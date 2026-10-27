#!/usr/bin/env python3
"""Plot p10–p90 score ranges from one or more DGA family summary CSVs.

Each bar is one model on one family. The bar spans the p10 to p90 normalized
log-likelihood, and a tick on the bar is the median. Each model has its own
color. Pass --heatmap to color bars by how many names were scored instead.
Families are sorted by the first model's median so models can be compared down
the same score axis. Family names are not labeled.

The plot is shown on screen. Pass -o to write an image instead.

Example:
    python3 misc/plot_dga_family_scores.py dga-family-stats.csv
    python3 misc/plot_dga_family_scores.py dga-random-stats.csv --heatmap
    python3 misc/plot_dga_family_scores.py dga-random-stats.csv dga-word-stats.csv \
        -o dga-score-ranges.png
"""

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_MODEL_COLORS = (
    "#1b4f72",
    "#922b21",
    "#117a65",
    "#6c3483",
    "#b9770e",
    "#1a5276",
)

# Few names are yellow, the most names are red.
_HEAT_COLORS = (
    "#fee08b",
    "#fdae61",
    "#f46d43",
    "#d73027",
)


@dataclass
class FamilyScore:
    p10: float
    p50: float
    p90: float
    scored: int = 0


def _parse_float(value: str) -> Optional[float]:
    text = value.strip()
    if not text:
        return None
    if text == "-inf":
        return float("-inf")
    try:
        return float(text)
    except ValueError:
        return None


def load_scores(
    paths: list[Path],
    need_scored: bool,
) -> tuple[list[str], dict[tuple[str, str], FamilyScore]]:
    """Return model names in first-seen order and scores keyed by (model, family)."""
    models: list[str] = []
    seen_models: set[str] = set()
    scores: dict[tuple[str, str], FamilyScore] = {}

    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"model", "family", "p10", "p50", "p90"}
            if need_scored:
                required.add("scored")
            fieldnames = set(reader.fieldnames or [])
            missing = required - fieldnames
            if missing:
                raise SystemExit(f"{path}: missing columns {', '.join(sorted(missing))}")

            for row in reader:
                model = row["model"].strip()
                family = row["family"].strip()
                if not model or not family:
                    continue
                p10 = _parse_float(row["p10"])
                p50 = _parse_float(row["p50"])
                p90 = _parse_float(row["p90"])
                if p10 is None or p50 is None or p90 is None:
                    continue
                if p90 < p10:
                    p10, p90 = p90, p10
                scored = 0
                if need_scored:
                    parsed = _parse_float(row["scored"])
                    if parsed is None:
                        continue
                    scored = int(parsed)

                if model not in seen_models:
                    seen_models.add(model)
                    models.append(model)

                key = (model, family)
                if key in scores:
                    print(
                        f"duplicate {model} / {family} in {path}; keeping the later row",
                        file=sys.stderr,
                    )
                scores[key] = FamilyScore(p10=p10, p50=p50, p90=p90, scored=scored)

    if not scores:
        raise SystemExit("no score rows found")
    return models, scores


def sorted_families(
    models: list[str],
    scores: dict[tuple[str, str], FamilyScore],
) -> list[str]:
    """Sort by the first model's median, lowest score first. Others follow."""
    reference = models[0]
    families = sorted({family for _model, family in scores})

    def sort_key(family: str) -> tuple[int, float, str]:
        score = scores.get((reference, family))
        if score is None:
            return (1, 0.0, family)
        return (0, score.p50, family)

    families.sort(key=sort_key)
    return families


def _pyplot():
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        raise SystemExit("matplotlib is required: pip install -r misc/requirements.txt")
    return plt


def _heat_scale(scored_counts: list[int]):
    from matplotlib.colors import LinearSegmentedColormap, LogNorm

    positive = [count for count in scored_counts if count > 0]
    low = min(positive) if positive else 1
    high = max(positive) if positive else 1
    if high <= low:
        high = low * 10
    cmap = LinearSegmentedColormap.from_list("scored_heat", list(_HEAT_COLORS))
    return cmap, LogNorm(vmin=low, vmax=high)


def plot_ranges(
    models: list[str],
    families: list[str],
    scores: dict[tuple[str, str], FamilyScore],
    output: Optional[Path],
    heatmap: bool,
) -> None:
    plt = _pyplot()

    family_count = len(families)
    model_count = len(models)
    # Most of each family slot is the bar. The remainder is the gap between families.
    bar_width = 0.92 / model_count
    cmap = None
    norm = None
    if heatmap:
        cmap, norm = _heat_scale([score.scored for score in scores.values()])

    figure, axis = plt.subplots(figsize=(12, 6))

    for index, model in enumerate(models):
        offset = (index - (model_count - 1) / 2) * bar_width
        positions = []
        bottoms = []
        heights = []
        bar_colors = []
        medians = []
        for family_index, family in enumerate(families):
            score = scores.get((model, family))
            if score is None:
                continue
            center = family_index + offset
            positions.append(center)
            bottoms.append(score.p10)
            heights.append(score.p90 - score.p10)
            medians.append((center, score.p50))
            if cmap is not None and norm is not None:
                bar_colors.append(cmap(norm(max(score.scored, 1))))

        color = bar_colors if heatmap else _MODEL_COLORS[index % len(_MODEL_COLORS)]
        axis.bar(
            positions,
            heights,
            bar_width * 0.96,
            bottom=bottoms,
            color=color,
            linewidth=0,
            label=None if heatmap else model,
            zorder=2,
        )
        half = bar_width * 0.42
        for center, median in medians:
            axis.plot(
                [center - half, center + half],
                [median, median],
                color="black" if heatmap else "white",
                linewidth=0.7,
                solid_capstyle="butt",
                zorder=3,
            )

    axis.set_xlim(-0.5, family_count - 0.5)
    axis.set_xticks([])
    axis.set_xlabel("DGA families, sorted by median")
    axis.set_ylabel("Normalized log-likelihood")
    axis.set_title("Score range by family (bar: p10–p90, tick: median)")
    axis.grid(axis="y", linewidth=0.6, color="#cccccc", zorder=0)
    axis.set_axisbelow(True)
    if model_count > 1 and not heatmap:
        axis.legend(title="Model", frameon=False)
    if heatmap and cmap is not None and norm is not None:
        scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
        scalar.set_array([])
        colorbar = figure.colorbar(scalar, ax=axis)
        colorbar.set_label("Names scored")

    figure.tight_layout()
    if output is None:
        plt.show()
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot p10–p90 score ranges from DGA family summary CSVs. "
            "Each model is drawn in its own color. "
            "--heatmap colors bars by how many names were scored."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="One or more summary CSVs written by score_dga_families.py",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the figure to this path. Without -o, the plot is shown and not saved.",
    )
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="Color each bar by how many names were scored, from yellow (few) to red (many).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    for path in args.inputs:
        if not path.is_file():
            print(f"input not found: {path}", file=sys.stderr)
            return 1

    models, scores = load_scores(args.inputs, need_scored=args.heatmap)
    families = sorted_families(models, scores)
    plot_ranges(models, families, scores, args.output, heatmap=args.heatmap)
    if args.output is not None:
        print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
