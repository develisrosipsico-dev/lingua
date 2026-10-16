"""Atalho para Vetores: cadeia real, inferência controlada e histórico durável."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app import create_app, load_vector_context
from anotacao import ErroModelo
from fixtures_contexto import LINHAS, TEXTO
from fixtures_regras import construir_analise, ponto, token
from fixtures_vetorizacao import AdaptadorControlado
from trabalhos_vetorizacao import processar_trabalho
from unidades_contexto import validar_unidades_contexto


class VectorShortcutIntegrationTests(unittest.TestCase):
    TABLES = (
        "submissions", "preparations", "segmentations", "annotations",
        "analyses", "rule_runs", "context_runs",
    )

    @classmethod
    def setUpClass(cls):
        # Atributos e árvores manuais das fixtures; só o tokenizador é real.
        cls.analysis_template = construir_analise(texto=TEXTO, linhas=LINHAS)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.client = create_app({
            "TESTING": True, "DATABASE": str(self.database),
            "VECTORS_AUTO_START": False,
        }).test_client()

    def rows(self, table):
        self.assertIn(table, self.TABLES)
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]

    def latest_record(self, table):
        return json.loads(self.rows(table)[-1]["record_json"])

    def morphology(self, segmented, _text):
        annotation_template = self.analysis_template["anotacao"]
        expected = annotation_template["segmentacao"]["tokens"]
        self.assertEqual([t["texto"] for t in segmented["tokens"]], [t["texto"] for t in expected])
        annotations = []
        for token, reference in zip(segmented["tokens"], annotation_template["anotacoes_tokens"]):
            annotation = deepcopy(reference)
            annotation.update({key: deepcopy(token[key]) for key in (
                "texto", "periodo_id", "paragrafo_id", "trabalho", "original",
            )})
            annotation["token_id"] = token["id"]
            annotations.append(annotation)
        processing = deepcopy(annotation_template["processamento"])
        processing["configuracoes"]["tokenizador"]["versao_origem"] = segmented["processamento"]["versao"]
        return annotations, processing

    def syntax(self, annotated, _text):
        segmented = annotated["segmentacao"]
        reference_tokens = self.analysis_template["anotacao"]["segmentacao"]["tokens"]
        token_ids = {old["id"]: new["id"] for old, new in zip(reference_tokens, segmented["tokens"])}
        syntax = []
        for token, reference in zip(segmented["tokens"], self.analysis_template["sintaxe"]):
            item = deepcopy(reference)
            item.update({key: deepcopy(token[key]) for key in (
                "texto", "periodo_id", "paragrafo_id", "trabalho", "original",
            )})
            item["token_id"] = token["id"]
            item["cabeca_token_id"] = token_ids[reference["cabeca_token_id"]]
            syntax.append(item)
        processing = deepcopy(self.analysis_template["processamento"])
        processing["configuracoes"]["tokenizador"]["versao_origem"] = segmented["processamento"]["versao"]
        return syntax, [], processing

    def shortcut(self, **options):
        with patch("anotacao._executar", side_effect=self.morphology), patch(
            "sintaxe_entidades._executar", side_effect=self.syntax,
        ):
            return self.client.post("/envios", data={"content": TEXTO, "destino": "vetorizacao", **options})

    def test_shortcut_preserves_complete_chain_and_opens_vectors_without_running_e5(self):
        with patch("app.enfileirar_vetorizacao") as enqueue, patch("app.iniciar_trabalho") as start:
            response = self.shortcut()
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        enqueue.assert_not_called()
        start.assert_not_called()
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.fragment, "etapa-vetorizacao")
        self.assertEqual(location.path, "/envios/1")
        context = self.latest_record("context_runs")
        self.assertEqual(parse_qs(location.query)["contexto_execucao_id"], [context["execucao_id"]])
        self.assertTrue(validar_unidades_contexto(context)["pronto_para_etapa_09"])
        self.assertEqual(context["configuracao"], {
            "raio_anterior": 1, "raio_seguinte": 1, "atravessar_paragrafos": False,
        })
        self.assertEqual(len(context["unidades"]), 5)
        for table in self.TABLES:
            self.assertEqual(len(self.rows(table)), 1, table)
        self.assertEqual(self.rows("submissions")[0]["content"], TEXTO)
        html = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Gerar vetores", html)
        self.assertIn("Gerar notebook para o Colab", html)
        self.assertNotIn("Preparando o processamento.", html)

    def test_regular_registration_keeps_only_original_and_preparation(self):
        with patch("app.segmentar_preparacao") as segmentation, patch("app.anotar_segmentacao") as annotation:
            response = self.client.post("/envios", data={"content": TEXTO})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/envios/1")
        segmentation.assert_not_called()
        annotation.assert_not_called()
        self.assertEqual(len(self.rows("submissions")), 1)
        self.assertEqual(len(self.rows("preparations")), 1)
        for table in self.TABLES[2:]:
            self.assertEqual(self.rows(table), [], table)

    def test_annotation_failure_keeps_completed_stages_and_escapes_error(self):
        with patch("app.anotar_segmentacao", side_effect=ErroModelo("Modelo ausente <script>alert(1)</script>")):
            response = self.client.post("/envios", data={"content": TEXTO, "destino": "vetorizacao"})
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        for table in self.TABLES[:3]:
            self.assertEqual(len(self.rows(table)), 1, table)
        for table in self.TABLES[3:]:
            self.assertEqual(self.rows(table), [], table)
        self.assertEqual(self.rows("submissions")[0]["content"], TEXTO)
        html = response.get_data(as_text=True)
        self.assertIn("Modelo ausente &lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn('data-default-stage="morfologia"', html)
        self.assertEqual(self.client.get("/envios/1/segmentacao.json").status_code, 200)

    def test_incomplete_rules_are_saved_and_stop_before_context(self):
        with patch("regras_linguisticas.detectar_regra", side_effect=RuntimeError("Falha controlada")):
            response = self.shortcut()
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        for table in self.TABLES[:-1]:
            self.assertEqual(len(self.rows(table)), 1, table)
        self.assertEqual(self.rows("context_runs"), [])
        ruled = self.latest_record("rule_runs")
        self.assertFalse(ruled["validacao"]["pronto_para_etapa_08"])
        self.assertTrue(any(rule["estado"] == "falhou" for rule in ruled["execucoes_regras"]))
        html = response.get_data(as_text=True)
        self.assertIn('data-default-stage="regras"', html)
        self.assertIn("Execução salva com diagnóstico incompleto", html)
        self.assertEqual(self.client.get("/envios/1/regras.json").status_code, 200)

    def test_shortcut_respects_crlf_option_without_changing_original(self):
        response = self.shortcut(normalizar_crlf="on")
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        prepared = self.latest_record("preparations")
        self.assertEqual(self.rows("submissions")[0]["content"], TEXTO)
        self.assertEqual(prepared["original"]["texto"], TEXTO)
        self.assertEqual(prepared["trabalho"]["texto"], TEXTO.replace("\r\n", "\n"))
        self.assertTrue(prepared["politica"]["opcoes"]["normalizar_crlf"])
        context = self.latest_record("context_runs")
        self.assertTrue(validar_unidades_contexto(context)["pronto_para_etapa_09"])

    def test_empty_shortcut_does_not_register_or_process_a_document(self):
        with patch("app.segmentar_preparacao") as segmentation:
            response = self.client.post("/envios", data={"content": " \n ", "destino": "vetorizacao"})
        self.assertEqual(response.status_code, 400)
        segmentation.assert_not_called()
        for table in self.TABLES:
            self.assertEqual(self.rows(table), [], table)

    def test_one_paragraph_over_400_words_reaches_vector_export_from_the_first_page(self):
        text = " ".join(["palavra"] * 401) + "."
        rows = [token("palavra", "palavra", "NOUN", 0, "ROOT" if i == 0 else "nmod") for i in range(401)]
        self.analysis_template = construir_analise(texto=text, linhas=rows + [ponto(0)])
        with patch("anotacao._executar", side_effect=self.morphology), patch("sintaxe_entidades._executar", side_effect=self.syntax):
            response = self.client.post("/envios", data={"content": text, "destino": "vetorizacao"})
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("Parágrafos, contextos e documento — E5", page)
        self.assertNotIn("O perfil do Agente Analista requer", page)
        context = self.latest_record("context_runs")
        queued = self.client.post("/envios/1/vetorizacoes", json={"contexto_execucao_id": context["execucao_id"]})
        self.assertEqual(queued.status_code, 202)
        job = queued.get_json()
        completed = processar_trabalho(self.database, job["execucao_id"],
            fabrica_adaptador=AdaptadorControlado, carregar_contexto=load_vector_context)
        self.assertEqual(completed["estado"], "concluida", completed)
        exported = self.client.get("/envios/1/vetorizacao.json", query_string={"vetorizacao_execucao_id": job["execucao_id"]})
        self.assertEqual(exported.status_code, 200)
        record = exported.get_json()
        self.assertEqual([r["tipo"] for r in record["representacoes"]], ["paragrafo", "contextual", "documento"])
        self.assertEqual(record["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["original"]["texto"], text)


if __name__ == "__main__":
    unittest.main()
