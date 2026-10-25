#!/usr/bin/env python3
"""Log why the wineserver refuses a cross-thread GetThreadContext (SOSOalqawe12 fork).

Every GetThreadContext on another thread in the ONE PIECE Bounty Rush logs (builds 21-22) came back
STATUS_ACCESS_DENIED ("[ec-getctx] ml715 ... status=c0000022") while the server had captured the
target's context ("[srv-getctx]") and SuspendThread on it had worked. Steam's crash handler (an
assert about SDL3.dll) then suspends threads, reads their contexts, gets nothing, and the client
in BCD Dock dies on a call through a null pointer. On Windows a thread handle opened for it reads
the context. Three places in the get_thread_context request can answer ACCESS_DENIED: the handle's
granted access lacks THREAD_GET_CONTEXT, the target's context was torn down while pending
(cleanup_thread), or an error left by the capture. The request now prints one
"[srv-getctx-err]" line naming which, with the handle's granted access, the first 64 of a session.

Patches wine/server/thread.c in place; idempotent; exits 1 if an anchor moved.
Usage: patch-wineserver-getctx-log.py wine/server/thread.c
"""
import sys

MARKER = "SOSOalqawe12 fork: getctx-err log"
EDITS = [
    ("        if (!(thread = get_thread_from_handle( req->handle, THREAD_GET_CONTEXT ))) return;\n",
     "        if (!(thread = get_thread_from_handle( req->handle, THREAD_GET_CONTEXT )))\n"
     "        {\n"
     "            /* " + MARKER + " (tools/patch-wineserver-getctx-log.py) */\n"
     "            static int logged;\n"
     "            if (logged++ < 64)\n"
     "                fprintf( stderr, \"[srv-getctx-err] handle=%04x granted=%08x status=%08x: the handle lacks \"\n"
     "                         \"THREAD_GET_CONTEXT or is not a thread\\n\", req->handle,\n"
     "                         get_handle_access( current->process, req->handle ), get_error() );\n"
     "            return;\n"
     "        }\n"),
    ("        release_object( thread );\n        if (!thread_context) return;\n",
     "        if (!thread_context && get_error())\n"
     "        {\n"
     "            static int logged_capture;\n"
     "            if (logged_capture++ < 64)\n"
     "                fprintf( stderr, \"[srv-getctx-err] tid=%04x state=%d status=%08x: the capture left an error \"\n"
     "                         \"(context=%p)\\n\", thread->id, (int)thread->state, get_error(), (void *)thread->context );\n"
     "        }\n"
     "        release_object( thread );\n        if (!thread_context) return;\n"),
    ("    else\n    {\n        set_error( thread_context->status );\n        if (thread_context->status == STATUS_PENDING)\n",
     "    else\n    {\n"
     "        if (thread_context->status != STATUS_PENDING)\n"
     "        {\n"
     "            static int logged_status;\n"
     "            if (logged_status++ < 64)\n"
     "                fprintf( stderr, \"[srv-getctx-err] status=%08x from the context object (a target that \"\n"
     "                         \"ended while its context was pending reads ACCESS_DENIED)\\n\", thread_context->status );\n"
     "        }\n"
     "        set_error( thread_context->status );\n        if (thread_context->status == STATUS_PENDING)\n"),
]


def main(path):
    src = open(path).read()
    if MARKER in src:
        print("patch-wineserver-getctx-log: already patched in", path)
        return 0
    handler = src.find("DECL_HANDLER(get_thread_context)")
    end = src.find("DECL_HANDLER(set_thread_context)", handler)
    if handler < 0 or end < 0:
        sys.stderr.write("patch-wineserver-getctx-log: get_thread_context handler not found\n")
        return 1
    body = src[handler:end]
    for old, new in EDITS:
        if body.count(old) != 1:
            sys.stderr.write("patch-wineserver-getctx-log: anchor moved:\n" + old)
            return 1
        body = body.replace(old, new)
    open(path, "w").write(src[:handler] + body + src[end:])
    print("patch-wineserver-getctx-log: get_thread_context names why it refuses in", path)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__.strip().splitlines()[-1] + "\n")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
