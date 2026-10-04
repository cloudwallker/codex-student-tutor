"""Observable PDF CLI checks; all fixtures are tiny, synthetic PDFs."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "pdf_to_text.py"


def make_pdf(path, texts):
    """Build a valid text PDF using only the standard library."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Count %d /Kids [%s] >>" % (
            len(texts), " ".join("%d 0 R" % (4 + 2 * i) for i in range(len(texts)))
        )).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(texts):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = ("BT /F1 12 Tf 72 720 Td (%s) Tj ET" % escaped).encode("ascii")
        objects.extend([
            ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
             "/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (5 + 2 * i)).encode("ascii"),
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        ])
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(("%d 0 obj\n" % number).encode("ascii") + obj + b"\nendobj\n")
    startxref = len(result)
    result.extend(("xref\n0 %d\n0000000000 65535 f \n" % len(offsets)).encode("ascii"))
    for offset in offsets[1:]:
        result.extend(("%010d 00000 n \n" % offset).encode("ascii"))
    result.extend(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(offsets), startxref)).encode("ascii"))
    path.write_bytes(result)


class PDFToolTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL_PATH.is_file(), "PDF CLI implementation is not present yet")
        spec = importlib.util.spec_from_file_location("pdf_to_text_under_test", TOOL_PATH)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.pdf = self.directory / "lesson sample.pdf"
        make_pdf(self.pdf, ["PAGE ONE TEXT", "PAGE TWO TEXT", "PAGE THREE TEXT"])

    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = self.tool.main([str(self.pdf), *args], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_ranges_preserve_real_page_numbers(self):
        code, out, err = self.run_tool("--pages", "3,1")
        self.assertEqual(code, 0, err)
        self.assertIn("第 1 页", out)
        self.assertIn("第 3 页", out)
        self.assertIn("PAGE THREE TEXT", out)
        self.assertNotIn("PAGE TWO TEXT", out)

    def test_text_layer_is_extracted(self):
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("PAGE TWO TEXT", out)

    def test_real_unicode_pdf_text_layer_is_extracted(self):
        text = "数学五年级：异分母分数相加，先通分。"
        cmap = b"""/CIDInit /ProcSet findresource begin
12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Test-UCS def /CMapType 2 def
1 begincodespacerange <0000> <FFFF> endcodespacerange
1 beginbfrange <0000> <FFFF> <0000> endbfrange
endcmap CMapName currentdict /CMap defineresource pop end end"""
        content = b"BT /F1 12 Tf 72 720 Td <" + text.encode("utf-16-be").hex().encode("ascii") + b"> Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>",
            b"<< /Type /Font /Subtype /Type0 /BaseFont /TestCJK /Encoding /Identity-H /DescendantFonts [5 0 R] /ToUnicode 6 0 R >>",
            b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /TestCJK /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>",
            b"<< /Length " + str(len(cmap)).encode("ascii") + b" >>\nstream\n" + cmap + b"\nendstream",
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        ]
        result = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for number, obj in enumerate(objects, 1):
            offsets.append(len(result))
            result.extend(("%d 0 obj\n" % number).encode("ascii") + obj + b"\nendobj\n")
        startxref = len(result)
        result.extend(("xref\n0 %d\n0000000000 65535 f \n" % len(offsets)).encode("ascii"))
        for offset in offsets[1:]:
            result.extend(("%010d 00000 n \n" % offset).encode("ascii"))
        result.extend(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets), startxref)).encode("ascii"))
        self.pdf.write_bytes(result)
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn(text, out)

    def test_default_page_limit_is_visible(self):
        make_pdf(self.pdf, ["PAGE %d" % i for i in range(1, 15)])
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("第 12 页", out)
        self.assertNotIn("第 13 页", out)
        self.assertIn("12", out)
        self.assertIn("警示", out)

    def test_bad_and_empty_ranges_fail_before_output(self):
        for spec in ["", " ", "0", "-1", "3-1", "1,", "1,,2", "a", "1-2-3", "1.5", "4", "1-999999999"]:
            with self.subTest(spec=spec):
                code, out, err = self.run_tool("--pages", spec)
                self.assertNotEqual(code, 0)
                self.assertFalse(out)
                self.assertTrue(err)

    def test_explicit_selection_over_max_pages_is_rejected(self):
        code, out, err = self.run_tool("--pages", "1-3", "--max-pages", "2")
        self.assertNotEqual(code, 0)
        self.assertFalse(out)
        self.assertIn("max-pages", err)

    def test_nonpositive_max_pages_is_rejected(self):
        for value in ["0", "-1"]:
            code, out, err = self.run_tool("--max-pages", value)
            self.assertNotEqual(code, 0)
            self.assertFalse(out)
            self.assertTrue(err)

    def test_missing_file_is_rejected(self):
        self.pdf.unlink()
        code, out, err = self.run_tool()
        self.assertNotEqual(code, 0)
        self.assertFalse(out)
        self.assertIn("不存在", err)

    def test_existing_nonpdf_and_fake_pdf_are_rejected(self):
        original = self.pdf.read_bytes()
        for content in [b"", b"plain text, not a PDF", b"PNG"]:
            self.pdf.write_bytes(content)
            code, out, err = self.run_tool()
            self.assertNotEqual(code, 0)
            self.assertFalse(out)
            self.assertTrue(err)
        other = self.directory / "lesson.txt"
        other.write_bytes(original)
        out, err = io.StringIO(), io.StringIO()
        self.assertNotEqual(self.tool.main([str(other)], stdout=out, stderr=err), 0)
        self.assertFalse(out.getvalue())

    def test_empty_document_is_rejected(self):
        make_pdf(self.pdf, [])
        code, out, err = self.run_tool()
        self.assertNotEqual(code, 0)
        self.assertFalse(out)
        self.assertIn("页", err)

    def test_page_without_text_requests_student_transcription(self):
        make_pdf(self.pdf, [""])
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("第 1 页", out)
        self.assertIn("警示", out)
        self.assertIn("补充", out)

    def test_sparse_text_page_is_not_claimed_complete(self):
        make_pdf(self.pdf, ["7"])
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("警示", out)
        self.assertIn("不足", out)

    def test_missing_text_dependency_is_clear(self):
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            code, out, err = self.run_tool()
        self.assertNotEqual(code, 0)
        self.assertFalse(out)
        self.assertIn("pypdf", err)

    def test_chinese_text_is_not_lost(self):
        pages = [SimpleNamespace(extract_text=lambda: "中文教材内容：先尝试，再解释。")]
        reader = SimpleNamespace(pages=pages, is_encrypted=False)
        with mock.patch.dict(sys.modules, {"pypdf": SimpleNamespace(PdfReader=lambda _: reader)}):
            code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("中文教材内容", out)

    def test_encrypted_document_is_rejected(self):
        reader = SimpleNamespace(pages=[], is_encrypted=True)
        with mock.patch.dict(sys.modules, {"pypdf": SimpleNamespace(PdfReader=lambda _: reader)}):
            code, out, err = self.run_tool()
        self.assertNotEqual(code, 0)
        self.assertFalse(out)
        self.assertIn("加密", err)

    def test_long_pages_have_visible_truncation_and_total_output_cap(self):
        make_pdf(self.pdf, ["X" * 15000] * 12)
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("截断", out)
        self.assertLess(len(out), 45000)

    def test_single_long_page_can_be_read_without_losing_tail(self):
        text = "A" * 6000 + "B" * 6000 + "TAIL-END"
        make_pdf(self.pdf, [text])
        parts = []
        offset = 0
        while True:
            report = self.tool.extract_pdf(self.pdf, pages="1", text_offset=offset)
            page = report["pages"][0]
            self.assertEqual(page["text_offset"], offset)
            self.assertEqual(page["total_text_chars"], len(text))
            self.assertLessEqual(len(page["text"]), 6000)
            parts.append(page["text"])
            if page["next_offset"] is None:
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertEqual("".join(parts), text)

    def test_cli_offset_reads_tail_and_exposes_continuation(self):
        make_pdf(self.pdf, ["A" * 6000 + "TAIL-CONTENT"])
        code, out, err = self.run_tool("--pages", "1")
        self.assertEqual(code, 0, err)
        self.assertIn("--text-offset 6000", out)
        code, out, err = self.run_tool("--pages", "1", "--text-offset", "6000")
        self.assertEqual(code, 0, err)
        self.assertIn("TAIL-CONTENT", out)
        self.assertNotIn("A" * 100, out)

    def test_invalid_or_ambiguous_offset_does_not_claim_reading(self):
        for args in [("--text-offset", "-1"), ("--text-offset", "1", "--pages", "1-2")]:
            code, out, err = self.run_tool(*args)
            self.assertNotEqual(code, 0)
            self.assertFalse(out)
            self.assertTrue(err)

    def test_offset_at_end_has_clear_warning_without_fake_scan_claim(self):
        make_pdf(self.pdf, ["CLEAR PAGE TEXT"])
        code, out, err = self.run_tool("--pages", "1", "--text-offset", "99")
        self.assertEqual(code, 0, err)
        self.assertIn("偏移", out)
        self.assertNotIn("扫描页", out)

    def test_exact_total_limit_is_not_falsely_claimed_truncated(self):
        make_pdf(self.pdf, ["X" * 6000] * 6 + ["Y" * 4000])
        report = self.tool.extract_pdf(self.pdf)
        self.assertEqual(sum(len(page["text"]) for page in report["pages"]), 40000)
        self.assertFalse(any("截断" in warning for warning in report["warnings"]))
        self.assertTrue(all(page["next_offset"] is None for page in report["pages"]))

    def test_cli_writes_utf8_and_does_not_persist_outputs(self):
        before = sorted(p.name for p in self.directory.iterdir())
        result = subprocess.run([sys.executable, "-B", str(TOOL_PATH), str(self.pdf), "--pages", "2"],
                                cwd=self.directory, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        out = result.stdout.decode("utf-8")
        self.assertIn("第 2 页", out)
        self.assertIn("PAGE TWO TEXT", out)
        self.assertEqual(before, sorted(p.name for p in self.directory.iterdir()))


if __name__ == "__main__":
    unittest.main()
