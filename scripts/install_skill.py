"""Install the portable Skill and its managed Codex guidance without replacing user rules."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
from build_release import ROOT, SKILL_NAME, build_skill
from verify_install import verify

BEGIN = "<!-- time-aware-execution:begin -->"
END = "<!-- time-aware-execution:end -->"


def merge_instructions(original: str, body: str) -> str:
    """Only replace this installer's delimited block; preserve all other bytes."""
    block = f"{BEGIN}\n{body.strip()}\n{END}"
    if BEGIN not in original and END not in original:
        return original + ("\n\n" if original else "") + block + "\n"
    if original.count(BEGIN) != 1 or original.count(END) != 1:
        raise ValueError(
            "ambiguous managed instruction markers; repair them before installation"
        )
    start, end = original.index(BEGIN), original.index(END)
    if end < start:
        raise ValueError("reversed managed instruction markers")
    return original[:start] + block + original[end + len(END) :]


def _matches(target: Path, manifest: dict) -> bool:
    """Do not mistake local edits or extra files for an unchanged managed install."""
    expected = manifest["files"]
    try:
        if (
            json.loads((target / "BUNDLE-MANIFEST.json").read_text(encoding="utf-8"))
            != manifest
        ):
            return False
    except (OSError, ValueError):
        return False
    paths = list(target.rglob("*"))
    if any(path.is_symlink() for path in paths):
        return False
    names = {
        p.relative_to(target).as_posix()
        for p in paths
        if p.is_file() and not (p.parent.name == "__pycache__" and p.suffix == ".pyc")
    }
    if names != set(expected) | {"BUNDLE-MANIFEST.json"}:
        return False
    return all(
        hashlib.sha256((target / name).read_bytes()).hexdigest() == value
        for name, value in expected.items()
    )


def install(
    source: Path, *, project: Path | None = None, replace: bool = False
) -> dict:
    source = source.resolve()
    base = project.resolve() if project is not None else Path.home()
    target = base / ".agents" / "skills" / SKILL_NAME
    config = (
        base
        if project is not None
        else Path(os.environ.get("CODEX_HOME", str(base / ".codex")))
    )
    rules = config / (
        "AGENTS.override.md"
        if (config / "AGENTS.override.md").exists()
        else "AGENTS.md"
    )
    if target.is_symlink() or rules.is_symlink():
        raise ValueError(
            "installation target and instruction file must not be symbolic links"
        )
    old_rules = rules.read_bytes() if rules.exists() else None
    body = (source / "integrations" / "AGENTS.md.snippet").read_text(encoding="utf-8")
    body += f"\nFor applicable tasks, read `{target / 'SKILL.md'}` and use its bundled tools with an available Python 3.10+ interpreter.\n"
    new_rules = merge_instructions((old_rules or b"").decode("utf-8"), body).encode(
        "utf-8"
    )
    staging_parent = base / ".agents"
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="time-aware-staging-", dir=staging_parent
    ) as temp:
        stage = Path(temp)
        archive = stage / "skill.zip"
        manifest = build_skill(source, archive)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(stage / "expanded")
        candidate = stage / "expanded" / SKILL_NAME
        selftest = verify(candidate)
        current = target.is_dir() and _matches(target, manifest)
        if target.exists() and not current and not replace:
            raise FileExistsError(
                f"existing or locally modified Skill: {target}; use --replace for an explicit backed-up replacement"
            )
        backup = None
        if (target.exists() and not current) or (
            old_rules is not None and old_rules != new_rules
        ):
            backup = staging_parent / "time-aware-backups" / uuid.uuid4().hex
            backup.mkdir(parents=True)
            if old_rules is not None:
                (backup / rules.name).write_bytes(old_rules)
        target.parent.mkdir(parents=True, exist_ok=True)
        config.mkdir(parents=True, exist_ok=True)
        moved = False
        try:
            if not current:
                if target.exists():
                    target.rename(backup / SKILL_NAME)
                candidate.rename(target)
                moved = True
            if old_rules != new_rules:
                if (rules.read_bytes() if rules.exists() else None) != old_rules:
                    raise RuntimeError(
                        "instructions changed during installation; retry after reviewing the concurrent change"
                    )
                fd, filename = tempfile.mkstemp(prefix=".time-aware-rules-", dir=config)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(new_rules)
                    if old_rules is not None:
                        os.chmod(filename, rules.stat().st_mode & 0o777)
                    os.replace(filename, rules)
                finally:
                    Path(filename).unlink(missing_ok=True)
        except BaseException:
            if moved:
                shutil.rmtree(target)
            if backup is not None and (backup / SKILL_NAME).exists():
                (backup / SKILL_NAME).rename(target)
            raise
    return {
        "status": "unchanged" if current and old_rules == new_rules else "installed",
        "skill_dir": str(target),
        "instructions_file": str(rules),
        "instructions_changed": old_rules != new_rules,
        "backup_dir": str(backup) if backup is not None else None,
        "source_files_sha256": hashlib.sha256(
            json.dumps(manifest["files"], sort_keys=True).encode()
        ).hexdigest(),
        "version": manifest["version"],
        "self_test": selftest,
        "agent_activation": "verify in a new agent session; not proven by file installation",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=Path,
        help="Install into this project's .agents/skills and AGENTS.md instead of user-level Codex paths",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Back up and replace an existing different Skill; never discard local edits silently",
    )
    args = parser.parse_args()
    try:
        result = install(ROOT, project=args.project, replace=args.replace)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
