package classifier

import (
	"fmt"

	"github.com/dnstapir/tapir-analyse-dga-scorer/internal/markov"
)

type Class int

const (
	NotDGA Class = iota
	LikelyDGA
)

func (c Class) String() string {
	switch c {
	case NotDGA:
		return "not_dga"
	case LikelyDGA:
		return "likely_dga"
	default:
		return fmt.Sprintf("Class(%d)", int(c))
	}
}

type Conf struct {
	ModelPath string  `toml:"model_path"`
	Threshold float64 `toml:"threshold"`
}

type Classifier struct {
	model     *markov.Model
	threshold float64
}

func Create(conf Conf) (*Classifier, error) {
	if conf.ModelPath == "" {
		return nil, fmt.Errorf("model_path is required")
	}

	model, err := markov.Load(conf.ModelPath)
	if err != nil {
		return nil, fmt.Errorf("could not load model from %q: %w", conf.ModelPath, err)
	}

	return &Classifier{
		model:     model,
		threshold: conf.Threshold,
	}, nil
}

// Classify scores label with the Markov model and returns not_dga or
// likely_dga when the normalized score is strictly below the configured
// threshold.
func (c *Classifier) Classify(label string) (Class, float64, error) {
	if label == "" {
		return NotDGA, 0, fmt.Errorf("empty label")
	}

	score, err := c.model.ScoreNormalized(label)
	if err != nil {
		return NotDGA, 0, err
	}

	if score < c.threshold {
		return LikelyDGA, score, nil
	}
	return NotDGA, score, nil
}
