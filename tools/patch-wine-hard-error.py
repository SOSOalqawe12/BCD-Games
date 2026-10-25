#!/usr/bin/env python3
"""Log the text of a game's fatal-error box (SOSOalqawe12 fork).

A program's MessageBox with MB_SERVICE_NOTIFICATION, and a game's own fatal
error report (GTA V Enhanced: exit 0xdeadc0de right after it), reaches
NtRaiseHardError (wine/dlls/ntdll/unix/system.c) with
STATUS_SERVICE_NOTIFICATION and two UNICODE_STRING parameters, the text and
the caption. Wine stubs the call, so the message was never shown or logged and
only the exit code remained. The patch prints both as one
"[hard-error] caption=... text=..." line; the call still returns
STATUS_NOT_IMPLEMENTED, so the program behaves as before.

Patches system.c in place; the submodule pin and the cache keys (wine revision)
do not change. Idempotent; exits 1 with a message if the anchor is missing.
Usage: patch-wine-hard-error.py wine/dlls/ntdll/unix/system.c
"""
import sys

MARKER = "SOSOalqawe12 fork: hard-error text"
ANCHOR = ('    FIXME( "%#08x %u %#x %p %u %p: stub\\n", status, count, params_mask, params, option, response );\n'
          '    return STATUS_NOT_IMPLEMENTED;\n')
NEW = ('    FIXME( "%#08x %u %#x %p %u %p: stub\\n", status, count, params_mask, params, option, response );\n'
       '    /* ' + MARKER + ' (tools/patch-wine-hard-error.py): the box is never shown;\n'
       '     * print its caption and text so the log says why the program quit. */\n'
       '    if ((status & ~0x10000000) == STATUS_SERVICE_NOTIFICATION && count >= 2 && (params_mask & 3) == 3 && params)\n'
       '    {\n'
       '        const UNICODE_STRING *text = params[0], *caption = params[1];\n'
       '        ERR( "[hard-error] caption=%s text=%s\\n", caption ? debugstr_us( caption ) : "(null)",\n'
       '             text ? debugstr_us( text ) : "(null)" );\n'
       '    }\n'
       '    return STATUS_NOT_IMPLEMENTED;\n')


def patch(src):
    if MARKER in src:
        return src, "already patched"
    if src.count(ANCHOR) != 1:
        raise ValueError("the NtRaiseHardError stub was found %d times (expected 1); system.c changed "
                         "upstream, update the patch" % src.count(ANCHOR))
    return src.replace(ANCHOR, NEW), "NtRaiseHardError logs the box's caption and text"


def main(path):
    src = open(path).read()
    try:
        out, what = patch(src)
    except ValueError as e:
        sys.stderr.write("patch-wine-hard-error: %s\n" % e)
        return 1
    if out != src:
        open(path, "w").write(out)
    print("patch-wine-hard-error:", what, "in", path)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__.strip().splitlines()[-1] + "\n")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
