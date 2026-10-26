import unittest

from plan import render_markdown, render_plan_page


class PlanMarkdownTests(unittest.TestCase):
    def test_headings_tables_and_lists(self):
        html = render_markdown(
            "# Piano\n\n"
            "| Esercizio | Watt |\n"
            "| --- | --- |\n"
            "| VO2max | 200 |\n\n"
            "- uno\n"
            "- due"
        )
        self.assertIn("<h1>Piano</h1>", html)
        self.assertIn("<table>", html)
        self.assertIn("<th>Esercizio</th>", html)
        self.assertIn("<td>200</td>", html)
        self.assertIn("<li>uno</li>", html)

    def test_inline_formatting_and_escaping(self):
        html = render_markdown("**forte** e `x < y` e <script>")
        self.assertIn("<strong>forte</strong>", html)
        self.assertIn("<code>x &lt; y</code>", html)
        self.assertNotIn("<script>", html)

    def test_plan_page_links(self):
        page = render_plan_page("# Piano")
        self.assertIn("<h1>Piano</h1>", page)
        self.assertIn('href="/"', page)
        self.assertIn('href="/piano.md"', page)


if __name__ == "__main__":
    unittest.main()
