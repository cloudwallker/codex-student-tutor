#!/usr/bin/env python3
"""Build only whitelisted source artifacts, excluding all learning data."""
import hashlib
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("cn-primary-tutor", "cn-junior-tutor", "cn-senior-tutor")


def write_archive(target, files, base, prefix):
    files = sorted(files)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(base)
            assert not any(part in {".student-tutor", "__pycache__", ".venv", ".git", "dist"}
                           for part in relative.parts)
            assert path.suffix.lower() not in {".pdf", ".env", ".pyc"}
            archive.write(path, (Path(prefix) / relative).as_posix())
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files)
        for path in files:
            member = (Path(prefix) / path.relative_to(base)).as_posix()
            assert archive.read(member) == path.read_bytes()
    print(target.name + ": " + str(len(files)) + " files verified")


def main():
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    files = [ROOT / "README.md", ROOT / ".gitignore", ROOT / "tools/requirements.txt"]
    if (ROOT / "README_ZH.md").is_file():
        files.append(ROOT / "README_ZH.md")
    files += list((ROOT / "docs").glob("*.md"))
    files += list((ROOT / "docs/images").glob("*.svg"))
    files += list((ROOT / "tools").glob("*.py"))
    files += list((ROOT / "tests").glob("*.py"))
    for skill in SKILLS:
        folder = ROOT / "skills" / skill
        skill_files = [path for path in folder.rglob("*") if path.is_file() and
                       (path.suffix in {".md", ".yaml", ".py"} or path.name == "requirements.txt")]
        assert (folder / "SKILL.md") in skill_files
        assert len(skill_files) == 9
        files.extend(skill_files)
        write_archive(destination / (skill + "-v1.zip"), skill_files, folder, skill)
    write_archive(destination / "codex-student-tutor-v1.zip", files, ROOT, ROOT.name)
    archives = sorted(destination.glob("*.zip"))
    checksums = "\n".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name for path in archives) + "\n"
    (destination / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
