#!/usr/bin/env python3
"""Score a folder of DGA-family CSVs and write summary statistics.

Each CSV is one family (for example a DGArchive export). Domains are reduced
to the leftmost label, ignoring the public suffix, then scored with one or
more Markov models. The report records detection rates and score
distributions. It does not write a score for every domain.

A label is detected when its normalized log-likelihood is strictly below a
threshold, matching internal/classifier.

Example:
    python3 misc/score_dga_families.py ../2024-11-28-dgarchive_full \
        -m data/models/se_min8_k1_model.json \
        -m data/models/tranco_k1.json \
        -t -5 -t -4 \
        -o dga-family-stats.md
"""

import argparse
import csv
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, TextIO

from parse_csv_to_corpus import passes_filters

_HEADER_NAMES = {"domain", "qname", "fqdn", "hostname"}
_BIN_WIDTH = 0.1
_PROGRESS_ROWS = 1_000_000
# Fixed histogram columns, higher scores first. Scores above 0 share (-2, 0].
_BUCKET_COLUMNS = (
    "(-2, 0]",
    "(-3, -2]",
    "(-4, -3]",
    "(-5, -4]",
    "(-6, -5]",
    "(-7, -6]",
    "(-inf, -7]",
)


def _json_log_prob(value: object) -> float:
    if value == "-Inf":
        return math.inf * -1
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"invalid log-likelihood value {value!r}")


class MarkovModel:
    """In-memory copy of a model JSON file.

    Missing characters contribute 0, matching a missing Go map lookup.
    Characters stored as "-Inf" stay negative infinity.
    """

    def __init__(self, path: Path, init: list[float], transitions: list[list[float]], trained_names: int):
        self.path = path
        self.name = path.stem
        self.init = init
        self.transitions = transitions
        self.trained_names = trained_names

    @classmethod
    def load(cls, path: Path) -> "MarkovModel":
        import json

        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)

        init = [0.0] * 128
        transitions = [[0.0] * 128 for _ in range(128)]

        for char, prob in raw.get("init", {}).items():
            if len(char) != 1:
                raise ValueError(f"{path}: invalid init key {char!r}")
            init[ord(char)] = _json_log_prob(prob)

        for char, row in raw.get("transitions", {}).items():
            if len(char) != 1:
                raise ValueError(f"{path}: invalid transitions key {char!r}")
            src = ord(char)
            for next_char, prob in row.items():
                if len(next_char) != 1:
                    raise ValueError(f"{path}: invalid transitions[{char!r}] key {next_char!r}")
                transitions[src][ord(next_char)] = _json_log_prob(prob)

        trained = raw.get("trained_names", 0)
        if not isinstance(trained, int):
            raise ValueError(f"{path}: trained_names must be an integer")

        return cls(path, init, transitions, trained)

    def score_normalized(self, label: str) -> float:
        first = ord(label[0])
        score = self.init[first] if first < 128 else 0.0
        transitions = self.transitions
        prev = first
        for char in label[1:]:
            nxt = ord(char)
            if prev < 128 and nxt < 128:
                score += transitions[prev][nxt]
            prev = nxt
        return score / len(label)


@dataclass
class ScoreStats:
    """Streaming score summary. Percentiles come from 0.1-wide bins."""

    count: int = 0
    neg_inf: int = 0
    finite_sum: float = 0.0
    finite_min: float = math.inf
    finite_max: float = math.inf * -1
    bins: dict[float, int] = field(default_factory=dict)
    integer_bins: dict[str, int] = field(default_factory=dict)
    detected: dict[float, int] = field(default_factory=dict)

    def add(self, score: float, thresholds: Iterable[float]) -> None:
        self.count += 1
        bucket = _report_bucket(score)
        self.integer_bins[bucket] = self.integer_bins.get(bucket, 0) + 1

        if math.isinf(score) and score < 0:
            self.neg_inf += 1
            upper = math.inf * -1
        else:
            self.finite_sum += score
            if score < self.finite_min:
                self.finite_min = score
            if score > self.finite_max:
                self.finite_max = score
            upper = _bin_upper(score)

        self.bins[upper] = self.bins.get(upper, 0) + 1

        for threshold in thresholds:
            if score < threshold:
                self.detected[threshold] = self.detected.get(threshold, 0) + 1

    @property
    def finite_count(self) -> int:
        return self.count - self.neg_inf

    @property
    def mean(self) -> Optional[float]:
        if self.finite_count == 0:
            return None
        return self.finite_sum / self.finite_count

    @property
    def minimum(self) -> Optional[float]:
        if self.finite_count == 0:
            return None
        return self.finite_min

    @property
    def maximum(self) -> Optional[float]:
        if self.finite_count == 0:
            return None
        return self.finite_max

    def percentile(self, fraction: float) -> Optional[float]:
        """Upper edge of the bin that contains this nearest-rank percentile."""
        if self.count == 0:
            return None
        rank = max(1, math.ceil(fraction * self.count))
        running = 0
        for upper in sorted(self.bins, key=_bin_sort_key):
            running += self.bins[upper]
            if running >= rank:
                return upper
        return None

    def detection_rate(self, threshold: float) -> Optional[float]:
        if self.count == 0:
            return None
        return self.detected.get(threshold, 0) / self.count

    def bucket_count(self, label: str) -> int:
        return self.integer_bins.get(label, 0)


