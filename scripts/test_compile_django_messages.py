from __future__ import annotations

import ast
import contextlib
import gettext
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compile_django_messages as compiler


def entry(source: str, translation: str) -> str:
    # repr is not PO syntax; escape only the characters PO strings require.
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'

    return f"msgid {quote(source)}\nmsgstr {quote(translation)}\n"


def header(language="en"):
    return entry("", f"Content-Type: text/plain; charset=UTF-8\nLanguage: {language}\n")


def messages(language="en"):
    return {"": f"Content-Type: text/plain; charset=UTF-8\nLanguage: {language}\n", "a": "A"}


class ParserTests(unittest.TestCase):
    def assert_invalid(self, text: str, line: int | None = None) -> None:
        with self.assertRaises(compiler.PoError) as caught:
            compiler.parse_po_text(text, "fixture.po")
        self.assertIsInstance(caught.exception, ValueError)
        self.assertRegex(str(caught.exception), r"^fixture\.po:\d+: ")
        if line is not None:
            self.assertTrue(str(caught.exception).startswith(f"fixture.po:{line}:"))

    def test_header_comments_multiline_and_unicode(self):
        text = '''# Translator comment
#. Extracted comment
#: example.py:1
#, python-format
msgid ""
msgstr ""
"Language: fr\\n"
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "First "
"line\\nsecond"
msgstr "Première "
"ligne\\ndeuxième"
'''
        self.assertEqual(compiler.parse_po_text(text), {
            "": "Language: fr\nContent-Type: text/plain; charset=UTF-8\n",
            "First line\nsecond": "Première ligne\ndeuxième",
        })
        self.assertEqual(compiler.parse_po_text('msgid ""\nmsgstr ""'), {"": ""})

    def test_escapes(self):
        source = r'''msgid "Quote: \" slash: \\ bell: \a back: \b form: \f newline: \n return: \r tab: \t vertical: \v octal: \101 hex: \x42 question: \? apostrophe: \' end"
msgstr "Quote: \" slash: \\ bell: \a back: \b form: \f newline: \n return: \r tab: \t vertical: \v octal: \101 hex: \x42 question: \? apostrophe: \' end"'''
        value = 'Quote: " slash: \\ bell: \a back: \b form: \f newline: \n return: \r tab: \t vertical: \v octal: A hex: B question: ? apostrophe: \' end'
        self.assertEqual(compiler.parse_po_text(source), {value: value})

    def test_numeric_escapes_are_utf8_bytes(self):
        self.assertEqual(
            compiler.parse_po_text(r'msgid "caf\303\251"' + '\n' + r'msgstr "caf\xc3\xa9"'),
            {"café": "café"},
        )
        self.assert_invalid('msgid "a"\n' + r'msgstr "\xff"', 2)

    def test_crlf_and_adjacent_entries(self):
        text = '  msgid "a"\r\n\tmsgstr "A"\r\nmsgid "b"\r\nmsgstr "B"'
        self.assertEqual(compiler.parse_po_text(text), {"a": "A", "b": "B"})

    def test_unsupported_syntax(self):
        for syntax in ('#, fuzzy', '#, python-format, fuzzy', '#~ msgid "old"',
                       '#| msgid "old"', 'msgctxt "context"', 'msgid_plural "many"',
                       'msgstr[0] "one"', 'unknown "value"'):
            with self.subTest(syntax=syntax):
                self.assert_invalid(syntax + '\n' + entry("a", "A"), 1)

    def test_malformed_and_incomplete(self):
        cases = [
            ('msgid "a"', 1), ('msgstr "A"', 1), ('"orphan"', 1),
            ('msgid "a"\n\nmsgstr "A"', 1),
            ('msgid "a"\nmsgid "b"\nmsgstr "B"', 1),
            ('msgid "a"\nmsgstr "A"\nmsgstr "B"', 3),
            ('msgid "a"\n# comment\nmsgstr "A"', 1),
            ('msgid "a"\nmsgstr "A"\n\n"orphan"', 4),
            ('msgid a\nmsgstr "A"', 1),
            ("msgid 'a'\nmsgstr \"A\"", 1),
            ('msgid "a" "b"\nmsgstr "A"', 1),
            ('msgid "a\nmsgstr "A"', 1),
            ('msgid "a" # comment\nmsgstr "A"', 1),
        ]
        for text, line in cases:
            with self.subTest(text=text):
                self.assert_invalid(text, line)

    def test_invalid_escapes(self):
        for value in (r'"\q"', r'"\u0041"', r'"\x"', r'"\x100"', r'"\400"',
                      '"a\\"', '"raw\ttab"'):
            with self.subTest(value=value):
                self.assert_invalid('msgid "a"\nmsgstr ' + value, 2)

    def test_duplicate_entries_including_header(self):
        for source in ("a", ""):
            for translated in ("A", "B"):
                self.assert_invalid(entry(source, "A") + entry(source, translated), 3)

    def test_invalid_user_text(self):
        for value in ("", " ", "\t", " leading", "trailing ", "line\n", "a·b", "\0"):
            with self.subTest(value=value):
                self.assert_invalid(entry("source", value), 2)
                if value:
                    self.assert_invalid(entry(value, "translation"), 1)
        self.assert_invalid('msgid "a"\nmsgstr "\\000"', 2)
        self.assert_invalid('msgid ""\nmsgstr "header\\0"', 2)
        self.assert_invalid('# comment\0\n' + entry("a", "A"), 1)

    def test_placeholder_validation(self):
        good = [
            ("Hello {name!r:>10}", "{name!r:>10}, bonjour"),
            ("{value:{width}.{precision}f}", "Valeur {value:{width}.{precision}f}"),
            ("{} {:d}", "Valeurs {} {:d}"),
            ("%(name)s: %(count)d", "%(count)d pour %(name)s"),
            ("%s %d %%", "Texte %s : %d %%"),
            ("%*.*f", "Valeur %*.*f"),
            ("%1$s %2$d", "%2$d %1$s"),
            ("Literal {{name}} and %%", "Littéral {{autre}} et %%"),
        ]
        for source, translated in good:
            with self.subTest(source=source):
                self.assertEqual(compiler.parse_po_text(entry(source, translated)), {source: translated})
        bad = [
            ("{name}", "{other}"), ("{name!r}", "{name!s}"),
            ("{x:.2f}", "{x:.3f}"), ("{x:{width}}", "{x:{height}}"),
            ("{x} {x}", "{x}"), ("{} {:d}", "{:d} {}"),
            ("%(count)d", "%(count)s"), ("%s %d", "%d %s"),
            ("%*f", "%f"), ("{x}", "{{x}}"), ("%s", "%%s"),
            ("%ld", "%d"), ("%Lf", "%f"),
        ]
        for source, translated in bad:
            with self.subTest(source=source, translated=translated):
                self.assert_invalid(entry(source, translated), 2)

    def test_malformed_placeholders(self):
        for value in ("{", "}", "{x!z}", "{} {0}", "{x[}", "%(x", "%(x)q", "%(x)*s",
                      "%(name)s %s"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    compiler.placeholder_signature(value)
                self.assert_invalid(entry(value, value), 1)

    def test_literal_percentages_and_explicit_format_flags(self):
        for value in ("100%", "50% complete", "Literal %q"):
            self.assertEqual(compiler.placeholder_signature(value), ())
            self.assertEqual(compiler.parse_po_text(entry(value, value)), {value: value})
        self.assert_invalid('#, python-format\n' + entry("%", "%"), 2)
        self.assert_invalid('#, c-format\n' + entry("%q", "%q"), 2)
        self.assertEqual(compiler.placeholder_signature("% d", strict_printf=True),
                         (("printf-position", "0", "d"),))
        self.assert_invalid('#, python-format\n' + entry("% d", "% s"), 3)

    def test_split_utf8_escape_continuations(self):
        text = 'msgid "caf\\303"\n"\\251"\nmsgstr "café"'
        self.assertEqual(compiler.parse_po_text(text), {"café": "café"})

    def test_signature_is_hashable_and_preserves_multiplicity(self):
        signature = compiler.placeholder_signature("{x} {x} %(n)d %% {{literal}}")
        self.assertEqual(signature.count(("brace", "x", "", "")), 2)
        self.assertEqual(len(signature), 3)
        hash(signature)

    def test_file_and_utf8_error_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "django.po"
            path.write_text(header() + entry("é", "É"), encoding="utf-8")
            self.assertEqual(compiler.parse_po_file(path), {
                "": messages()[""], "é": "É",
            })
            path.write_bytes(b'msgid "a"\nmsgstr "\xff"')
            with self.assertRaisesRegex(compiler.PoError, r"django.po:2:"):
                compiler.parse_po_file(path)


class CompilationTests(unittest.TestCase):
    def test_fixed_byte_layout(self):
        expected = struct.pack("<7I", 0x950412DE, 0, 1, 28, 36, 0, 0)
        expected += struct.pack("<4I", 1, 44, 1, 46) + b"a\0A\0"
        self.assertEqual(compiler.compile_messages({"a": "A"}), expected)

    def test_deterministic_and_readable_by_gettext(self):
        messages = {"": "Content-Type: text/plain; charset=UTF-8\n", "é": "É", "a": "A"}
        output = compiler.compile_messages(messages)
        self.assertEqual(output, compiler.compile_messages(dict(reversed(list(messages.items())))))
        catalog = gettext.GNUTranslations(io.BytesIO(output))
        self.assertEqual(catalog.gettext("é"), "É")
        self.assertEqual(catalog.gettext("a"), "A")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "django.mo"
            compiler.write_mo_file(messages, path)
            self.assertEqual(path.read_bytes(), output)

    def test_current_catalogs_retain_legacy_messages_and_committed_bytes(self):
        catalogs = sorted(compiler.LOCALE_DIR.glob("*/LC_MESSAGES/django.po"))
        self.assertTrue(catalogs)
        for path in catalogs:
            with self.subTest(path=path):
                # Independent reconstruction of the old parser on supported catalogs.
                legacy = {}
                key = value = None
                section = None
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("msgid "):
                        if key is not None and value is not None:
                            legacy[key] = value
                        key, value, section = ast.literal_eval(line[6:]), None, "id"
                    elif line.startswith("msgstr "):
                        value, section = ast.literal_eval(line[7:]), "str"
                    elif line.startswith('"'):
                        if section == "id":
                            key += ast.literal_eval(line)
                        elif section == "str":
                            value += ast.literal_eval(line)
                if key is not None and value is not None:
                    legacy[key] = value
                messages = compiler.parse_po_file(path)
                self.assertEqual(messages, legacy)
                self.assertEqual(compiler.compile_messages(messages), path.with_suffix(".mo").read_bytes())


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(compiler, "LOCALE_DIR", self.root).start()
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.stdout))
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def catalog(self, language: str, text: str, output: bytes | None = None) -> Path:
        path = self.root / language / "LC_MESSAGES" / "django.po"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header(language) + "\n" + text, encoding="utf-8")
        if output is not None:
            path.with_suffix(".mo").write_bytes(output)
        return path

    def test_check_success_without_writes(self):
        output = compiler.compile_messages(messages())
        path = self.catalog("en", entry("a", "A"), output)
        before = path.with_suffix(".mo").stat().st_mtime_ns
        with patch.object(Path, "write_bytes", side_effect=AssertionError("write attempted")):
            self.assertEqual(compiler.main(["--check"]), 0)
        self.assertEqual(path.with_suffix(".mo").stat().st_mtime_ns, before)

    def test_check_missing_stale_and_corrupted_outputs_without_writes(self):
        for output in (None, b"stale", b"\0" * 100):
            with self.subTest(output=output):
                path = self.catalog("en", entry("a", "A"), output)
                with patch.object(Path, "write_bytes", side_effect=AssertionError("write attempted")):
                    self.assertEqual(compiler.main(["--check"]), 1)
                if output is None:
                    self.assertFalse(path.with_suffix(".mo").exists())
                else:
                    self.assertEqual(path.with_suffix(".mo").read_bytes(), output)
        self.assertIn("cannot read MO", self.stderr.getvalue())
        self.assertIn("generated MO bytes differ", self.stderr.getvalue())

    def test_validate_all_catalogs_before_any_write(self):
        path = self.catalog("fr", entry("a", "A"), b"original")
        self.catalog("en", 'msgid "incomplete"')
        self.catalog("de", '#, fuzzy\n' + entry("c", "C"))
        with patch.object(Path, "write_bytes", side_effect=AssertionError("write attempted")):
            self.assertEqual(compiler.main([]), 1)
            self.assertEqual(compiler.main(["--check"]), 1)
        self.assertEqual(path.with_suffix(".mo").read_bytes(), b"original")
        self.assertIn("msgid has no msgstr", self.stderr.getvalue())
        self.assertIn("fuzzy", self.stderr.getvalue())

    def test_default_compiles_after_validation(self):
        paths = [self.catalog(language, entry("a", "A")) for language in ("fr", "en")]
        self.assertEqual(compiler.main([]), 0)
        for path in paths:
            self.assertEqual(path.with_suffix(".mo").read_bytes(),
                             compiler.compile_messages(messages(path.parent.parent.name)))

    def test_empty_catalog_discovery_fails(self):
        with patch.object(Path, "write_bytes", side_effect=AssertionError("write attempted")):
            self.assertEqual(compiler.main(["--check"]), 1)
        self.assertIn("no Django PO catalogs", self.stderr.getvalue())

    def test_catalog_header_validation(self):
        path = self.catalog("en", entry("a", "A"))
        for text in (entry("a", "A"), header(), entry("", "broken"),
                     header("fr") + entry("a", "A"),
                     header().replace("charset=UTF-8", "charset=latin1") + entry("a", "A"),
                     entry("a", "A") + header()):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(compiler.PoError):
                    compiler.parse_po_file(path)

    def test_unknown_cli_argument(self):
        with self.assertRaises(SystemExit) as caught:
            compiler.main(["--unknown"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
