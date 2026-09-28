"""Build a portable Skill ZIP and optional Python distributions from an explicit file set."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "time-aware-execution"
ROOT_FILES = (
    "SKILL.md",
    "LICENSE",
    "TIME_AWARENESS_STANDARD.md",
    "INSTALL.md",
    "README.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "CONFORMANCE.md",
    "pyproject.toml",
)
RUNTIME_SCRIPTS = (
    "deadline_clock.py",
    "budget_gate.py",
    "deadline_run.py",
    "forecast_remaining.py",
    "calibration_report.py",
    "evaluate_runs.py",
    "verify_install.py",
)


def version(root: Path) -> str:
    match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        (root / "src/agent_time_control/__init__.py").read_text(),
        re.MULTILINE,
    )
    if match is None:
        raise ValueError("missing package version")
    return match.group(1)


def skill_files(root: Path) -> list[str]:
    root = root.resolve()
    names = set(ROOT_FILES)
    names.update("scripts/" + name for name in RUNTIME_SCRIPTS)
    for directory, pattern in (
        ("src/agent_time_control", "*.py"),
        ("references", "*.md"),
        ("schemas", "*.json"),
        ("agents", "*.yaml"),
        ("examples", "*.py"),
    ):
        names.update(
            path.relative_to(root).as_posix()
            for path in (root / directory).rglob(pattern)
        )
    for name in names:
        path = root / name
        if (
            not path.is_file()
            or any(
                parent.is_symlink()
                for parent in [path, *path.parents]
                if parent.is_relative_to(root)
            )
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError(f"missing or symlinked release file: {name}")
    return sorted(names)


def build_skill(root: Path, output: Path) -> dict:
    files = {name: (root / name).read_bytes() for name in skill_files(root)}
    manifest = {
        "version": version(root),
        "skill": SKILL_NAME,
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
    }
    files["BUNDLE-MANIFEST.json"] = (
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    ).encode()
    with zipfile.ZipFile(
        output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(
                f"{SKILL_NAME}/{name}", date_time=(1980, 1, 1, 0, 0, 0)
            )
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return manifest


def source_files(root: Path) -> list[str]:
    root = root.resolve()
    names = set(skill_files(root))
    names.update(
        (
            "MANIFEST.in",
            "CHANGELOG.md",
            "SECURITY.md",
            "CONFORMANCE.md",
            ".github/workflows/test.yml",
        )
    )
    for directory, patterns in (
        ("scripts", ("*.py",)),
        ("tests", ("*.py",)),
        ("evals", ("*.py", "*.md")),
        ("evals/results", ("*.json", "*.jsonl")),
        ("integrations", ("*.md", "*.json", "*.snippet")),
    ):
        for pattern in patterns:
            names.update(
                path.relative_to(root).as_posix()
                for path in (root / directory).rglob(pattern)
            )
    return sorted(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New output directory; existing paths are never overwritten",
    )
    parser.add_argument(
        "--python-dist",
        action="store_true",
        help="Also build wheel and sdist; requires installed build and setuptools",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("output already exists; choose a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="agent-time-release-", dir=output.parent
    ) as temp:
        stage = Path(temp) / "release"
        stage.mkdir()
        name = f"{SKILL_NAME}-{version(ROOT)}.zip"
        build_skill(ROOT, stage / name)
        if args.python_dist:
            source = Path(temp) / "source"
            for relative in source_files(ROOT):
                origin = ROOT / relative
                if origin.is_symlink() or not origin.resolve().is_relative_to(
                    ROOT.resolve()
                ):
                    raise ValueError(f"unsafe source path: {relative}")
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--outdir",
                    str(stage),
                    str(source),
                ],
                check=True,
                timeout=120,
            )
        hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(stage.iterdir())
            if path.is_file()
        }
        (stage / "SHA256SUMS").write_text(
            "".join(f"{value}  {name}\n" for name, value in hashes.items()),
            encoding="utf-8",
        )
        stage.rename(output)
    print(
        json.dumps(
            {"version": version(ROOT), "output": str(output), "artifacts": hashes},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
