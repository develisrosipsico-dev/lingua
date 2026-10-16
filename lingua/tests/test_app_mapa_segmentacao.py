"""Abertura de períodos e análises por oração sem inferência neural nos testes."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from app import create_app
from anotacao import ErroModelo
from fixtures_regras import CASOS, construir_analise
from visualizacao_segmentacao import construir_visualizacao_segmentacao


class ClauseAnalysisIntegrationTests(unittest.TestCase):
    TEXT = "Ela quer sair.\r\n\r\nEla quer sair."
    TABLES = ("submissions", "preparations", "segmentations", "annotations", "analyses", "rule_runs")

    @classmethod
    def setUpClass(cls):
        rows = CASOS["desejo"]["tokens"]
        second = [(text, lemma, pos, head + len(rows), dependency, morph)
                  for text, lemma, pos, head, dependency, morph in rows]
        cls.reference = construir_analise(texto=cls.TEXT, linhas=rows + second, normalizar=True)

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "mapa.sqlite3"
        self.client = create_app({"TESTING": True, "DATABASE": str(self.database),
                                  "VECTORS_AUTO_START": False}).test_client()
        self.client.post("/envios", data={"content": self.TEXT, "normalizar_crlf": "on"})
        self.seg = self.client.post("/envios/1/segmentacoes", json={}).get_json()
        self.data = {"segmentacao_id": self.seg["segmentacao_id"], "periodo_id": self.seg["periodos"][0]["id"]}

    def rows(self, table):
        self.assertIn(table, self.TABLES)
        with sqlite3.connect(self.database) as c:
            return c.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def snapshot(self):
        return {t: self.rows(t) for t in self.TABLES}

    def morphology(self, segmented, _text):
        template = self.reference["anotacao"]
        output = []
        for source, ref in zip(segmented["tokens"], template["anotacoes_tokens"]):
            item = deepcopy(ref)
            item.update({k: deepcopy(source[k]) for k in ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")})
            item["token_id"] = source["id"]
            output.append(item)
        metadata = deepcopy(template["processamento"])
        metadata["configuracoes"]["tokenizador"]["versao_origem"] = segmented["processamento"]["versao"]
        return output, metadata

    def syntax(self, annotated, _text):
        seg = annotated["segmentacao"]
        old = self.reference["anotacao"]["segmentacao"]["tokens"]
        ids = {a["id"]: b["id"] for a, b in zip(old, seg["tokens"])}
        output = []
        for source, ref in zip(seg["tokens"], self.reference["sintaxe"]):
            item = deepcopy(ref)
            item.update({k: deepcopy(source[k]) for k in ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")})
            item["token_id"] = source["id"]
            item["cabeca_token_id"] = ids[ref["cabeca_token_id"]]
            output.append(item)
        metadata = deepcopy(self.reference["processamento"])
        metadata["configuracoes"]["tokenizador"]["versao_origem"] = seg["processamento"]["versao"]
        return output, [], metadata

    def identify(self, data=None):
        with patch("anotacao._executar", side_effect=self.morphology), patch("sintaxe_entidades._executar", side_effect=self.syntax):
            response = self.client.post("/envios/1/segmentacao/mapa-periodo", json=data or self.data)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def first_clause(self, execution):
        ruled = json.loads(self.rows("rule_runs")[-1][-1])
        analysis = ruled["analise"]
        annotation = analysis["anotacao"]
        view = construir_visualizacao_segmentacao(annotation["segmentacao"], annotation, analysis, ruled)
        return {"execucao_id": execution["execucao_id"], "oracao_id": view["paragraphs"][0]["periods"][0]["clauses"][0]["id"]}

    def test_opening_a_saved_map_does_not_run_models_or_change_exports(self):
        before = self.snapshot()
        with patch("anotacao._executar", side_effect=AssertionError("GET não infere")), patch("sintaxe_entidades._executar", side_effect=AssertionError("GET não infere")):
            page = self.client.get("/envios/1").get_data(as_text=True)
        self.assertIn('id="seg-paragrafo-1"', page)
        self.assertIn('id="seg-paragrafo-2"', page)
        self.assertNotIn('class="map-clause"', page)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client.get("/envios/1/segmentacao.json").get_json(), self.seg)

    def test_each_clause_analysis_is_requested_independently_and_reuses_the_chain(self):
        with patch("app.enfileirar_vetorizacao") as enqueue, patch("app.iniciar_trabalho") as start:
            execution = self.identify()
        enqueue.assert_not_called()
        start.assert_not_called()
        self.assertEqual(execution["html"].count('class="map-clause"'), 2)
        self.assertTrue(execution["atualizou_cadeia"])
        self.assertIn("Análise sintática", execution["html"])
        self.assertNotIn("<table", execution["html"])
        target = self.first_clause(execution)
        before = self.snapshot()
        with patch("anotacao._executar", side_effect=AssertionError("cache")), patch("sintaxe_entidades._executar", side_effect=AssertionError("cache")):
            for kind, column, absent in (("sintaxe", "Cabeça sintática", "Traços morfológicos"),
                                          ("morfologia", "Traços morfológicos", "Cabeça sintática")):
                result = self.client.post("/envios/1/segmentacao/analise-oracao", json={**target, "tipo": kind})
                self.assertEqual(result.status_code, 200, result.get_json())
                self.assertIn(column, result.get_json()["html"])
                self.assertNotIn(absent, result.get_json()["html"])
                self.assertEqual(result.get_json()["oracao_id"], target["oracao_id"])
            cached = self.client.post("/envios/1/segmentacao/mapa-periodo", json=self.data)
            self.assertEqual(cached.status_code, 200)
            self.assertFalse(cached.get_json()["atualizou_cadeia"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client.get("/envios/1/segmentacao.json").get_json(), self.seg)

    def test_no_javascript_forms_redirect_to_the_same_clause_and_analysis(self):
        execution = self.identify()
        target = self.first_clause(execution)
        response = self.client.post("/envios/1/segmentacao/analise-oracao", data={**target, "tipo": "morfologia"})
        self.assertEqual(response.status_code, 303)
        self.assertIn("#seg-oracao-1-1", response.location)
        html = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("Traços morfológicos", html)
        self.assertIn('data-loaded="true"', html)

    def test_ids_from_other_documents_or_non_clause_occurrences_are_rejected(self):
        execution = self.identify()
        target = self.first_clause(execution)
        self.client.post("/envios", data={"content": "Outro documento."})
        for endpoint, data in (("mapa-periodo", self.data), ("analise-oracao", {**target, "tipo": "sintaxe"})):
            self.assertEqual(self.client.post(f"/envios/2/segmentacao/{endpoint}", json=data).status_code, 404)
        self.assertEqual(self.client.post("/envios/1/segmentacao/analise-oracao", json={**target, "oracao_id": "inexistente", "tipo": "sintaxe"}).status_code, 404)

    def test_invalid_or_duplicated_parameters_do_not_start_analysis(self):
        before = self.snapshot()
        with patch("anotacao._executar", side_effect=AssertionError("não executar")):
            for data in ({}, {**self.data, "extra": True}, {**self.data, "periodo_id": " "}):
                self.assertEqual(self.client.post("/envios/1/segmentacao/mapa-periodo", json=data).status_code, 400)
            duplicate = MultiDict([*self.data.items(), ("periodo_id", self.data["periodo_id"])])
            self.assertEqual(self.client.post("/envios/1/segmentacao/mapa-periodo", data=duplicate).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_unavailable_model_preserves_segmentation_and_can_be_retried(self):
        before = self.snapshot()
        with patch("anotacao._executar", side_effect=ErroModelo("Modelo indisponível")):
            response = self.client.post("/envios/1/segmentacao/mapa-periodo", json=self.data)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)
        self.identify()

    def test_selected_history_is_not_replaced_by_a_later_annotation(self):
        first = self.identify()
        with patch("anotacao._executar", side_effect=self.morphology):
            newer = self.client.post("/envios/1/anotacoes", json={"segmentacao_id": self.seg["segmentacao_id"]})
        self.assertEqual(newer.status_code, 201)
        before = self.snapshot()
        with patch("sintaxe_entidades._executar", side_effect=AssertionError("A seleção histórica deve ser respeitada")):
            response = self.client.post("/envios/1/segmentacao/mapa-periodo", json={**self.data, "execucao_id": first["execucao_id"]})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["execucao_id"], first["execucao_id"])
        self.assertFalse(response.get_json()["atualizou_cadeia"])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
