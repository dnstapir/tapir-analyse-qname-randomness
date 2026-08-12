package markov

import (
	"encoding/json"
	"fmt"
	"os"
)

const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789-"

type Model struct {
	Init        map[byte]float64
	Transitions map[byte]map[byte]float64

	TrainedNames int
}

// on-disk representation of a Model
type modelJSON struct {
	Init        map[string]float64            `json:"init"`
	Transitions map[string]map[string]float64 `json:"transitions"`

	TrainedNames int `json:"trained_names"`
}

func New() *Model {
	m := &Model{
		Init:         make(map[byte]float64, len(alphabet)),
		Transitions:  make(map[byte]map[byte]float64, len(alphabet)),
		TrainedNames: 0,
	}

	for i := range len(alphabet) {
		char := alphabet[i]
		m.Init[char] = 0

		m.Transitions[char] = make(map[byte]float64, len(alphabet))
		for j := range len(alphabet) {
			m.Transitions[char][alphabet[j]] = 0
		}
	}

	return m
}

func (m *Model) Train(domains <-chan string) error {
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

	for char, count := range initCounts {
		m.Init[char] = float64(count) / float64(m.TrainedNames)
		fmt.Printf("init[%c] = %f\n", char, m.Init[char])
	}

	for char, counts := range transitionCounts {
		m.Transitions[char] = make(map[byte]float64, len(counts))
		for nextChar, count := range counts {
			m.Transitions[char][nextChar] = float64(count) / float64(transitionTotals[char])
			fmt.Printf("transitions[%c][%c] = %f\n", char, nextChar, m.Transitions[char][nextChar])
		}
	}

	return nil
}

func (m *Model) Score(domain string) (float64, error) {
	score := 0.0
	for i := 0; i < len(domain); i++ {
		if i == 0 {
			score += m.Init[domain[i]]
		} else {
			score *= m.Transitions[domain[i-1]][domain[i]]
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
		Init:         make(map[string]float64, len(m.Init)),
		Transitions:  make(map[string]map[string]float64, len(m.Transitions)),
		TrainedNames: m.TrainedNames,
	}

	for char, prob := range m.Init {
		aux.Init[string(char)] = prob
	}

	for char, counts := range m.Transitions {
		inner := make(map[string]float64, len(counts))
		for nextChar, prob := range counts {
			inner[string(nextChar)] = prob
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
		m.Init[char[0]] = prob
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
			inner[nextChar[0]] = prob
		}
		m.Transitions[char[0]] = inner
	}

	return m, nil
}
