#!/usr/bin/env python3
"""Generate randomized domain labels for testing.

Labels use lowercase letters and digits (a-z, 0-9) only.
"""

import argparse
import random
import sys
from typing import Iterator, Optional, TextIO

_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def generate_label(rng: random.Random, length: int) -> str:
    if length < 1:
        raise ValueError("length must be at least 1")
    return "".join(rng.choice(_CHARS) for _ in range(length))


def generate_labels(
    rng: random.Random,
    count: int,
    min_length: int,
    max_length: int,
) -> Iterator[str]:
    for _ in range(count):
        length = rng.randint(min_length, max_length)
        yield generate_label(rng, length)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate randomized domain labels, one per line.",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=100,
        help="Number of labels to generate (default: 100)",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=8,
        help="Minimum label length (default: 8)",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=63,
        help="Maximum label length (default: 63)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Random seed for reproducible output",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write labels to this file instead of stdout",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.count < 1:
        print("count must be at least 1", file=sys.stderr)
        return 1
    if args.min_length < 1:
        print("min-length must be at least 1", file=sys.stderr)
        return 1
    if args.max_length < args.min_length:
        print("max-length must be >= min-length", file=sys.stderr)
        return 1
    if args.max_length > 63:
        print("max-length must be <= 63 (DNS label limit)", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)

    out: TextIO
    if args.output:
        out = open(args.output, "w", encoding="utf-8")
    else:
        out = sys.stdout

    try:
        for label in generate_labels(rng, args.count, args.min_length, args.max_length):
            print(label, file=out)
    finally:
        if args.output:
            out.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
