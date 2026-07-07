---
title: Understanding `unsafe` in Go
description: Go spends a lot of effort convincing you that pointers are tame — bounds-checked slices, no pointer arithmetic, a GC that moves things around behind your back (well, mostly doesn't, but reserves the right to). 
category: programing
date: 2026-07-07
reading_time: 13
tags: [go, unsafe, memory, internals, low-level]
slug: understanding-in-go
---

# Understanding `unsafe` in Go

Go spends a lot of effort convincing you that pointers are tame — bounds-checked slices, no pointer arithmetic, a GC that moves things around behind your back (well, mostly doesn't, but reserves the right to). Then it ships a package literally called `unsafe` that lets you throw all of that away. If you've spent time in C with pointers or in reversing binaries and staring at memory layouts, `unsafe` is the point where Go stops pretending to be a managed language and lets you talk to the machine directly. This is a tour of what it actually does, why it exists, and where it bites people.

## Why `unsafe` exists at all

Go's type system and memory model are deliberately restrictive: no casting an `int32` pointer to a `float32` pointer, no pointer arithmetic, no peeking at a struct's raw byte layout. That's good for correctness but it means some things become literally impossible to express in "safe" Go — zero-copy conversions between `[]byte` and `string`, interfacing with C memory layouts via cgo, implementing sync primitives that need atomic access to arbitrary memory, or writing serialization code that wants to touch a struct's bytes directly instead of walking it field by field with reflection.

`unsafe` is the escape hatch. It's part of the language spec (sort of — it's a package but the compiler treats it specially), and using it means you're opting out of Go's memory-safety guarantees and its compatibility guarantees. The Go 1 promise explicitly does not cover `unsafe` — code using it can break on a compiler upgrade with no notice.

## The three core pieces

### `unsafe.Pointer`

This is the load-bearing type. It's a pointer type that can be converted to or from any other pointer type, and to/from `uintptr`. Regular Go pointers are strongly typed (`*int` isn't convertible to `*float64`); `unsafe.Pointer` is the hole in that wall.

```go
var i int64 = 42
var f *float64 = (*float64)(unsafe.Pointer(&i))
fmt.Println(*f) // reinterprets the int64 bits as a float64 — garbage, but valid Go
```

Four conversion rules govern legal use of `unsafe.Pointer` (from the package docs, and the compiler/vet will complain if you break them):

1. `*T1` → `unsafe.Pointer` → `*T2` — reinterpret one pointer type as another.
2. `unsafe.Pointer` → `uintptr` — get the numeric address.
3. `uintptr` → `unsafe.Pointer` — arithmetic on a pointer, then convert back **immediately, in the same expression**. This one has a sharp edge covered below.
4. Calling `syscall.Syscall` with a `uintptr` derived from `unsafe.Pointer`, for passing pointers to the kernel.

### `uintptr`

Just an integer large enough to hold an address. Critically, `uintptr` is **not a pointer** as far as the garbage collector is concerned — it's just a number. The GC doesn't scan `uintptr` values to find live objects. This is the source of the single most common `unsafe` bug:

```go
// WRONG — dangerous, classic use-after-free-by-GC pattern
p := uintptr(unsafe.Pointer(&someStruct))
// ... GC runs here, someStruct may move or be collected,
// because nothing marks it as reachable via p ...
q := unsafe.Pointer(p) // p may now point at freed/moved memory
```

Go's compiler and `go vet` will flag obvious versions of this, but it's still the number one way people get `unsafe` wrong. The fix is rule 3 above: do the `uintptr` arithmetic and the conversion back to `unsafe.Pointer` in a single expression, so the compiler can see the object is still needed and keep it alive/pinned through the operation:

```go
// Correct pattern: single expression, no GC-unsafe window
p := unsafe.Pointer(uintptr(unsafe.Pointer(&s)) + offset)
```

### `unsafe.Sizeof`, `unsafe.Alignof`, `unsafe.Offsetof`

These are compile-time constants that tell you a type's size, its alignment requirement, and a field's byte offset within a struct — exactly the information you'd get from `sizeof`/`offsetof` in C, and exactly what you need if you're hand-rolling a binary format or matching a C struct layout for cgo.

