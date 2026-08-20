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

	root, err := os.MkdirTemp("", "LinkVideo.Helper-installer-selftest-")
	if err != nil {
		return fmt.Errorf("create temp directory: %w", err)
	}
	defer os.RemoveAll(root)
	dest := filepath.Join(root, "LinkVideo.Helper")

	// Model a legacy installation. Staging must leave it completely untouched
	// until the new embedded payload has been extracted and verified.
	staleFiles := []string{
		filepath.Join(dest, "tools", "ffmpeg.exe"),
		filepath.Join(dest, "_internal", "stale-runtime.dll"),
		filepath.Join(dest, "obsolete.py"),
	}
	for _, path := range staleFiles {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return fmt.Errorf("prepare stale fixture: %w", err)
		}
		if err := os.WriteFile(path, []byte("stale"), 0o644); err != nil {
			return fmt.Errorf("write stale fixture: %w", err)
		}
	}
	legacyApp := filepath.Join(dest, "LinkVideo VPN Helper.exe")
	if err := os.WriteFile(legacyApp, make([]byte, 120_001), 0o755); err != nil {
		return fmt.Errorf("write legacy application fixture: %w", err)
	}

	staging, err := stageRuntimeSnapshot(dest, nil)
	if err != nil {
		return fmt.Errorf("stage embedded payload: %w", err)
	}
	defer os.RemoveAll(staging)
	for _, path := range staleFiles {
		if _, err := os.Stat(path); err != nil {
			return fmt.Errorf("staging changed installed runtime %s: %w", filepath.Base(path), err)
		}
	}

	backup, err := activateStagedRuntime(dest, staging)
	if err != nil {
		return fmt.Errorf("activate staged payload: %w", err)
	}
	if backup == "" {
		return fmt.Errorf("upgrade did not preserve the previous runtime")
	}
	for _, path := range staleFiles {
		if _, err := os.Stat(path); err == nil {
			return fmt.Errorf("stale file survived atomic activation: %s", filepath.Base(path))
		} else if !os.IsNotExist(err) {
			return fmt.Errorf("check stale fixture: %w", err)
		}
	}
	if err := verifyRuntimeSnapshot(dest); err != nil {
		return fmt.Errorf("verify activated payload: %w", err)
	}
	// Simulate a process exit immediately after the atomic switch. Recovery must
	// recognize the valid new runtime and discard the preserved old directory.
	if err := recoverInterruptedRuntimeUpgrade(dest); err != nil {
		return fmt.Errorf("complete interrupted activation: %w", err)
	}
	if _, err := os.Stat(backup); !os.IsNotExist(err) {
		return fmt.Errorf("old runtime backup survived completed activation")
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

	// Simulate a crash in the narrow switch window: the old runtime was renamed
	// to .rollback but no valid new directory exists yet. Recovery must put the
	// old version back instead of deleting it.
	crashDest := filepath.Join(root, "crash-recovery")
	crashBackup := crashDest + ".rollback"
	if err := os.MkdirAll(crashBackup, 0o755); err != nil {
		return fmt.Errorf("prepare crash backup: %w", err)
	}
	crashLegacy := filepath.Join(crashBackup, "LinkVideo VPN Helper.exe")
	if err := os.WriteFile(crashLegacy, make([]byte, 120_001), 0o755); err != nil {
		return fmt.Errorf("prepare crash legacy app: %w", err)
	}
	if err := os.MkdirAll(crashDest, 0o755); err != nil {
		return fmt.Errorf("prepare incomplete runtime: %w", err)
	}
	if err := os.WriteFile(filepath.Join(crashDest, "partial.new"), []byte("partial"), 0o644); err != nil {
		return fmt.Errorf("prepare incomplete runtime file: %w", err)
	}
	if err := recoverInterruptedRuntimeUpgrade(crashDest); err != nil {
		return fmt.Errorf("recover previous runtime: %w", err)
	}
	if !previousRuntimeUsable(crashDest) {
		return fmt.Errorf("previous runtime was not restored after interrupted switch")
	}
	if _, err := os.Stat(crashBackup); !os.IsNotExist(err) {
		return fmt.Errorf("rollback directory survived successful recovery")
	}

	return nil
}
