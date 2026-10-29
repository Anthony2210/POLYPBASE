"""Standard-library tests; temporary trees only, no Django or database setup."""
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    from . import check_i18n as gate
except ImportError:
    import check_i18n as gate


class ExtractionTests(unittest.TestCase):
    def test_actual_import_aliases_and_literals(self):
        cases = (
            'from django.utils.translation import gettext_lazy as tr\ntr("Hello" " world")',
            'from django.utils import translation as t\nt.gettext("Hello world")',
            'import django.utils.translation as t\nt.gettext_lazy("Hello world")',
            'import django.utils.translation\ndjango.utils.translation.gettext("Hello world")',
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(gate.extract_python(text, "source.py"), ["Hello world"])

    def test_unrelated_calls_and_fixture_strings_are_not_sources(self):
        self.assertEqual(gate.extract_python(
            'fixture = \'_("fake")\'\nobj.gettext("not Django")\n_("not imported")',
            "source.py"), [])

    def test_unsupported_python_fails_closed(self):
        bodies = ('tr(value)', 'tr(f"{value}")', 'tr("x" + "y")',
                  'tr("x", context="y")', 'tr(message="x")',
                  'alias = tr', 'tr = custom\ntr("x")',
                  'def f(tr):\n    return tr("x")',
                  'def f():\n    tr = custom\n    return tr("x")',
                  'def tr(value):\n    return value\ntr("x")',
                  'from other import call as tr\ntr("x")')
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(gate.ExtractionError):
                gate.extract_python('from django.utils.translation import gettext as tr\n' + body,
                                    "source.py")
        for function in ('ngettext', 'pgettext', 'gettext_noop', 'npgettext_lazy'):
            with self.subTest(function=function), self.assertRaises(gate.ExtractionError):
                gate.extract_python(
                    f'from django.utils.translation import {function} as tr\ntr("x")', "source.py")
        with self.assertRaises(gate.ExtractionError):
            gate.extract_python('from django.utils.translation import *', "source.py")
        for body in ('getattr(t, name)("x")', 't.__dict__[name]("x")', 'alias = t'):
            with self.subTest(body=body), self.assertRaises(gate.ExtractionError):
                gate.extract_python('from django.utils import translation as t\n' + body, "source.py")

    def test_unused_local_underscore_does_not_shadow_other_function(self):
        self.assertEqual(gate.extract_python(
            'from django.utils.translation import gettext as _\n'
            'def f():\n    row, _ = pair\n'
            'def g():\n    return _("real")', "source.py"), ["real"])

    def test_template_literals_comments_and_alias(self):
        self.assertEqual(gate.extract_template(
            '{# {% trans "ignored" %} #}{% comment %}{% trans bad %}{% endcomment %}'
            '{% trans "Hello" %}{% translate \'World\' as label %}', "page.html"),
            ["Hello", "World"])

    def test_template_escape_semantics_match_django_literals(self):
        self.assertEqual(gate.extract_template(r'''{% trans "literal\npath\\file\"quote" %}''', "page.html"),
                         [r'''literal\npath\file"quote'''])

    def test_unsupported_templates_fail_closed(self):
        for text in ('{% trans variable %}', '{% trans "x" context "y" %}',
                     '{% trans "x" noop %}', '{% blocktrans %}x{% endblocktrans %}',
                     '{% blocktranslate %}x{% endblocktranslate %}',
                     '{{ _("x") }}', '{% trans "unclosed"'):
            with self.subTest(text=text), self.assertRaises(gate.ExtractionError):
                gate.extract_template(text, "page.html")

    def test_discovery_deliberately_excludes_tests_and_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ('backend/apps/models.py', 'backend/apps/test_i18n.py',
                             'backend/apps/tests.py', 'backend/apps/tests_extra.py',
                             'backend/apps/tests/fixture.py', 'backend/apps/migrations/0001.py'):
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('from django.utils.translation import gettext as _\n_("real")',
                                encoding="utf-8")
            self.assertEqual(gate.discover_sources(root), {"real": ("backend/apps/models.py",)})


class CatalogTests(unittest.TestCase):
    def check(self, sources=None, catalogs=None, **kwargs):
        return gate.check_catalogs(
            sources if sources is not None else {"Hello": ("backend/example.py",)},
            catalogs if catalogs is not None else {"fr": {"Hello": "Bonjour"}, "en": {"Hello": "Hello"}},
            permission_msgids=(), **kwargs)

    def test_complete_catalog_identity_and_zero_valid(self):
        for value in ("Hello", "0"):
            self.assertEqual(self.check({value: ("backend/source.py",)},
                                       {lang: {value: value} for lang in gate.LANGUAGES}).errors, [])

    def test_missing_translations_report_raw_and_blocking_separately(self):
        report = self.check(catalogs={"fr": {}, "en": {"Hello": "Hello"}})
        self.assertEqual(report.raw_missing, {"fr": ["Hello"], "en": []})
        self.assertEqual(report.exempted, {"fr": [], "en": []})
        self.assertIn("missing translation", report.errors[0])

    def exception(self, **kwargs):
        return replace(gate.CoverageException("fr", "Hello", ("backend/example.py",),
                                             "Awaiting decision", "deferred"), **kwargs)

    def decisions(self):
        return {self.exception().decision_identity: gate.RegisterEvent("DEFERRED", "deferred")}

    def test_template_error_preserves_comment_lines(self):
        with self.assertRaisesRegex(gate.ExtractionError, r"page.html:4:"):
            gate.extract_template('{% comment %}\nignored\n{% endcomment %}\n{% trans variable %}',
                                  "page.html")

    def test_exact_exception_and_reference(self):
        report = self.check(catalogs={"fr": {}, "en": {"Hello": "Hello"}},
                            exceptions=(self.exception(),), decision_events=self.decisions())
        self.assertEqual(report.errors, [])
        self.assertEqual(report.raw_missing["fr"], ["Hello"])
        self.assertEqual(report.exempted["fr"], ["Hello"])

    def test_stale_wrong_scope_count_language_reason_and_reference(self):
        for exception in (self.exception(sources=("backend/other.py",)),
                          self.exception(sources=("backend/example.py",) * 2),
                          self.exception(language="ja"), self.exception(reason=""),
                          self.exception(decision_ref="unregistered"), self.exception(msgid="Other")):
            with self.subTest(exception=exception):
                report = self.check(catalogs={"fr": {}, "en": {"Hello": "Hello"}},
                                    exceptions=(exception,), decision_events=self.decisions())
                self.assertTrue(report.errors)
                self.assertEqual(report.exempted["fr"], [])
        self.assertTrue(any("stale" in error for error in self.check(
            exceptions=(self.exception(),), decision_events=self.decisions()).errors))
        self.assertTrue(any("duplicate" in error for error in self.check(
            catalogs={"fr": {}, "en": {"Hello": "Hello"}},
            exceptions=(self.exception(), self.exception()), decision_events=self.decisions()).errors))

    def test_main_identity_guard_exemptions_require_exact_occurrences(self):
        scopes = {
            "The culture identity changed. Reload the culture.": (
                "backend/apps/cultures/models.py", "backend/apps/cultures/models.py"),
            "This code is reserved by a historical culture identity.": (
                "backend/apps/cultures/models.py", "backend/apps/cultures/box_codes.py"),
        }
        for msgid, expected in scopes.items():
            for language in gate.LANGUAGES:
                with self.subTest(msgid=msgid, language=language):
                    exceptions = tuple(exception for exception in gate.COVERAGE_EXCEPTIONS
                                       if (exception.language, exception.msgid) == (language, msgid))
                    self.assertEqual(len(exceptions), 1)
                    exception = exceptions[0]
                    self.assertCountEqual(exception.sources, expected)
                    self.assertEqual(exception.reason, gate.IDENTITY_GUARD)
                    self.assertEqual(exception.decision_ref, "B02-scope-deferred")
                    events = {exception.decision_identity:
                              gate.RegisterEvent("DEFERRED", "B02-scope-deferred")}
                    catalogs = {lang: {} if lang == language else {msgid: msgid}
                                for lang in gate.LANGUAGES}
                    report = self.check({msgid: tuple(reversed(expected))}, catalogs,
                                        exceptions=exceptions, decision_events=events)
                    self.assertEqual(report.errors, [])
                    self.assertEqual(report.exempted[language], [msgid])
                    for drift in (expected[:1], expected + (expected[0],),
                                  expected + ("backend/unexpected.py",),
                                  (expected[0], "backend/unexpected.py")):
                        with self.subTest(drift=drift):
                            report = self.check({msgid: drift}, catalogs,
                                                exceptions=exceptions, decision_events=events)
                            self.assertTrue(any("source occurrence scope drift" in error
                                                for error in report.errors))
                            self.assertEqual(report.exempted[language], [])
                            self.assertIn(msgid, report.raw_missing[language])

    def test_register_exception_requires_latest_exact_identity(self):
        identity = self.exception().decision_identity
        wrong_identities = (
            ("react", *identity[1:]),
            (identity[0], "Other", *identity[2:]),
            (*identity[:2], "EN", identity[3]),
            (*identity[:3], "backend/locale/en/LC_MESSAGES/django.po"),
        )
        invalid = [{key: gate.RegisterEvent("DEFERRED", "deferred")} for key in wrong_identities]
        invalid += [{identity: gate.RegisterEvent("APPLIED", "deferred")},
                    {identity: gate.RegisterEvent("DEFERRED", "unrelated")}]
        for events in invalid:
            with self.subTest(events=events):
                report = self.check(catalogs={"fr": {}, "en": {"Hello": "Hello"}},
                                    exceptions=(self.exception(),), decision_events=events)
                self.assertEqual(report.exempted["fr"], [])
                self.assertTrue(any("latest exact" in error for error in report.errors))

    def test_empty_whitespace_and_middle_dot_are_blocking(self):
        for value in ("", "   ", " Bonjour", "Bonjour\n", "Technicien·ne"):
            with self.subTest(value=value):
                self.assertTrue(self.check(catalogs={"fr": {"Hello": value},
                                                     "en": {"Hello": "Hello"}}).errors)
        for msgid in ("", " source", "source ", "source·label"):
            with self.subTest(msgid=msgid):
                self.assertTrue(self.check({msgid: ("backend/a.py",)},
                                           {lang: {} for lang in gate.LANGUAGES}).errors)

    def test_placeholders_source_fr_en(self):
        examples = (("Count {count}", "Compte {count}", "Count {count}"),
                    ("Count %(count)d", "Compte %(count)d", "Count %(count)d"),
                    ("Count %s", "Compte %s", "Count %s"))
        for source, fr, en in examples:
            with self.subTest(source=source):
                self.assertEqual(self.check({source: ("backend/a.py",)},
                                            {"fr": {source: fr}, "en": {source: en}}).errors, [])
                for language in gate.LANGUAGES:
                    catalogs = {"fr": {source: fr}, "en": {source: en}}
                    catalogs[language][source] = "Lost placeholder"
                    self.assertTrue(any("placeholder" in error for error in self.check(
                        {source: ("backend/a.py",)}, catalogs).errors))
        for source in ("Broken {", "Broken %(count)q"):
            report = self.check({source: ("backend/a.py",)},
                                {lang: {source: source} for lang in gate.LANGUAGES})
            self.assertTrue(any("source: invalid placeholders" in error for error in report.errors))
        for language in gate.LANGUAGES:
            report = self.check(catalogs={lang: {"Hello": "Broken {" if lang == language else "Hello"}
                                         for lang in gate.LANGUAGES})
            self.assertTrue(any("invalid placeholders" in error for error in report.errors))

    def test_permission_allowlist_not_validation_or_lexical_ban(self):
        catalogs = {lang: {msgid: gate.ADMIN_INVITATIONS[lang]
                           for msgid in gate.PERMISSION_MSGIDS} for lang in gate.LANGUAGES}
        for lang in gate.LANGUAGES:
            catalogs[lang]["Only an active box can be subcultured."] = "Validation"
            catalogs[lang]["Repiquage"] = "Subculture"  # No broad terminology ban.
        self.assertEqual(gate.check_catalogs({}, catalogs).errors, [])
        for language in gate.LANGUAGES:
            for msgid in gate.PERMISSION_MSGIDS:
                broken = {lang: dict(values) for lang, values in catalogs.items()}
                broken[language][msgid] = "No access"
                self.assertTrue(any("permission invitation" in error
                                    for error in gate.check_catalogs({}, broken).errors))
                broken[language][msgid] = gate.ADMIN_INVITATIONS[language] * 2
                self.assertTrue(any("permission invitation" in error
                                    for error in gate.check_catalogs({}, broken).errors))


class TreeTests(unittest.TestCase):
    def make_tree(self, root):
        source = root / "backend/source.py"
        source.parent.mkdir(parents=True)
        source.write_text('from django.utils.translation import gettext as _\n_("Hello")', encoding="utf-8")
        for language in gate.LANGUAGES:
            po = root / "backend/locale" / language / "LC_MESSAGES/django.po"
            po.parent.mkdir(parents=True)
            messages = {"": f"Content-Type: text/plain; charset=UTF-8\nLanguage: {language}\n",
                        "Hello": "Hello"}
            po.write_text('\n\n'.join(f'msgid {json.dumps(key)}\nmsgstr {json.dumps(value)}'
                                      for key, value in messages.items()) + '\n', encoding="utf-8")
            po.with_suffix('.mo').write_bytes(gate.compiler.compile_messages(messages))
        register = root / "docs/translation-review/application/register.csv"
        register.parent.mkdir(parents=True)
        register.write_text(
            'surface,key_or_msgid,language,source_file,decision_ref,status\n'
            'django,Hello,FR,backend/locale/fr/LC_MESSAGES/django.po,deferred,DEFERRED\n',
            encoding="utf-8")

    def test_strict_po_mo_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gate, "COVERAGE_EXCEPTIONS", ()):
            root = Path(directory)
            self.make_tree(root)
            with patch.object(gate, "PERMISSION_MSGIDS", ()):
                # check_catalogs has a bound default: explicitly override its permission scope.
                original = gate.check_catalogs
                def check(*args, **kwargs):
                    return original(*args, permission_msgids=(), **kwargs)
                with patch.object(gate, "check_catalogs", side_effect=check):
                    self.assertEqual(gate.check_tree(root).errors, [])
                    mo = root / "backend/locale/fr/LC_MESSAGES/django.mo"
                    mo.write_bytes(b"corrupt")
                    self.assertTrue(any("MO bytes differ" in e for e in gate.check_tree(root).errors))
                    mo.unlink()
                    self.assertTrue(any("catalog error" in e for e in gate.check_tree(root).errors))
                    po = root / "backend/locale/en/LC_MESSAGES/django.po"
                    po.write_text('msgid "Hello"\nmsgstr "one"\n\nmsgid "Hello"\nmsgstr "two"\n', encoding="utf-8")
                    self.assertTrue(any("catalog error" in e for e in gate.check_tree(root).errors))
            expected = ("django", "Hello", "FR", "backend/locale/fr/LC_MESSAGES/django.po")
            self.assertEqual(gate.load_register_events(root), {
                expected: gate.RegisterEvent("DEFERRED", "deferred"),
            })
            register = root / "docs/translation-review/application/register.csv"
            with register.open("a", encoding="utf-8") as stream:
                stream.write('django,Hello,FR,backend/locale/fr/LC_MESSAGES/django.po,new,APPLIED\n')
            self.assertEqual(gate.load_register_events(root)[expected], gate.RegisterEvent("APPLIED", "new"))
            (root / "backend/source.py").write_text(
                'from django.utils.translation import gettext as _\n_(dynamic)', encoding="utf-8")
            self.assertTrue(any("source extraction" in e for e in gate.check_tree(root).errors))

    def test_cli_nonzero_and_separate_counts(self):
        report = gate.Report(1, {"fr": ["Hello"], "en": []}, {"fr": ["Hello"], "en": []}, ["broken"])
        output = io.StringIO()
        with patch.object(gate, "check_tree", return_value=report), contextlib.redirect_stdout(output):
            self.assertEqual(gate.main(), 1)
        self.assertIn("raw coverage 0/1; raw gaps 1; exempted 1; blocking coverage gaps 0", output.getvalue())
        self.assertIn("Blocking errors: 1", output.getvalue())
        report.errors.clear()
        with patch.object(gate, "check_tree", return_value=report), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(), 0)


if __name__ == "__main__":
    unittest.main()
