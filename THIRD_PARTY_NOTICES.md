# Third-party notices

The current FileMind AI original source is licensed under AGPL-3.0-only.
Dependencies retain their own licenses; this change does not relicense upstream code.

Python dependencies are pinned in `requirements.lock`; browser dependencies and
their dependency metadata are pinned in `web/package-lock.json`.

The Windows installer contains unmodified dynamically loaded Qt for Python
(PySide6/Shiboken) libraries. Their DLLs and Python extension modules remain in
the `_internal` directory. Do not remove the bundled `Third-party notices`
directory when redistributing the application. Qt for Python offers multiple
licensing options; this project does not supply a commercial Qt license.

- Qt for Python licensing: https://doc.qt.io/qtforpython-6/licenses.html
- Qt sources: https://download.qt.io/official_releases/qt/
- Qt for Python sources: https://download.qt.io/official_releases/QtForPython/
- LGPL v3 and GPL v3 texts: `licenses/LGPL-3.0.txt` and `licenses/GPL-3.0.txt`
- PyInstaller licensing and exception: https://pyinstaller.org/en/stable/license.html

The installer build collects license/notice files from the installed wheel
metadata, includes the project license and these notices, and preserves the
separate Qt libraries. Rebuilding uses the versions in `requirements.lock`.
Source links refer to the official upstream source archives; no third-party
source ownership or additional license grants are implied.
