# BAC Contribution Ledger for VS Code

English | [简体中文](README.zh-CN.md)

Open a `.bac` file to explore human and AI collaboration, then use Git to inspect ledger changes and related code diffs. The viewer is read-only: it never modifies the ledger or executes commands recorded in events.

## Interface language

The interface defaults to **English**, regardless of the VS Code display language. Select **简体中文** in the upper-right language menu, or set `bacViewer.language` to `zh-CN` in VS Code settings. Select **English** or set it to `en` to switch back.

Your choice is saved in user settings and retained when reopening a ledger. Changing the language reloads open viewers; the search query, source filter and selected event are retained. Run Git comparisons again when needed.

The extension name, command palette entries and setting descriptions use English for consistent discovery. Recorded summaries, commands, evidence, raw JSON and external verifier output stay in their original language.

## Usage

- Open `.bac` from Explorer to view the contribution timeline. Filter by Human, AI, Tool or System, and search summaries, event types and related files.
- Select an event to inspect its source, timestamp, trust claim, Git commit, file hashes, command evidence and raw JSON.
- Switch to **Git changes**, select **HEAD → Working tree**, **HEAD → Index** or **Index → Working tree**, then click **Compare ledger**.
- Comparisons show added, modified, removed and reordered events, plus manifest and project binding changes. Select a change to open a native event JSON diff.
- Related files support file navigation and native code diffs for **HEAD → Working tree**, **HEAD → Index** and **Recorded commit → Working tree**.
- Click **Verify ledger** for the full BAC verifier report. File changes refresh the viewer and invalidate old verification results. Rerun comparisons after Git versions change.

Explorer and Source Control context menus provide **BAC: Compare Ledger with HEAD** for `.bac` files. The command palette also provides **BAC: Open Contribution Ledger** and **BAC: Verify Ledger**.

## Installation

Building requires Node.js 22.12+ (or a supported newer version). Requires desktop VS Code 1.90+. Viewing and Git comparisons do not require Python. Build and install from the repository root:

```bash
cd softwares/vscode-plugin
npm ci
npm run package
code --install-extension dist/bac-viewer-0.1.1.vsix --force
```

Alternatively, choose **Install from VSIX** in the Extensions view menu. For an already open `.bac`, right-click its tab and choose **Reopen Editor With → BAC Contribution Ledger**. Run **Developer: Reload Window** if the viewer does not activate after installation.

### Marketplace publishing

Follow Microsoft's [extension publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension). Repository-specific instructions are in [docs/vscode-marketplace-release.md](../../docs/vscode-marketplace-release.md). The VSIX filename follows the extension version in `package.json`, which is independent of the Python package version.

### Full verification

Install the BAC CLI:

```bash
python -m pip install bensz-auto-contribution
```

The extension looks for `bac` in `PATH` or `~/.local/bin/bac`. For a separate Python environment, set `bacViewer.bacExecutable` to that environment's executable. This setting is a file path, not a shell command.

In remote SSH or container workspaces, the extension, Git and verifier run on the remote extension host. Install BAC CLI there too.

## Format, verification and security boundaries

- Supports `bac.container.v2` ZIP containers and `bac.event.v2` events. Reading a container does not verify it: newly opened ledgers show **Not verified**.
- Read limits: 50 MiB compressed, 2 MiB decompressed per member, 256 MiB total decompressed and 100,000 events. Duplicate entries, non-contiguous event numbering, duplicate event IDs, unknown members and invalid UTF-8/JSON are rejected. Use BAC CLI for ledgers beyond the viewer's limits.
- Comparisons check event IDs, parsed content and relative order, rather than only claimed hashes. JSON formatting changes do not count as contribution changes; full verification separately checks content digests and the hash chain.
- Verification runs on a temporary snapshot with restricted permissions, deleted afterward. Results bind to the current file digest, event count and head, and become invalid when the file changes. Reports apply to the working tree ledger; Git comparison results are not automatically verified.
- Signature and anchor status follow the BAC verifier report. The current core verifier does not support general event signatures; remote anchor receipt verification depends on the BAC environment.
- Untrusted workspaces allow ledger viewing only. Trusted workspace program calls use argument arrays without a shell. The webview blocks external resources and renders all ledger content as plain text. Language changes affect interface labels only, preserving original audit evidence.
- File navigation uses the current Git repository or workspace root, never a ledger's declared absolute root. Paths escaping the project, including through symbolic links, are rejected.
- Code diffs support UTF-8 text up to 5 MiB. Missing files are represented as empty text. Merge conflicts, binary files, non-UTF-8 content and unavailable Git commits produce a message.
- File hashes and `diff --stat` cannot reconstruct historical uncommitted content or prove the origin of each code line. BAC is tamper-evident supporting process evidence; it cannot independently prove identity, contribution completeness or final authorship.

## Development and tests

Keep extension code, UI assets, tests, build scripts and package configuration within `softwares/vscode-plugin`. See the repository's [AGENTS.md](../../AGENTS.md) for project rules.

```bash
npm test
npm run test:vscode
npm run package
```

`npm test` checks TypeScript, container parsing, comparisons, path boundaries and localization. `test:vscode` exercises the actual extension host, language switching and persistence, viewer interactions and native code diffs. JPG screenshots are saved under `tmp/img-frontend/run-YYYYMMDD-HHMMSS` in the repository root. It defaults to macOS `/Applications/Visual Studio Code.app/Contents/MacOS/Code`; set `BAC_VSCODE_EXECUTABLE` for another installation. Tests require local VS Code, Git and an executable BAC CLI.

## License

MIT. The bundled extension includes `yauzl` and its dependencies; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
