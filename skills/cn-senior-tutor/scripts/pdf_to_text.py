#!/usr/bin/env python3
"""Read an existing PDF text layer into bounded, page-labelled stdout.

This helper writes no files, creates no index/profile, and makes no network calls.
Images and scanned text are deliberately outside the v1 scope.

API: extract_pdf(path, pages=None, max_pages=12, text_offset=0) returns total_pages,
pages ({page, text, text_offset, total_text_chars, next_offset, warnings}), and
global warnings. Page numbers are one-based; text offsets are zero-based.
"""
import argparse
from pathlib import Path
import re
import sys

# Prevent import caches from becoming an unintended persistent output.
sys.dont_write_bytecode = True

DEFAULT_MAX_PAGES = 12
MAX_CHARS_PER_PAGE = 6000
MAX_TOTAL_CHARS = 40000
SPARSE_TEXT_THRESHOLD = 30


class PDFToolError(ValueError):
    """A readable input/dependency error suitable for the CLI."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PDFToolError(message)


def select_pages(spec, total_pages, max_pages=DEFAULT_MAX_PAGES):
    """Return unique, sorted, one-based page numbers; reject invalid ranges."""
    if max_pages <= 0:
        raise PDFToolError("--max-pages 必须是大于 0 的整数。")
    if total_pages <= 0:
        raise PDFToolError("PDF 没有可读取的页面。")
    if spec is None:
        return list(range(1, min(total_pages, max_pages) + 1))
    if not spec.strip():
        raise PDFToolError("--pages 不能为空；示例：1-3,5。")
    selected = set()
    for part in spec.split(","):
        match = re.fullmatch(r"([0-9]+)(?:\s*-\s*([0-9]+))?", part.strip())
        if match is None:
            raise PDFToolError("页码范围无效；请使用 1-3,5 这样的格式。")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise PDFToolError("页码必须从 1 开始，范围终点不能小于起点。")
        if end > total_pages:
            raise PDFToolError("页码超出 PDF 范围；本文件共 %d 页。" % total_pages)
        # Bound each range before building a potentially huge Python range/set.
        if end - start + 1 > max_pages:
            raise PDFToolError("选定页面超过 --max-pages；请分批读取或明确调整上限。")
        selected.update(range(start, end + 1))
        if len(selected) > max_pages:
            raise PDFToolError("选定页面超过 --max-pages；请分批读取或明确调整上限。")
    return sorted(selected)


def _validate_pdf_path(path):
    source = Path(path).expanduser()
    if not source.exists():
        raise PDFToolError("PDF 文件不存在。")
    if not source.is_file():
        raise PDFToolError("输入必须是一个 PDF 文件，不能是目录。")
    if source.suffix.lower() != ".pdf":
        raise PDFToolError("仅接受 .pdf 文件；其他文件不能当作 PDF 文字读取。")
    try:
        with source.open("rb") as stream:
            header = stream.read(1024)
    except OSError as exc:
        raise PDFToolError("无法打开 PDF 文件，请检查读取权限。") from exc
    if not header:
        raise PDFToolError("PDF 文件为空。")
    if b"%PDF-" not in header:
        raise PDFToolError("文件没有有效的 PDF 标识，不能当作教材 PDF 读取。")
    return source


def extract_pdf(path, *, pages=None, max_pages=DEFAULT_MAX_PAGES, text_offset=0):
    """Extract bounded existing text, preserving actual PDF page numbers.

    Missing/sparse text and truncation are explicitly recorded as warnings.
    The function reads only; it never creates a file or starts a subprocess.
    """
    if max_pages <= 0:
        raise PDFToolError("--max-pages 必须是大于 0 的整数。")
    if type(text_offset) is not int or text_offset < 0:
        raise PDFToolError("--text-offset 必须是从 0 开始的非负整数。")
    source = _validate_pdf_path(path)
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PDFToolError(
            "缺少 pypdf。请自行在当前 Python 环境安装本脚本同目录 requirements.txt 中的依赖；工具不会自动安装。"
        ) from exc
    warnings = []
    result_pages = []
    total_chars = 0
    try:
        with source.open("rb") as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise PDFToolError("PDF 已加密；请提供可正常读取的未加密版本。")
            total_pages = len(reader.pages)
            selected = select_pages(pages, total_pages, max_pages)
            if text_offset and len(selected) != 1:
                raise PDFToolError("非零 --text-offset 只用于单页续读；请用 --pages 指定一个页码。")
            if pages is None and total_pages > max_pages:
                warnings.append(
                    "本文件共 %d 页，本次仅读取前 %d 页；用 --pages 指定后续页码。" % (total_pages, max_pages)
                )
            for position, page_number in enumerate(selected):
                page_warnings = []
                try:
                    text = reader.pages[page_number - 1].extract_text() or ""
                    text = text.replace("\x00", "").strip()
                except Exception:
                    text = ""
                    page_warnings.append("本页文字层提取失败；请对照原页补充可复制文字。")
                count = sum(not char.isspace() for char in text)
                if not count:
                    page_warnings.append(
                        "本页没有可提取文字，可能为空白页、扫描页或图片页；请补充可复制文字。"
                    )
                elif count < SPARSE_TEXT_THRESHOLD:
                    page_warnings.append(
                        "本页提取的文字可能不足，不能据此认定已读取完整教材内容；如有缺失请补充文字。"
                    )
                full_length = len(text)
                if full_length and text_offset >= full_length:
                    page_warnings.append("文字偏移已达到或超过本页文字末尾，本次没有剩余文字。")
                available = MAX_TOTAL_CHARS - total_chars
                text = text[text_offset:text_offset + min(MAX_CHARS_PER_PAGE, available)]
                next_offset = text_offset + len(text) if text_offset + len(text) < full_length else None
                if next_offset is not None:
                    page_warnings.append(
                        "本页文字输出已截断，尚有未输出内容；续读用 --pages %d --text-offset %d。"
                        % (page_number, next_offset)
                    )
                result_pages.append({"page": page_number, "text": text, "text_offset": text_offset,
                                     "total_text_chars": full_length, "next_offset": next_offset,
                                     "warnings": page_warnings})
                total_chars += len(text)
                if total_chars >= MAX_TOTAL_CHARS:
                    remaining = len(selected) - position - 1
                    if remaining or next_offset is not None:
                        warnings.append(
                            "本次文字达到 %d 字符上限，输出已截断；另有 %d 个选定页面未输出，请分批读取。"
                            % (MAX_TOTAL_CHARS, remaining)
                        )
                    break
    except PDFToolError:
        raise
    except Exception as exc:
        raise PDFToolError("无法解析 PDF；请检查文件是否损坏或使用有效的 PDF 版本。") from exc
    return {"total_pages": total_pages, "pages": result_pages, "warnings": warnings}


def main(argv=None, *, stdout=None, stderr=None):
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    parser = _Parser(description="按实际 PDF 页码读取现有文字层，结果只输出到终端。")
    parser.add_argument("pdf", help="本地 PDF 文件路径")
    parser.add_argument("--pages", help="1 基页码，例如 1-3,5；未指定时读取前 max-pages 页")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="本次最多读取页数，默认 12")
    parser.add_argument("--text-offset", type=int, default=0, help="单页续读的字符偏移，从0开始；按截断提示取下一段")
    try:
        args = parser.parse_args(argv)
        report = extract_pdf(args.pdf, pages=args.pages, max_pages=args.max_pages, text_offset=args.text_offset)
    except PDFToolError as exc:
        print("错误：%s" % exc, file=stderr)
        return 2
    print(
        "[读取说明] 以下是 PDF 现有文字层；图片与几何图形未识别，公式、表格和版面顺序请对照原页。",
        file=stdout,
    )
    for warning in report["warnings"]:
        print("[警示] %s" % warning, file=stdout)
    for page in report["pages"]:
        print("\n## PDF 第 %d 页" % page["page"], file=stdout)
        print("[文字片段] 偏移 %d—%d；本页共 %d 字符。" % (
            page["text_offset"], page["text_offset"] + len(page["text"]), page["total_text_chars"]), file=stdout)
        for warning in page["warnings"]:
            print("[警示] %s" % warning, file=stdout)
        print(page["text"] or "（本次没有文字输出，请查看偏移与警示）", file=stdout)
    return 0


if __name__ == "__main__":
    for output_stream in (sys.stdout, sys.stderr):
        if hasattr(output_stream, "reconfigure"):
            output_stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
