# Project Proposal: File Recovery System

**Course:** Operating Systems
**Institution:** Bangladesh University of Business and Technology (BUBT)
**Project Title:** Design and Implementation of a File Recovery System

---

## 1. Introduction

### 1.1 Background
A file system is the part of an operating system that decides how data is stored, named, and retrieved on a storage device. When a user deletes a file, the operating system usually does not erase the actual data. It only removes or marks the file's metadata (directory entry, inode, or FAT entry) as free, and the data blocks stay on disk until they are overwritten. This behavior makes file recovery possible.

### 1.2 Problem Statement
Accidental deletion, formatting, and file system corruption cause data loss for students, professionals, and organizations. Most users do not understand how deletion works internally and cannot recover lost files without commercial tools. There is a need for a simple, educational system that shows how deleted files can be located and restored.

### 1.3 Objectives
- To simulate a simple file system with disk blocks and metadata structures.
- To demonstrate what happens internally when a file is created and deleted.
- To implement recovery of deleted files using leftover metadata.
- To implement signature-based file carving to recover files when metadata is lost.
- To analyze the success rate and limitations of recovery techniques.

### 1.4 Scope
**Included:**
- A simulated file system (FAT or inode-based) on a virtual disk image.
- File create, delete, and list operations.
- Metadata-based recovery, recycle bin mechanism, and file carving (e.g., JPEG, PNG, PDF signatures).
- Recovery result analysis.

**Excluded:**
- Recovery from physically damaged hardware.
- Recovery of encrypted or securely wiped (overwritten) data.
- Support for all commercial file systems (NTFS, ext4, etc.) in full detail.

### 1.5 Significance
The project gives practical understanding of file system internals, disk management, and data recovery, which are core Operating System concepts. It also builds a base for further study in digital forensics and data security.

### 1.6 Tools and Technologies
| Item | Choice |
|---|---|
| Programming language | Python (or C) |
| Environment | Linux / Windows |
| Disk image creation | `dd` command or a Python-generated binary file |
| Reference tools for comparison | TestDisk, Foremost |
| Version control | Git / GitHub |

---

## 2. Project Development Steps

### Step 1: Requirement Analysis and Study
- Study file system concepts: blocks, inodes, FAT, directory structure, free-space management.
- Study how deletion works and why data remains on disk.
- Study file signatures (magic numbers), e.g., JPEG starts with `FF D8 FF`.
- Review existing tools (TestDisk, Foremost) to understand common approaches.

### Step 2: System Design
- Define the virtual disk layout: boot/superblock, metadata area (FAT or inode table), free-space bitmap, data blocks.
- Define the metadata structure: file name, size, starting block, block list, status (active/deleted).
- Design the module architecture: disk manager, file system manager, recovery engine, analyzer, user interface.
- Prepare diagrams: system architecture, flowcharts for create/delete/recover, and data structure layout.

### Step 3: Virtual Disk and File System Implementation
- Create a fixed-size virtual disk file divided into blocks.
- Implement block allocation and free-space tracking.
- Implement file operations: create, write, read, delete, list.

### Step 4: Delete Mechanism and Recycle Bin
- Implement normal deletion (mark metadata as deleted, free the blocks, keep the data).
- Implement a recycle bin so deleted files can be restored with their original information.
- Implement permanent deletion (data overwritten) for testing and comparison.

### Step 5: Metadata-Based Recovery
- Scan the metadata area for entries marked as deleted.
- Check whether the file's data blocks are still unallocated and not overwritten.
- Restore the metadata entry and re-allocate the blocks to bring the file back.

### Step 6: File Carving Module
- Scan raw disk blocks without relying on metadata.
- Detect file headers and footers using signatures for common types (JPEG, PNG, PDF, ZIP).
- Extract the data between header and footer and save it as a recovered file.

### Step 7: Fragmented File Handling (Advanced)
- Create test cases where files are stored in non-contiguous blocks.
- Implement a simple method to reconnect fragments using block order and file-type structure.
- Document where this method works and where it fails.

### Step 8: Journaling / Snapshot (Optional Enhancement)
- Implement a basic journal that logs file operations before they are applied.
- Use the journal or periodic snapshots to restore files and to recover from inconsistent states.

### Step 9: User Interface
- Build a command-line interface (CLI) with commands such as `create`, `delete`, `list`, `recover`, `carve`.
- Optionally build a simple GUI to show disk block usage and recovery results.

### Step 10: Testing and Evaluation
- Prepare test cases: delete one file, delete many files, delete then overwrite, fragmented files, different file types.
- Measure the recovery success rate for each method (metadata recovery vs. carving).
- Compare results with TestDisk/Foremost on the same disk image.
- Record limitations, for example overwritten blocks cannot be recovered.

### Step 11: Documentation and Presentation
- Write the final report with design, implementation details, and result analysis.
- Prepare the presentation slides and a live demo.
- Upload the source code and documentation to GitHub.

---

## 3. Expected Outcome
A working File Recovery System that simulates a file system, supports file deletion, and recovers deleted files using metadata and file carving, along with an analysis of success rates and limitations.

## 4. Proposed Timeline

| Phase | Steps | Duration |
|---|---|---|
| Research and design | 1 to 2 | Week 1 to 2 |
| Core implementation | 3 to 5 | Week 3 to 5 |
| Advanced features | 6 to 8 | Week 6 to 8 |
| Interface and testing | 9 to 10 | Week 9 to 10 |
| Documentation and presentation | 11 | Week 11 to 12 |

*Adjust the duration according to the course deadline.*

## 5. References
- A. Silberschatz, P. B. Galvin, G. Gagne, *Operating System Concepts*.
- A. S. Tanenbaum, *Modern Operating Systems*.
- B. Carrier, *File System Forensic Analysis*.
- TestDisk and Foremost official documentation.