@dataclass
class FamilyResult:
    family: str
    path: Path
    rows: int = 0
    skipped: int = 0
    duplicates: int = 0
    scored: int = 0
    by_model: dict[str, ScoreStats] = field(default_factory=dict)
    error: str = ""


def _bin_upper(score: float) -> float:
    upper = math.ceil((score / _BIN_WIDTH) - 1e-9) * _BIN_WIDTH
    return round(upper, 10)


def _bin_sort_key(upper: float) -> float:
    if math.isinf(upper) and upper < 0:
        return math.inf * -1
    return upper


def _report_bucket(score: float) -> str:
    """Map a normalized score onto one of _BUCKET_COLUMNS.

    Intervals are open on the left and closed on the right, except that
    every score at or below -7, including negative infinity, shares the
    bottom column, and every score above 0 shares (-2, 0].
    """
    if (math.isinf(score) and score < 0) or score <= -7:
        return "(-inf, -7]"
    if score <= -6:
        return "(-7, -6]"
    if score <= -5:
        return "(-6, -5]"
    if score <= -4:
        return "(-5, -4]"
    if score <= -3:
        return "(-4, -3]"
    if score <= -2:
        return "(-3, -2]"
    return "(-2, 0]"


def _format_score(value: Optional[float]) -> str:
    if value is None:
        return ""
    if math.isinf(value) and value < 0:
        return "-inf"
    return f"{value:.3f}"


def _format_percent(value: Optional[float]) -> str:
    if value is None:
        return ""
    percent = 100.0 * value
    if value > 0 and percent < 0.05:
        return "<0.1%"
    return f"{percent:.1f}%"


def _format_threshold(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _is_header(cell: str) -> bool:
    return cell.strip().lower() in _HEADER_NAMES


def leftmost_label(domain: str) -> Optional[str]:
    """Return the leftmost DNS label, ignoring the public suffix.

    "acadaqac.bazar" and "www.example.com" both yield their first label.
    This is intentionally not eTLD+1: DGA exports use suffixes that are
    missing from the public suffix list, and the generated name is the
    leftmost label in either case.
    """
    trimmed = domain.strip().lower().strip(".")
    if not trimmed:
        return None
    label, _, _ = trimmed.partition(".")
    return label or None


def iter_labels(
    path: Path,
    column: int,
    unique: bool,
    limit: int,
    progress: TextIO,
) -> tuple[Iterable[str], dict[str, int]]:
    """Yield scoring labels from one CSV.

    The counts dict is filled as iteration proceeds: rows, skipped, duplicates.
    """
    counts = {"rows": 0, "skipped": 0, "duplicates": 0}

    def generate() -> Iterable[str]:
        seen: set[str] = set()
        yielded = 0
        next_progress = _PROGRESS_ROWS

        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            first = True
            for row in reader:
                if column >= len(row):
                    counts["skipped"] += 1
                    continue

                cell = row[column].strip()
                if first:
                    first = False
                    if _is_header(cell):
                        continue

                counts["rows"] += 1
                if counts["rows"] == next_progress:
                    print(
                        f"  {path.name}: read {counts['rows']} rows...",
                        file=progress,
                    )
                    next_progress += _PROGRESS_ROWS

                if not cell:
                    counts["skipped"] += 1
                    continue

                label = leftmost_label(cell)
                if label is None or not passes_filters(label):
                    counts["skipped"] += 1
                    continue

                if unique:
                    if label in seen:
                        counts["duplicates"] += 1
                        continue
                    seen.add(label)

                yielded += 1
                yield label
                if limit and yielded >= limit:
                    return

    return generate(), counts


def score_file(
    path: Path,
    models: list[MarkovModel],
    thresholds: list[float],
    column: int,
    unique: bool,
    limit: int,
    progress: TextIO,
) -> FamilyResult:
    result = FamilyResult(family=path.stem, path=path)
    for model in models:
        result.by_model[model.name] = ScoreStats(detected={threshold: 0 for threshold in thresholds})

    labels, counts = iter_labels(path, column, unique, limit, progress)
    try:
        for label in labels:
            for model in models:
                result.by_model[model.name].add(model.score_normalized(label), thresholds)
    except (OSError, UnicodeError, csv.Error) as exc:
        result.error = str(exc)

    result.rows = counts["rows"]
    result.skipped = counts["skipped"]
    result.duplicates = counts["duplicates"]
    result.scored = next(iter(result.by_model.values())).count if result.by_model else 0
    return result


def score_folder(
    input_dir: Path,
    models: list[MarkovModel],
    thresholds: list[float],
    column: int,
    unique: bool,
    limit: int,
    progress: TextIO,
) -> list[FamilyResult]:
    paths = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".csv")
    if not paths:
        raise SystemExit(f"no CSV files found in {input_dir}")

    results = []
    started = time.monotonic()
    for index, path in enumerate(paths, start=1):
        print(f"[{index}/{len(paths)}] {path.name}", file=progress)
        file_started = time.monotonic()
        result = score_file(path, models, thresholds, column, unique, limit, progress)
        elapsed = time.monotonic() - file_started
        if result.error:
            print(f"  error: {result.error}", file=progress)
        else:
            print(
                f"  scored {result.scored} labels "
                f"({result.duplicates} duplicate, {result.skipped} skipped) "
                f"in {elapsed:.1f}s",
                file=progress,
            )
        results.append(result)

    print(f"finished {len(results)} files in {time.monotonic() - started:.1f}s", file=progress)
    return results


