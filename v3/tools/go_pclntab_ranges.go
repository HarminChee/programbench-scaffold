// Command go_pclntab_ranges recovers Go function ranges from .gopclntab.
// It is a fallback for stripped Go ELF binaries where `go tool nm` cannot
// read a conventional symbol table.
package main

import (
	"debug/elf"
	"debug/gosym"
	"encoding/json"
	"fmt"
	"os"
)

type functionRange struct {
	Start uint64 `json:"start"`
	End   uint64 `json:"end"`
	Name  string `json:"name"`
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: go_pclntab_ranges <go-elf>")
		os.Exit(2)
	}
	f, err := elf.Open(os.Args[1])
	if err != nil {
		panic(err)
	}
	defer f.Close()
	pclnSection := f.Section(".gopclntab")
	textSection := f.Section(".text")
	if pclnSection == nil || textSection == nil {
		fmt.Fprintln(os.Stderr, "missing .gopclntab or .text")
		os.Exit(1)
	}
	pcln, err := pclnSection.Data()
	if err != nil {
		panic(err)
	}
	lineTable := gosym.NewLineTable(pcln, textSection.Addr)
	table, err := gosym.NewTable(nil, lineTable)
	if err != nil {
		panic(err)
	}
	encoder := json.NewEncoder(os.Stdout)
	for _, fn := range table.Funcs {
		if fn.End <= fn.Entry || fn.Name == "" {
			continue
		}
		if err := encoder.Encode(functionRange{Start: fn.Entry, End: fn.End, Name: fn.Name}); err != nil {
			panic(err)
		}
	}
}
