package main

import (
	"bufio"
	"encoding/csv"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/dnstapir/tapir-analyse-dga-scorer/internal/markov"
)

var (
	modelPath  string
	domain     string
	inputPath  string
	outputPath string
	debug      bool
)

func main() {
	flag.StringVar(&modelPath, "model", "data/models/se_min8_k1_model.json", "path to the model file")
	flag.StringVar(&domain, "domain", "example", "domain label to score (single-domain mode)")
	flag.StringVar(&inputPath, "input", "", "path to a file with one domain per line, or - for stdin (batch mode)")
	flag.StringVar(&outputPath, "output", "scores.csv", "path to write CSV results in batch mode")
	flag.BoolVar(&debug, "debug", false, "print progress updates to stderr")
	flag.Parse()

	model, err := markov.Load(modelPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "could not load model: %s\n", err)
		os.Exit(1)
	}

	if inputPath != "" {
		if err := scoreBatch(model); err != nil {
			fmt.Fprintf(os.Stderr, "%s\n", err)
			os.Exit(1)
		}
		return
	}

	score, err := model.ScoreNormalized(domain)
	if err != nil {
		fmt.Fprintf(os.Stderr, "could not score domain: %s\n", err)
		os.Exit(1)
	}

	fmt.Printf("score: %f\n", score)
}

func scoreBatch(model *markov.Model) error {
	reader, closer, err := openInput(inputPath)
	if err != nil {
		return err
	}
	if closer != nil {
		defer closer.Close()
	}

	domains, err := readDomains(reader)
	if err != nil {
		return err
	}
	if len(domains) == 0 {
		return fmt.Errorf("no domains to score")
	}

	out, err := os.Create(outputPath)
	if err != nil {
		return fmt.Errorf("could not create output file %q: %w", outputPath, err)
	}
	defer out.Close()

	total := len(domains)
	progressInterval := max(1, total/100)
	start := time.Now()
	lastProgress := time.Time{}
	buckets := make(map[string]int)
	scores := make([]float64, total)

	if debug {
		fmt.Fprintf(os.Stderr, "scoring %d domains...\n", total)
	}

	for index, name := range domains {
		score, err := model.ScoreNormalized(name)
		if err != nil {
			return fmt.Errorf("could not score %q: %w", name, err)
		}

		scores[index] = score
		buckets[scoreBucket(score)]++

		if debug {
			now := time.Now()
			done := index + 1
			if done == total || done%progressInterval == 0 || now.Sub(lastProgress) >= 5*time.Second {
				printProgress(done, total, start)
				lastProgress = now
			}
		}
	}

	if err := writeCSV(out, modelPath, inputPath, domains, scores, buckets, total); err != nil {
		return err
	}

	if debug {
		fmt.Fprintf(os.Stderr, "finished scoring in %s\n", formatDuration(time.Since(start)))
	}
	printBucketStats(buckets, total)
	fmt.Printf("\nwrote scores to %s\n", outputPath)

	return nil
}

func openInput(path string) (io.Reader, io.Closer, error) {
	if path == "-" {
		return os.Stdin, nil, nil
	}

	f, err := os.Open(path)
	if err != nil {
		return nil, nil, fmt.Errorf("could not open input file %q: %w", path, err)
	}

	return f, f, nil
}

func readDomains(reader io.Reader) ([]string, error) {
	scanner := bufio.NewScanner(reader)
	domains := make([]string, 0, 1024)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		domains = append(domains, line)
	}

	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("could not read domains: %w", err)
	}

	return domains, nil
}

func scoreBucket(score float64) string {
	if math.IsInf(score, -1) {
		return "(-inf, -inf]"
	}

	upper := math.Ceil(score - 1e-12)
	lower := upper - 1
	return fmt.Sprintf("(%g, %g]", lower, upper)
}

func bucketUpper(label string) float64 {
	if label == "(-inf, -inf]" {
		return math.Inf(-1)
	}

	parts := strings.Split(strings.TrimSuffix(label, "]"), ", ")
	if len(parts) != 2 {
		return math.Inf(-1)
	}

	upper, err := parseFloat(parts[1])
	if err != nil {
		return math.Inf(-1)
	}

	return upper
}