def _unweighted_mean(rates: list[Optional[float]]) -> Optional[float]:
    present = [rate for rate in rates if rate is not None]
    if not present:
        return None
    return sum(present) / len(present)


def write_markdown(
    path: Path,
    input_dir: Path,
    models: list[MarkovModel],
    thresholds: list[float],
    unique: bool,
    results: list[FamilyResult],
) -> None:
    label_mode = "unique leftmost labels" if unique else "every CSV row that yields a leftmost label"
    lines = [
        "# DGA family score summary",
        "",
        f"Input folder: `{input_dir}`",
        "",
        f"Scored {label_mode}. The label is the leftmost label of the domain, "
        "with no public-suffix check. A label is detected when its "
        "normalized log-likelihood is strictly below the threshold.",
        "",
        "Percentiles are the upper edge of the 0.1-wide bin that contains that "
        "rank, so they can sit up to 0.1 above the exact percentile. "
        "`min`, `mean`, and `max` ignore scores of negative infinity; those "
        "labels are counted in the `-inf` column and still count as detected.",
        "",
        "## Models",
        "",
        "| model | trained names | file |",
        "| --- | ---: | --- |",
    ]
    for model in models:
        lines.append(f"| {model.name} | {model.trained_names} | `{model.path}` |")

    lines.extend(["", "## Detection rate", ""])
    header = ["family", "scored"]
    for model in models:
        for threshold in thresholds:
            header.append(f"{model.name} < {_format_threshold(threshold)}")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---", "---:"] + ["---:"] * (len(header) - 2)) + " |")

    rate_columns: list[list[Optional[float]]] = [[] for _ in range(len(header) - 2)]
    for result in results:
        if result.error:
            cells = [result.family, f"error: {result.error}"] + [""] * (len(header) - 2)
            lines.append("| " + " | ".join(cells) + " |")
            continue
        cells = [result.family, str(result.scored)]
        column = 0
        for model in models:
            stats = result.by_model[model.name]
            for threshold in thresholds:
                rate = stats.detection_rate(threshold)
                rate_columns[column].append(rate)
                column += 1
                cells.append(_format_percent(rate))
        lines.append("| " + " | ".join(cells) + " |")

    mean_cells = ["**unweighted mean**", ""]
    for rates in rate_columns:
        mean_cells.append(f"**{_format_percent(_unweighted_mean(rates))}**")
    lines.append("| " + " | ".join(mean_cells) + " |")

    for model in models:
        lines.extend(["", f"## Score distribution: {model.name}", ""])
        lines.append("| family | scored | -inf | min | p10 | p50 | mean | p90 | max |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for result in results:
            if result.error:
                continue
            stats = result.by_model[model.name]
            cells = [
                result.family,
                str(stats.count),
                str(stats.neg_inf),
                _format_score(stats.minimum),
                _format_score(stats.percentile(0.10)),
                _format_score(stats.percentile(0.50)),
                _format_score(stats.mean),
                _format_score(stats.percentile(0.90)),
                _format_score(stats.maximum),
            ]
            lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(
    path: Path,
    models: list[MarkovModel],
    thresholds: list[float],
    results: list[FamilyResult],
) -> None:
    fieldnames = [
        "model",
        "family",
        "file",
        "rows",
        "scored",
        "duplicates",
        "skipped",
        "neg_inf",
        "min",
        "p10",
        "p50",
        "mean",
        "p90",
        "max",
    ]
    for threshold in thresholds:
        label = _format_threshold(threshold)
        fieldnames.append(f"detected_lt_{label}")
        fieldnames.append(f"detected_pct_lt_{label}")
    fieldnames.extend(_BUCKET_COLUMNS)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            for model in models:
                stats = result.by_model.get(model.name)
                row = {
                    "model": model.name,
                    "family": result.family,
                    "file": result.path.name,
                    "rows": result.rows,
                    "scored": result.scored,
                    "duplicates": result.duplicates,
                    "skipped": result.skipped,
                    "neg_inf": "" if stats is None else stats.neg_inf,
                    "min": "" if stats is None else _format_score(stats.minimum),
                    "p10": "" if stats is None else _format_score(stats.percentile(0.10)),
                    "p50": "" if stats is None else _format_score(stats.percentile(0.50)),
                    "mean": "" if stats is None else _format_score(stats.mean),
                    "p90": "" if stats is None else _format_score(stats.percentile(0.90)),
                    "max": "" if stats is None else _format_score(stats.maximum),
                }
                for label in _BUCKET_COLUMNS:
                    row[label] = 0 if stats is None else stats.bucket_count(label)
                if stats is not None:
                    for threshold in thresholds:
                        label = _format_threshold(threshold)
                        detected = stats.detected.get(threshold, 0)
                        row[f"detected_lt_{label}"] = detected
                        rate = stats.detection_rate(threshold)
                        row[f"detected_pct_lt_{label}"] = "" if rate is None else f"{100.0 * rate:.1f}"
                writer.writerow(row)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Score every CSV in a folder with one or more Markov models and "
            "write per-family detection statistics."
        )
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Folder containing one CSV per DGA family",
    )
    parser.add_argument(
        "-m",
        "--model",
        type=Path,
        action="append",
        required=True,
        dest="models",
        help="Path to a model JSON file. Repeat to compare models.",
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=float,
        action="append",
        dest="thresholds",
        help="Detection threshold. Score < threshold counts as DGA. Repeatable. Default: -5",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("dga-family-stats.md"),
        help="Markdown report path. A CSV with the same stem is written beside it.",
    )
    parser.add_argument(
        "--column",
        type=int,
        default=0,
        help="Zero-indexed CSV column that holds the domain (default: 0)",
    )
    parser.add_argument(
        "--no-unique",
        action="store_true",
        help="Score every row. By default each leftmost label is scored once per file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum labels to score per file, after filtering (0 = no limit)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    input_dir = args.input_dir
    if not input_dir.is_dir():
        print(f"input folder not found: {input_dir}", file=sys.stderr)
        return 1

    thresholds = args.thresholds if args.thresholds else [-5.0]
    for threshold in thresholds:
        if not math.isfinite(threshold):
            print(f"threshold must be finite, got {threshold}", file=sys.stderr)
            return 1

    models: list[MarkovModel] = []
    seen_names: dict[str, Path] = {}
    for model_path in args.models:
        if not model_path.is_file():
            print(f"model not found: {model_path}", file=sys.stderr)
            return 1
        model = MarkovModel.load(model_path)
        previous = seen_names.get(model.name)
        if previous is not None:
            print(
                f"two models share the name {model.name!r}: {previous} and {model_path}",
                file=sys.stderr,
            )
            return 1
        seen_names[model.name] = model_path
        models.append(model)

    if args.limit < 0:
        print("--limit must be >= 0", file=sys.stderr)
        return 1

    results = score_folder(
        input_dir,
        models,
        thresholds,
        args.column,
        unique=not args.no_unique,
        limit=args.limit,
        progress=sys.stderr,
    )

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output.with_suffix(".csv")
    write_markdown(output, input_dir.resolve(), models, thresholds, not args.no_unique, results)
    write_csv(csv_path, models, thresholds, results)
    print(f"wrote {output}", file=sys.stderr)
    print(f"wrote {csv_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
