// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Add a game's engine side (BCDAddGame.swift): drive_c's folders (LibraryModel.drive), a program
// read as the library reads one (LibraryModel.inspect, LibraryModel.programExtensions), saved to
// the library (LibraryModel.save, which gives a game seen for the first time its recommended
// settings), and every game of drive_c added at once (BCDLibraryImport). Engine-bound: the
// preview build leaves it out (tools/ui-preview.py, ENGINE_BOUND). BCDAddGameSource finds it by
// its Objective-C name.

@objc(BCDAddGameLive)
@MainActor final class BCDAddGameLive: NSObject, BCDAddGameBackend {
    nonisolated override init() { super.init() }

    private var drive: URL { LibraryModel.drive }

    private func url(_ path: String) -> URL {
        path.isEmpty ? drive : drive.appendingPathComponent(path)
    }

    /// Only what lies inside drive_c, links followed: a link out of it is not listed, but for one into
    /// an external drive added to it (ExternalDrives, drive_c/External).
    private func inside(_ url: URL) -> Bool { ExternalDrives.driveRelative(url) != nil }

    func list(_ folder: String) throws -> [BCDDriveItem] {
        let base = url(folder)
        guard folder.isEmpty || inside(base) else { throw BCDGameError("This folder is outside drive_c.") }
        // SOSOalqawe12 fork: a folder on an external drive is read at the drive's own path (the
        // security-scoped URL Files gave), not through its link in drive_c/External; the items
        // keep their drive_c paths.
        let real = ExternalDrives.realURL(forDrivePath: folder) ?? base
        let names: [URL]
        do {
            names = try FileManager.default.contentsOfDirectory(at: real, includingPropertiesForKeys: [.isDirectoryKey],
                                                                options: .skipsHiddenFiles)
        } catch {
            LogStore.shared.log("[external] list \(real == base ? "drive_c" : "drive") failed: \(error.localizedDescription)")
            throw BCDGameError(real == base ? "BCD couldn't open this folder."
                                            : "BCD couldn't open this folder on the drive: \(error.localizedDescription)")
        }
        var items: [BCDDriveItem] = []
        for file in names where real != base || inside(file) {
            let isFolder = (try? file.resolvingSymlinksInPath().resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory ?? false
            guard isFolder || LibraryModel.programExtensions.contains(file.pathExtension.lowercased()) else { continue }
            let path = folder.isEmpty ? file.lastPathComponent : folder + "/" + file.lastPathComponent
            items.append(BCDDriveItem(name: file.lastPathComponent, path: path, folder: isFolder))
        }
        return items.sorted {
            $0.folder != $1.folder ? $0.folder : $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
    }

    func inspect(_ path: String) throws -> BCDProgramInfo {
        let entry = try LibraryModel.inspect(ExternalDrives.realURL(forDrivePath: path) ?? url(path))
        return BCDProgramInfo(title: entry.title, windowsPath: entry.windowsPath, bits: entry.bits, api: entry.graphicsAPI)
    }

    /// The program, read again and saved with its name; a program the library already holds is
    /// not added twice: its game is the answer.
    func add(_ path: String, title: String) throws -> String {
        var entry = try LibraryModel.inspect(ExternalDrives.realURL(forDrivePath: path) ?? url(path))
        let library = LibraryModel.shared
        if let known = library.entries.first(where: {
            $0.desktop != true && $0.steamAppID == nil && $0.relativePath.lowercased() == entry.relativePath.lowercased()
        }) {
            return BCDLiveID.local(known.id)
        }
        let name = title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !name.isEmpty { entry.title = name }
        library.save(entry)
        guard library.entries.contains(where: { $0.id == entry.id }) else {
            throw BCDGameError("BCD couldn't add the game to the library.")
        }
        LogStore.shared.log("[bcd-ui] added a game: \(entry.bits)-bit \(entry.graphicsAPI ?? "-")")
        return BCDLiveID.local(entry.id)
    }

    /// SOSOalqawe12 fork: a folder on an external drive, picked in Files, linked as drive_c/External/<name>.
    func addExternal(_ url: URL) throws -> String {
        do { return try ExternalDrives.add(url) } catch { throw BCDGameError(error.localizedDescription) }
    }

    /// SOSOalqawe12 fork: forgets an external drive (ExternalDrives.remove); its files stay where they are.
    func removeExternal(_ name: String) throws {
        do { try ExternalDrives.remove(name) } catch { throw BCDGameError(error.localizedDescription) }
    }

    /// SOSOalqawe12 fork: Rockstar's launcher, where Rockstar's installer and Dock's one-time installs put it.
    static let rockstarLauncher = "Program Files/Rockstar Games/Launcher/Launcher.exe"
    /// Rockstar's own installer, the one its Social Club page links to; saved in drive_c/bcd-games.
    static let rockstarInstallerURL = URL(string: "https://gamedownloads.rockstargames.com/public/installer/Rockstar-Games-Launcher.exe")!
    static let rockstarInstaller = "bcd-games/Rockstar-Games-Launcher.exe"

    func addRockstarLauncher(progress: @escaping (String) -> Void) async throws -> String {
        let library = LibraryModel.shared
        if FileManager.default.fileExists(atPath: url(Self.rockstarLauncher).path) {
            // The installer's entry has done its job.
            for old in library.entries where old.relativePath.lowercased() == Self.rockstarInstaller.lowercased() { library.remove(old.id) }
            return try add(Self.rockstarLauncher, title: "Rockstar Games Launcher")
        }
        let target = url(Self.rockstarInstaller)
        if !FileManager.default.fileExists(atPath: target.path) {
            progress("Downloading Rockstar's installer…")
            LogStore.shared.log("[rockstar] downloading the launcher's installer from \(Self.rockstarInstallerURL.host ?? "")")
            let download: (URL, URLResponse)
            do {
                download = try await URLSession.shared.download(from: Self.rockstarInstallerURL)
            } catch {
                throw BCDGameError("BCD couldn't download Rockstar's installer (\(error.localizedDescription)). Download it from rockstargames.com in Safari, copy it into BCD › wine › drive_c with Files and add it here.")
            }
            let (file, response) = download
            let size = ((try? FileManager.default.attributesOfItem(atPath: file.path))?[.size] as? Int) ?? 0
            guard (response as? HTTPURLResponse)?.statusCode == 200, size > 1 << 20 else {
                try? FileManager.default.removeItem(at: file)
                throw BCDGameError("Rockstar's server didn't send its installer. Download it from rockstargames.com in Safari, copy it into BCD › wine › drive_c with Files and add it here.")
            }
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: target)
            try FileManager.default.moveItem(at: file, to: target)
            LogStore.shared.log("[rockstar] installer saved: \(size) bytes")
        }
        let id = try add(Self.rockstarInstaller, title: "Install Rockstar Games Launcher")
        // Its windows show in a Wine desktop; once it has run, this row adds the launcher.
        if let index = library.entries.firstIndex(where: { BCDLiveID.local($0.id) == id }), library.entries[index].launchMode == nil {
            var entry = library.entries[index]
            entry.launchMode = "desktop"
            library.save(entry)
        }
        return id
    }

    func addEveryGame() async -> Int {
        await withCheckedContinuation { done in
            BCDLibraryImport.run { added in done.resume(returning: added) }
        }
    }
}
