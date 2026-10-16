"""Subtipos aparecem na navegação, na análise sintática e no ZIP de revisão."""

from io import BytesIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app import create_app
from fixtures_vetorizacao import AdaptadorControlado
from visualizacao_segmentacao import construir_visualizacao_segmentacao


class ClauseSubtypeAppTests(unittest.TestCase):
    TEXT = ("Eu ainda guardo no bolso do casaco as meias de lã que minha avó tricotava "
            "para os netos, embora nunca tivesse um neto mulher.")

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Path(folder.name) / "subtipos.sqlite3"
        self.client = create_app({"TESTING": True, "DATABASE": str(self.database),
            "GRAMMAR_MODEL": "pt_core_news_sm", "VECTORS_AUTO_START": False,
            "VECTORS_ADAPTER_FACTORY": AdaptadorControlado}).test_client()
        self.client.post("/envios", data={"content": self.TEXT})
        segmented = self.client.post("/envios/1/segmentacoes", json={}).get_json()
        response = self.client.post("/envios/1/gramatica/reprocessar", json={
            "segmentacao_id": segmented["segmentacao_id"], "modelo": "pt_core_news_sm"})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.execution = response.json["execucao_id"]
        self.rules = self.client.get("/envios/1/regras.json", query_string={"execucao_id": self.execution}).json
        analysis = self.rules["analise"]
        annotation = analysis["anotacao"]
        view = construir_visualizacao_segmentacao(annotation["segmentacao"], annotation, analysis, self.rules)
        self.clauses = view["paragraphs"][0]["periods"][0]["clauses"]

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return tuple(connection.iterdump())

    def test_headings_show_specific_subtypes_for_the_original_example(self):
        labels = [clause["classification"] for clause in self.clauses]
        self.assertEqual(labels, ["Oração principal", "Oração subordinada adjetiva restritiva",
                                 "Oração subordinada adverbial concessiva"])
        before = self.snapshot()
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("Leitura não infere")), \
                patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("Leitura não infere")):
            page = self.client.get("/envios/1", query_string={"execucao_id": self.execution}).get_data(as_text=True)
        self.assertIn("Oração subordinada adjetiva restritiva", page)
        self.assertIn("Oração subordinada adverbial concessiva", page)
        self.assertEqual(self.snapshot(), before)

    def test_ajax_syntax_contains_explanation_and_morphology_keeps_its_own_view(self):
        before = self.snapshot()
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("Análise salva")), \
                patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("Análise salva")):
            for clause in self.clauses[1:]:
                payload = {"execucao_id": self.execution, "oracao_id": clause["id"], "tipo": "sintaxe"}
                syntax = self.client.post("/envios/1/segmentacao/analise-oracao", json=payload)
                self.assertEqual(syntax.status_code, 200)
                self.assertIn(clause["classification"], syntax.json["html"])
                self.assertIn('aria-label="Classificação da oração"', syntax.json["html"])
                self.assertIn("Elementos usados na classificação", syntax.json["html"])
                morphology = self.client.post("/envios/1/segmentacao/analise-oracao", json={**payload, "tipo": "morfologia"})
                self.assertEqual(morphology.status_code, 200)
                self.assertNotIn('aria-label="Classificação da oração"', morphology.json["html"])
                self.assertIn("data-morph-word", morphology.json["html"])
        self.assertEqual(self.snapshot(), before)

    def test_without_javascript_syntax_has_the_same_classification(self):
        clause = self.clauses[2]
        response = self.client.post("/envios/1/segmentacao/analise-oracao", data={
            "execucao_id": self.execution, "oracao_id": clause["id"], "tipo": "sintaxe"})
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.location).get_data(as_text=True)
        self.assertIn('aria-label="Classificação da oração"', page)
        self.assertIn("Oração subordinada adverbial concessiva", page)

    def test_review_package_preserves_raw_results_and_exports_subtypes(self):
        response = self.client.post("/envios/1/revisao.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Lingua-Revisao-Estado"], "completo")
        with ZipFile(BytesIO(response.data)) as archive:
            review = json.loads(archive.read("revisao_gramatical.json"))
            complete = json.loads(archive.read("processamento_completo.json"))
            classification = review["execucoes"][0]["classificacao_oracoes"]
            labels = {item["rotulo"] for item in classification["oracoes"] if item["estado"] == "classificada"}
            self.assertEqual(labels, {"Oração subordinada adjetiva restritiva", "Oração subordinada adverbial concessiva"})
            self.assertEqual(complete["revisao_gramatical"], review)
            self.assertEqual(complete["etapas"]["07"]["registros"][0]["registro"], self.rules)
            self.assertEqual(archive.read("texto_original.txt").decode(), self.TEXT)
        response.close()


if __name__ == "__main__":
    unittest.main()
