//go:build windows

package main

import "os"

// Move the uninstaller out of Program Files before main starts. This guarantees
// that the elevated worker can remove the whole application directory instead
// of leaving its own executable behind.
func init() {
    if buildMode != "uninstaller" {
        return
    }
    proceed, err := ensureUninstallerRunsFromTemp()
    if err != nil {
        // main() will show normal elevation/UI errors when possible; failing
        // closed here is safer than partially deleting the installation.
        os.Exit(2)
    }
    if !proceed {
        os.Exit(0)
    }
}
