"""Exportação Colab pela interface: origem exata, sem inferência ou escrita."""

import ast
import base64
import builtins
import copy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

import test_app_vetorizacao as vector_fixtures
from colab_notebook import gerar_notebook_colab


class ColabAppTests(unittest.TestCase):
    def setUp(self):
        # Composição evita repetir os testes herdados da etapa 09.
        self.fixture = vector_fixtures.VectorAppTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client
        self.database = self.fixture.database

    def export(self, document_id, **payload):
        return self.client.post(f"/envios/{document_id}/notebook-colab.ipynb", json=payload)

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return {
                name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
                for (name,) in tables
            }

    def capsule(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        notebook = json.loads(response.get_data(as_text=True))
        self.assertEqual(notebook["nbformat"], 4)
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            source = cell["source"]
            tree = ast.parse("".join(source) if isinstance(source, list) else source)
            for statement in tree.body:
                if not isinstance(statement, ast.Assign):
                    continue
                if any(isinstance(target, ast.Name) and target.id == "CAPSULA_BASE64" for target in statement.targets):
                    raw = base64.b64decode(ast.literal_eval(statement.value), validate=True)
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), notebook["metadata"]["lingua"]["capsula_sha256"])
                    return notebook, json.loads(raw.decode("utf-8"))
        self.fail("Notebook sem cápsula de origem verificável.")

    def test_attachment_contains_exact_source_and_receipt_without_loading_or_writing(self):
        document_id, context = self.fixture.source(normalizar=True)
        before = self.snapshot()
        original_import = builtins.__import__
        heavy = {"torch", "transformers", "sentence_transformers", "huggingface_hub", "fastembed", "onnxruntime", "spacy"}

        def no_model_import(name, *args, **kwargs):
            if name.split(".", 1)[0] in heavy:
                raise AssertionError(f"Exportar um notebook tentou importar {name}.")
            return original_import(name, *args, **kwargs)

        with (
            patch("app.enfileirar_vetorizacao") as enqueue,
            patch("app.iniciar_trabalho") as start,
            patch("builtins.__import__", side_effect=no_model_import),
        ):
            response = self.export(document_id, contexto_execucao_id=context["execucao_id"])
        notebook, capsule = self.capsule(response)
        self.assertEqual(response.mimetype, "application/x-ipynb+json")
        self.assertEqual(response.headers["Content-Disposition"], f'attachment; filename="lingua-documento-{document_id}-colab.ipynb"')
        self.assertEqual(capsule["contexto"], context)
        with sqlite3.connect(self.database) as connection:
            content, created_at = connection.execute("SELECT content,created_at FROM submissions WHERE id=?", (document_id,)).fetchone()
        self.assertEqual(capsule["documento"], {"id": document_id, "content": content, "created_at": created_at})
        original = capsule["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["original"]
        self.assertEqual(original["texto"], content)
        self.assertIn("\r\n", content)
        self.assertEqual(notebook["metadata"]["lingua"]["contexto_execucao_id"], context["execucao_id"])
        self.assertEqual(self.snapshot(), before)
        enqueue.assert_not_called()
        start.assert_not_called()

    def test_form_preserves_selected_profile_and_settings_for_colab_runtime(self):
        document_id, context = self.fixture.source()
        with patch("app.gerar_notebook_colab", wraps=gerar_notebook_colab) as producer:
            response = self.client.post(f"/envios/{document_id}/notebook-colab.ipynb", data={
                "contexto_execucao_id": context["execucao_id"], "perfil_id": "e5_conteudo",
                "dispositivo": "mps", "lote": "3", "formato_armazenamento": "float16",
            })
        self.capsule(response)
        self.assertEqual(producer.call_args.args, (context,))
        self.assertEqual(producer.call_args.kwargs["perfil_id"], "e5_conteudo")
        self.assertEqual(producer.call_args.kwargs["opcoes"], {"dispositivo": "mps", "lote": 3, "formato_armazenamento": "float16"})

    def test_json_accepts_other_model_with_immutable_revision(self):
        document_id, context = self.fixture.source()
        options = {"modelo": "modelo-controlado/alternativo", "revisao": "a" * 40, "limite_tokens": 2048, "pooling": "modelo"}
        with patch("app.gerar_notebook_colab", wraps=gerar_notebook_colab) as producer:
            response = self.export(document_id, contexto_execucao_id=context["execucao_id"], opcoes=options)
        self.capsule(response)
        self.assertEqual(producer.call_args.kwargs["opcoes"], options)

    def test_historical_context_remains_exact_after_new_preparation(self):
        document_id, old_context = self.fixture.source()
        _, new_context = self.fixture.source(document_id=document_id, normalizar=True, raio_anterior=0, raio_seguinte=0)
        old_notebook, old_capsule = self.capsule(self.export(document_id, contexto_execucao_id=old_context["execucao_id"]))
        _, latest_capsule = self.capsule(self.export(document_id))
        self.assertEqual(old_capsule["contexto"], old_context)
        self.assertEqual(latest_capsule["contexto"], new_context)
        self.assertNotEqual(old_context["preparacao_id"], new_context["preparacao_id"])
        page = self.client.get(f"/envios/{document_id}", query_string={"contexto_execucao_id": old_context["execucao_id"]}).get_data(as_text=True)
        self.assertIn(f'name="contexto_execucao_id" value="{old_context["execucao_id"]}"', page)
        self.assertEqual(old_notebook["metadata"]["lingua"]["contexto_execucao_id"], old_context["execucao_id"])

    def test_missing_document_context_and_foreign_context_are_not_found(self):
        self.assertEqual(self.export(999).status_code, 404)
        document_id, context = self.fixture.source()
        other_id, _ = self.fixture.source()
        self.assertEqual(self.export(document_id, contexto_execucao_id="ausente").status_code, 404)
        self.assertEqual(self.export(other_id, contexto_execucao_id=context["execucao_id"]).status_code, 404)
        response = self.client.post("/envios", data={"content": "Apenas recebido."})
        bare_id = int(response.headers["Location"].rsplit("/", 1)[1])
        self.assertEqual(self.export(bare_id).status_code, 404)

    def test_invalid_json_fields_and_profile_are_explicit_errors(self):
        document_id, _ = self.fixture.source()
        invalid = [None, [], "texto", {"opcoes": None}, {"opcoes": []}, {"perfil_id": ""}, {"perfil_id": "inexistente"},
                   {"contexto_execucao_id": 1}, {"contexto_execucao_id": ""}, {"execucao_id": "um"},
                   {"opcoes": {"lote": True}}, {"opcoes": {"formato_armazenamento": "inteiro"}},
                   {"opcoes": {"backend": "fastembed"}}, {"opcoes": {"modelo": "outro/sem-revisao"}}]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.client.post(f"/envios/{document_id}/notebook-colab.ipynb", data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        response = self.client.post(f"/envios/{document_id}/notebook-colab.ipynb", data="{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        for repeated in ('{"perfil_id":"e5_conteudo","perfil_id":"e5_consulta"}', '{"opcoes":{"lote":1,"lote":2}}'):
            with self.subTest(repeated=repeated):
                response = self.client.post(f"/envios/{document_id}/notebook-colab.ipynb", data=repeated, content_type="application/json")
                self.assertEqual(response.status_code, 400)

    def test_invalid_form_duplicates_lot_and_device_are_explicit_errors(self):
        document_id, context = self.fixture.source()
        common = {"contexto_execucao_id": context["execucao_id"]}
        invalid = [{}, {**common, "perfil_id": ""}, {**common, "lote": "0"}, {**common, "lote": "-1"},
                   {**common, "lote": "1.5"}, {**common, "lote": "١"}, {**common, "dispositivo": "colab"},
                   {**common, "backend": "simulado"}, MultiDict([("contexto_execucao_id", context["execucao_id"]), ("contexto_execucao_id", context["execucao_id"])])]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(f"/envios/{document_id}/notebook-colab.ipynb", data=payload).status_code, 400)

    def test_query_parameters_and_get_cannot_select_or_create_exports(self):
        document_id, _ = self.fixture.source()
        before = self.snapshot()
        path = f"/envios/{document_id}/notebook-colab.ipynb"
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.client.post(path, query_string={"perfil_id": "e5_consulta"}, json={}).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_context_is_blocked_before_export(self):
        document_id, context = self.fixture.source()
        corrupted = copy.deepcopy(context)
        corrupted["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["original"]["texto"] += "alterado"
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET record_json=? WHERE execution_id=?", (json.dumps(corrupted, ensure_ascii=False), context["execucao_id"]))
        with patch("app.gerar_notebook_colab") as producer:
            response = self.export(document_id, contexto_execucao_id=context["execucao_id"])
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        producer.assert_not_called()

    def test_receipt_mismatch_is_blocked_even_with_valid_context_json(self):
        document_id, context = self.fixture.source()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content=content || 'alterado' WHERE id=?", (document_id,))
        with patch("app.gerar_notebook_colab") as producer:
            response = self.export(document_id, contexto_execucao_id=context["execucao_id"])
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        producer.assert_not_called()

    def test_not_ready_context_cannot_be_exported(self):
        document_id, _ = self.fixture.source()
        from app import validar_unidades_contexto
        actual_validator = validar_unidades_contexto

        def not_ready(context):
            report = actual_validator(context)
            return {**report, "pronto_para_etapa_09": False}

        with patch("app.validar_unidades_contexto", side_effect=not_ready), patch("app.gerar_notebook_colab") as producer:
            response = self.export(document_id)
        self.assertEqual(response.status_code, 409)
        producer.assert_not_called()

    def test_interface_has_download_action_only_when_context_exists(self):
        document_id, context = self.fixture.source()
        page = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Gerar notebook para o Colab", page)
        self.assertIn(f'formaction="/envios/{document_id}/notebook-colab.ipynb"', page)
        self.assertIn('aria-describedby="vector-colab-help vector-colab-data"', page)
        self.assertIn(f'name="contexto_execucao_id" value="{context["execucao_id"]}"', page)
        self.assertIn("Google", page)
        self.assertIn("não são importados automaticamente", page)
        response = self.client.post("/envios", data={"content": "Texto novo."})
        bare_page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertNotIn("Gerar notebook para o Colab", bare_page)


if __name__ == "__main__":
    unittest.main()