func parseFloat(value string) (float64, error) {
	if value == "-inf" {
		return math.Inf(-1), nil
	}
	if value == "inf" || value == "+inf" {
		return math.Inf(1), nil
	}

	var f float64
	_, err := fmt.Sscanf(value, "%f", &f)
	return f, err
}

type bucketStat struct {
	label string
	upper float64
	count int
}

func sortedBucketStats(buckets map[string]int) []bucketStat {
	stats := make([]bucketStat, 0, len(buckets))
	for label, count := range buckets {
		stats = append(stats, bucketStat{
			label: label,
			upper: bucketUpper(label),
			count: count,
		})
	}

	sort.Slice(stats, func(i, j int) bool {
		return stats[i].upper > stats[j].upper
	})

	return stats
}

type scoredDomain struct {
	name  string
	score float64
}

func writeCSV(out *os.File, modelPath, inputPath string, domains []string, scores []float64, buckets map[string]int, total int) error {
	if err := writeCSVMetadata(out, modelPath, inputPath, buckets, total); err != nil {
		return err
	}

	rows := make([]scoredDomain, len(domains))
	for index, name := range domains {
		rows[index] = scoredDomain{name: name, score: scores[index]}
	}
	sort.Slice(rows, func(i, j int) bool {
		return rows[i].score > rows[j].score
	})

	writer := csv.NewWriter(out)
	if err := writer.Write([]string{"domain", "score"}); err != nil {
		return err
	}

	for _, row := range rows {
		if err := writer.Write([]string{row.name, fmt.Sprintf("%f", row.score)}); err != nil {
			return err
		}
	}

	writer.Flush()
	return writer.Error()
}

func writeCSVMetadata(out io.Writer, modelPath, inputPath string, buckets map[string]int, total int) error {
	lines := []string{
		fmt.Sprintf("# model: %s", filepath.Base(modelPath)),
		fmt.Sprintf("# input: %s", filepath.Base(inputPath)),
		fmt.Sprintf("# scored: %d", total),
	}

	for _, stat := range sortedBucketStats(buckets) {
		percent := 100.0 * float64(stat.count) / float64(total)
		lines = append(lines, fmt.Sprintf("# bucket: %s count=%d percent=%.1f%%", stat.label, stat.count, percent))
	}

	for _, line := range lines {
		if _, err := fmt.Fprintln(out, line); err != nil {
			return err
		}
	}

	return nil
}

func printBucketStats(buckets map[string]int, total int) {
	fmt.Printf("scored %d domains\n\n", total)
	fmt.Println("bucket\tcount\tpercent")
	for _, stat := range sortedBucketStats(buckets) {
		percent := 100.0 * float64(stat.count) / float64(total)
		fmt.Printf("%s\t%d\t%.1f%%\n", stat.label, stat.count, percent)
	}
}

func printProgress(done, total int, start time.Time) {
	elapsed := time.Since(start)
	pct := 100.0 * float64(done) / float64(total)

	var remaining time.Duration
	if done > 0 {
		remaining = time.Duration(float64(elapsed) / float64(done) * float64(total-done))
	}

	fmt.Fprintf(
		os.Stderr,
		"scoring %d/%d (%.1f%%) - elapsed %s - ETA %s\n",
		done,
		total,
		pct,
		formatDuration(elapsed),
		formatDuration(remaining),
	)
}

func formatDuration(d time.Duration) string {
	if d < time.Minute {
		return fmt.Sprintf("%.0fs", d.Seconds())
	}

	minutes := int(d.Minutes())
	seconds := int(d.Seconds()) % 60
	if minutes < 60 {
		return fmt.Sprintf("%dm %ds", minutes, seconds)
	}

	hours := minutes / 60
	minutes %= 60
	return fmt.Sprintf("%dh %dm", hours, minutes)
}
