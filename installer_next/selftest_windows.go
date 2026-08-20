//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// --self-test is intentionally handled in init(), before main asks Windows for
// elevation or opens any UI.  CI can therefore execute the exact produced Setup
// and verify its embedded payload without touching Program Files, the registry,
// shortcuts, scheduled tasks or user settings.
func init() {
	if strings.EqualFold(buildMode, "uninstaller") || !hasArg("--self-test") {
		return
	}
	if err := installerSelfTest(); err != nil {
		fmt.Fprintln(os.Stderr, "LinkVideo.Helper installer self-test failed:", err)
		os.Exit(20)
	}
	os.Exit(0)
}

func installerSelfTest() error {
	if len(payload) == 0 {
		return fmt.Errorf("embedded payload is empty")
	}

	dest, err := os.MkdirTemp("", "LinkVideo.Helper-installer-selftest-")
	if err != nil {
		return fmt.Errorf("create temp directory: %w", err)
	}
	defer os.RemoveAll(dest)

	// Exercise the same authoritative-upgrade cleanup that runs in Program Files.
	// These fixtures represent files that must never survive a full upgrade.
	staleFiles := []string{
		filepath.Join(dest, "tools", "ffmpeg.exe"),
		filepath.Join(dest, "_internal", "stale-runtime.dll"),
		filepath.Join(dest, "LinkVideo VPN Helper.exe"),
	}
	for _, path := range staleFiles {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return fmt.Errorf("prepare stale fixture: %w", err)
		}
		if err := os.WriteFile(path, []byte("stale"), 0o644); err != nil {
			return fmt.Errorf("write stale fixture: %w", err)
		}
	}
	if err := cleanRuntimeBeforeInstall(dest); err != nil {
		return fmt.Errorf("runtime cleanup: %w", err)
	}
	for _, path := range staleFiles {
		if _, err := os.Stat(path); err == nil {
			return fmt.Errorf("stale file survived cleanup: %s", filepath.Base(path))
		} else if !os.IsNotExist(err) {
			return fmt.Errorf("check stale fixture: %w", err)
		}
	}

	if err := extractPayload(dest, nil); err != nil {
		return fmt.Errorf("extract embedded payload: %w", err)
	}

	required := map[string]int64{
		appExeName:                      100_000,
		"LinkVideo.Helper.Updater.exe": 200_000,
		"Uninstall.exe":                500_000,
	}
	for name, minSize := range required {
		path := filepath.Join(dest, name)
		info, err := os.Stat(path)
		if err != nil {
			return fmt.Errorf("required payload file missing %s: %w", name, err)
		}
		if !info.Mode().IsRegular() || info.Size() < minSize {
			return fmt.Errorf("required payload file is invalid %s (%d bytes)", name, info.Size())
		}
	}

	// FFmpeg must only live in the per-user LocalAppData cache after first use.
	// A stale developer copy in Setup would recreate the 3.0.10 packaging bug.
	err = filepath.WalkDir(dest, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if !entry.IsDir() && strings.EqualFold(entry.Name(), "ffmpeg.exe") {
			return fmt.Errorf("ffmpeg.exe unexpectedly bundled at %s", path)
		}
		return nil
	})
	if err != nil {
		return err
	}

	return nil
}
