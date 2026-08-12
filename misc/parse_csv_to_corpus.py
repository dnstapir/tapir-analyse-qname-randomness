#!/usr/bin/env python3
"""Extract second-level domain labels from a CSV file into a corpus.

Reads domain names from a chosen column of a CSV file, strips the public
suffix (TLD, e.g. "com", "co.uk") and any subdomains, keeping only the
second-level label (e.g. "example.co.uk" -> "example"). The remaining
labels are filtered and written to an output text file, one per line, for
use as training data (see internal/corpus/corpus.go).
"""

import argparse
import csv
import re
import sys
from typing import Iterator, Optional

import tldextract

_LDH_LABEL_RE = re.compile(r"^[a-z0-9-]+$")
_HAS_ALPHA_RE = re.compile(r"[a-z]")

# Use only the public suffix list snapshot bundled with tldextract instead
# of fetching a fresh copy over the network on every run. This keeps the
# script fast, deterministic, and usable offline.
_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def extract_second_level_label(domain: str) -> Optional[str]:
    """Return the lowercased second-level label of domain, or None.

    Uses the Public Suffix List (via tldextract) to correctly strip
    multi-part TLDs (e.g. "co.uk") and any subdomains, e.g.:
        "www.example.co.uk" -> "example"
        "sub.sub2.example.com" -> "example"
    Returns None if domain does not have a recognized public suffix
    (e.g. it is empty, a bare hostname, or malformed).
    """
    domain = domain.strip().lower()
    if not domain:
        return None

    extracted = _TLD_EXTRACTOR(domain)
    if not extracted.domain or not extracted.suffix:
        return None

    return extracted.domain


def passes_filters(
    label: str,
    min_length: int = 2,
    max_length: int = 63,
    require_alpha: bool = True,
    allow_idn: bool = False,
) -> bool:
    """Decide whether label is suitable for inclusion in the corpus."""
    if not (min_length <= len(label) <= max_length):
        return False

    if not _LDH_LABEL_RE.match(label):
        return False

    if label.startswith("-") or label.endswith("-"):
        return False

    if not allow_idn and label.startswith("xn--"):
        return False

    if require_alpha and not _HAS_ALPHA_RE.search(label):
        return False

    return True


def parse_csv_to_corpus(
    csv_file: str,
    domain_column: int,
    has_header: bool = False,
    min_length: int = 2,
    max_length: int = 63,
    require_alpha: bool = True,
    allow_idn: bool = False,
    unique: bool = False,
) -> Iterator[str]:
    """Yield filtered second-level domain labels read from csv_file."""
    seen = set() if unique else None

    with open(csv_file, "r", newline="") as file:
        reader = csv.reader(file)

        if has_header:
            next(reader, None)

        for row in reader:
            if domain_column >= len(row):
                continue

            domain = row[domain_column]
            if not domain:
                continue

            label = extract_second_level_label(domain)
            if label is None:
                continue

            if not passes_filters(
                label,
                min_length=min_length,
                max_length=max_length,
                require_alpha=require_alpha,
                allow_idn=allow_idn,
            ):
                continue

            if seen is not None:
                if label in seen:
                    continue
                seen.add(label)

            yield label


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract second-level domain labels from a CSV column into a "
            "plain-text corpus, one label per line."
        )
    )
    parser.add_argument("input", help="Path to the input CSV file")
    parser.add_argument("output", help="Path to write the output text file to")
    parser.add_argument(
        "-c",
        "--column",
        type=int,
        default=0,
        help="Zero-indexed column number containing domain names (default: 0)",
    )
    parser.add_argument(
        "--has-header",
        action="store_true",
        help="Skip the first row of the CSV file (a header row)",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=2,
        help="Minimum label length to keep (default: 2)",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=63,
        help="Maximum label length to keep (default: 63, the DNS label limit)",
    )
    parser.add_argument(
        "--allow-numeric",
        action="store_true",
        help="Keep labels with no alphabetic characters (default: they are dropped)",
    )
    parser.add_argument(
        "--allow-idn",
        action="store_true",
        help="Keep punycode/IDN labels (starting with 'xn--') (default: they are dropped)",
    )
    parser.add_argument(
        "--unique",
        action="store_true",
        help="Drop duplicate labels, keeping only the first occurrence",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    total_rows = 0
    written = 0

    with open(args.output, "w") as out_file:
        with open(args.input, "r", newline="") as in_file:
            total_rows = sum(1 for _ in in_file) - (1 if args.has_header else 0)

        for label in parse_csv_to_corpus(
            args.input,
            args.column,
            has_header=args.has_header,
            min_length=args.min_length,
            max_length=args.max_length,
            require_alpha=not args.allow_numeric,
            allow_idn=args.allow_idn,
            unique=args.unique,
        ):
            out_file.write(label + "\n")
            written += 1

    print(
        f"read {total_rows} rows from {args.input!r}, "
        f"wrote {written} labels to {args.output!r} "
        f"({total_rows - written} rows filtered out)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
