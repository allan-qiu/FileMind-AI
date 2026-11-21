# Public release guide

## Public tree

Run `python scripts/prepare_github_release.py --output <new-directory>` to export
the public source tree. This preserves the development checkout and its history.
Only the exported directory is intended for a new public repository; do not push
the original development Git history or attach old private acceptance archives.

The export retains complete application code, migrations, tests, startup scripts,
generic deployment templates, locked dependencies, MIT and dependency notices.
Historical machine-specific evidence is omitted and reported in the local export
manifest. Public README is generated from `docs/github/README.md`.

`python scripts/prepare_github_release.py --check .` checks tracked public files
for excluded runtime/configuration paths and credential-shaped text. Reports
include file/rule names, never matched secrets. This is a repository publishing
check, not a file analysis feature in the application.

## Repository upload

Review the exported files and create a new empty GitHub repository. From the
exported directory, initialize Git, create the initial commit, and add the empty
repository as origin. The prepared handoff may already contain a fresh local
repository; check `git status` before these steps. No push or public Release is
performed by the export command.

```text
git remote add origin https://github.com/YOUR_ACCOUNT/FileMind-AI.git
git push -u origin main
```

Replace the placeholder with your own repository. Never copy a personal access
token into a URL, file or command saved in the repository.

## Validation and assets

CI uses Windows for Python/Qt and Ubuntu for the Web tests/build. The manually
triggered “Build Windows release assets” workflow builds an unsigned MSI using
PyInstaller and WiX 5.0.2, then uploads source/summary ZIPs and checksums as Actions
artifacts. It does not deploy services or automatically publish a Release.

For local packaging use `scripts/build_windows_installer.py --wix <wix.exe>
--output <isolated-build-directory>`. Keep the dependency notices with the MSI.
Build the public export, not an unrelated developer working tree.

Create a **draft pre-release** for tag `v2.6.2` after reviewing CI results. Attach
the MSI, complete source review ZIP, stage summary ZIP and SHA256SUMS.txt. Mark
unverified physical-device, external Provider and production checks accurately.

The project MIT license does not replace dependency licenses. Qt libraries are
distributed separately under `_internal`; upstream source and license texts are
listed in THIRD_PARTY_NOTICES.md. No production server/account is supplied.

## Upstream workflow references

- https://github.com/actions/checkout
- https://github.com/actions/setup-python
- https://github.com/actions/setup-node
- https://github.com/actions/upload-artifact

Actions are pinned to the verified official v7 tag commits at preparation time.
Their hosted execution is pending until this source is uploaded and workflows run.
