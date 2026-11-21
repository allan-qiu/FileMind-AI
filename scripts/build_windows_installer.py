"""Build the per-user Windows x64 MSI with PyInstaller and WiX 5."""

import argparse
import hashlib
import importlib.metadata
import shutil
import subprocess
import sys
import tomllib
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = "http://wixtoolset.org/schemas/v4/wxs"
UPGRADE_CODE = uuid.UUID("E87564AF-E312-4DD4-B444-7FEE6E866117")
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
ET.register_namespace("", NS)


def add(parent, tag, **attributes):
    return ET.SubElement(parent, f"{{{NS}}}{tag}", attributes)


def item_id(prefix, name):
    return prefix + "_" + hashlib.sha256(name.encode()).hexdigest()[:32]


def registry_key(component, name):
    add(
        component,
        "RegistryValue",
        Root="HKCU",
        Key=r"Software\FileMind AI\DesktopInstaller",
        Name=name,
        Value="1",
        Type="integer",
        KeyPath="yes",
    )


def write_wix(payload, destination):
    tree = ET.Element(f"{{{NS}}}Wix")
    package = add(
        tree,
        "Package",
        Name="FileMind AI",
        Manufacturer="FileMind AI",
        Version=VERSION,
        UpgradeCode=str(UPGRADE_CODE),
        Scope="perUser",
        InstallerVersion="500",
    )
    add(package, "MajorUpgrade", DowngradeErrorMessage="A newer FileMind AI is installed.")
    add(package, "MediaTemplate", EmbedCab="yes", CompressionLevel="high")
    add(package, "Property", Id="ARPCOMMENTS", Value=f"FileMind AI {VERSION}, Windows x64")
    local = add(package, "StandardDirectory", Id="LocalAppDataFolder")
    # The installer uses the per-user standard location on install and uninstall.
    # A private directory identifier keeps unsupported command-line overrides out.
    install = add(local, "Directory", Id="FileMindProgramDir", Name="FileMind AI Program")
    menu = add(package, "StandardDirectory", Id="ProgramMenuFolder")
    menu_component = add(menu, "Component", Id="ProgramShortcut", Guid="*")
    add(
        menu_component,
        "Shortcut",
        Id="FileMindStartMenu",
        Name="FileMind AI",
        Target="[#Executable]",
        WorkingDirectory="FileMindProgramDir",
    )
    registry_key(menu_component, "ProgramShortcut")

    feature = add(package, "Feature", Id="Desktop", Title="FileMind AI", Level="1")
    add(feature, "ComponentRef", Id="ProgramShortcut")
    directories = {payload: install}
    covered = set()

    def directory(path):
        if path not in directories:
            relative = path.relative_to(payload).as_posix()
            directories[path] = add(
                directory(path.parent), "Directory", Id=item_id("dir", relative), Name=path.name
            )
        return directories[path]

    for file in sorted(p for p in payload.rglob("*") if p.is_file()):
        relative = file.relative_to(payload).as_posix()
        component_id = item_id("component", relative)
        component = add(
            directory(file.parent),
            "Component",
            Id=component_id,
            Guid=str(uuid.uuid5(UPGRADE_CODE, relative.casefold())),
        )
        file_id = "Executable" if relative == "FileMind.exe" else item_id("file", relative)
        add(component, "File", Id=file_id, Source=str(file))
        registry_key(component, component_id)
        if file.parent not in covered:
            add(component, "RemoveFolder", Id=item_id("remove", relative), On="uninstall")
            covered.add(file.parent)
        add(feature, "ComponentRef", Id=component_id)

    for path in sorted(directories.keys() - covered):
        identifier = item_id("folder", path.relative_to(payload).as_posix())
        component = add(directories[path], "Component", Id=identifier, Guid="*")
        add(component, "RemoveFolder", Id=identifier, On="uninstall")
        registry_key(component, identifier)
        add(feature, "ComponentRef", Id=identifier)

    ET.indent(tree, space="  ")
    ET.ElementTree(tree).write(destination, encoding="utf-8", xml_declaration=True)


def collect_notices(payload):
    notices = payload / "Third-party notices"
    notices.mkdir(exist_ok=True)
    for source in [
        ROOT / "LICENSE",
        ROOT / "THIRD_PARTY_NOTICES.md",
        *(ROOT / "licenses").glob("*.txt"),
    ]:
        if source.is_file():
            shutil.copyfile(source, notices / source.name)
    distributions = (
        "PySide6",
        "PySide6-Essentials",
        "shiboken6",
        "PyInstaller",
        "pypdf",
        "watchdog",
        "pydantic",
        "pydantic-core",
        "annotated-types",
        "typing-extensions",
        "typing-inspection",
        "httpx",
        "httpcore",
        "h11",
        "idna",
        "anyio",
        "certifi",
    )
    for name in distributions:
        distribution = importlib.metadata.distribution(name)
        for resource in distribution.files or ():
            if not (
                resource.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
                or any(part.lower() == "licenses" for part in resource.parts)
            ):
                continue
            source = Path(distribution.locate_file(resource))
            if source.is_file():
                shutil.copyfile(source, notices / f"{name}-{resource.name}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, notices / "CPython-LICENSE.txt")
    (notices / "Qt.txt").write_text(
        "FileMind bundles the unmodified dynamically loaded Qt for Python (PySide6 and "
        "Shiboken) libraries from their official wheels.\n"
        "Qt for Python licensing and sources: https://doc.qt.io/qtforpython-6/licenses.html\n"
        "Qt sources: https://download.qt.io/official_releases/qt/\n"
        "Qt for Python sources: https://download.qt.io/official_releases/QtForPython/\n"
        "The Qt and PySide6 DLLs are in the installed _internal directory.\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wix", type=Path, required=True, help="Path to WiX 5 wix.exe")
    parser.add_argument("--output", type=Path, required=True, help="Isolated build directory")
    parser.add_argument("--reuse-payload", action="store_true", help="Compile an existing payload")
    opts = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        parser.error("Build with Windows x64 Python.")
    output = opts.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = output / "dist/FileMind"
    if not opts.reuse_payload:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--onedir",
                "--windowed",
                "--noupx",
                "--name",
                "FileMind",
                "--paths",
                str(ROOT / "src"),
                "--distpath",
                str(output / "dist"),
                "--workpath",
                str(output / "work"),
                "--specpath",
                str(output),
                str(ROOT / "run.py"),
            ],
            check=True,
            cwd=ROOT,
        )
    if not (payload / "FileMind.exe").is_file():
        raise SystemExit("Missing frozen FileMind.exe")
    # Qt 6.12 uses Windows' ICU API. PyInstaller can pick up an incompatible,
    # unversioned third-party ICU on the build host's PATH ahead of System32.
    for dll in ("icu.dll", "icuin.dll", "icuuc.dll"):
        (payload / "_internal" / dll).unlink(missing_ok=True)
    collect_notices(payload)
    definitions = output / "FileMind.wxs"
    write_wix(payload, definitions)
    installer = output / f"FileMind_AI_{VERSION}_Windows_x64.msi"
    subprocess.run(
        [str(opts.wix.resolve()), "build", "-arch", "x64", "-o", str(installer), str(definitions)],
        check=True,
        cwd=output,
    )
    print(installer)


if __name__ == "__main__":
    main()
