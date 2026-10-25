#!/usr/bin/env python3
"""ARM64EC: run code from executable section views the emulator was not told about (SOSOalqawe12 fork).

ONE PIECE Bounty Rush's launcher stub (ucldr_..._loader_x64.exe, log 2026-10-25 19:39) remaps
its own image: it maps a copy of a section, unmaps the original image and maps views of that
(non-image) section back at the image's addresses, section #6 with PAGE_EXECUTE_READWRITE.
Wine's ntdll grants the execute permission (the view gets its JIT-pool copy), but on ARM64EC
FEX only learns about executable memory from image maps (NotifyMapViewOfSection with an image
name) and from allocation/protection notifications; a plain section view arrives with no
ArbitraryUserPointer and is skipped ("[map-notify] SKIP"). The first jump into it was then a
miss in the executable-range table ("[iOS-xquery] MISS ... addr=0x126ac2530"), the decoder
emitted a NoExec block, its thunk read through a null pointer, and that host fault was
redelivered 2000 times until the process was terminated. On Windows the same code runs.

The patch gives the ARM64EC tracker the second look the WOW64 tracker already has for DEP-off
pages: on a miss, VirtualQuery the address, and when it lies in a committed section view
(MEM_MAPPED or MEM_IMAGE) whose protection includes execute (and no guard or no-access bit),
record that region as executable (and as RWX, so the SMC write-trap covers it, when it is
writable), then answer from the table. Only what Windows itself reports as executable is
adopted, private memory is left to the allocation notifications as before, and a wild branch
still faults. Nothing is invalidated: nothing can have been compiled for a range the decoder is
only now asking about. VirtualQuery and the log line run outside IntervalsLock (a log line may
allocate, and an allocation comes back through the memory notification).

MADEIRA_FEX_EXEC_VIEWS=0 at launch turns it off. Idempotent.
Usage: patch-fex-ios-exec-views.py FEX/Source/Windows/Common
"""
import os
import sys

d = sys.argv[1]
MARKER = "madeira-bcd: exec-views"


def patch(name, edits):
    path = os.path.join(d, name)
    src = open(path).read()
    if MARKER in src:
        print(f"{name}: already patched")
        return
    for old, new in edits:
        if src.count(old) != 1:
            sys.exit(f"patch-fex-ios-exec-views: anchor not found once in {name}:\n{old}")
        src = src.replace(old, new)
    open(path, "w").write(src)
    print(f"{name}: patched")


patch("InvalidationTracker.h", [
    ("""private:
#if !defined(ARCHITECTURE_arm64ec)
  // Lazy DEP-off promotion under a guest window:""",
     """private:
#if defined(ARCHITECTURE_arm64ec)
  /* madeira-bcd: exec-views (tools/patch-fex-ios-exec-views.py). On a miss, a committed section view
   * whose protection includes execute is recorded as executable. True when it was. Takes IntervalsLock
   * itself: the caller must not hold it. */
  bool AdoptExecutableView(uint64_t Address);
#endif
#if !defined(ARCHITECTURE_arm64ec)
  // Lazy DEP-off promotion under a guest window:"""),
])

patch("InvalidationTracker.cpp", [
    ("""#else
FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {
  std::shared_lock Lock(IntervalsLock);
#endif
  const auto XResult = XIntervals.Query(Address);
  if (!XResult.Enclosed) {
    return {};
  }""",
     """#else
FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {
  std::shared_lock Lock(IntervalsLock);
#endif
  const auto XResult = XIntervals.Query(Address);
  if (!XResult.Enclosed) {
#if defined(ARCHITECTURE_arm64ec)
    /* madeira-bcd: exec-views (tools/patch-fex-ios-exec-views.py): a section view mapped executable is
     * never notified; adopt it on the first execute attempt, then answer from the table. */
    Lock.unlock();
    if (AdoptExecutableView(Address)) {
      std::shared_lock Again(IntervalsLock);
      const auto Adopted = XIntervals.Query(Address);
      if (Adopted.Enclosed) {
        const auto Writable = RWXIntervals.Query(Address);
        if (Writable.Enclosed) {
          return {Writable.Interval.Offset, Writable.Interval.End - Writable.Interval.Offset, true};
        }
        return {Adopted.Interval.Offset, Adopted.Interval.End - Adopted.Interval.Offset, false};
      }
    }
#endif
    return {};
  }"""),
    ("""void InvalidationTracker::DetectMonoBackpatcherBlock(FEXCore::Core::InternalThreadState* Thread, uint64_t HostPc) {""",
     """#if defined(ARCHITECTURE_arm64ec)
bool InvalidationTracker::AdoptExecutableView(uint64_t Address) {
  static const bool Enabled = [] {
    const char* Opt = getenv("MADEIRA_FEX_EXEC_VIEWS");
    return !(Opt && Opt[0] == '0');
  }();
  if (!Enabled || IntervalsLock.HeldByThisThread()) {
    return false;
  }
  MEMORY_BASIC_INFORMATION Info;
  if (!VirtualQuery(reinterpret_cast<LPCVOID>(Address), &Info, sizeof(Info))) {
    return false;
  }
  // Only a committed view of a section that Windows itself reports executable. Private memory is
  // notified through NtAllocateVirtualMemory/NtProtectVirtualMemory and stays with that path.
  if (Info.State != MEM_COMMIT || (Info.Type != MEM_MAPPED && Info.Type != MEM_IMAGE) ||
      (Info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) || !ProtHasExec(Info.Protect)) {
    return false;
  }
  const auto BaseAddress = reinterpret_cast<uint64_t>(Info.BaseAddress);
  const auto AlignedBase = BaseAddress & FEXCore::Utils::FEX_PAGE_MASK;
  const auto AlignedSize = (BaseAddress - AlignedBase + Info.RegionSize + FEXCore::Utils::FEX_PAGE_SIZE - 1) & FEXCore::Utils::FEX_PAGE_MASK;
  FEXCore::IntervalList<uint64_t>::Interval ViewInterval {AlignedBase, AlignedBase + AlignedSize};
  if (Address < ViewInterval.Offset || Address >= ViewInterval.End) {
    return false;
  }
  {
    std::unique_lock Lock(IntervalsLock);
    XIntervals.Insert(ViewInterval);
    if (ProtIsWritable(Info.Protect)) {
      RWXIntervals.Insert(ViewInterval);
    }
  }
  static std::atomic<uint32_t> AdoptLogCount {0};
  if (AdoptLogCount.fetch_add(1, std::memory_order_relaxed) < 32) {
    LogMan::Msg::EFmt("[exec-view] {} adopting an executable section view {:#x}+{:#x} (prot={:#x} type={:#x}) on an execute attempt",
                      MARKER_TEXT, AlignedBase, AlignedSize, Info.Protect, Info.Type);
  }
  return true;
}
#endif

void InvalidationTracker::DetectMonoBackpatcherBlock(FEXCore::Core::InternalThreadState* Thread, uint64_t HostPc) {""".replace(
        "MARKER_TEXT", '"' + MARKER + '"')),
])
