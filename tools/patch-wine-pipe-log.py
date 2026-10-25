#!/usr/bin/env python3
"""Log every named pipe a program creates (SOSOalqawe12 fork).

GTA V Enhanced (log 2026-10-25 20:19) loads, then waits on its loading screen for a Social Club
profile: "Attempting to find profile from Rockstar Games Launcher (result: failed)". Before that the
Launcher's Social Club layer could not open \\??\\pipe\\chrome.rgsc_launcher_0 (status c0000034,
"failed to create pipe: 2", "OnChannelError() - rgsc_launcher_0"). Which program creates that pipe,
and when, is not in the log: Wine logs only failed opens. NtCreateNamedPipeFile
(wine/dlls/ntdll/unix/file.c) now prints one "[pipe-create] <name> status=<status>" line per call,
the first 400 of a session, so the order of the server side and the client's open shows.

Patches file.c in place; idempotent; exits 1 if the anchor moved.
Usage: patch-wine-pipe-log.py wine/dlls/ntdll/unix/file.c
"""
import sys

MARKER = "SOSOalqawe12 fork: pipe-create log"
ANCHOR = "    SERVER_END_REQ;\n\n    free( objattr );\n    return io->Status = status;\n}"
NEW = ("    SERVER_END_REQ;\n\n"
       "    {\n"
       "        /* " + MARKER + " (tools/patch-wine-pipe-log.py): who serves a pipe, and when. */\n"
       "        static LONG logged;\n"
       "        if (InterlockedIncrement( &logged ) <= 400)\n"
       "            ERR( \"[pipe-create] %s status=%08x\\n\", debugstr_us( attr->ObjectName ), (unsigned int)status );\n"
       "    }\n"
       "    free( objattr );\n    return io->Status = status;\n}")


def main(path):
    src = open(path).read()
    if MARKER in src:
        print("patch-wine-pipe-log: already patched in", path)
        return 0
    start = src.find("NTSTATUS WINAPI NtCreateNamedPipeFile")
    at = src.find(ANCHOR, start)
    if start < 0 or at < 0 or at - start > 4000:
        sys.stderr.write("patch-wine-pipe-log: NtCreateNamedPipeFile's end was not found; file.c changed upstream\n")
        return 1
    open(path, "w").write(src[:at] + NEW + src[at + len(ANCHOR):])
    print("patch-wine-pipe-log: NtCreateNamedPipeFile logs each pipe in", path)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__.strip().splitlines()[-1] + "\n")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
