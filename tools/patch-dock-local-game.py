#!/usr/bin/env python3
"""Dock host: start a game of your own with Steam running (SOSOalqawe12 fork).

A Steam copy kept outside Steam's library (on an external drive, say) stops with
"SteamAPI_Init() failed; unable to locate a running instance of Steam": nothing
runs Valve's client in its session. BCD's Options > Start with Steam gives such a
game its Steam App ID; the app then starts BCD Dock as for an installed Steam game
(LocalSteamStart, app/Madeira/MadeiraDock.swift) and sets
MADEIRA_STEAM_HOST_LOCAL_EXE to the game's program (and
MADEIRA_STEAM_HOST_LOCAL_ARGS to its arguments).

Valve's client signs in and confirms the account's licence for the App ID exactly
as before; sh_launch is reached only then. With the variable set, sh_launch does
not ask the client for the install folder (there is no install record), publishes
the client for discovery as it does for every launch (HKCU ActiveProcess pid and
ActiveUser, HKLM SteamPID), sets SteamAppId and SteamGameId to the App ID, and
starts the program itself in its own folder instead of calling LaunchApp. It
reports launch-game-running when the program started and launch-game-ended when
it exited, keeps reading the client's callbacks meanwhile, and restores the
discovery values afterwards, as after a LaunchApp start. The game's own Steam
API then finds the signed-in client; its DRM and licence checks are untouched.
Without the variable nothing changes.

Usage: patch-dock-local-game.py madeira-dock/src/launch.c (idempotent)
"""
import sys

path = sys.argv[1]
src = open(path).read()
if "MADEIRA_STEAM_HOST_LOCAL_EXE" in src:
    print("already patched"); sys.exit(0)

def replace_once(text, old, new, what):
    if text.count(old) != 1:
        sys.exit("patch-dock-local-game: %s: anchor found %d times" % (what, text.count(old)))
    return text.replace(old, new)

# 1. The helper, right before the first (64-bit) sh_launch.
anchor_fn = '''int sh_launch(HMODULE module, void *engine, void *client_user,
              const struct sh_api *api, const struct sh_observer *o,
              int32_t pipe, int32_t user, uint64_t steamid, uint32_t appid,
              const struct dock_client_layout *layout)
{
    if (!layout'''
helper = '''/* SOSOalqawe12 fork: MADEIRA_STEAM_HOST_LOCAL_EXE, see tools/patch-dock-local-game.py. Starts the
 * program with the App ID in its environment and waits for it, reading the client's callbacks. */
static int run_local_game(const struct sh_api *api, const struct sh_observer *o, int32_t pipe,
                          uint32_t appid, const wchar_t *exe)
{
    static wchar_t folder[32768], args[8192], command[32768 + 8192 + 4];
    wchar_t id[16];
    swprintf(id, 16, L"%u", (unsigned)appid);
    if (!SetEnvironmentVariableW(L"SteamAppId", id) || !SetEnvironmentVariableW(L"SteamGameId", id)) return 43;
    wcsncpy(folder, exe, 32767);
    folder[32767] = 0;
    wchar_t *slash = wcsrchr(folder, L'\\\\');
    if (!slash) return 43;
    *slash = 0;
    DWORD n = GetEnvironmentVariableW(L"MADEIRA_STEAM_HOST_LOCAL_ARGS", args, 8192);
    if (n >= 8192) n = 0;
    args[n] = 0;
    swprintf(command, sizeof(command) / sizeof(command[0]), n ? L"\\"%ls\\" %ls" : L"\\"%ls\\"", exe, args);
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si));
    memset(&pi, 0, sizeof(pi));
    si.cb = sizeof(si);
    if (!CreateProcessW(exe, command, NULL, NULL, FALSE, 0, NULL, folder, &si, &pi)) {
        o->event("local-game-start-error", (int32_t)GetLastError());
        return 43;
    }
    CloseHandle(pi.hThread);
    o->event("launch-game-running", 1);
    while (!InterlockedCompareExchange(&interrupted, 0, 0) &&
           WaitForSingleObject(pi.hProcess, 50) == WAIT_TIMEOUT) {
        for (unsigned batch = 0; batch < 64; ++batch) {
            struct sh_callback cb = {0};
            if (!api->get_callback(pipe, &cb)) break;
            api->free_callback(pipe);
        }
    }
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    o->event("local-game-exit-code", (int32_t)code);
    o->event("launch-game-ended", 1);
    return 0;
}

'''
src = replace_once(src, anchor_fn, helper + anchor_fn, "sh_launch")

# 2. The install-folder check is for a Steam install; a game of your own has none.
old = '''    static char actual_utf8[32768];
    static wchar_t actual[32768], expected[32768], normalized[32768];
    DWORD length = GetEnvironmentVariableW(L"MADEIRA_STEAM_HOST_EXPECTED_INSTALL", expected, 32768);
    if (!length || length >= 32768) return 41;'''
new = '''    static char actual_utf8[32768];
    static wchar_t actual[32768], expected[32768], normalized[32768];
    static wchar_t local_exe[32768];
    DWORD local_length = GetEnvironmentVariableW(L"MADEIRA_STEAM_HOST_LOCAL_EXE", local_exe, 32768);
    bool local_game = local_length > 3 && local_length < 32768;
    if (local_game) o->event("local-game", 1);
    if (!local_game) {
    DWORD length = GetEnvironmentVariableW(L"MADEIRA_STEAM_HOST_EXPECTED_INSTALL", expected, 32768);
    if (!length || length >= 32768) return 41;'''
src = replace_once(src, old, new, "install check start")
old = '''    o->event("launch-install-directory-verified", 1);'''
new = '''    o->event("launch-install-directory-verified", 1);
    }'''
src = replace_once(src, old, new, "install check end")

# 3. After the client is published for discovery: start the program instead of LaunchApp.
old = '''    o->event("launch-client-discovery-published", 1);
    SetConsoleCtrlHandler(on_control, TRUE);'''
new = '''    o->event("launch-client-discovery-published", 1);
    SetConsoleCtrlHandler(on_control, TRUE);
    if (local_game) {
        result = run_local_game(api, o, pipe, appid, local_exe);
        goto done;
    }'''
src = replace_once(src, old, new, "discovery published")

open(path, "w").write(src)
print("patched " + path)
