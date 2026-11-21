"""Export a fresh public source tree; never export developer Git history or runtime data."""

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = {
    "docs/V2_6_ARCHITECTURE.md",
    "docs/V2_6_API.md",
    "docs/PUBLIC_RELEASE.md",
    "docs/github/README.md",
}
OMIT_SCRIPTS = {
    "scripts/v2_5_baseline.py",
    "scripts/collect_v2_5_evidence.py",
    "scripts/collect_v2_6_evidence.py",
}
BLOCKED_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".filemind",
    ".filemind-server",
    ".artifacts",
    ".cache",
    ".pytest_cache",
    ".pytest_tmp",
    "deliveries",
    "private",
}
SECRET_RULES = {
    "private-key": re.compile(rb"(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r]?$"),
    "provider-key": re.compile(rb"\bsk-[A-Za-z0-9]{24,}"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})"),
    "local-user-path": re.compile(
        rb"(?i)[A-Z]:[\\/]Users[\\/](?!Public[\\/]|example[\\/])[^\\/\s]+"
    ),
    "configured-provider-env": re.compile(
        rb"(?m)^FILEMIND_DEEPSEEK_API_KEY[ \t]*=[ \t]*[^\s#\r\n]+"
    ),
}


def validate_file(name: str, raw: bytes) -> list[str]:
    path = Path(name)
    problems = []
    if (
        any(part in BLOCKED_PARTS for part in path.parts)
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.suffix.lower() in {".sqlite3", ".db", ".pem", ".key", ".pfx", ".p12"}
        or ".sqlite3-" in path.name
    ):
        problems.append("excluded-runtime-or-credential-path")
    for rule, pattern in SECRET_RULES.items():
        if pattern.search(raw):
            problems.append(rule)
    return problems


def check_tree(root: Path) -> dict:
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        )
        paths = [root / name for name in result.stdout.decode("utf-8").split("\0") if name]
    else:
        paths = [p for p in root.rglob("*") if p.is_file()]
    findings = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            findings.append({"file": name, "rule": "symlink-or-path-escape"})
            continue
        findings.extend(
            {"file": name, "rule": rule} for rule in validate_file(name, path.read_bytes())
        )
    return {
        "files_checked": len(paths),
        "findings": findings,
        "status": "FAIL" if findings else "PASS",
    }


def export_public(output: Path) -> dict:
    if output.exists():
        raise ValueError("Output must be a new directory; existing work will not be overwritten")
    spec = importlib.util.spec_from_file_location("release_allowlist", ROOT / "scripts/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = []
    omitted = []
    for path in module.source_files():
        name = path.relative_to(ROOT).as_posix()
        if (name.startswith("docs/") and name not in PUBLIC_DOCS) or name in OMIT_SCRIPTS:
            omitted.append(name)
        else:
            selected.append(path)
    files = {}
    findings = []
    for path in selected:
        name = path.relative_to(ROOT).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Refusing linked source: " + name)
        raw = (
            (ROOT / "docs/github/README.md").read_bytes()
            if name == "README.md"
            else path.read_bytes()
        )
        findings.extend({"file": name, "rule": rule} for rule in validate_file(name, raw))
        files[name] = raw
    if findings:
        raise ValueError(json.dumps({"findings": findings}, ensure_ascii=False))
    required = {
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "licenses/LGPL-3.0.txt",
        "licenses/GPL-3.0.txt",
        ".github/workflows/ci.yml",
        ".github/workflows/windows-release.yml",
        "src/filemind/app.py",
        "src/filemind_server/app.py",
        "web/src/Portal.tsx",
        "web/app/index.html",
        "web/admin/index.html",
        "web/share/index.html",
    }
    if not required.issubset(files):
        raise ValueError("Missing required public files: " + str(sorted(required - files.keys())))
    output.mkdir(parents=True)
    for name, raw in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    report = {
        "version": "2.6.2",
        "license": "MIT",
        "history_exported": False,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
        "omitted_historical_files": omitted,
        "check": check_tree(output),
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--output", type=Path)
    choice.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check:
        report = check_tree(args.check.resolve())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1 if report["findings"] else 0)
    report = export_public(args.output.resolve())
    # Keep development-only paths/history inventory outside the public export.
    manifest = args.output.resolve().parent / (args.output.name + "-export-manifest.json")
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"version": report["version"], "files": len(report["files"]), "check": report["check"]}
        )
    )


if __name__ == "__main__":
    main()
