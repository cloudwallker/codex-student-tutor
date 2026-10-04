"""Behavioral tests for the local learner state CLI (stdlib only)."""

import contextlib
import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "study_state.py"


class StudyStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "学习档案"
        self.learner_dir = self.root / "learners" / "student-1"
        self.course_path = self.learner_dir / "courses" / "math-5.json"
        self.payload = {
            "source_file": "五年级数学上册.pdf",
            "subject": "数学",
            "stage": "primary",
            "grade": 5,
            "edition": "示例版",
            "volume": "上册",
            "topics": [
                {"id": "fractions", "title": "异分母分数加法",
                 "pdf_pages": [43, 44], "prerequisites": [],
                 "read_status": "verified"}
            ],
        }
        self.attempt = {
            "attempt_id": "attempt-1", "topic_id": "fractions",
            "question": "计算 1/2 + 1/3。", "student_answer": "5/6",
            "reasoning": "通分成六分之三和六分之二。",
            "outcome": "correct", "assistance": "hinted",
            "feedback": "在提示通分后答对。", "error_cause": "忘记通分",
            "evidence": "需要一次提示；未验证独立作答。",
            "review_on": "2026-10-06",
        }

    def cli(self, command, *extra, expected=0):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), command, "--root", str(self.root),
             "--learner", "student-1", *map(str, extra)],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        stream = result.stdout if expected == 0 else result.stderr
        self.assertTrue(stream.strip().startswith("{"), stream)
        try:
            data = json.loads(stream)
        except json.JSONDecodeError:
            self.fail("CLI must return a single valid JSON object")
        if expected:
            self.assertIn("error", data)
        return data

    def write_input(self, data):
        path = self.base / "input.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def initialize(self):
        return self.cli("init", "--stage", "primary", "--grade", "5")

    def course(self, payload=None, expected=0):
        return self.cli("course", "--course", "math-5", "--input",
                        self.write_input(self.payload if payload is None else payload),
                        expected=expected)

    def record(self, payload=None, expected=0):
        return self.cli("record", "--course", "math-5", "--input",
                        self.write_input(self.attempt if payload is None else payload),
                        expected=expected)

    def ready(self):
        self.initialize()
        self.course()

    def stored(self):
        return json.loads(self.course_path.read_text(encoding="utf-8"))

    def test_init_creates_minimal_profile_and_utf8_paths(self):
        data = self.initialize()
        self.assertEqual(data["profile"], {
            "schema_version": 1, "learner_id": "student-1", "stage": "primary", "grade": 5,
        })
        self.assertTrue((self.learner_dir / "profile.json").is_file())

    def test_repeated_init_is_idempotent_and_conflicting_stage_is_rejected(self):
        self.initialize()
        before = (self.learner_dir / "profile.json").read_bytes()
        self.initialize()
        self.cli("init", "--stage", "junior", "--grade", "2", expected=2)
        self.assertEqual((self.learner_dir / "profile.json").read_bytes(), before)

    def test_stage_relative_grade_validation_and_optional_grade(self):
        self.cli("init", "--stage", "junior", "--grade", "8", expected=2)
        self.assertFalse(self.root.exists())
        data = self.cli("init", "--stage", "junior")
        self.assertIsNone(data["profile"]["grade"])

    def test_root_is_required(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "init", "--learner",
                                 "student-1", "--stage", "primary"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.root.exists())

    def test_course_inherits_stage_and_optional_metadata(self):
        self.initialize()
        payload = {"source_file": "数学.pdf", "subject": "数学", "topics": self.payload["topics"]}
        self.course(payload)
        stored = self.stored()
        self.assertEqual(stored["stage"], "primary")
        self.assertEqual(stored["grade"], 5)
        self.assertIsNone(stored["edition"])
        self.assertEqual(stored["attempts"], [])

    def test_junior_learner_can_study_primary_fifth_grade_course(self):
        self.cli("init", "--stage", "junior", "--grade", "2")
        self.course()
        self.record()
        shown = self.cli("show", "--course", "math-5")
        self.assertEqual(shown["profile"]["stage"], "junior")
        self.assertEqual(shown["profile"]["grade"], 2)
        self.assertEqual(shown["courses"][0]["stage"], "primary")
        self.assertEqual(shown["courses"][0]["grade"], 5)
        self.assertEqual(shown["courses"][0]["attempts_total"], 1)

    def test_new_cross_stage_course_does_not_inherit_learner_grade(self):
        self.initialize()
        payload = copy.deepcopy(self.payload)
        payload["stage"] = "junior"
        del payload["grade"]
        self.course(payload)
        self.assertEqual(self.stored()["stage"], "junior")
        self.assertIsNone(self.stored()["grade"])

    def test_new_cross_stage_course_does_not_reuse_even_valid_numeric_grade(self):
        self.cli("init", "--stage", "junior", "--grade", "2")
        payload = copy.deepcopy(self.payload)
        del payload["grade"]
        self.course(payload)
        self.assertEqual(self.stored()["stage"], "primary")
        self.assertIsNone(self.stored()["grade"])

    def change_profile_fixture(self, stage, grade):
        """Represent a valid existing profile changed independently of course files."""
        path = self.learner_dir / "profile.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value.update(stage=stage, grade=grade)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_existing_course_is_readable_after_learner_stage_changes(self):
        self.ready()
        self.record()
        before = self.course_path.read_bytes()
        self.change_profile_fixture("junior", 1)
        shown = self.cli("show", "--course", "math-5")
        self.assertEqual(shown["courses"][0]["stage"], "primary")
        self.assertEqual(shown["courses"][0]["grade"], 5)
        self.assertEqual(shown["courses"][0]["attempts_total"], 1)
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_existing_course_without_stage_or_grade_keeps_own_metadata(self):
        self.ready()
        self.record()
        self.change_profile_fixture("junior", 1)
        payload = copy.deepcopy(self.payload)
        del payload["stage"]
        del payload["grade"]
        payload["topics"] = [{"id": "decimals", "title": "小数", "pdf_pages": [],
                             "prerequisites": [], "read_status": "unread"}]
        self.course(payload)
        self.assertEqual(self.stored()["stage"], "primary")
        self.assertEqual(self.stored()["grade"], 5)
        self.assertEqual(len(self.stored()["topics"]), 2)
        self.assertEqual(len(self.stored()["attempts"]), 1)
        payload["stage"] = None
        self.course(payload)
        self.assertEqual(self.stored()["stage"], "primary")

    def test_cross_stage_course_grade_is_validated_for_the_textbook(self):
        self.cli("init", "--stage", "junior", "--grade", "2")
        self.course()
        before = self.course_path.read_bytes()
        payload = copy.deepcopy(self.payload)
        payload["grade"] = 7
        self.course(payload, expected=2)
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_update_profile_preserves_multiple_courses_and_attempts(self):
        self.ready()
        self.record()
        second = copy.deepcopy(self.payload)
        second["source_file"] = "六年级数学.pdf"
        second["grade"] = 6
        self.cli("course", "--course", "math-6", "--input", self.write_input(second))
        paths = sorted((self.learner_dir / "courses").glob("*.json"))
        before = {path.name: path.read_bytes() for path in paths}
        result = self.cli("update-profile", "--stage", "junior", "--grade", "1")
        self.assertEqual(result["profile"], {
            "schema_version": 1, "learner_id": "student-1", "stage": "junior", "grade": 1,
        })
        for path in paths:
            self.assertEqual(path.read_bytes(), before[path.name])
        shown = self.cli("show")
        self.assertEqual(len(shown["courses"]), 2)
        self.assertEqual(shown["courses"][0]["attempts_total"], 1)
        event = copy.deepcopy(self.attempt)
        event["attempt_id"] = "after-promotion"
        self.record(event)
        self.assertEqual(len(self.stored()["attempts"]), 2)

    def test_update_profile_omitted_grade_becomes_unknown(self):
        self.initialize()
        result = self.cli("update-profile", "--stage", "primary")
        self.assertEqual(result["profile"]["stage"], "primary")
        self.assertIsNone(result["profile"]["grade"])
        self.assertIsNone(self.cli("show")["profile"]["grade"])

    def test_update_profile_requires_existing_valid_profile(self):
        self.cli("update-profile", "--stage", "junior", "--grade", "1", expected=2)
        self.assertFalse(self.root.exists())
        self.ready()
        path = self.learner_dir / "profile.json"
        path.write_text("invalid-json", encoding="utf-8")
        before = self.course_path.read_bytes()
        self.cli("update-profile", "--stage", "junior", "--grade", "1", expected=2)
        self.assertEqual(path.read_text(encoding="utf-8"), "invalid-json")
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_update_profile_invalid_grade_and_missing_stage_preserve_profile(self):
        self.initialize()
        path = self.learner_dir / "profile.json"
        before = path.read_bytes()
        self.cli("update-profile", "--stage", "junior", "--grade", "5", expected=2)
        self.cli("update-profile", "--grade", "1", expected=2)
        self.assertEqual(path.read_bytes(), before)

    def test_update_profile_existing_lock_preserves_all_files(self):
        self.ready()
        profile_path = self.learner_dir / "profile.json"
        before = profile_path.read_bytes(), self.course_path.read_bytes()
        (self.learner_dir / ".write.lock").write_text("busy", encoding="utf-8")
        self.cli("update-profile", "--stage", "junior", "--grade", "1", expected=2)
        self.assertEqual((profile_path.read_bytes(), self.course_path.read_bytes()), before)
        self.assertEqual((self.learner_dir / ".write.lock").read_text(encoding="utf-8"), "busy")

    def test_update_profile_atomic_failure_preserves_profile_and_courses(self):
        self.ready()
        self.record()
        profile_path = self.learner_dir / "profile.json"
        before = profile_path.read_bytes(), self.course_path.read_bytes()
        spec = importlib.util.spec_from_file_location("study_state_profile_test_module", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.os, "replace", side_effect=OSError("simulated disk failure")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = module.main(["update-profile", "--root", str(self.root), "--learner", "student-1",
                                    "--stage", "junior", "--grade", "1"])
        self.assertEqual(code, 2)
        self.assertEqual((profile_path.read_bytes(), self.course_path.read_bytes()), before)
        self.assertEqual(list(self.learner_dir.glob("*.tmp")), [])
        self.assertFalse((self.learner_dir / ".write.lock").exists())

    def test_false_or_empty_stage_is_not_treated_as_omitted(self):
        self.initialize()
        for value in (False, 0, "", []):
            with self.subTest(value=value):
                payload = copy.deepcopy(self.payload)
                payload["stage"] = value
                self.course(payload, expected=2)

    def test_invalid_stored_stage_is_not_silently_normalized(self):
        self.ready()
        stored = self.stored()
        stored["stage"] = None
        self.course_path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
        before = self.course_path.read_bytes()
        self.cli("show", "--course", "math-5", expected=2)
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_course_merge_keeps_other_topics_and_all_attempts(self):
        self.ready()
        self.record()
        update = copy.deepcopy(self.payload)
        update["topics"] = [{"id": "decimals", "title": "小数", "pdf_pages": [],
                             "prerequisites": ["fractions"], "read_status": "unread"}]
        self.course(update)
        stored = self.stored()
        self.assertEqual([item["id"] for item in stored["topics"]], ["fractions", "decimals"])
        self.assertEqual(stored["topics"][1]["read_status"], "unread")
        self.assertEqual(stored["attempts"][0]["attempt_id"], "attempt-1")

    def test_topic_update_only_replaces_matching_id(self):
        self.ready()
        updated = copy.deepcopy(self.payload)
        updated["topics"][0]["title"] = "分数加法（待核对）"
        updated["topics"][0]["read_status"] = "needs_confirmation"
        self.course(updated)
        self.assertEqual(len(self.stored()["topics"]), 1)
        self.assertEqual(self.stored()["topics"][0]["read_status"], "needs_confirmation")

    def test_course_identity_changes_are_rejected_without_data_loss(self):
        self.ready()
        self.record()
        before = self.course_path.read_bytes()
        for field, value in (("source_file", "另一本教材.pdf"), ("subject", "语文"),
                             ("stage", "junior")):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.payload)
                payload[field] = value
                self.course(payload, expected=2)
                self.assertEqual(self.course_path.read_bytes(), before)

    def test_source_hash_binds_same_filename_and_cannot_be_cleared(self):
        self.initialize()
        payload = copy.deepcopy(self.payload)
        payload["source_sha256"] = "a" * 64
        self.course(payload)
        self.course()
        self.assertEqual(self.stored()["source_sha256"], "a" * 64)
        payload["source_sha256"] = None
        self.course(payload)
        self.assertEqual(self.stored()["source_sha256"], "a" * 64)
        before = self.course_path.read_bytes()
        payload["source_sha256"] = "b" * 64
        self.course(payload, expected=2)
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_source_hash_validation_rejects_malformed_values(self):
        self.initialize()
        for value in ("a" * 63, "A" * 64, "z" * 64, 123):
            with self.subTest(value=value):
                payload = copy.deepcopy(self.payload)
                payload["source_sha256"] = value
                self.course(payload, expected=2)

    def test_verified_topic_requires_valid_pdf_pages(self):
        self.initialize()
        for pages in ([], [0], [-1], [True], ["43"], [43, 43]):
            with self.subTest(pages=pages):
                payload = copy.deepcopy(self.payload)
                payload["topics"][0]["pdf_pages"] = pages
                self.course(payload, expected=2)
                self.assertFalse(self.course_path.exists())

    def test_topic_fields_and_read_status_are_validated(self):
        self.initialize()
        for change in ({"read_status": "mastered"}, {"prerequisites": ["../escape"]},
                       {"title": ""}, {"read_status": None}):
            with self.subTest(change=change):
                payload = copy.deepcopy(self.payload)
                payload["topics"][0].update(change)
                self.course(payload, expected=2)

    def test_duplicate_topic_ids_are_rejected(self):
        self.initialize()
        payload = copy.deepcopy(self.payload)
        payload["topics"].append(copy.deepcopy(payload["topics"][0]))
        self.course(payload, expected=2)

    def test_record_append_preserves_assistance_and_has_no_mastery_inference(self):
        self.ready()
        self.record()
        event = copy.deepcopy(self.attempt)
        event["attempt_id"] = "attempt-2"
        event["assistance"] = "independent"
        self.record(event)
        stored = self.stored()
        self.assertEqual([e["assistance"] for e in stored["attempts"]], ["hinted", "independent"])
        self.assertNotIn("mastery", stored)
        self.assertNotIn("mastery", stored["topics"][0])

    def test_duplicate_attempt_is_idempotent_but_conflict_is_rejected(self):
        self.ready()
        self.record()
        before = self.course_path.read_bytes()
        self.record()
        self.assertEqual(self.course_path.read_bytes(), before)
        changed = copy.deepcopy(self.attempt)
        changed["student_answer"] = "6/5"
        self.record(changed, expected=2)
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_record_optional_fields_normalize_and_empty_student_answer_is_allowed(self):
        self.ready()
        event = {k: self.attempt[k] for k in ("attempt_id", "topic_id", "question", "outcome", "assistance")}
        event["student_answer"] = ""
        event["outcome"] = "not_evaluated"
        event["assistance"] = "unresolved"
        self.record(event)
        stored = self.stored()["attempts"][0]
        self.assertEqual(stored["reasoning"], "")
        self.assertIsNone(stored["review_on"])

    def test_record_unknown_topic_is_rejected(self):
        self.ready()
        event = copy.deepcopy(self.attempt)
        event["topic_id"] = "missing"
        self.record(event, expected=2)
        self.assertEqual(self.stored()["attempts"], [])

    def test_record_enums_and_review_date_are_validated(self):
        self.ready()
        for field, value in (("outcome", "mastered"), ("assistance", "none"),
                             ("review_on", "2026-02-30"), ("review_on", "20261006")):
            with self.subTest(field=field, value=value):
                event = copy.deepcopy(self.attempt)
                event[field] = value
                self.record(event, expected=2)
        self.assertEqual(self.stored()["attempts"], [])

    def test_ids_reject_path_traversal_unicode_and_windows_device_names(self):
        self.initialize()
        for identifier in ("../outside", "汉字", "bad.id", "CON", "a" * 65):
            with self.subTest(identifier=identifier):
                self.cli("show", "--learner", identifier, expected=2)
                self.cli("course", "--course", identifier, "--input",
                         self.write_input(self.payload), expected=2)
                payload = copy.deepcopy(self.payload)
                payload["topics"][0]["id"] = identifier
                self.course(payload, expected=2)
        self.course()
        event = copy.deepcopy(self.attempt)
        event["attempt_id"] = "../outside"
        self.record(event, expected=2)

    def test_unknown_fields_are_rejected_without_echoing_values(self):
        self.ready()
        secret = "do-not-echo-sensitive-value"
        for kind, original in (("course", self.payload), ("record", self.attempt)):
            with self.subTest(kind=kind):
                payload = copy.deepcopy(original)
                payload["real_name"] = secret
                result = self.course(payload, expected=2) if kind == "course" else self.record(payload, expected=2)
                self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(self.stored()["attempts"], [])

    def test_input_size_and_field_length_limits_preserve_state(self):
        self.ready()
        before = self.course_path.read_bytes()
        payload = copy.deepcopy(self.attempt)
        payload["question"] = "题" * 8001
        self.record(payload, expected=2)
        oversized = self.base / "huge.json"
        oversized.write_text(" " * (256 * 1024 + 1), encoding="utf-8")
        self.cli("record", "--course", "math-5", "--input", oversized, expected=2)
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_very_large_json_integer_returns_json_error_without_traceback(self):
        self.ready()
        before = self.course_path.read_bytes()
        input_path = self.base / "integer.json"
        input_path.write_text('{"source_file":"a.pdf","subject":"math","grade":' +
                              "9" * 5000 + ',"topics":[]}', encoding="utf-8")
        self.cli("course", "--course", "math-5", "--input", input_path, expected=2)
        self.assertEqual(self.course_path.read_bytes(), before)

    def test_bad_json_duplicate_keys_and_unknown_schema_never_reset_state(self):
        self.ready()
        for raw in ("{broken", '{"schema_version":1,"schema_version":2}',
                    '{"schema_version":999}', '{"schema_version":NaN}'):
            with self.subTest(raw=raw):
                self.course_path.write_text(raw, encoding="utf-8")
                before = self.course_path.read_bytes()
                self.cli("show", "--course", "math-5", expected=2)
                self.course(expected=2)
                self.assertEqual(self.course_path.read_bytes(), before)

    def test_corrupt_profile_cannot_be_silently_reinitialized(self):
        self.initialize()
        path = self.learner_dir / "profile.json"
        path.write_text("not-json", encoding="utf-8")
        self.initialize_error = self.cli("init", "--stage", "primary", "--grade", "5", expected=2)
        self.assertEqual(path.read_text(encoding="utf-8"), "not-json")

    def test_show_defaults_to_recent_ten_without_deleting_history(self):
        self.ready()
        for n in range(12):
            event = copy.deepcopy(self.attempt)
            event["attempt_id"] = "attempt-" + str(n)
            self.record(event)
        data = self.cli("show", "--course", "math-5")
        course = data["courses"][0]
        self.assertEqual(course["attempts_total"], 12)
        self.assertEqual(len(course["attempts"]), 10)
        self.assertEqual(course["attempts"][0]["attempt_id"], "attempt-2")
        self.assertEqual(len(self.stored()["attempts"]), 12)
        short = self.cli("show", "--limit", "1")
        self.assertEqual(short["courses"][0]["attempts"][0]["attempt_id"], "attempt-11")
        for limit in (0, 101):
            self.cli("show", "--limit", limit, expected=2)

    def test_missing_learner_or_course_errors_without_creating_storage(self):
        self.cli("show", expected=2)
        self.assertFalse(self.root.exists())
        self.initialize()
        self.cli("show", "--course", "missing", expected=2)

    def test_existing_write_lock_rejects_concurrent_mutation(self):
        self.ready()
        before = self.course_path.read_bytes()
        (self.learner_dir / ".write.lock").write_text("busy", encoding="utf-8")
        self.record(expected=2)
        self.assertEqual(self.course_path.read_bytes(), before)
        self.assertEqual((self.learner_dir / ".write.lock").read_text(encoding="utf-8"), "busy")

    def test_atomic_replace_failure_keeps_previous_json_and_cleans_temp_file(self):
        self.ready()
        before = self.course_path.read_bytes()
        spec = importlib.util.spec_from_file_location("study_state_test_module", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        input_path = self.write_input(self.attempt)
        with patch.object(module.os, "replace", side_effect=OSError("simulated disk failure")):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = module.main(["record", "--root", str(self.root), "--learner", "student-1",
                                    "--course", "math-5", "--input", str(input_path)])
        self.assertEqual(code, 2)
        self.assertEqual(self.course_path.read_bytes(), before)
        self.assertEqual(list(self.course_path.parent.glob("*.tmp")), [])
        self.assertFalse((self.learner_dir / ".write.lock").exists())


if __name__ == "__main__":
    unittest.main()
