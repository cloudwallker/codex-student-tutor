#!/usr/bin/env python3
"""Local textbook indexes and append-only learning evidence, using only stdlib.

Every command requires explicit --root and --learner after the subcommand.
The helper neither reads/modifies textbooks nor infers knowledge mastery.
"""

import argparse
import contextlib
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path


SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 256 * 1024
MAX_STATE_BYTES = 8 * 1024 * 1024
STAGES = {"primary", "junior", "senior"}
READ_STATUSES = {"verified", "needs_confirmation", "unread"}
OUTCOMES = {"correct", "partially_correct", "incorrect", "not_evaluated"}
ASSISTANCE = {"independent", "hinted", "explained", "unresolved"}
COURSE_FIELDS = {"source_file", "source_sha256", "subject", "stage", "grade",
                 "edition", "volume", "topics"}
TOPIC_FIELDS = {"id", "title", "pdf_pages", "prerequisites", "read_status"}
EVENT_FIELDS = {"attempt_id", "topic_id", "question", "student_answer", "reasoning",
                "outcome", "assistance", "feedback", "error_cause", "evidence", "review_on"}
EVENT_REQUIRED = {"attempt_id", "topic_id", "question", "student_answer", "outcome", "assistance"}
RESERVED_IDS = {"CON", "PRN", "AUX", "NUL"} | {
    prefix + str(n) for prefix in ("COM", "LPT") for n in range(1, 10)
}


