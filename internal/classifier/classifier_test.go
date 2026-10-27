package classifier

import (
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/dnstapir/tapir-analyse-qname-randomness/internal/markov"
)

func TestCreateRequiresModelPath(t *testing.T) {
	_, err := Create(Conf{Threshold: -5})
	if err == nil {
		t.Fatal("expected error for empty model_path")
	}
}

func TestCreateBadModelPath(t *testing.T) {
	_, err := Create(Conf{
		ModelPath: filepath.Join(t.TempDir(), "missing.json"),
		Threshold: -5,
	})
	if err == nil {
		t.Fatal("expected error for missing model file")
	}
}

func TestClassifyThreshold(t *testing.T) {
	modelPath := writeTestModel(t)

	cMeasure, err := Create(Conf{ModelPath: modelPath, Threshold: 0})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	_, score, err := cMeasure.Classify("aaa")
	if err != nil {
		t.Fatalf("Classify: %v", err)
	}
	if score >= 0 {
		t.Fatalf("expected negative log-likelihood, got %f", score)
	}

	// score < threshold => likely DGA
	cStrict, err := Create(Conf{ModelPath: modelPath, Threshold: score + 1})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	class, got, err := cStrict.Classify("aaa")
	if err != nil {
		t.Fatalf("Classify: %v", err)
	}
	if class != LikelyDGA {
		t.Fatalf("expected likely_dga when score < threshold, got %s (score %f)", class, got)
	}

	// score >= threshold => not DGA
	cLoose, err := Create(Conf{ModelPath: modelPath, Threshold: score - 1})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	class, got, err = cLoose.Classify("aaa")
	if err != nil {
		t.Fatalf("Classify: %v", err)
	}
	if class != NotDGA {
		t.Fatalf("expected not_dga when score >= threshold, got %s (score %f)", class, got)
	}
	if math.Abs(got-score) > 1e-9 {
		t.Fatalf("score mismatch: got %f want %f", got, score)
	}
}

func TestClassifyEmptyLabel(t *testing.T) {
	c, err := Create(Conf{ModelPath: writeTestModel(t), Threshold: -5})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	_, _, err = c.Classify("")
	if err == nil {
		t.Fatal("expected error for empty label")
	}
}

func writeTestModel(t *testing.T) string {
	t.Helper()

	ch := make(chan string, 1)
	ch <- "aaa"
	close(ch)

	m := markov.New()
	if err := m.Train(ch, 1); err != nil {
		t.Fatalf("Train: %v", err)
	}

	path := filepath.Join(t.TempDir(), "model.json")
	if err := m.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("model file missing: %v", err)
	}
	return path
}
