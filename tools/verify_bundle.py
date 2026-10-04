#!/usr/bin/env python3
"""Check packaged copies and exercise each skill in a temporary study workspace."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SKILLS = {"cn-primary-tutor": ("primary", 5),
          "cn-junior-tutor": ("junior", 2),
          "cn-senior-tutor": ("senior", 2)}


def run(script, *args):
    result = subprocess.run([sys.executable, "-B", str(script), *map(str, args)],
                            capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        raise RuntimeError("Synthetic smoke command failed: " + result.stderr)
    return result.stdout


def main():
    spec = importlib.util.spec_from_file_location("pdf_fixture", ROOT / "tests/test_pdf_to_text.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    for name, (stage, grade) in SKILLS.items():
        folder = ROOT / "skills" / name
        for filename in ("pdf_to_text.py", "study_state.py", "requirements.txt"):
            assert (folder / "scripts" / filename).read_bytes() == (ROOT / "tools" / filename).read_bytes(), filename
        for source in folder.rglob("*.py"):
            ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for source in folder.rglob("*.md"):
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", source.read_text(encoding="utf-8")):
                if not target.startswith(("https://", "http://", "#")):
                    assert (source.parent / target).is_file(), target
        metadata = (folder / "agents/openai.yaml").read_text(encoding="utf-8")
        assert "$" + name in metadata
        for field in ("display_name:", "short_description:", "default_prompt:"):
            assert field in metadata
        with tempfile.TemporaryDirectory(prefix="codex-tutor-smoke-") as temporary:
            workspace = Path(temporary)
            pdf = workspace / "synthetic lesson.pdf"
            fixture.make_pdf(pdf, ["Adding unlike fractions: first use a common denominator. 1/2 + 1/3."])
            pdf_output = run(folder / "scripts/pdf_to_text.py", pdf, "--pages", "1")
            assert "1/2 + 1/3" in pdf_output and "第 1 页" in pdf_output
            state = folder / "scripts/study_state.py"
            common = ["--root", workspace / ".student-tutor", "--learner", "smoke-student"]
            json.loads(run(state, "init", *common, "--stage", stage, "--grade", grade))
            course_file = workspace / "synthetic-course.json"
            course_file.write_text(json.dumps({
                "source_file": str(pdf), "source_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "subject": "数学", "stage": "primary", "grade": 5,
                "topics": [{"id": "fractions-add", "title": "异分母分数加法", "pdf_pages": [1],
                            "prerequisites": [], "read_status": "verified"}]
            }, ensure_ascii=False), encoding="utf-8")
            json.loads(run(state, "course", *common, "--course", "math-p5", "--input", course_file))
            event_file = workspace / "synthetic-answer.json"
            event_file.write_text(json.dumps({
                "attempt_id": "smoke-answer-1", "topic_id": "fractions-add",
                "question": "计算1/2+1/3。", "student_answer": "5/6", "outcome": "correct",
                "assistance": "hinted", "evidence": "合成验证数据：提示通分后完成。"
            }, ensure_ascii=False), encoding="utf-8")
            first = json.loads(run(state, "record", *common, "--course", "math-p5", "--input", event_file))
            second = json.loads(run(state, "record", *common, "--course", "math-p5", "--input", event_file))
            assert first["attempts_count"] == second["attempts_count"] == 1
            assert second["status"] == "unchanged"
            next_grade = grade + 1 if grade < (6 if stage == "primary" else 3) else grade
            json.loads(run(state, "update-profile", *common, "--stage", stage, "--grade", next_grade))
            resumed = json.loads(run(state, "show", *common, "--course", "math-p5"))
            assert resumed["profile"]["grade"] == next_grade
            assert resumed["courses"][0]["stage"] == "primary"
            assert resumed["courses"][0]["grade"] == 5
            assert resumed["courses"][0]["attempts"][0]["assistance"] == "hinted"
        print(name + ": 副本、引用、PDF读取、教材索引、作答幂等、升年级与续学通过。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
