"""Reprocessamento explícito preserva histórico, perfis e continuidade até E5."""

from importlib.util import find_spec
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from werkzeug.datastructures import MultiDict

from app import create_app
from anotacao import ErroModelo
from fixtures_vetorizacao import AdaptadorControlado


class GrammarReprocessingTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "gramatica.sqlite3"
        self.application = create_app({"TESTING": True, "DATABASE": str(self.database),
            "GRAMMAR_MODEL": "pt_core_news_sm", "VECTORS_AUTO_START": False,
            "VECTORS_ADAPTER_FACTORY": AdaptadorControlado})
        self.client = self.application.test_client()
        self.client.post("/envios", data={"content": "Ela continua estudando."})
        self.segmentation = self.client.post("/envios/1/segmentacoes", json={}).get_json()

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return tuple(connection.iterdump())

    def reprocess(self, modelo="pt_core_news_sm"):
        return self.client.post("/envios/1/gramatica/reprocessar", json={
            "segmentacao_id": self.segmentation["segmentacao_id"], "modelo": modelo})

    def test_new_chain_preserves_original_rows_and_does_not_run_e5(self):
        first = self.reprocess()
        self.assertEqual(first.status_code, 201, first.get_json())
        old = self.client.get("/envios/1/anotacao.json").get_json()
        with patch("app.enfileirar_vetorizacao") as enqueue, patch("app.iniciar_trabalho") as start:
            second = self.reprocess()
        enqueue.assert_not_called()
        start.assert_not_called()
        self.assertEqual(second.status_code, 201, second.get_json())
        self.assertNotEqual(first.json["anotacao_id"], second.json["anotacao_id"])
        self.assertNotEqual(first.json["contexto_execucao_id"], second.json["contexto_execucao_id"])
        original = self.client.get("/envios/1/anotacao.json", query_string={"anotacao_id": old["anotacao_id"]})
        self.assertEqual(original.get_json(), old)
        with sqlite3.connect(self.database) as connection:
            for table in ("annotations", "analyses", "rule_runs", "context_runs"):
                self.assertEqual(connection.execute("SELECT count(*) FROM " + table).fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM vector_jobs").fetchone()[0], 0)

    @unittest.skipUnless(find_spec("pt_core_news_md"), "Modelo médio opcional não instalado")
    def test_medium_model_is_explicit_and_used_by_the_entire_new_chain(self):
        self.application.config["GRAMMAR_MODEL"] = "pt_core_news_md"
        first = self.reprocess()
        self.assertEqual(first.status_code, 201, first.json)
        old = self.client.get("/envios/1/anotacao.json").get_json()
        self.assertEqual(old["processamento"]["modelo"]["nome"], "pt_core_news_sm")
        second = self.reprocess("pt_core_news_md")
        self.assertEqual(second.status_code, 201, second.json)
        annotation = self.client.get("/envios/1/anotacao.json").get_json()
        analysis = self.client.get("/envios/1/analise.json").get_json()
        self.assertEqual(annotation["processamento"]["modelo"]["nome"], "pt_core_news_md")
        self.assertEqual(analysis["processamento"]["modelo"], annotation["processamento"]["modelo"])
        self.assertEqual(analysis["anotacao"], annotation)
        page = self.client.get("/envios/1", query_string={"contexto_execucao_id": second.json["contexto_execucao_id"]})
        self.assertIn("pt_core_news_md 3.8.0", page.get_data(as_text=True))
        self.assertEqual(self.client.get("/envios/1/anotacao.json", query_string={"anotacao_id": old["anotacao_id"]}).json, old)

    def test_invalid_or_foreign_selection_has_no_side_effects(self):
        self.client.post("/envios", data={"content": "Outro texto."})
        other = self.client.post("/envios/2/segmentacoes", json={}).get_json()
        before = self.snapshot()
        cases = [({}, 400), ({"segmentacao_id": self.segmentation["segmentacao_id"], "modelo": "modelo-inventado"}, 400),
                 ({"segmentacao_id": other["segmentacao_id"], "modelo": "pt_core_news_sm"}, 404)]
        with patch("anotacao._executar", side_effect=AssertionError("Não inferir entrada inválida")):
            for payload, status in cases:
                self.assertEqual(self.client.post("/envios/1/gramatica/reprocessar", json=payload).status_code, status)
            duplicated = MultiDict([("segmentacao_id", self.segmentation["segmentacao_id"]),
                                   ("modelo", "pt_core_news_sm"), ("modelo", "pt_core_news_md")])
            self.assertEqual(self.client.post("/envios/1/gramatica/reprocessar", data=duplicated).status_code, 400)
        self.assertEqual(self.client.get("/envios/1/gramatica/reprocessar").status_code, 405)
        self.assertEqual(self.snapshot(), before)

    def test_unavailable_model_does_not_replace_previous_results(self):
        self.assertEqual(self.reprocess().status_code, 201)
        before = self.snapshot()
        with patch("anotacao._carregar_modelo", side_effect=ErroModelo("Modelo médio indisponível")):
            failed = self.reprocess("pt_core_news_md")
        self.assertEqual(failed.status_code, 503)
        self.assertTrue(failed.json["resultados_concluidos_preservados"])
        self.assertEqual(self.snapshot(), before)

    def test_review_exports_diagnostics_and_vectors_after_reprocessing(self):
        self.assertEqual(self.reprocess().status_code, 201)
        response = self.client.post("/envios/1/revisao.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Lingua-Revisao-Estado"], "completo")
        with ZipFile(BytesIO(response.data)) as package:
            diagnostic = json.loads(package.read("revisao_gramatical.json"))
            complete = json.loads(package.read("processamento_completo.json"))
            self.assertEqual(complete["revisao_gramatical"], diagnostic)
            self.assertEqual(len(diagnostic["execucoes"]), 1)
            self.assertFalse(diagnostic["erros"])
            self.assertTrue(complete["processamento_solicitado"]["completo"])
            rules = complete["etapas"]["07"]["registros"][0]["registro"]
            self.assertEqual(rules["processamento"]["modulo"]["versao"], "1.2.0")
        response.close()


if __name__ == "__main__":
    unittest.main()
