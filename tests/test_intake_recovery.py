"""Intake rendering regressions; runnable without services or model credentials."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inputs = {}
        self.options = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.inputs.setdefault(attrs["name"], []).append(attrs)
        if tag == "option":
            self.options.append(attrs)


class IntakeRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        templates = Path(__file__).resolve().parents[1] / "moving_agent/web/templates"
        cls.template = Environment(
            loader=FileSystemLoader(templates), autoescape=select_autoescape()
        ).get_template("intake.html")

    def render(self, **context):
        return self.template.render(home_sizes=["studio", "1 bedroom"], today="2026-11-02", **context)

    def test_first_visit_keeps_default_services(self):
        controls = Controls(self.render())
        selected = [i["value"] for i in controls.inputs["needs"] if "checked" in i]
        self.assertEqual(selected, ["truck", "labor"])

    def test_error_preserves_values_and_selections(self):
        html = self.render(errors=["Please correct your budget"], form_values={
            "name": ["Jenny"], "email": ["jenny@example.com"],
            "budget_usd": ["0"], "needs": ["storage"],
            "vehicles": ["car", "suv"], "from_elevator": ["true"],
            "home_size": ["1 bedroom"], "flexible_days": ["3"],
            "notes": ["Keep my notes"],
        })
        controls = Controls(html)
        self.assertEqual(controls.inputs["name"][0]["value"], "Jenny")
        self.assertEqual(controls.inputs["budget_usd"][0]["value"], "0")
        self.assertEqual([i["value"] for i in controls.inputs["needs"] if "checked" in i], ["storage"])
        self.assertEqual([i["value"] for i in controls.inputs["vehicles"] if "checked" in i], ["car", "suv"])
        self.assertIn("checked", controls.inputs["from_elevator"][0])
        self.assertNotIn("checked", controls.inputs["to_elevator"][0])
        self.assertEqual([o["value"] for o in controls.options if "selected" in o], ["3", "1 bedroom"])
        self.assertIn(">Keep my notes</textarea>", html)

    def test_empty_selection_does_not_restore_defaults(self):
        controls = Controls(self.render(form_values={"name": ["Jenny"]}))
        self.assertFalse(any("checked" in i for i in controls.inputs["needs"]))

    def test_submitted_text_is_escaped(self):
        html = self.render(form_values={"name": ['"><script>alert(1)</script>'], "notes": ["</textarea><script>alert(1)</script>"]})
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)


if __name__ == "__main__":
    unittest.main()
