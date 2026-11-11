# tapir-analyse-qname-randomness

DNS TAPIR microanalyst that tags domains whose names look randomly generated, as
many DGA domains do. It reads new-qname events from NATS, scores each domain
with a character-level Markov model and tags the domain if the score is below
a threshold.

## How it works

For each event the analyst:

1. Skips the domain if it contains `_`, has no valid public suffix, is an IDN,
   or is a subdomain (only eTLD+1 names are scored).
2. Takes the label left of the public suffix (`example` in `example.co.uk`).
3. Scores that label: the model's log-probability of the first character plus
   each character transition, divided by the label length.
4. If the score is strictly below `threshold`, sets `tag` on the domain. The
   tag value is `{"score": <score>, "model": "PLACEHOLDER"}`.

## Binaries

- `cmd/tapir-analyse-qname-randomness`: the analyst service.
- `cmd/train-domain-markov-model`: trains a model from a list of labels.
- `cmd/test-domain-markov-score`: scores labels with a model, for choosing a
  threshold.

## Build

```sh
make          # all binaries into out/
make test
make ko       # container image of the service into the local Docker daemon
```

## Run

```sh
out/tapir-analyse-qname-randomness -config config.toml
```

Flags: `-config` (default `config.toml`), `-debug`, `-version`. The
`DNSTAPIR_NATS_URL` environment variable overrides `nats.url`.

Example `config.toml`:

```toml
debug = false

# Trained model to load (required)
model_path = "data/models/se_min8_k1_model.json"

# Labels scoring below this are tagged. Retune whenever the model changes.
threshold = -4.0

# Tag set on flagged domains (required)
tag = "seemingly_random"

# Domains to filter out, including their subdomains (optional)
ignore_suffixes = ["looptest.dnstapir.se", "translate.goog"]

[nats]
debug = false
url = "nats://localhost:4222"
event_subject = "internal.events.new_qname"
tag_subject_prefix = "internal.tags"

# Must already exist unless create = true
[nats.tag_bucket]
name = "tag_bucket"

[api]
active = false
```

The service exits on unknown config keys.

## Train a model

The trainer reads one label per line, lowercase `a-z`, `0-9` and `-` only.
Blank lines and lines starting with `#` are skipped.
`misc/parse_csv_to_corpus.py` builds such a file from a CSV of domains (run it
with `-h` for its filters):

```sh
pip install -r misc/requirements.txt
python3 misc/parse_csv_to_corpus.py domains.csv data/parsed/labels.txt --column 1
go run ./cmd/train-domain-markov-model -input data/parsed/labels.txt \
    -output data/models/model.json -k 1
```

`-k` is the Laplace smoothing constant. With the default `-k 0`, a transition
never seen in training has probability zero, so any label containing one
scores `-Inf` and gets tagged.

`data/` is gitignored. Keep corpora and models there.

## Choose a threshold

```sh
# One label
go run ./cmd/test-domain-markov-score -model data/models/model.json -domain example

# One label per line (or - for stdin). Writes scores.csv sorted by score and
# prints how many labels fall in each score interval of width 1.
go run ./cmd/test-domain-markov-score -model data/models/model.json \
    -input labels.txt -output scores.csv
```

Score a list of legitimate labels and a list of random or DGA labels, then
pick a threshold between the two distributions.

## Python helpers

In `misc/`, not part of the Go build:

- `parse_csv_to_corpus.py`: CSV of domains to training corpus.
- `generate_random_domains.py`: random `a-z0-9` labels for testing.
- `score_dga_families.py`: scores a folder of DGA family CSVs (one CSV per
  family, e.g. a DGArchive export) with one or more models and thresholds,
  and writes detection rates per family.
- `plot_dga_family_scores.py`: plots score ranges from
  `score_dga_families.py` output.
