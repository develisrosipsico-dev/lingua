import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from preparacao import ErroPreparacao, mapear_intervalo, validar_registro


class PreparationIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()
        self.original = "Eu não queria sair.\r\nMas fiquei."

    def submit(self, **options):
        response = self.client.post("/envios", data={"content": self.original, **options})
        self.assertEqual(response.status_code, 303)
        return int(response.headers["Location"].rsplit("/", 1)[-1])

    def saved_preparations(self, document_id):
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                "SELECT record_json FROM preparations WHERE submission_id = ? ORDER BY id",
                (document_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def test_submission_creates_literal_preparation_and_download(self):
        document_id = self.submit()
        response = self.client.get(f"/envios/{document_id}/preparacao.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        prepared = response.get_json()
        self.assertEqual(prepared["documento_id"], document_id)
        self.assertEqual(prepared["original"]["texto"], self.original)
        self.assertEqual(prepared["trabalho"]["texto"], self.original)
        self.assertFalse(prepared["politica"]["opcoes"]["normalizar_crlf"])
        self.assertEqual(prepared["alteracoes"], [])
        self.assertTrue(validar_registro(prepared)["pronto_para_etapa_04"])
        self.assertEqual(len(self.saved_preparations(document_id)), 1)

    def test_checkbox_is_optional_and_does_not_modify_database_original(self):
        document_id = self.submit(normalizar_crlf="on")
        prepared = self.saved_preparations(document_id)[0]
        self.assertEqual(prepared["trabalho"]["texto"], self.original.replace("\r\n", "\n"))
        self.assertEqual(mapear_intervalo(prepared, 20, 31), {"inicio": 21, "fim": 32})
        with sqlite3.connect(self.database) as connection:
            saved = connection.execute("SELECT content FROM submissions WHERE id = ?", (document_id,)).fetchone()[0]
        self.assertEqual(saved, self.original)

    def test_new_preparation_is_appended_and_old_version_stays_available(self):
        document_id = self.submit()
        first = self.saved_preparations(document_id)[0]
        response = self.client.post(
            f"/envios/{document_id}/preparacoes",
            json={"normalizar_crlf": True, "preparacao_id": "preparacao-testada", "registrado_em": "2026-10-07T12:00:00-03:00"},
        )
        self.assertEqual(response.status_code, 201)
        second = response.get_json()
        self.assertEqual(second["preparacao_id"], "preparacao-testada")
        self.assertEqual(second["registrado_em"], "2026-10-07T12:00:00-03:00")
        self.assertEqual(second["original"], first["original"])
        self.assertEqual(second["trabalho"]["versao"], first["original"]["versao"] + 1)
        self.assertEqual(self.saved_preparations(document_id), [first, second])
        original_download = self.client.get(
            f"/envios/{document_id}/preparacao.json", query_string={"preparacao_id": first["preparacao_id"]},
        )
        self.assertEqual(original_download.get_json(), first)

    def test_preparation_persists_after_app_restart(self):
        document_id = self.submit(normalizar_crlf="on")
        saved = self.saved_preparations(document_id)[0]
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get(f"/envios/{document_id}/preparacao.json").get_json(), saved)

    def test_failed_preparation_rolls_back_new_submission(self):
        with patch("app.preparar_texto", side_effect=ErroPreparacao("Metadados inconsistentes.")):
            response = self.client.post("/envios", data={"content": self.original})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Metadados inconsistentes.", response.get_data(as_text=True))
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM submissions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM preparations").fetchone()[0], 0)

    def test_duplicate_id_cannot_overwrite_a_preparation(self):
        document_id = self.submit()
        endpoint = f"/envios/{document_id}/preparacoes"
        self.assertEqual(self.client.post(endpoint, json={"preparacao_id": "id-estavel"}).status_code, 201)
        before = self.saved_preparations(document_id)
        duplicate = self.client.post(endpoint, json={"preparacao_id": "id-estavel", "normalizar_crlf": True})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.saved_preparations(document_id), before)

    def test_invalid_options_cannot_create_or_change_records(self):
        document_id = self.submit()
        before = self.saved_preparations(document_id)
        for payload in [{"normalizar_crlf": "true"}, {"content": "Original adulterado"}, {"registrado_em": "2026-10-07"}, []]:
            with self.subTest(payload=payload):
                response = self.client.post(f"/envios/{document_id}/preparacoes", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("erro", response.get_json())
        self.assertEqual(self.saved_preparations(document_id), before)

    def test_tampered_saved_json_is_not_delivered_as_ready(self):
        document_id = self.submit()
        prepared = self.saved_preparations(document_id)[0]
        prepared["original"]["sha256"] = "0" * 64
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE preparations SET record_json = ?", (json.dumps(prepared),))
        response = self.client.get(f"/envios/{document_id}/preparacao.json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("erro", response.get_json())

    def test_saved_identifier_and_previous_metadata_must_match_database(self):
        document_id = self.submit()
        saved = self.saved_preparations(document_id)[0]
        for field in ["preparacao_id", "recebido_em"]:
            with self.subTest(field=field):
                prepared = json.loads(json.dumps(saved))
                if field == "preparacao_id":
                    prepared[field] = "outro-identificador"
                else:
                    prepared["metadados_origem"][field] = "2026-01-01T00:00:00+00:00"
                # O objeto isolado continua válido; o vínculo com a etapa anterior não.
                validar_registro(prepared)
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE preparations SET record_json = ?", (json.dumps(prepared),))
                response = self.client.get(f"/envios/{document_id}/preparacao.json")
                self.assertEqual(response.status_code, 409)
                self.assertIn("erro", response.get_json())

    def test_invalid_checkbox_is_not_silently_accepted(self):
        response = self.client.post("/envios", data={"content": self.original, "normalizar_crlf": "yes"})
        self.assertEqual(response.status_code, 400)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM submissions").fetchone()[0], 0)
        document_id = self.submit()
        response = self.client.post(f"/envios/{document_id}/preparacoes", data={"normalizar_crlf": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.saved_preparations(document_id)), 1)

    def test_existing_documents_are_preserved_and_can_be_prepared(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO submissions (id, content, created_at) VALUES (?, ?, ?)",
                (21, self.original, "2026-10-06T12:00:00+00:00"),
            )
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get("/envios/21").status_code, 200)
        self.assertEqual(restarted.get("/envios/21/preparacao.json").status_code, 404)
        self.assertEqual(restarted.post("/envios/21/preparacoes", json={}).status_code, 201)
        prepared = restarted.get("/envios/21/preparacao.json").get_json()
        self.assertEqual(prepared["metadados_origem"]["recebido_em"], "2026-10-06T12:00:00+00:00")
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT content, created_at FROM submissions WHERE id = 21").fetchone(),
                             (self.original, "2026-10-06T12:00:00+00:00"))

    def test_missing_document_cannot_be_prepared(self):
        self.assertEqual(self.client.post("/envios/999/preparacoes", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
