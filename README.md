# tapir-analyse-qname-randomness

This repository contains two binaries:

- [cmd/tapir-analyse-qname-randomness](cmd/tapir-analyse-qname-randomness) -
  the NATS-driven analyst service, which should flag incoming domains which seem to
  be generated from a uniform distribution.
- [cmd/train-domain-markov-model](cmd/train-domain-markov-model) - an offline
  CLI tool for training a Markov model consumed by the service above. 

## Repository layout

- `cmd/` - entry points for the two binaries above.
- `internal/` - shared packages: `api`, `app` (the service), `classifier`
  (Markov DGA classification), and `corpus`, `markov` (the training CLI and
  its model).
- `data/` - training corpora and generated models. This folder is gitignored
  (except for a small tracked example) since these files can be large and are
  regenerable; keep your own corpora and trained models here locally.
- `misc/` - auxiliary tooling that isn't part of the Go build, such as the
  Python script used to turn a raw CSV domain list into a training corpus
  (see [misc/parse_csv_to_corpus.py](misc/parse_csv_to_corpus.py)).

## Training the Markov model

```sh
go run ./cmd/train-domain-markov-model -input data/domains.txt -output data/model.json
```

- `-input` (required): path to a file with one domain name per line. Blank
  lines and lines starting with `#` are ignored.
- `-output` (default `model.json`): where to write the trained model.

## Configuration example
```toml
# Debugging for entire app, CLI flag "-debug" has the same effect
debug = true

# Interval at which periodic tasks can be executed, if needed
interval = 120

# Path to a trained Markov model JSON file (required)
model_path = "data/models/se_min8_k2_model.json"

# Normalized log-likelihood breakpoint: score < threshold => likely DGA.
# This value is model-dependent; retune when changing training data or smoothing.
threshold = -5.0

# NATS configuration
[nats]
# Debugging NATS parts only
debug = true

# URL where NATS server can be found, can also be set via env "DNSTAPIR_NATS_URL"
url = "nats://localhost:4222"

# Subject for listening to incoming events that typically drive the analyst
event_subject = "internal.events.new_qname"

# Subject prefix used when when setting observation flags for a domain
observation_subject_prefix = "internal.observations"

# Configuration for observation buckets
[[nats.observation_buckets]]
# What is the name of the NATS bucket that will contain the observations?
name = "<BUCKET NAME>"

# What is the observation we are trying to set?
observation = "<OBSERVATION NAME>"

# Time-to-live for the observations in this bucket
ttl = 10

# Should this analyst create the bucket? Observation buckets are typically pre-provisioned
create = false

# Configuration for this analysts private bucket that it can use for arbitrary data
[nats.private_bucket]
# Bucket name, ensure it's unique if there are multiple instances of this analyst
name = "private_tapir-analyse-qname-randomness"

# Should this analyst create the bucket? Typically yes for private buckets
create = true

# Configuration for the "seen domains" bucket which keeps track of seen domains
# Keys in this bucket do not expire
# Should only be used by a dedicated analyst such as github.com/dnstapir/tapir-analyse-new-qname
[nats.seen_domains_bucket]
# Bucket name
name = "seen_domains"

# Should this analyst create the bucket? Most of the time, the answer is no
create = false

```
