package markov

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
)

const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789-"

type Model struct {
	Init        map[byte]float64
	Transitions map[byte]map[byte]float64

	TrainedNames int
}

// logProb marshals as a JSON number, or "-Inf" since JSON has no native
// representation for infinity.
type logProb float64

func (p logProb) MarshalJSON() ([]byte, error) {
	if math.IsInf(float64(p), -1) {
		return json.Marshal("-Inf")
	}
	return json.Marshal(float64(p))
}

func (p *logProb) UnmarshalJSON(data []byte) error {
	if string(data) == `"-Inf"` {
		*p = logProb(math.Inf(-1))
		return nil
	}

	var f float64
	if err := json.Unmarshal(data, &f); err != nil {
		return fmt.Errorf("invalid log-likelihood value %s: %w", data, err)
	}
	*p = logProb(f)
	return nil
}

// on-disk representation of a Model
type modelJSON struct {
	Init        map[string]logProb            `json:"init"`
	Transitions map[string]map[string]logProb `json:"transitions"`

	TrainedNames int `json:"trained_names"`
}

func New() *Model {
	m := &Model{
		Init:         make(map[byte]float64, len(alphabet)),
		Transitions:  make(map[byte]map[byte]float64, len(alphabet)),
		TrainedNames: 0,
	}

	negInf := math.Inf(-1)
	for i := range len(alphabet) {
		char := alphabet[i]
		m.Init[char] = negInf

		m.Transitions[char] = make(map[byte]float64, len(alphabet))
		for j := range len(alphabet) {
			m.Transitions[char][alphabet[j]] = negInf
		}
	}

	return m
}

// Train estimates log-likelihoods from domains. When k is 0, only observed
// symbols and transitions are updated (unobserved stay at -Inf). When k > 0,
// Laplace smoothing is applied over the full alphabet.
func (m *Model) Train(domains <-chan string, k float64) error {
	if k < 0 {
		return fmt.Errorf("smoothing k must be >= 0, got %v", k)
	}

	var initCounts = make(map[byte]int)
	var transitionCounts = make(map[byte]map[byte]int)
	var transitionTotals = make(map[byte]int)

	for domain := range domains {
		initCounts[domain[0]]++
		m.TrainedNames++

		for i := 0; i < len(domain); i++ {
			if i == 0 {
				continue
			}
			if transitionCounts[domain[i-1]] == nil {
				transitionCounts[domain[i-1]] = make(map[byte]int)
			}
			transitionCounts[domain[i-1]][domain[i]]++
			transitionTotals[domain[i-1]]++
		}
	}

	vocabSize := len(alphabet)

	if k == 0 {
		for char, count := range initCounts {
			m.Init[char] = math.Log(float64(count)) - math.Log(float64(m.TrainedNames))
		}

		for char, counts := range transitionCounts {
			for nextChar, count := range counts {
				m.Transitions[char][nextChar] = math.Log(float64(count)) - math.Log(float64(transitionTotals[char]))
			}
		}

		return nil
	}

	for i := range vocabSize {
		char := alphabet[i]
		m.Init[char] = math.Log(float64(initCounts[char])+k) - math.Log(float64(m.TrainedNames)+k*float64(vocabSize))
	}

	for i := range vocabSize {
		char := alphabet[i]
		counts := transitionCounts[char]
		total := transitionTotals[char]
		for j := range vocabSize {
			nextChar := alphabet[j]
			count := 0
			if counts != nil {
				count = counts[nextChar]
			}
			m.Transitions[char][nextChar] = math.Log(float64(count)+k) - math.Log(float64(total)+k*float64(vocabSize))
		}
	}

	return nil
}

// Score returns the total log-likelihood of domain.
func (m *Model) Score(domain string) (float64, error) {
	score := 0.0
	for i := 0; i < len(domain); i++ {
		if i == 0 {
			score += m.Init[domain[i]]
		} else {
			score += m.Transitions[domain[i-1]][domain[i]]
		}
	}
	return score, nil
}

func (m *Model) Save(path string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	return enc.Encode(m.toJSON())
}

func Load(path string) (*Model, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var aux modelJSON
	dec := json.NewDecoder(f)
	if err := dec.Decode(&aux); err != nil {
		return nil, err
	}

	return aux.toModel()
}

func (m *Model) toJSON() modelJSON {
	aux := modelJSON{
		Init:         make(map[string]logProb, len(m.Init)),
		Transitions:  make(map[string]map[string]logProb, len(m.Transitions)),
		TrainedNames: m.TrainedNames,
	}

	for char, prob := range m.Init {
		aux.Init[string(char)] = logProb(prob)
	}

	for char, counts := range m.Transitions {
		inner := make(map[string]logProb, len(counts))
		for nextChar, prob := range counts {
			inner[string(nextChar)] = logProb(prob)
		}
		aux.Transitions[string(char)] = inner
	}

	return aux
}

func (aux *modelJSON) toModel() (*Model, error) {
	m := &Model{
		Init:         make(map[byte]float64, len(aux.Init)),
		Transitions:  make(map[byte]map[byte]float64, len(aux.Transitions)),
		TrainedNames: aux.TrainedNames,
	}

	for char, prob := range aux.Init {
		if len(char) != 1 {
			return nil, fmt.Errorf("invalid init key %q: expected a single character", char)
		}
		m.Init[char[0]] = float64(prob)
	}

	for char, counts := range aux.Transitions {
		if len(char) != 1 {
			return nil, fmt.Errorf("invalid transitions key %q: expected a single character", char)
		}
		inner := make(map[byte]float64, len(counts))
		for nextChar, prob := range counts {
			if len(nextChar) != 1 {
				return nil, fmt.Errorf("invalid transitions[%q] key %q: expected a single character", char, nextChar)
			}
			inner[nextChar[0]] = float64(prob)
		}
		m.Transitions[char[0]] = inner
	}

	return m, nil
}
