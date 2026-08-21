//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

const fixedFileInfoSignature = 0xFEEF04BD

type vsFixedFileInfo struct {
	Signature        uint32
	StrucVersion     uint32
	FileVersionMS    uint32
	FileVersionLS    uint32
	ProductVersionMS uint32
	ProductVersionLS uint32
	FileFlagsMask    uint32
	FileFlags        uint32
	FileOS           uint32
	FileType         uint32
	FileSubtype      uint32
	FileDateMS       uint32
	FileDateLS       uint32
}

var (
	versionDLL              = syscall.NewLazyDLL("version.dll")
	getFileVersionInfoSizeW = versionDLL.NewProc("GetFileVersionInfoSizeW")
	getFileVersionInfoW     = versionDLL.NewProc("GetFileVersionInfoW")
	verQueryValueW          = versionDLL.NewProc("VerQueryValueW")
)

// nativeProductVersion reads VS_FIXEDFILEINFO directly. Auto-update must not
// depend on PowerShell startup time or execution policy just to read a local
// executable's ProductVersion.
func nativeProductVersion(path string) (string, error) {
	if _, err := os.Stat(path); err != nil {
		return "", fmt.Errorf("не найден установленный %s", filepath.Base(path))
	}
	widePath, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return "", fmt.Errorf("некорректный путь файла версии: %w", err)
	}

	size, _, sizeErr := getFileVersionInfoSizeW.Call(uintptr(unsafe.Pointer(widePath)), 0)
	if size == 0 {
		return "", fmt.Errorf("Windows не вернула version resource для %s: %v", filepath.Base(path), sizeErr)
	}
	buffer := make([]byte, int(size))
	ok, _, infoErr := getFileVersionInfoW.Call(
		uintptr(unsafe.Pointer(widePath)),
		0,
		size,
		uintptr(unsafe.Pointer(&buffer[0])),
	)
	if ok == 0 {
		return "", fmt.Errorf("не удалось прочитать version resource %s: %v", filepath.Base(path), infoErr)
	}

	root, _ := syscall.UTF16PtrFromString(`\`)
	var value unsafe.Pointer
	var valueLen uint32
	ok, _, queryErr := verQueryValueW.Call(
		uintptr(unsafe.Pointer(&buffer[0])),
		uintptr(unsafe.Pointer(root)),
		uintptr(unsafe.Pointer(&value)),
		uintptr(unsafe.Pointer(&valueLen)),
	)
	if ok == 0 || value == nil || valueLen < uint32(unsafe.Sizeof(vsFixedFileInfo{})) {
		return "", fmt.Errorf("ProductVersion отсутствует в %s: %v", filepath.Base(path), queryErr)
	}

	info := (*vsFixedFileInfo)(value)
	if info.Signature != fixedFileInfoSignature {
		return "", fmt.Errorf("version resource %s имеет неверную сигнатуру", filepath.Base(path))
	}
	return fmt.Sprintf(
		"%d.%d.%d.%d",
		info.ProductVersionMS>>16,
		info.ProductVersionMS&0xffff,
		info.ProductVersionLS>>16,
		info.ProductVersionLS&0xffff,
	), nil
}
