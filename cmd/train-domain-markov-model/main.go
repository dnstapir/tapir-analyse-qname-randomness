package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/dnstapir/tapir-analyse-uniform-distribution/internal/corpus"
	"github.com/dnstapir/tapir-analyse-uniform-distribution/internal/markov"
)

func main() {
	var inputPath string
	var outputPath string
	var smoothingK float64

	flag.StringVar(&inputPath,
		"input",
		"",
		"Path to a file containing domain names to train on, one per line",
	)
	flag.StringVar(&outputPath,
		"output",
		"model.json",
		"Path to write the trained model to",
	)
	flag.Float64Var(&smoothingK,
		"k",
		0,
		"Laplace smoothing parameter (0 = no smoothing, 1 = add-one)",
	)
	flag.Parse()

	if inputPath == "" {
		fmt.Fprintln(os.Stderr, "missing required -input flag")
		os.Exit(1)
	}

	domains, err := corpus.ReadFile(inputPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "could not read domain list from %q: %s\n", inputPath, err)
		os.Exit(1)
	}

	model := markov.New()

	if err := model.Train(domains, smoothingK); err != nil {
		fmt.Fprintf(os.Stderr, "training failed: %s\n", err)
		os.Exit(1)
	}

	if err := model.Save(outputPath); err != nil {
		fmt.Fprintf(os.Stderr, "could not save model to %q: %s\n", outputPath, err)
		os.Exit(1)
	}

	fmt.Printf("trained model on domains from %q, wrote model to %q\n", inputPath, outputPath)
}