```go
type Header struct {
    Magic   uint32
    Version uint16
    Flags   uint16
}

fmt.Println(unsafe.Sizeof(Header{}))       // 8 (with padding rules applied)
fmt.Println(unsafe.Offsetof(Header{}.Flags)) // 6
```

Struct padding in Go follows the same alignment rules you'd expect from C, so `unsafe.Sizeof` isn't just "sum of field sizes" — it accounts for padding, which matters if you're building something that needs to match an external binary format exactly.

## Real, legitimate use cases

**Zero-copy string/byte conversion.** The standard idiom (used inside the Go runtime and standard library itself, e.g. in `strings.Builder`) avoids an allocation and copy when you know the underlying bytes won't be mutated:

```go
func bytesToString(b []byte) string {
    return unsafe.String(unsafe.SliceData(b), len(b))
}
```

(Go 1.20+ added `unsafe.String` and `unsafe.SliceData` specifically to make this pattern safer and clearer than the old three-word-header-reinterpretation trick people used to write by hand.)

**cgo interop.** When crossing the Go/C boundary, you're fundamentally dealing with raw memory that C allocated or expects a specific layout for — `unsafe.Pointer` is how `cgo`-generated code passes Go memory to C functions and back.

**Atomics on arbitrary fields.** Before generics-based atomic types, `sync/atomic` functions took `unsafe.Pointer` to operate on arbitrary memory locations for CAS operations on custom structures.

**High-performance serialization / low-GC-pressure code.** Some codecs reinterpret a `[]byte` buffer directly as a struct pointer to avoid per-field decoding overhead. This is legitimate but fragile — see portability concerns below.

## Where this gets dangerous

**It defeats the type system entirely.** The compiler will let you reinterpret a `*User` as a `*int64` and read whatever bytes happen to be there. There's no runtime check. If you get an offset or size wrong, you get memory corruption, not a panic — this is exactly the class of bug that "memory safe" language marketing implies you're immune to in Go, and `unsafe` is precisely where that immunity ends.

**Struct layout is not guaranteed across Go versions or platforms.** Field ordering, padding, and even whether fields get reordered by the compiler for better packing are implementation details. Code that assumes a specific memory layout via `unsafe.Offsetof` can silently break on a compiler upgrade or when cross-compiling to a different architecture with different alignment rules.

**GC interaction is subtle.** As shown above, treating a pointer as a plain integer (`uintptr`) for too long, or storing it in a way the GC can't trace, can lead to the object being collected or moved out from under you. This isn't a "maybe" — it's a real, exploitable class of bug in `unsafe`-heavy code, functionally similar to a use-after-free in C, just triggered by the collector instead of an explicit `free()`.

**It breaks the Go 1 compatibility promise.** Anthropic — sorry, Go itself — explicitly excludes `unsafe` usage from backward compatibility guarantees. Production code using it should be pinned to tested Go versions and re-verified on upgrade.

**Security surface.** From an offensive angle, `unsafe`-heavy Go code is one of the few places where classic memory-corruption primitives (type confusion, OOB read/write via miscalculated offsets, stale-pointer-via-uintptr) become relevant again in an otherwise memory-safe language. If you're auditing Go code, `grep -rn unsafe` is a genuinely productive first step — it's a strong prior for "here's where the interesting bugs are," the same way you'd zero in on `strcpy`/`memcpy` call sites in a C codebase.

## Practical guidance

- Default to not using it. Almost every ergonomic reason to reach for `unsafe` ("avoid a copy," "avoid reflection overhead") has a safe alternative that's fast enough; profile before reaching for this.
- If you do use it, isolate it — wrap the unsafe operations in a small, heavily commented, well-tested function rather than scattering `unsafe.Pointer` casts through business logic.
- Run `go vet` — it specifically checks for the common `unsafe.Pointer`/`uintptr` misuse patterns.
- Never persist a `uintptr` derived from a pointer across a function boundary or a potential GC safepoint and expect it to still be valid; the conversion back to `unsafe.Pointer` must happen in the same expression as the arithmetic.
- Treat any `unsafe.Sizeof`/`Offsetof` assumption about layout as something to re-verify (ideally via a test) on every Go version bump and every new target architecture, not just at write time.

