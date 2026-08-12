package corpus

import (
	"bufio"
	"os"
	"strings"
)

// ReadFile reads domain names from a plain text file, one per line
func ReadFile(path string) (<-chan string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}

	ch := make(chan string, 1024)

	go func() {
		defer f.Close()
		defer close(ch)

		scanner := bufio.NewScanner(f)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}

			ch <- line
		}
	}()

	return ch, nil
}
