#!/usr/bin/env python3
"""
File Recovery System - Operating Systems course project
=======================================================

A small simulated file system living inside a single "disk image" file, plus
three recovery techniques:

  1. Metadata recovery   - undelete using the leftover inode (pointers survive deletion)
  2. Recycle bin (trash) - soft delete that can be restored instantly
  3. File carving        - rebuild files from raw blocks using magic numbers
                           (JPEG, PNG, PDF) when metadata is gone

Disk layout (block size 512 bytes, 512 blocks = 256 KB)
-------------------------------------------------------
  block 0        superblock (magic number)
  blocks 1-16    inode table (32 inodes x 256 bytes)
  block 17       allocation bitmap (1 byte per block: 0 = free, 1 = used)
  blocks 18-511  data blocks

Usage
-----
  python file_recovery_system.py demo                 # run all scenarios + analysis
  python file_recovery_system.py format
  python file_recovery_system.py create a.txt --text "hello"
  python file_recovery_system.py create pic.jpg --from-file /path/to/pic.jpg
  python file_recovery_system.py ls
  python file_recovery_system.py delete a.txt --mode normal|trash|secure
  python file_recovery_system.py recover [names...]   # metadata-based undelete
  python file_recovery_system.py restore a.txt        # restore from trash
  python file_recovery_system.py wipe-meta            # simulate lost/corrupt inode table
  python file_recovery_system.py carve -o carved/     # signature-based carving
  python file_recovery_system.py map                  # visual block map
"""

import argparse
import hashlib
import os
import random
import shutil
import struct
import sys

# ----------------------------------------------------------------------------
# Disk geometry
# ----------------------------------------------------------------------------
BLOCK_SIZE = 512
TOTAL_BLOCKS = 512
MAX_FILES = 32
INODE_SIZE = 256
MAX_PTRS = 32                      # max blocks per file (16 KB)
INODE_BLOCKS = MAX_FILES * INODE_SIZE // BLOCK_SIZE   # 16
INODE_START = 1
BITMAP_BLOCK = INODE_START + INODE_BLOCKS             # 17
DATA_START = BITMAP_BLOCK + 1                         # 18
MAGIC = b"RFS1"

# Inode status values
FREE, ACTIVE, DELETED, TRASH = 0, 1, 2, 3
STATUS_NAME = {FREE: "FREE", ACTIVE: "ACTIVE", DELETED: "DELETED", TRASH: "TRASH"}

INODE_FMT = "<32sBIB" + "I" * MAX_PTRS   # name, status, size, nblocks, pointers

