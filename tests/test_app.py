from pathlib import Path
import sqlite3
import tempfile
import unittest

from app import create_app


class TextRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "nested" / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()

    def records(self):
        with sqlite3.connect(self.database) as connection:
            return connection.execute("SELECT id, content, created_at FROM submissions").fetchall()

    def test_original_text_persists_after_app_restart(self):
        content = "  Olá, linguagem!\r\nSegunda linha.\nCitação: 'ação'; 漢字 📝  "
        response = self.client.post("/envios", data={"content": content})
        self.assertEqual(response.status_code, 303)
        rows = self.records()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], content)
        self.assertTrue(rows[0][2].endswith("+00:00"))
        new_client = create_app(self.config).test_client()
        confirmation = new_client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("Texto registrado", confirmation.get_data(as_text=True))
        # Atualizar a confirmação não repete o envio.
        new_client.get(response.headers["Location"])
        self.assertEqual(len(self.records()), 1)

    def test_empty_and_whitespace_are_not_saved(self):
        for content in ["", " \t\r\n", "\u2003"]:
            with self.subTest(content=repr(content)):
                response = self.client.post("/envios", data={"content": content})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.records(), [])

    def test_markup_is_saved_as_text_and_escaped_in_html(self):
        content = "</textarea><script>alert('texto')</script>"
        response = self.client.post("/envios", data={"content": content}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn(content, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(self.records()[0][1], content)

    def test_unknown_record_returns_404(self):
        self.assertEqual(self.client.get("/envios/999").status_code, 404)

    def test_oversized_request_is_not_saved(self):
        response = self.client.post("/envios", data={"content": "a" * (2 * 1024 * 1024)})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