`unsafe` is small — three or four functions and a special pointer type — but it's the seam where Go's safety guarantees end and the raw machine begins. Used narrowly and deliberately, it's how the standard library itself gets its performance in a handful of hot paths. Used casually, it reintroduces exactly the bug classes Go was designed to remove.

---
title: "Understanding `unsafe` in Go"
category: programming
tags: [go, unsafe, memory, internals, low-level]
---

# Understanding `unsafe` in Go

Go spends a lot of effort convincing you that pointers are tame — bounds-checked slices, no pointer arithmetic, a GC that moves things around behind your back (well, mostly doesn't, but reserves the right to). Then it ships a package literally called `unsafe` that lets you throw all of that away. If you've spent time in C with pointers or in reversing binaries and staring at memory layouts, `unsafe` is the point where Go stops pretending to be a managed language and lets you talk to the machine directly. This is a tour of what it actually does, why it exists, and where it bites people.

## Why `unsafe` exists at all

Go's type system and memory model are deliberately restrictive: no casting an `int32` pointer to a `float32` pointer, no pointer arithmetic, no peeking at a struct's raw byte layout. That's good for correctness but it means some things become literally impossible to express in "safe" Go — zero-copy conversions between `[]byte` and `string`, interfacing with C memory layouts via cgo, implementing sync primitives that need atomic access to arbitrary memory, or writing serialization code that wants to touch a struct's bytes directly instead of walking it field by field with reflection.

`unsafe` is the escape hatch. It's part of the language spec (sort of — it's a package but the compiler treats it specially), and using it means you're opting out of Go's memory-safety guarantees and its compatibility guarantees. The Go 1 promise explicitly does not cover `unsafe` — code using it can break on a compiler upgrade with no notice.

## The three core pieces

### `unsafe.Pointer`

This is the load-bearing type. It's a pointer type that can be converted to or from any other pointer type, and to/from `uintptr`. Regular Go pointers are strongly typed (`*int` isn't convertible to `*float64`); `unsafe.Pointer` is the hole in that wall.

```go
var i int64 = 42
var f *float64 = (*float64)(unsafe.Pointer(&i))
fmt.Println(*f) // reinterprets the int64 bits as a float64 — garbage, but valid Go
```

Four conversion rules govern legal use of `unsafe.Pointer` (from the package docs, and the compiler/vet will complain if you break them):

1. `*T1` → `unsafe.Pointer` → `*T2` — reinterpret one pointer type as another.
2. `unsafe.Pointer` → `uintptr` — get the numeric address.
3. `uintptr` → `unsafe.Pointer` — arithmetic on a pointer, then convert back **immediately, in the same expression**. This one has a sharp edge covered below.
4. Calling `syscall.Syscall` with a `uintptr` derived from `unsafe.Pointer`, for passing pointers to the kernel.

### `uintptr`

Just an integer large enough to hold an address. Critically, `uintptr` is **not a pointer** as far as the garbage collector is concerned — it's just a number. The GC doesn't scan `uintptr` values to find live objects. This is the source of the single most common `unsafe` bug:

```go
// WRONG — dangerous, classic use-after-free-by-GC pattern
p := uintptr(unsafe.Pointer(&someStruct))
// ... GC runs here, someStruct may move or be collected,
// because nothing marks it as reachable via p ...
q := unsafe.Pointer(p) // p may now point at freed/moved memory
```

Go's compiler and `go vet` will flag obvious versions of this, but it's still the number one way people get `unsafe` wrong. The fix is rule 3 above: do the `uintptr` arithmetic and the conversion back to `unsafe.Pointer` in a single expression, so the compiler can see the object is still needed and keep it alive/pinned through the operation:

```go
// Correct pattern: single expression, no GC-unsafe window
p := unsafe.Pointer(uintptr(unsafe.Pointer(&s)) + offset)
```

### `unsafe.Sizeof`, `unsafe.Alignof`, `unsafe.Offsetof`

These are compile-time constants that tell you a type's size, its alignment requirement, and a field's byte offset within a struct — exactly the information you'd get from `sizeof`/`offsetof` in C, and exactly what you need if you're hand-rolling a binary format or matching a C struct layout for cgo.

```go
type Header struct {
    Magic   uint32
    Version uint16
    Flags   uint16
}

fmt.Println(unsafe.Sizeof(Header{}))       // 8 (with padding rules applied)
fmt.Println(unsafe.Offsetof(Header{}.Flags)) // 6
```

