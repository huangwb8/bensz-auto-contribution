# BAC Contribution Ledger for VS Code

English | [简体中文](README.zh-CN.md)

Open a `.bac` file to explore human and AI collaboration, then use Git to inspect ledger changes and related code diffs. The viewer is read-only: it never modifies the ledger or executes commands recorded in events.

## Interface language

The interface defaults to **English**, regardless of the VS Code display language. Select **简体中文** in the upper-right language menu, or set `bacViewer.language` to `zh-CN` in VS Code settings. Select **English** or set it to `en` to switch back.

Your choice is saved in user settings and retained when reopening a ledger. Changing the language reloads open viewers; the search query, source filter and selected event are retained. Run Git comparisons again when needed.

The extension name, command palette entries and setting descriptions use English for consistent discovery. Recorded summaries, commands, evidence, raw JSON and external verifier output stay in their original language.

## Usage

- Open `.bac` from Explorer to view the contribution timeline. Filter by Human, AI, Tool or System, and search summaries, event types and related files.
- Click a `.bac` change in **Source Control** to open its Git versions directly. Each side displays its actual source and its own timeline, filters and verification state.
- Select an event to inspect its source, timestamp, trust claim, Git commit, file hashes, command evidence and raw JSON.
- Switch to **Git changes**, select **HEAD → Working tree**, **HEAD → Index** or **Index → Working tree**, then click **Compare ledger**.
- Comparisons show added, modified, removed and reordered events, plus manifest and project binding changes. Select a change to open a native event JSON diff.
- Related files support file navigation and native code diffs for **HEAD → Working tree**, **HEAD → Index** and **Recorded commit → Working tree**.
- Click **Verify ledger** for the full BAC verifier report. Working tree and index content changes refresh the corresponding viewer and invalidate its verification. Unrelated Git status updates preserve valid reports. Fixed commit views retain their content. Rerun event comparisons after updates.

Explorer and Source Control context menus provide **BAC: Compare Ledger with HEAD** for `.bac` files. The command palette also provides **BAC: Open Contribution Ledger** and **BAC: Verify Ledger**.

## Source Control versions

For tracked changes, **Changes** compares the index baseline with the working tree; **Staged Changes** compares HEAD with the index. VS Code can open a single existing side for additions or deletions. When an explicitly opened version has no ledger, the viewer shows **Version absent**; corrupt containers, merge conflicts, unavailable commits and access errors show a separate error with recovery guidance.

The viewer accepts local `file:` and built-in `git:` resources only. Git resources must map to an open project and its repository. The empty Git ref addresses the index, `~` follows the built-in Git extension's index-or-HEAD baseline, and `~1`–`~3` / `:1`–`:3` address individual merge stages. HEAD is pinned when opened; full object IDs retain their revision. Arbitrary refs, submodule diff resources, malformed URIs and mismatched paths are rejected.

Git ledgers are read as bounded raw blobs with `git cat-file`. The viewer bypasses textconv and content filters so the ZIP and verification snapshots preserve the stored bytes. It requires an enabled built-in Git extension and a trusted workspace, never substitutes the current working tree for a failed historical read, and does not use proposed diff APIs.

**Open file in this version** reads related code from the viewed Git source. The three existing code comparison actions and **BAC: Compare Ledger with HEAD** keep their explicit HEAD/index/working tree ranges. Event comparisons do not change the timeline's version or transfer verification between sides.

## Installation

Building requires Node.js 22.12+ (or a supported newer version). Requires desktop VS Code 1.90+. Viewing and Git comparisons do not require Python. Build and install from the repository root:

```bash
cd softwares/vscode-plugin
npm ci
npm run package
code --install-extension dist/bac-viewer-0.1.2.vsix --force
```

Alternatively, choose **Install from VSIX** in the Extensions view menu. For an already open `.bac`, right-click its tab and choose **Reopen Editor With → BAC Contribution Ledger**. Run **Developer: Reload Window** if the viewer does not activate after installation.

### Marketplace publishing

Follow Microsoft's [extension publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension). Repository-specific instructions are in [docs/vscode-marketplace-release.md](https://github.com/huangwb8/bensz-auto-contribution/blob/main/docs/vscode-marketplace-release.md). The VSIX filename follows the extension version in `package.json`, which is independent of the Python package version.

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
- Verification runs on a temporary snapshot with restricted permissions, deleted afterward. Each report binds to the viewed resource URI, raw snapshot digest, event count and head. It is rejected if that source changes during verification and invalidated after a content update. Working tree, index and historical sides are verified separately; event comparisons are not automatically verified.
- Signature and anchor status follow the BAC verifier report. The current core verifier does not support general event signatures; remote anchor receipt verification depends on the BAC environment.
- Untrusted workspaces allow local ledger viewing only; Git resources remain blocked. Trusted workspace program calls use argument arrays without a shell. The webview blocks external resources and renders all ledger content as plain text. Language changes affect interface labels only, preserving original audit evidence.
- File navigation uses the current Git repository or workspace root, never a ledger's declared absolute root. Paths escaping the project, including through symbolic links, are rejected.
- Code diffs support UTF-8 text up to 5 MiB. Missing files are represented as empty text. Merge conflicts, binary files, non-UTF-8 content and unavailable Git commits produce a message.
- File hashes and `diff --stat` cannot reconstruct historical uncommitted content or prove the origin of each code line. BAC is tamper-evident supporting process evidence; it cannot independently prove identity, contribution completeness or final authorship.

## Development and tests

Keep extension code, UI assets, tests, build scripts and package configuration within `softwares/vscode-plugin`. See the repository's [AGENTS.md](https://github.com/huangwb8/bensz-auto-contribution/blob/main/AGENTS.md) for project rules.

```bash
npm test
npm run test:vscode
npm run package
```

`npm test` checks TypeScript, container parsing, comparisons, path boundaries and localization. `test:vscode` exercises the actual extension host, language switching and persistence, viewer interactions, native code diffs, real Source Control clicks in temporary Git repositories, per-side verification and lifecycle, and a real restricted workspace. JPG screenshots are saved under `tmp/img-frontend/run-YYYYMMDD-HHMMSS` in the repository root. It defaults to macOS `/Applications/Visual Studio Code.app/Contents/MacOS/Code`; set `BAC_VSCODE_EXECUTABLE` for another installation. Tests require local VS Code, Git and an executable BAC CLI. To repeat the SCM suite at the minimum supported version, run `BAC_VSCODE_VERSION=1.90.0 node scripts/test-scm.mjs` (downloads an isolated official VS Code copy). `node scripts/test-scm.mjs --untrusted` tests restricted mode; the launcher deliberately avoids test-electron's automatic trust bypass. The SCM suite records English/Chinese source labels, narrow-window and light/dark screenshots.

## License

MIT. The bundled extension includes `yauzl` and its dependencies; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