class StateError(ValueError):
    """An input or storage problem; messages must not echo submitted values."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise StateError("命令行参数无效；请使用 --help 检查必需参数及可用选项。")


def identifier(value, field):
    if (not isinstance(value, str) or
            re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None or
            value.upper() in RESERVED_IDS):
        raise StateError(field + " 必须是1-64位ASCII字母、数字、下划线或连字符，且不能是设备保留名。")
    return value


def fields(value, allowed, required, label):
    if not isinstance(value, dict):
        raise StateError(label + " 必须是JSON对象。")
    if set(value) - allowed:
        raise StateError(label + " 含不允许的字段；请只提交规定的学习字段。")
    if required - set(value):
        raise StateError(label + " 缺少必需字段。")


def text_value(value, field, maximum, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise StateError(field + " 必须是规定长度内的字符串。")
    return value


def enum_value(value, choices, field):
    if not isinstance(value, str) or value not in choices:
        raise StateError(field + " 不属于允许的枚举值。")
    return value


def grade_value(value, stage):
    if value is not None and (type(value) is not int or not 1 <= value <= (6 if stage == "primary" else 3)):
        raise StateError("grade 使用学段内年级：小学1-6，初中和高中1-3；也可省略或为null。")
    return value


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StateError("JSON包含重复字段，无法确定有效内容。")
        result[key] = value
    return result


def invalid_constant(value):
    raise StateError("JSON不能包含NaN或Infinity。")


def read_json(path, limit, label):
    if not path.is_file():
        raise StateError(label + " 文件不存在或不是普通文件。")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise StateError(label + " 超出允许的文件体积。")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs,
                          parse_constant=invalid_constant)
    except StateError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise StateError(label + " 不是有效UTF-8 JSON；已有档案未被重置。") from None


def topics_value(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise StateError("topics 必须是至多1000项的数组。")
    result, ids = [], set()
    for item in value:
        fields(item, TOPIC_FIELDS, TOPIC_FIELDS, "topic")
        topic_id = identifier(item["id"], "topic.id")
        if topic_id in ids:
            raise StateError("一次提交不能包含重复topic ID。")
        ids.add(topic_id)
        title = text_value(item["title"], "topic.title", 120)
        status = enum_value(item["read_status"], READ_STATUSES, "read_status")
        pages = item["pdf_pages"]
        if (not isinstance(pages, list) or len(pages) > 1000 or
                any(type(page) is not int or page < 1 or page > 1000000 for page in pages) or
                len(set(pages)) != len(pages)):
            raise StateError("pdf_pages 必须是不重复的正整数页码数组，至多1000项。")
        if status != "unread" and not pages:
            raise StateError("已读或待核对的topic必须提供PDF页码；未读topic可以使用空数组。")
        prerequisites = item["prerequisites"]
        if not isinstance(prerequisites, list) or len(prerequisites) > 64:
            raise StateError("prerequisites 必须是至多64项的topic ID数组。")
        prerequisites = [identifier(value, "prerequisite") for value in prerequisites]
        if len(set(prerequisites)) != len(prerequisites):
            raise StateError("prerequisites 不能包含重复ID。")
        result.append({"id": topic_id, "title": title, "pdf_pages": list(pages),
                       "prerequisites": prerequisites, "read_status": status})
    return result


def course_input(value, profile, previous=None):
    fields(value, COURSE_FIELDS, {"source_file", "subject", "topics"}, "course")
    source_file = text_value(value["source_file"], "source_file", 512)
    subject = text_value(value["subject"], "subject", 40)
    old = previous or {}
    submitted_stage = value.get("stage")
    default_stage = old.get("stage", profile["stage"])
    stage = enum_value(default_stage if submitted_stage is None else submitted_stage, STAGES, "stage")
    if previous and (source_file != previous["source_file"] or subject != previous["subject"]
                     or stage != previous["stage"]):
        raise StateError("已有课程的教材来源、学科或学段不能替换；请创建新的course ID。")
    default_grade = old.get("grade") if previous is not None else (
        profile["grade"] if stage == profile["stage"] else None
    )
    grade = grade_value(value.get("grade", default_grade), stage)
    source_hash = value.get("source_sha256")
    if source_hash is not None and (not isinstance(source_hash, str) or
                                   re.fullmatch(r"[0-9a-f]{64}", source_hash) is None):
        raise StateError("source_sha256 必须是64位小写十六进制字符串或null。")
    old_hash = old.get("source_sha256")
    if old_hash and source_hash and old_hash != source_hash:
        raise StateError("同名教材的文件哈希发生变化；请创建新的course ID，避免混用旧页码和作答。")
    metadata = {"source_file": source_file, "source_sha256": source_hash or old_hash,
                "subject": subject, "stage": stage, "grade": grade}
    for key in ("edition", "volume"):
        item = value.get(key, old.get(key))
        metadata[key] = None if item is None else text_value(item, key, 100)
    metadata["topics"] = topics_value(value["topics"])
    return metadata


def event_input(value, stored=False):
    fields(value, EVENT_FIELDS, EVENT_FIELDS if stored else EVENT_REQUIRED, "record")
    result = {
        "attempt_id": identifier(value["attempt_id"], "attempt_id"),
        "topic_id": identifier(value["topic_id"], "topic_id"),
        "question": text_value(value["question"], "question", 8000),
        "student_answer": text_value(value["student_answer"], "student_answer", 8000, empty=True),
        "outcome": enum_value(value["outcome"], OUTCOMES, "outcome"),
        "assistance": enum_value(value["assistance"], ASSISTANCE, "assistance"),
    }
    for key, maximum in (("reasoning", 8000), ("feedback", 4000),
                         ("error_cause", 1000), ("evidence", 4000)):
        result[key] = text_value(value.get(key, ""), key, maximum, empty=True)
    review_on = value.get("review_on")
    if review_on is not None:
        if not isinstance(review_on, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", review_on) is None:
            raise StateError("review_on 必须是有效YYYY-MM-DD日期或null。")
        try:
            datetime.date.fromisoformat(review_on)
        except ValueError:
            raise StateError("review_on 日期无效。") from None
    result["review_on"] = review_on
    return result


class Store:
    def __init__(self, root, learner):
        self.root = Path(root).expanduser().resolve()
        self.learner = identifier(learner, "learner")
        self.learner_dir = self.safe_path(self.root / "learners" / self.learner)
        self.profile_path = self.safe_path(self.learner_dir / "profile.json")
        self.course_dir = self.safe_path(self.learner_dir / "courses")

    def safe_path(self, path):
        try:
            path.resolve().relative_to(self.root)
        except ValueError:
            raise StateError("档案路径超出指定root；拒绝访问。") from None
        if path.is_symlink():
            raise StateError("学习档案文件或目录不能使用符号链接。")
        return path

    def course_path(self, course):
        return self.safe_path(self.course_dir / (identifier(course, "course") + ".json"))

    @contextlib.contextmanager
    def write_lock(self, create=False):
        if create:
            self.learner_dir.mkdir(parents=True, exist_ok=True)
        elif not self.learner_dir.is_dir():
            raise StateError("学习档案不存在；请先执行init。")
        lock_path = self.safe_path(self.learner_dir / ".write.lock")
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise StateError("学习档案正在写入或存在未清理的.write.lock；确认没有写入进程后再重试。") from None
        try:
            os.close(fd)
            yield
        finally:
            lock_path.unlink()

    def write_json(self, path, data):
        self.safe_path(path)
        raw = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_STATE_BYTES:
            raise StateError("档案超过8MiB上限；本次未写入，已有历史保持完整。")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="." + path.stem + "-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, str(path))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def profile(self):
        value = read_json(self.profile_path, MAX_STATE_BYTES, "学习档案")
        allowed = {"schema_version", "learner_id", "stage", "grade"}
        fields(value, allowed, allowed, "已有学习档案")
        if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
            raise StateError("不支持的学习档案schema_version；已有文件未被改写。")
        if identifier(value["learner_id"], "learner_id") != self.learner:
            raise StateError("已有学习档案的learner ID与路径不匹配。")
        stage = enum_value(value["stage"], STAGES, "stage")
        grade_value(value["grade"], stage)
        return value

    def course(self, course_id, profile):
        value = read_json(self.course_path(course_id), MAX_STATE_BYTES, "课程档案")
        allowed = COURSE_FIELDS | {"schema_version", "course_id", "attempts"}
        fields(value, allowed, allowed, "已有课程档案")
        if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
            raise StateError("不支持的课程档案schema_version；已有文件未被改写。")
        if identifier(value["course_id"], "course_id") != course_id:
            raise StateError("课程档案的course ID与路径不匹配。")
        enum_value(value["stage"], STAGES, "已有课程stage")
        normalized = course_input({key: value[key] for key in COURSE_FIELDS}, profile)
        attempts = value["attempts"]
        if not isinstance(attempts, list):
            raise StateError("已有课程的attempts必须是数组；已有文件未被改写。")
        ids, topic_ids = set(), {topic["id"] for topic in normalized["topics"]}
        for attempt in attempts:
            event = event_input(attempt, stored=True)
            if event["attempt_id"] in ids or event["topic_id"] not in topic_ids:
                raise StateError("已有作答的ID重复或topic不存在；已有文件未被改写。")
            ids.add(event["attempt_id"])
        return value

    def initialize(self, stage, grade):
        enum_value(stage, STAGES, "stage")
        grade_value(grade, stage)
        with self.write_lock(create=True):
            if self.profile_path.exists():
                profile = self.profile()
                if profile["stage"] != stage or (grade is not None and profile["grade"] != grade):
                    raise StateError("已有学习档案的学段或年级不同；请用update-profile显式更新。")
                status = "unchanged"
            else:
                if self.course_dir.exists() and any(self.course_dir.iterdir()):
                    raise StateError("已存在课程但缺失学习档案；请检查文件，不能直接重置。")
                profile = {"schema_version": SCHEMA_VERSION, "learner_id": self.learner,
                           "stage": stage, "grade": grade}
                self.write_json(self.profile_path, profile)
                status = "created"
        return {"schema_version": SCHEMA_VERSION, "status": status, "profile": profile}

    def update_profile(self, stage, grade):
        enum_value(stage, STAGES, "stage")
        grade_value(grade, stage)
        with self.write_lock():
            previous = self.profile()
            profile = {**previous, "stage": stage, "grade": grade}
            status = "unchanged" if profile == previous else "updated"
            if profile != previous:
                self.write_json(self.profile_path, profile)
        return {"schema_version": SCHEMA_VERSION, "status": status, "profile": profile}

    def merge_course(self, course_id, payload):
        path = self.course_path(course_id)
        with self.write_lock():
            profile = self.profile()
            previous = self.course(course_id, profile) if path.exists() else None
            metadata = course_input(payload, profile, previous)
            old_topics = {topic["id"]: topic for topic in (previous or {}).get("topics", [])}
            old_topics.update({topic["id"]: topic for topic in metadata.pop("topics")})
            if len(old_topics) > 1000:
                raise StateError("合并后的课程超过1000个topic；已有索引未被改写。")
            result = {"schema_version": SCHEMA_VERSION, "course_id": course_id, **metadata,
                      "topics": list(old_topics.values()), "attempts": (previous or {}).get("attempts", [])}
            status = "created" if previous is None else "updated"
            if result == previous:
                status = "unchanged"
            else:
                self.write_json(path, result)
        return self.summary(course_id, result, status)

    def append_record(self, course_id, payload):
        event = event_input(payload)
        with self.write_lock():
            profile = self.profile()
            course = self.course(course_id, profile)
            if event["topic_id"] not in {topic["id"] for topic in course["topics"]}:
                raise StateError("作答topic尚未建立索引；请先执行course。")
            existing = next((item for item in course["attempts"]
                             if item["attempt_id"] == event["attempt_id"]), None)
            if existing is not None:
                if existing != event:
                    raise StateError("attempt_id已存在且内容不同；旧作答不能被覆盖。")
                status = "unchanged"
            else:
                course["attempts"].append(event)
                self.write_json(self.course_path(course_id), course)
                status = "appended"
        return self.summary(course_id, course, status)

    def summary(self, course_id, course, status):
        return {"schema_version": SCHEMA_VERSION, "status": status, "learner": self.learner,
                "course": course_id, "topics_count": len(course["topics"]),
                "attempts_count": len(course["attempts"])}

    def show(self, course_id, limit):
        if not 1 <= limit <= 100:
            raise StateError("limit必须是1-100之间的整数。")
        profile = self.profile()
        ids = [identifier(course_id, "course")] if course_id is not None else [
            identifier(path.stem, "course") for path in sorted(self.course_dir.glob("*.json"))
        ]
        courses = []
        for current in ids:
            course = self.course(current, profile)
            courses.append({**course, "attempts_total": len(course["attempts"]),
                            "attempts": course["attempts"][-limit:]})
        return {"schema_version": SCHEMA_VERSION, "profile": profile, "courses": courses}


def parser():
    result = JsonArgumentParser(description="本地教材索引与学习证据档案；不自动推断掌握程度。")
    commands = result.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)
    for command in ("init", "update-profile", "course", "record", "show"):
        sub = commands.add_parser(command)
        sub.add_argument("--root", required=True, help="显式指定学习数据目录")
        sub.add_argument("--learner", required=True, help="昵称ID，仅ASCII字母数字_-，1-64位")
        if command in ("init", "update-profile"):
            sub.add_argument("--stage", required=True, choices=sorted(STAGES))
            sub.add_argument("--grade", type=int, help="学段内年级：小学1-6，初高中1-3")
        elif command in ("course", "record"):
            sub.add_argument("--course", required=True)
            sub.add_argument("--input", required=True, help="UTF-8 JSON文件，至多256KiB")
        else:
            sub.add_argument("--course")
            sub.add_argument("--limit", type=int, default=10, help="返回最近1-100条作答，默认10")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        store = Store(args.root, args.learner)
        if args.command == "init":
            result = store.initialize(args.stage, args.grade)
        elif args.command == "update-profile":
            result = store.update_profile(args.stage, args.grade)
        elif args.command in ("course", "record"):
            identifier(args.course, "course")
            data = read_json(Path(args.input), MAX_INPUT_BYTES, "输入")
            operation = store.merge_course if args.command == "course" else store.append_record
            result = operation(args.course, data)
        else:
            result = store.show(args.course, args.limit)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except StateError as error:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, RecursionError):
        print(json.dumps({"schema_version": SCHEMA_VERSION,
                          "error": "读写学习档案失败；请检查路径、权限和文件。已有档案未被主动清空。"},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
