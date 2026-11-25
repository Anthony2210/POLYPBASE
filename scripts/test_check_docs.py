"""Focused documentation checks using temporary trees, without Django setup."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import check_docs as gate


FRENCH = (
    "Cette page explique comment les membres peuvent retrouver leurs dossiers dans leur espace. "
    "Lorsque vous ouvrez la liste, vous voyez les changements que votre équipe a effectués. "
    "Pour continuer sans modifier ces dossiers, revenez sur la page précédente."
)
ENGLISH = (
    "This page explains how the team can find records in their workspace. "
    "When you open the list, you can see the changes that other members made. "
    "To continue without editing these records, return to the previous page."
)


class FrenchProseTests(unittest.TestCase):
    def test_flags_french_with_or_without_accents(self):
        self.assertTrue(gate.predominantly_french(FRENCH))
        self.assertTrue(gate.predominantly_french(FRENCH.replace("é", "e")))

    def test_leaves_english_mixed_and_short_text_alone(self):
        for text in (ENGLISH, ENGLISH + "\n\n" + FRENCH, "Les dossiers actifs dans cette liste.",
                     ENGLISH + "\n\nThe product name is Déjà Vu."):
            with self.subTest(text=text):
                self.assertFalse(gate.predominantly_french(text))

    def test_excludes_code_and_quoted_ui_examples(self):
        examples = (
            f"```text\n{FRENCH}\n```",
            f"~~~~text\n~~~\n{FRENCH}\n~~~~~",
            f"```text\n{FRENCH}",
            f"    {FRENCH}",
            f"`{FRENCH}`",
            f"`` `\n{FRENCH}\n` ``",
            f"<pre>{FRENCH}</pre>",
            f"« {FRENCH} »",
            f"“{FRENCH}”",
            f'"{FRENCH}"',
            f"'Consultez l'aide. {FRENCH}'",
            f"> {FRENCH}",
        )
        for example in examples:
            with self.subTest(example=example):
                self.assertFalse(gate.predominantly_french(f"Example:\n\n{example}"))

    def test_still_flags_prose_after_code_and_quotes(self):
        self.assertTrue(gate.predominantly_french(f'~~~text\nexample\n~~~~\n\n“Example”\n\n{FRENCH}'))


class MarkdownChecksTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.write("docs/INDEX.md", "[Status](STATUS.md)\n")
        self.write("docs/STATUS.md", "# Project status\n")

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_language_signal_only_applies_to_living_docs(self):
        self.write("docs/INDEX.md", "[Status](STATUS.md)\n[Guide](user/guide.md)\n"
                   "[Readme](user/README.md)\n[Source](sources/source.md)\n")
        for path in ("docs/user/guide.md", "docs/user/README.md", "docs/archive/old.md",
                     "docs/sources/source.md", "README.md"):
            self.write(path, FRENCH)
        self.assertCountEqual(gate.check_markdown(self.root), [
            "docs/user/README.md: living documentation appears predominantly French",
            "docs/user/guide.md: living documentation appears predominantly French",
        ])

    def test_status_uses_english_path_and_keeps_line_limit(self):
        self.assertEqual(gate.check_markdown(self.root), [])
        self.write("docs/STATUS.md", "\n" * 101)
        self.assertEqual(gate.check_markdown(self.root), ["docs/STATUS.md: exceeds 100 lines"])
        (self.root / "docs/STATUS.md").unlink()
        self.assertIn("docs/STATUS.md: missing project status", gate.check_markdown(self.root))

    def test_frozen_links_are_checked_by_default_and_exempted_exactly(self):
        self.write("docs/archive/old.md", "[Gone](../gone.md)\n[Anchor](../STATUS.md#old)\n"
                   "[New break](../STATUS.md#new)\n")
        self.write("docs/archive/other.md", "[Gone](../gone.md)\n")
        self.write("docs/STATUS.md", "[Gone](../gone.md)\n")
        allowed = {
            ("docs/archive/old.md", "../gone.md"),
            ("docs/archive/old.md", "../STATUS.md#old"),
            ("docs/STATUS.md", "../gone.md"),  # A living page cannot opt out.
        }
        remaining = [
            "docs/archive/old.md: missing anchor: ../STATUS.md#new",
            "docs/archive/other.md: broken link: ../gone.md",
            "docs/STATUS.md: broken link: ../gone.md",
        ]
        self.assertCountEqual(gate.check_markdown(self.root), remaining + [
            "docs/archive/old.md: broken link: ../gone.md",
            "docs/archive/old.md: missing anchor: ../STATUS.md#old",
        ])
        self.assertCountEqual(gate.check_markdown(self.root, frozen_archive_links=allowed), remaining)

    def test_model_inventory_requires_english_path_and_heading(self):
        self.write("docs/reference/modele-donnees.md", "# Data model\n")
        self.assertEqual(gate.check_models(self.root), ["docs/reference/data-model.md: missing model inventory"])
        self.write("docs/reference/data-model.md", "# Data model\n\n## Modèles concrets du projet\n")
        self.assertEqual(gate.check_models(self.root), [
            "docs/reference/data-model.md: missing heading: ## Concrete project models",
        ])

    def test_impact_recognizes_renamed_status_and_user_docs(self):
        for doc, warns in (("docs/STATUS.md", False), ("docs/user/guide.md", False),
                           ("docs/archive/old.md", True)):
            with self.subTest(doc=doc), redirect_stdout(io.StringIO()) as output:
                with patch.object(gate.subprocess, "check_output", return_value=f"backend/config/urls.py\0{doc}\0"):
                    gate.warn_impact("a" * 40)
                self.assertEqual("::warning" in output.getvalue(), warns)


if __name__ == "__main__":
    unittest.main()
