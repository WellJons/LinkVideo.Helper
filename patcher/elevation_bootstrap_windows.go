//go:build windows

package main

import "os"

// Avoid reporting success from the non-elevated parent process. The parent only
// launches the elevated patch worker and exits; all validation/apply/rollback UI
// belongs to the elevated child.
func init() {
    elevated, err := ensureElevated()
    if err != nil {
        return // applyPatch will surface the same error through the normal UI.
    }
    if !elevated {
        os.Exit(0)
    }
}
