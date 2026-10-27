package app

import "testing"

func TestLeftmostLabel(t *testing.T) {
	tests := []struct {
		in   string
		want string
	}{
		{"example.co.uk.", "example"},
		{"example.com.", "example"},
		{".", ""},
		{"", ""},
	}
	for _, tt := range tests {
		if got := leftmostLabel(tt.in); got != tt.want {
			t.Errorf("leftmostLabel(%q) = %q, want %q", tt.in, got, tt.want)
		}
	}
}

func TestIsIDN(t *testing.T) {
	if !isIDN("xn--bcher-kva.de.") {
		t.Error("expected punycode label to be IDN")
	}
	if !isIDN("bücher") {
		t.Error("expected non-ASCII to be IDN")
	}
	if isIDN("example.com.") {
		t.Error("expected ASCII domain not to be IDN")
	}
}
