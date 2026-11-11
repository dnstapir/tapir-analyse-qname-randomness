package app

import (
	"testing"

	"github.com/dnstapir/tapir-analyse-lib/libtapir"
)

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

func TestMatchesIgnoredSuffix(t *testing.T) {
	suffixes := map[string]struct{}{
		libtapir.NormalizeDomainName("example.com"):    {},
		libtapir.NormalizeDomainName("translate.goog"): {},
	}
	tests := []struct {
		domain string
		want   bool
	}{
		{"example.com.", true},
		{"www.example.com.", true},
		{"www-valkwijn-nl.translate.goog.", true},
		{"notexample.com.", false},
		{"example.com.evil.net.", false},
		{"example.org.", false},
		{"com.", false},
	}
	for _, tt := range tests {
		_, got := matchesIgnoredSuffix(tt.domain, suffixes)
		if got != tt.want {
			t.Errorf("matchesIgnoredSuffix(%q) = %t, want %t", tt.domain, got, tt.want)
		}
	}

	if _, got := matchesIgnoredSuffix("example.com.", nil); got {
		t.Error("expected no match with no suffixes configured")
	}
}
