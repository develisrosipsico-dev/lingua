"""Revisão completa processa o texto inteiro e preserva falhas para diagnóstico."""

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
from fixtures_contexto import TEXTO, LINHAS
from fixtures_regras import construir_analise
from fixtures_vetorizacao import AdaptadorControlado
import test_app_atalho_vetorizacao as origem_controlada


class CompleteReviewTests(unittest.TestCase):
    morphology = origem_controlada.VectorShortcutIntegrationTests.morphology
    syntax = origem_controlada.VectorShortcutIntegrationTests.syntax

    @classmethod
    def setUpClass(cls):
        cls.analysis_template = construir_analise(texto=TEXTO, linhas=LINHAS)

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "revisao.sqlite3"
        self.client = create_app({"TESTING": True, "DATABASE": str(self.database),
            "VECTORS_AUTO_START": False, "VECTORS_ADAPTER_FACTORY": AdaptadorControlado}).test_client()
        self.client.post("/envios", data={"content": TEXTO})

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return tuple(connection.iterdump())

    def review(self, **data):
        with patch("anotacao._executar", side_effect=self.morphology), patch("sintaxe_entidades._executar", side_effect=self.syntax):
            response = self.client.post("/envios/1/revisao.zip", data=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True) if response.mimetype != "application/zip" else "")
        self.assertEqual(response.mimetype, "application/zip")
        with ZipFile(BytesIO(response.data)) as archive:
            report = json.loads(archive.read("processamento_completo.json"))
            self.assertEqual(archive.read("texto_original.txt").decode("utf-8"), TEXTO)
        response.close()
        return response, report

    def test_first_page_button_completes_unopened_periods_and_vectors(self):
        page = self.client.get("/envios/1").get_data(as_text=True)
        self.assertIn("Gerar revisão completa", page)
        response, report = self.review()
        self.assertEqual(response.headers["X-Lingua-Revisao-Estado"], "completo")
        self.assertTrue(report["processamento_solicitado"]["completo"])
        analysis = report["etapas"]["06"]["registros"][0]["registro"]
        self.assertEqual(len(analysis["anotacao"]["segmentacao"]["periodos"]), 5)
        self.assertEqual(len(analysis["sintaxe"]), len(LINHAS))
        self.assertEqual(len(analysis["anotacao"]["anotacoes_tokens"]), len(LINHAS))
        self.assertEqual(report["etapas"]["09"]["execucoes"][0]["validacao"]["estado"], "valido")

    def test_repeating_review_reuses_the_complete_chain_without_inference_or_new_rows(self):
        self.review()
        before = self.snapshot()
        with patch("anotacao._executar", side_effect=AssertionError("Não repetir o modelo")), patch(
                "sintaxe_entidades._executar", side_effect=AssertionError("Não repetir o modelo")), patch(
                "app.processar_trabalho", side_effect=AssertionError("Não repetir o E5")):
            response = self.client.post("/envios/1/revisao.zip", data={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Lingua-Revisao-Estado"], "completo")
        self.assertEqual(self.snapshot(), before)
        response.close()

    def test_missing_grammar_model_still_downloads_saved_steps_and_error(self):
        with patch("anotacao._executar", side_effect=ErroModelo("Modelo de teste indisponível")):
            response = self.client.post("/envios/1/revisao.zip", data={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Lingua-Revisao-Estado"], "parcial")
        with ZipFile(BytesIO(response.data)) as archive:
            report = json.loads(archive.read("processamento_completo.json"))
        self.assertEqual(report["processamento_solicitado"]["erros"][0]["etapa"], "05")
        self.assertIn("indisponível", report["processamento_solicitado"]["erros"][0]["mensagem"])
        self.assertEqual(len(report["etapas"]["04"]["registros"]), 1)
        self.assertEqual(report["etapas"]["06"]["registros"], [])
        response.close()

    def test_failed_e5_does_not_discard_grammar_or_claim_complete_processing(self):
        def fail(profile):
            return AdaptadorControlado(profile, falhar=True)
        self.client.application.config["VECTORS_ADAPTER_FACTORY"] = fail
        response, report = self.review()
        self.assertEqual(response.headers["X-Lingua-Revisao-Estado"], "parcial")
        self.assertFalse(report["processamento_solicitado"]["completo"])
        self.assertEqual(report["processamento_solicitado"]["erros"][0]["etapa"], "09")
        self.assertEqual(len(report["etapas"]["06"]["registros"]), 1)
        self.assertEqual(report["etapas"]["09"]["trabalhos"][0]["banco"]["state"], "falhou")

    def test_invalid_or_cross_document_origin_never_starts_processing(self):
        self.client.post("/envios", data={"content": "Outro texto."})
        with sqlite3.connect(self.database) as connection:
            foreign = connection.execute("SELECT preparation_id FROM preparations WHERE submission_id=2").fetchone()[0]
        before = self.snapshot()
        with patch("anotacao._executar", side_effect=AssertionError("Não processar origem inválida")):
            self.assertEqual(self.client.get("/envios/1/revisao.zip").status_code, 405)
            self.assertEqual(self.client.post("/envios/99/revisao.zip").status_code, 404)
            self.assertEqual(self.client.post("/envios/1/revisao.zip", data={"preparacao_id": foreign}).status_code, 404)
            self.assertEqual(self.client.post("/envios/1/revisao.zip", data={"opcao": "extra"}).status_code, 400)
            repeated = MultiDict([("preparacao_id", foreign), ("preparacao_id", foreign)])
            self.assertEqual(self.client.post("/envios/1/revisao.zip", data=repeated).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_old_job_does_not_block_processing_another_selected_context(self):
        _, first = self.review()
        context = first["etapas"]["08"]["registros"][0]["registro"]
        created = self.client.post("/envios/1/contextos", json={"regras_execucao_id": context["regras_execucao_id"],
                                                            "raio_anterior": 0, "raio_seguinte": 0})
        self.assertEqual(created.status_code, 201, created.get_json())
        selected = created.get_json()["execucao_id"]
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE vector_jobs SET options_json='{' WHERE context_execution_id=?", (context["execucao_id"],))
        response, report = self.review(contexto_execucao_id=selected)
        self.assertEqual(response.headers["X-Lingua-Revisao-Estado"], "completo")
        self.assertTrue(report["processamento_solicitado"]["completo"])
        self.assertEqual(report["etapas"]["09"]["estado"], "com_erros")
        self.assertEqual(len(report["etapas"]["09"]["trabalhos"]), 2)


if __name__ == "__main__":
    unittest.main()