Struct padding in Go follows the same alignment rules you'd expect from C, so `unsafe.Sizeof` isn't just "sum of field sizes" — it accounts for padding, which matters if you're building something that needs to match an external binary format exactly.

## Real, legitimate use cases

**Zero-copy string/byte conversion.** The standard idiom (used inside the Go runtime and standard library itself, e.g. in `strings.Builder`) avoids an allocation and copy when you know the underlying bytes won't be mutated:

```go
func bytesToString(b []byte) string {
    return unsafe.String(unsafe.SliceData(b), len(b))
}
```

(Go 1.20+ added `unsafe.String` and `unsafe.SliceData` specifically to make this pattern safer and clearer than the old three-word-header-reinterpretation trick people used to write by hand.)

**cgo interop.** When crossing the Go/C boundary, you're fundamentally dealing with raw memory that C allocated or expects a specific layout for — `unsafe.Pointer` is how `cgo`-generated code passes Go memory to C functions and back.

**Atomics on arbitrary fields.** Before generics-based atomic types, `sync/atomic` functions took `unsafe.Pointer` to operate on arbitrary memory locations for CAS operations on custom structures.

**High-performance serialization / low-GC-pressure code.** Some codecs reinterpret a `[]byte` buffer directly as a struct pointer to avoid per-field decoding overhead. This is legitimate but fragile — see portability concerns below.

## Where this gets dangerous

**It defeats the type system entirely.** The compiler will let you reinterpret a `*User` as a `*int64` and read whatever bytes happen to be there. There's no runtime check. If you get an offset or size wrong, you get memory corruption, not a panic — this is exactly the class of bug that "memory safe" language marketing implies you're immune to in Go, and `unsafe` is precisely where that immunity ends.

**Struct layout is not guaranteed across Go versions or platforms.** Field ordering, padding, and even whether fields get reordered by the compiler for better packing are implementation details. Code that assumes a specific memory layout via `unsafe.Offsetof` can silently break on a compiler upgrade or when cross-compiling to a different architecture with different alignment rules.

**GC interaction is subtle.** As shown above, treating a pointer as a plain integer (`uintptr`) for too long, or storing it in a way the GC can't trace, can lead to the object being collected or moved out from under you. This isn't a "maybe" — it's a real, exploitable class of bug in `unsafe`-heavy code, functionally similar to a use-after-free in C, just triggered by the collector instead of an explicit `free()`.

**It breaks the Go 1 compatibility promise.** Anthropic — sorry, Go itself — explicitly excludes `unsafe` usage from backward compatibility guarantees. Production code using it should be pinned to tested Go versions and re-verified on upgrade.

**Security surface.** From an offensive angle, `unsafe`-heavy Go code is one of the few places where classic memory-corruption primitives (type confusion, OOB read/write via miscalculated offsets, stale-pointer-via-uintptr) become relevant again in an otherwise memory-safe language. If you're auditing Go code, `grep -rn unsafe` is a genuinely productive first step — it's a strong prior for "here's where the interesting bugs are," the same way you'd zero in on `strcpy`/`memcpy` call sites in a C codebase.

## Practical guidance

- Default to not using it. Almost every ergonomic reason to reach for `unsafe` ("avoid a copy," "avoid reflection overhead") has a safe alternative that's fast enough; profile before reaching for this.
- If you do use it, isolate it — wrap the unsafe operations in a small, heavily commented, well-tested function rather than scattering `unsafe.Pointer` casts through business logic.
- Run `go vet` — it specifically checks for the common `unsafe.Pointer`/`uintptr` misuse patterns.
- Never persist a `uintptr` derived from a pointer across a function boundary or a potential GC safepoint and expect it to still be valid; the conversion back to `unsafe.Pointer` must happen in the same expression as the arithmetic.
- Treat any `unsafe.Sizeof`/`Offsetof` assumption about layout as something to re-verify (ideally via a test) on every Go version bump and every new target architecture, not just at write time.

`unsafe` is small — three or four functions and a special pointer type — but it's the seam where Go's safety guarantees end and the raw machine begins. Used narrowly and deliberately, it's how the standard library itself gets its performance in a handful of hot paths. Used casually, it reintroduces exactly the bug classes Go was designed to remove.
