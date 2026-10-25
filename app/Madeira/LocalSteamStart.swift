// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// SOSOalqawe12 fork: Options › Start with Steam (BCD Dock with a game of your own). Kept out of MadeiraDock.swift,
// which the host checks compile on their own (check-dock-contract, check-steam-games): this file needs the library.

import Foundation

/// SOSOalqawe12 fork: a game of your own that needs Steam running, started through BCD Dock. Its
/// Steam App ID is the `steam-with` line of the game's file (Options › Start with Steam). Valve's
/// client signs in and confirms the account owns the App ID, as for any Dock start; then the Dock
/// host starts the game's own program (MADEIRA_STEAM_HOST_LOCAL_EXE, tools/patch-dock-local-game.py)
/// with Steam running and the game's App ID in its environment, instead of asking the client to start
/// an install it does not have. The game's own Steam checks run as they would on a PC.
enum LocalSteamStart {
    static let key = "steam-with"

    /// The App ID a library game of your own starts with, or nil: Steam games, the desktop and a
    /// game without the line start as before.
    static func appID(_ entry: LibraryEntry) -> Int? {
        guard entry.steamAppID == nil, entry.desktop != true,
              let text = GameProfile(windowsPath: entry.windowsPath).get(key), let id = Int(text),
              MadeiraDock.validAppID(id) else { return nil }
        return id
    }

    /// An App ID the game's own files name: steam_appid.txt beside its program or in one of the
    /// folders above it (up to drive_c, or up to its external drive's folder). nil when none names one.
    static func suggestedAppID(_ entry: LibraryEntry) -> Int? {
        var folder = entry.programFolderRelativePath.split(separator: "/").map(String.init)
        for _ in 0..<5 {
            guard !folder.isEmpty, folder != ["External"] else { break }
            let path = folder.joined(separator: "/")
            let dir = ExternalDrives.realURL(forDrivePath: path) ?? LibraryModel.drive.appendingPathComponent(path)
            if let data = try? Data(contentsOf: dir.appendingPathComponent("steam_appid.txt")), data.count <= 64,
               let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
               let id = Int(text), MadeiraDock.validAppID(id) {
                return id
            }
            folder.removeLast()
        }
        return nil
    }

    /// Where a suggestion came from, for its row: the game's steam_appid.txt, else Find on Steam.
    static func suggestion(_ entry: LibraryEntry) -> (id: Int, source: String)? {
        if let id = suggestedAppID(entry) { return (id, "The game's own steam_appid.txt names this App ID.") }
        if let id = entry.steamID, MadeiraDock.validAppID(id) { return (id, "Find on Steam matched the game to this App ID.") }
        return nil
    }

    /// The .exe names in the folder of the game's program (the list stays under the host's 1024 characters).
    static func programNames(_ entry: LibraryEntry) -> [String] {
        let folderPath = entry.programFolderRelativePath
        let folder = ExternalDrives.realURL(forDrivePath: folderPath) ?? LibraryModel.drive.appendingPathComponent(folderPath)
        var names: [String] = [], length = 0
        for name in ((try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []).sorted()
        where name.lowercased().hasSuffix(".exe") && !name.contains(";") {
            guard length + name.count + 1 < 1000 else { break }
            names.append(name); length += name.count + 1
        }
        return names
    }

    /// The Dock start's game: its App ID and name; it has no Steam library or install folder.
    static func game(_ entry: LibraryEntry, appID: Int) -> DockGame {
        DockGame(id: appID, name: entry.title, installDir: "", library: "", installed: true, customExecutables: false)
    }

    /// What a start needs that a Steam install's checks (MadeiraDock.validate) would otherwise confirm.
    static func validate(_ entry: LibraryEntry, drive: URL, bundled: Bool = MadeiraDock.bundled) throws {
        guard bundled else { throw DockError.message("This copy of BCD was built without BCD Dock.") }
        guard FileManager.default.fileExists(atPath: drive.appendingPathComponent(SteamRuntimeFiles.relativeRoot + "/steamclient64.dll").path) else {
            throw DockError.message("First download Valve's client components: the Steam client files aren't installed yet.")
        }
        guard !entry.isBatch else { throw DockError.message("Start with Steam starts a game's .exe, not a batch file.") }
    }

    /// What the last local start exported itself (name and value), so a later Dock start in the same app
    /// run does not inherit it (a Steam game started after GTA V got its service manager and keep-alive list).
    private static var exported: [String: String] = [:]

    /// The host's environment for this start; nil clears it, so no other Dock start inherits it.
    static func configure(_ entry: LibraryEntry?) {
        // Only a value still as this code set it: one that bcd.cfg or a game's file put there since stays.
        for (name, value) in exported where getenv(name).map({ String(cString: $0) }) == value { unsetenv(name) }
        exported = [:]
        guard let entry else {
            unsetenv("MADEIRA_STEAM_HOST_LOCAL_EXE"); unsetenv("MADEIRA_STEAM_HOST_LOCAL_ARGS"); return
        }
        setenv("MADEIRA_STEAM_HOST_LOCAL_EXE", entry.launchWindowsPath, 1)
        setenv("MADEIRA_STEAM_HOST_LOCAL_ARGS", entry.programArguments, 1)
        // A game of your own has no Steam-game settings: launchers want Windows' service manager (GTA V's
        // Rockstar service, RPC error 1722 without it), and a program that starts the game and exits must not
        // end the session, so the folder's programs keep it. The game's own lines (applied later) win.
        func export(_ name: String, _ value: String) { setenv(name, value, 1); exported[name] = value }
        let profile = GameProfile(windowsPath: entry.windowsPath)
        if profile.get("env.MADEIRA_DOCK_GAME_SCM") == nil { export("MADEIRA_DOCK_GAME_SCM", "1") }
        if profile.get("env.MADEIRA_DOCK_KEEP_ALIVE") == nil {
            let keep = programNames(entry)
            if !keep.isEmpty { export("MADEIRA_DOCK_KEEP_ALIVE", keep.joined(separator: ";")) }
        }
        SteamLog.event("[dock-launch] local-game=1")
    }
}