# File signatures used by the carver: (extension, header, footer)
SIGNATURES = [
    ("jpg", b"\xff\xd8\xff", b"\xff\xd9"),
    ("png", b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82"),
    ("pdf", b"%PDF", b"%%EOF"),
]


class FSError(Exception):
    pass


class Inode:
    def __init__(self, name="", status=FREE, size=0, blocks=None):
        self.name = name
        self.status = status
        self.size = size
        self.blocks = blocks or []

    def pack(self):
        ptrs = self.blocks + [0] * (MAX_PTRS - len(self.blocks))
        raw = struct.pack(INODE_FMT, self.name.encode()[:32], self.status,
                          self.size, len(self.blocks), *ptrs)
        return raw.ljust(INODE_SIZE, b"\x00")

    @staticmethod
    def unpack(raw):
        f = struct.unpack(INODE_FMT, raw[:struct.calcsize(INODE_FMT)])
        n = min(f[3], MAX_PTRS)
        return Inode(f[0].rstrip(b"\x00").decode(errors="replace"), f[1], f[2],
                     [b for b in f[4:4 + n]])


def ceil_div(a, b):
    return -(-a // b)


def ranges(blocks):
    """[18,19,22,23] -> '18-19,22-23' (makes fragmentation visible)."""
    if not blocks:
        return "-"
    out, start, prev = [], blocks[0], blocks[0]
    for b in blocks[1:]:
        if b == prev + 1:
            prev = b
            continue
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = b
    out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(out)


# ----------------------------------------------------------------------------
# Simulated disk + file system
# ----------------------------------------------------------------------------
class Disk:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            raise FSError(f"Disk image '{path}' not found. Run 'format' first.")
        with open(path, "rb") as f:
            self.data = bytearray(f.read())
        if self.data[:4] != MAGIC:
            raise FSError("Not a valid RFS disk image (bad superblock).")

    # ---- low level ---------------------------------------------------------
    @staticmethod
    def format(path):
        data = bytearray(BLOCK_SIZE * TOTAL_BLOCKS)
        data[:4] = MAGIC
        with open(path, "wb") as f:
            f.write(data)
        return Disk(path)

    def save(self):
        with open(self.path, "wb") as f:
            f.write(self.data)

    def read_block(self, b):
        return bytes(self.data[b * BLOCK_SIZE:(b + 1) * BLOCK_SIZE])

    def write_block(self, b, payload):
        self.data[b * BLOCK_SIZE:(b + 1) * BLOCK_SIZE] = payload.ljust(BLOCK_SIZE, b"\x00")

    def is_allocated(self, b):
        return self.data[BITMAP_BLOCK * BLOCK_SIZE + b] == 1

    def set_allocated(self, b, used):
        self.data[BITMAP_BLOCK * BLOCK_SIZE + b] = 1 if used else 0

    def read_inode(self, i):
        off = INODE_START * BLOCK_SIZE + i * INODE_SIZE
        return Inode.unpack(bytes(self.data[off:off + INODE_SIZE]))

    def write_inode(self, i, inode):
        off = INODE_START * BLOCK_SIZE + i * INODE_SIZE
        self.data[off:off + INODE_SIZE] = inode.pack()

    def inodes(self):
        return [(i, self.read_inode(i)) for i in range(MAX_FILES)]

    def find(self, name, statuses=(ACTIVE,)):
        for i, ino in self.inodes():
            if ino.status in statuses and ino.name == name:
                return i, ino
        return None, None

    def _alloc(self, n):
        """First-fit allocation. Blocks need not be contiguous (so files can fragment)."""
        got = [b for b in range(DATA_START, TOTAL_BLOCKS) if not self.is_allocated(b)][:n]
        if len(got) < n:
            raise FSError("Disk full.")
        return got

    # ---- file operations ---------------------------------------------------
    def create(self, name, content):
        if not name or len(name.encode()) > 31:
            raise FSError("File name must be 1-31 bytes.")
        if self.find(name, (ACTIVE, TRASH))[0] is not None:
            raise FSError(f"'{name}' already exists.")
        nblocks = max(1, ceil_div(len(content), BLOCK_SIZE))
        if nblocks > MAX_PTRS:
            raise FSError(f"File too large (max {MAX_PTRS * BLOCK_SIZE} bytes).")

        # Prefer never-used inode slots so deleted metadata survives as long as possible.
        slot = next((i for i, n in self.inodes() if n.status == FREE), None)
        if slot is None:
            slot = next((i for i, n in self.inodes() if n.status == DELETED), None)
        if slot is None:
            raise FSError("Inode table full.")

        blocks = self._alloc(nblocks)
        for k, b in enumerate(blocks):
            self.write_block(b, content[k * BLOCK_SIZE:(k + 1) * BLOCK_SIZE])
            self.set_allocated(b, True)
        self.write_inode(slot, Inode(name, ACTIVE, len(content), blocks))
        self.save()
        return slot

    def read(self, name):
        _, ino = self.find(name, (ACTIVE, TRASH))
        if ino is None:
            raise FSError(f"'{name}' not found.")
        raw = b"".join(self.read_block(b) for b in ino.blocks)
        return raw[:ino.size]

    def delete(self, name, mode="normal"):
        """
        normal : mark inode DELETED + free blocks. Data and pointers stay on disk (recoverable).
        trash  : move to recycle bin. Blocks stay allocated, so nothing can overwrite them.
        secure : zero the data blocks and wipe the inode (NOT recoverable).
        """
        i, ino = self.find(name, (ACTIVE,))
        if ino is None:
            raise FSError(f"'{name}' not found.")
        if mode == "normal":
            ino.status = DELETED
            for b in ino.blocks:
                self.set_allocated(b, False)
            self.write_inode(i, ino)
        elif mode == "trash":
            ino.status = TRASH
            self.write_inode(i, ino)
        elif mode == "secure":
            for b in ino.blocks:
                self.write_block(b, b"")
                self.set_allocated(b, False)
            self.write_inode(i, Inode())
        else:
            raise FSError("mode must be normal, trash or secure")
        self.save()

    def restore(self, name):
        i, ino = self.find(name, (TRASH,))
        if ino is None:
            raise FSError(f"'{name}' is not in the recycle bin.")
        ino.status = ACTIVE
        self.write_inode(i, ino)
        self.save()

    def empty_trash(self):
        n = 0
        for i, ino in self.inodes():
            if ino.status == TRASH:
                ino.status = DELETED
                for b in ino.blocks:
                    self.set_allocated(b, False)
                self.write_inode(i, ino)
                n += 1
        self.save()
        return n

    def wipe_metadata(self):
        """Simulate a quick-format / corrupted inode table: data blocks remain, pointers vanish."""
        start = INODE_START * BLOCK_SIZE
        self.data[start:start + INODE_BLOCKS * BLOCK_SIZE] = bytes(INODE_BLOCKS * BLOCK_SIZE)
        for b in range(DATA_START, TOTAL_BLOCKS):
            self.set_allocated(b, False)
        self.save()

    # ---- recovery technique 1: metadata based -------------------------------
    def recover_deleted(self, partial_dir=None, names=None):
        """
        Scan the inode table for DELETED entries. If every block of the file is still
        unallocated (nobody reused it), re-allocate the blocks and mark the inode ACTIVE.
        Pass names=[...] to recover only specific files. This matters when two stale
        inodes point at the same blocks (the blocks were reused): only one of them is
        really the owner of the data, and the user must choose.
        """
        results = []
        for i, ino in self.inodes():
            if ino.status != DELETED:
                continue
            if names and ino.name not in names:
                continue
            overwritten = [b for b in ino.blocks if self.is_allocated(b)]
            total = len(ino.blocks)
            if not overwritten:
                name = ino.name
                if self.find(name, (ACTIVE, TRASH))[0] is not None:
                    name += ".recovered"
                    ino.name = name
                ino.status = ACTIVE
                for b in ino.blocks:
                    self.set_allocated(b, True)
                self.write_inode(i, ino)
                results.append({"name": name, "state": "recovered",
                                "intact": total, "total": total})
            else:
                intact = total - len(overwritten)
                if partial_dir and intact:
                    os.makedirs(partial_dir, exist_ok=True)
                    with open(os.path.join(partial_dir, ino.name + ".partial"), "wb") as f:
                        for b in ino.blocks:
                            f.write(self.read_block(b) if b not in overwritten
                                    else b"\x00" * BLOCK_SIZE)
                results.append({"name": ino.name, "state": "partial" if intact else "lost",
                                "intact": intact, "total": total})
        self.save()
        return results

    # ---- recovery technique 2: file carving ---------------------------------
    def carve(self, outdir):
        """
        Ignore the inode table completely. Walk the unallocated blocks, look for known
        file headers (magic numbers) at block boundaries and read until the matching
        footer. Only works when the file is stored in CONTIGUOUS blocks.
        """
        os.makedirs(outdir, exist_ok=True)
        found, count, b = [], 0, DATA_START
        while b < TOTAL_BLOCKS:
            if self.is_allocated(b):
                b += 1
                continue
            blk = self.read_block(b)
            sig = next((s for s in SIGNATURES if blk.startswith(s[1])), None)
            if sig is None:
                b += 1
                continue
            ext, _, footer = sig
            end, buf = b, b""
            while end < TOTAL_BLOCKS and not self.is_allocated(end) and end - b < MAX_PTRS:
                buf += self.read_block(end)
                end += 1
            idx = buf.find(footer)
            if idx == -1:
                found.append({"file": None, "ext": ext, "start": b, "state": "incomplete"})
                b += 1
                continue
            data = buf[:idx + len(footer)]
            count += 1
            fname = os.path.join(outdir, f"carved_{count:03d}.{ext}")
            with open(fname, "wb") as f:
                f.write(data)
            found.append({"file": fname, "ext": ext, "start": b, "state": "carved",
                          "size": len(data), "sha": hashlib.sha256(data).hexdigest()})
            b += ceil_div(len(data), BLOCK_SIZE)
        return found

    # ---- display -------------------------------------------------------------
    def listing(self):
        rows = []
        for i, ino in self.inodes():
            if ino.status == FREE:
                continue
            note = ""
            if ino.status == DELETED:
                bad = sum(1 for b in ino.blocks if self.is_allocated(b))
                note = "data intact" if bad == 0 else f"{bad}/{len(ino.blocks)} blocks overwritten"
            rows.append((i, ino.name, STATUS_NAME[ino.status], ino.size, ranges(ino.blocks), note))
        return rows

    def block_map(self):
        """# used | x free but holds stale data | . empty | M metadata"""
        lines = []
        for row in range(0, TOTAL_BLOCKS, 64):
            cells = []
            for b in range(row, min(row + 64, TOTAL_BLOCKS)):
                if b < DATA_START:
                    cells.append("M")
                elif self.is_allocated(b):
                    cells.append("#")
                elif any(self.read_block(b)):
                    cells.append("x")
                else:
                    cells.append(".")
            lines.append(f"{row:4d} " + "".join(cells))
        return "\n".join(lines)


# ----------------------------------------------------------------------------
# Synthetic test files (so the demo needs no external data)
# ----------------------------------------------------------------------------
def make_jpeg(size, seed):
    rnd = random.Random(seed)
    body = bytes(0 if x == 0xFF else x for x in rnd.randbytes(size - 6))
    return b"\xff\xd8\xff\xe0" + body + b"\xff\xd9"


def make_png(size, seed):
    rnd = random.Random(seed)
    body = bytes(rnd.randrange(1, 256) for _ in range(size - 16))
    return b"\x89PNG\r\n\x1a\n" + body + b"IEND\xaeB`\x82"


def make_pdf(size, seed):
    rnd = random.Random(seed)
    letters = b"abcdefghijklmnopqrstuvwxyz \n"
    body = bytes(rnd.choice(letters) for _ in range(size - 15))
    return b"%PDF-1.4\n" + body + b"\n%%EOF"


def make_text(size, seed):
    rnd = random.Random(seed)
    return bytes(rnd.choice(b"abcdefghijklmnopqrstuvwxyz \n") for _ in range(size))


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ----------------------------------------------------------------------------
# Demo: five scenarios + success-rate analysis
# ----------------------------------------------------------------------------
def demo(workdir="demo_output"):
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    originals = {
        "photo1.jpg": make_jpeg(3000, 1),
        "photo2.jpg": make_jpeg(2000, 2),
        "report.pdf": make_pdf(4096, 3),
        "logo.png": make_png(2048, 4),
        "notes.txt": make_text(1000, 5),
    }
    summary = []

    def fresh(tag):
        d = Disk.format(os.path.join(workdir, f"{tag}.img"))
        for n, c in originals.items():
            d.create(n, c)
        return d

    def banner(t):
        print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)

    # --- Scenario 1: simple delete, metadata recovery --------------------------
    banner("Scenario 1: normal delete -> metadata-based recovery")
    d = fresh("s1")
    d.delete("photo1.jpg")
    d.delete("notes.txt")
    print("After delete:")
    for r in d.listing():
        print("  ", r)
    res = d.recover_deleted()
    ok = sum(1 for r in res if r["state"] == "recovered"
             and sha(d.read(r["name"])) == sha(originals[r["name"]]))
    print(f"Recovered {ok}/2 files with identical content (hash verified)")
    summary.append(("1. Normal delete, metadata recovery", 2, ok))

    # --- Scenario 2: delete then overwrite ---------------------------------------
    banner("Scenario 2: delete, then new file overwrites the freed blocks")
    d = fresh("s2")
    d.delete("report.pdf")
    d.create("new_data.bin", make_text(1500, 99))     # reuses the first freed blocks
    print(d.block_map())
    res = d.recover_deleted(partial_dir=os.path.join(workdir, "s2_partial"))
    for r in res:
        print(f"  {r['name']}: {r['state']} ({r['intact']}/{r['total']} blocks intact)")
    ok = sum(1 for r in res if r["state"] == "recovered")
    print("Limitation shown: overwritten blocks cannot be brought back.")
    summary.append(("2. Delete then overwrite", 1, ok))

    # --- Scenario 3: recycle bin ---------------------------------------------------
    banner("Scenario 3: recycle bin (trash) -> restore")
    d = fresh("s3")
    d.delete("logo.png", mode="trash")
    d.create("other.bin", make_text(2500, 7))          # cannot reuse trashed blocks
    d.restore("logo.png")
    ok = int(sha(d.read("logo.png")) == sha(originals["logo.png"]))
    print(f"logo.png restored from trash, content identical: {bool(ok)}")
    summary.append(("3. Recycle bin restore", 1, ok))

    # --- Scenario 4: lost metadata -> file carving -----------------------------------
    banner("Scenario 4: inode table lost -> file carving by magic number")
    d = fresh("s4")
    d.wipe_metadata()
    print("Inode table wiped. 'ls' now shows:", d.listing() or "nothing")
    found = d.carve(os.path.join(workdir, "s4_carved"))
    want = {sha(c): n for n, c in originals.items()}
    ok = 0
    for f in found:
        if f["state"] == "carved":
            match = want.get(f["sha"])
            ok += bool(match)
            print(f"  block {f['start']:3d}: {f['file']} ({f['size']} bytes) -> "
                  f"{'matches ' + match if match else 'hash mismatch'}")
        else:
            print(f"  block {f['start']:3d}: {f['ext']} header found, footer NOT found (incomplete)")
    print("notes.txt has no magic number, so carving cannot find it (limitation).")
    summary.append(("4. Carving, metadata lost (5 files)", len(originals), ok))

    # --- Scenario 5: fragmented file ---------------------------------------------------
    banner("Scenario 5: fragmented file -> carving fails, metadata recovery works")
    d = Disk.format(os.path.join(workdir, "s5.img"))
    d.create("a.txt", make_text(1000, 11))
    d.create("b.txt", make_text(1000, 12))
    d.create("c.txt", make_text(1000, 13))
    d.delete("a.txt")
    d.delete("c.txt")
    frag = make_jpeg(3000, 14)
    d.create("fragmented.jpg", frag)                   # lands in holes left by a.txt and c.txt
    d.delete("fragmented.jpg")
    i, ino = d.find("fragmented.jpg", (DELETED,))
    print(f"fragmented.jpg blocks: {ranges(ino.blocks)}  (non-contiguous)")
    found = d.carve(os.path.join(workdir, "s5_carved"))
    carved_ok = sum(1 for f in found if f["state"] == "carved" and f["sha"] == sha(frag))
    print(f"Carving recovered the intact file: {bool(carved_ok)}")
    res = d.recover_deleted(names=["fragmented.jpg"])
    meta_ok = int(any(r["name"] == "fragmented.jpg" and r["state"] == "recovered"
                      and sha(d.read("fragmented.jpg")) == sha(frag) for r in res))
    print(f"Metadata recovery recovered the intact file: {bool(meta_ok)}")
    summary.append(("5a. Fragmented file, carving", 1, carved_ok))
    summary.append(("5b. Fragmented file, metadata recovery", 1, meta_ok))

    # --- Analysis -------------------------------------------------------------------------
    banner("Recovery success-rate analysis")
    print(f"{'Scenario':45} {'Tried':>6} {'OK':>4} {'Rate':>6}")
    for name, tried, ok in summary:
        print(f"{name:45} {tried:6d} {ok:4d} {100 * ok // tried:5d}%")
    print("\nConclusions:")
    print(" - Deleted data survives until its blocks are reused, so recover early.")
    print(" - Metadata recovery handles fragmented files; carving needs contiguous blocks.")
    print(" - Carving works without metadata but only for types with known signatures.")
    print(" - Recycle bin gives 100% recovery because blocks stay allocated.")
    print(" - Secure delete (zeroing) makes recovery impossible.")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="File Recovery System (simulated file system)")
    p.add_argument("-i", "--image", default="disk.img", help="disk image path")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("format", help="create/format an empty disk image")
    c = sub.add_parser("create", help="create a file")
    c.add_argument("name")
    c.add_argument("--from-file")
    c.add_argument("--text")
    sub.add_parser("ls", help="list files, including deleted ones")
    r = sub.add_parser("read", help="print a file")
    r.add_argument("name")
    d = sub.add_parser("delete", help="delete a file")
    d.add_argument("name")
    d.add_argument("--mode", choices=["normal", "trash", "secure"], default="normal")
    rs = sub.add_parser("restore", help="restore a file from the recycle bin")
    rs.add_argument("name")
    sub.add_parser("empty-trash", help="empty the recycle bin")
    rc = sub.add_parser("recover", help="metadata-based undelete (optionally only the named files)")
    rc.add_argument("names", nargs="*")
    rc.add_argument("--partial-dir", default=None)
    sub.add_parser("wipe-meta", help="simulate loss of the inode table")
    cv = sub.add_parser("carve", help="signature-based file carving")
    cv.add_argument("-o", "--outdir", default="carved")
    sub.add_parser("map", help="show block usage map")
    dm = sub.add_parser("demo", help="run all demo scenarios")
    dm.add_argument("--workdir", default="demo_output")

    a = p.parse_args(argv)
    try:
        if a.cmd == "demo":
            demo(a.workdir)
        elif a.cmd == "format":
            Disk.format(a.image)
            print(f"Formatted {a.image}: {TOTAL_BLOCKS} blocks x {BLOCK_SIZE} bytes")
        else:
            disk = Disk(a.image)
            if a.cmd == "create":
                if a.from_file:
                    with open(a.from_file, "rb") as f:
                        content = f.read()
                else:
                    content = (a.text or "").encode()
                disk.create(a.name, content)
                print(f"Created {a.name} ({len(content)} bytes)")
            elif a.cmd == "ls":
                print(f"{'ino':>3} {'name':20} {'status':8} {'size':>6}  {'blocks':20} note")
                for row in disk.listing():
                    print(f"{row[0]:3d} {row[1]:20} {row[2]:8} {row[3]:6d}  {row[4]:20} {row[5]}")
            elif a.cmd == "read":
                sys.stdout.buffer.write(disk.read(a.name) + b"\n")
            elif a.cmd == "delete":
                disk.delete(a.name, a.mode)
                print(f"Deleted {a.name} ({a.mode})")
            elif a.cmd == "restore":
                disk.restore(a.name)
                print(f"Restored {a.name}")
            elif a.cmd == "empty-trash":
                print(f"Emptied trash ({disk.empty_trash()} files now deleted)")
            elif a.cmd == "recover":
                results = disk.recover_deleted(a.partial_dir, a.names or None)
                if not results:
                    print("No deleted files found.")
                for x in results:
                    print(f"{x['name']}: {x['state']} ({x['intact']}/{x['total']} blocks intact)")
            elif a.cmd == "wipe-meta":
                disk.wipe_metadata()
                print("Inode table wiped.")
            elif a.cmd == "carve":
                results = disk.carve(a.outdir)
                if not results:
                    print("Nothing found.")
                for x in results:
                    if x["state"] == "carved":
                        print(f"block {x['start']}: {x['file']} ({x['size']} bytes)")
                    else:
                        print(f"block {x['start']}: {x['ext']} header, no footer (incomplete)")
            elif a.cmd == "map":
                print("M metadata   # used   x free but holds old data   . empty")
                print(disk.block_map())
    except FSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
