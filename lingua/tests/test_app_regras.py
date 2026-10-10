"""Etapa 07 no Flask: origem exata, histórico, diagnósticos e SQLite real.

As previsões do modelo são usadas apenas para conferir a integração. O gabarito
linguístico independente está nos testes próprios do mecanismo de regras.
"""

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from werkzeug.datastructures import MultiDict

import app as app_module
from app import create_app
from anotacao import validar_anotacao
from regras_linguisticas import ErroRegras, validar_regras_linguisticas


class RulesIntegrationTests(unittest.TestCase):
    INSTANTE = "2026-10-07T12:34:56-03:00"
    TABELAS = ("submissions", "preparations", "segmentations", "annotations", "analyses", "rule_runs")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()
        self.original = "João não chegou hoje.\r\nMaria pode sair amanhã."

    def rows(self, table):
        self.assertIn(table, self.TABELAS)
        with sqlite3.connect(self.database) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def snapshot(self):
        return {table: self.rows(table) for table in self.TABELAS}

    def submit(self, content=None, **options):
        response = self.client.post("/envios", data={"content": self.original if content is None else content, **options})
        self.assertEqual(response.status_code, 303)
        document_id = int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[-1])
        return document_id, self.client.get(f"/envios/{document_id}/preparacao.json").get_json()

    def earlier(self, document_id, stage, **options):
        response = self.client.post(f"/envios/{document_id}/{stage}", json=options)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def source(self, **options):
        document_id, prepared = self.submit(**options)
        segmented = self.earlier(document_id, "segmentacoes")
        annotated = self.earlier(document_id, "anotacoes")
        analyzed = self.earlier(document_id, "analises")
        return document_id, prepared, segmented, annotated, analyzed

    def run_rules(self, document_id, *, expected_status=201, **options):
        response = self.client.post(f"/envios/{document_id}/regras", json=options)
        self.assertEqual(response.status_code, expected_status, response.get_data(as_text=True))
        ruled = response.get_json()
        validar_regras_linguisticas(ruled)
        return ruled

    def download(self, document_id, execution_id=None, client=None):
        query = {} if execution_id is None else {"execucao_id": execution_id}
        return (client or self.client).get(f"/envios/{document_id}/regras.json", query_string=query)

    def replace_json(self, execution_id, ruled):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE rule_runs SET record_json = ? WHERE execution_id = ?",
                (json.dumps(ruled, ensure_ascii=False, allow_nan=False), execution_id),
            )

    def assert_conflict(self, document_id, execution_id):
        response = self.download(document_id, execution_id)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("erro", response.get_json())
        response = self.client.get(f"/envios/{document_id}", query_string={"execucao_id": execution_id})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))

    def test_earlier_stages_do_not_automatically_apply_rules(self):
        document_id, _prepared = self.submit()
        self.assertEqual(self.rows("rule_runs"), [])
        self.assertNotIn("Aplicar regras linguísticas", self.client.get(f"/envios/{document_id}").get_data(as_text=True))
        self.earlier(document_id, "segmentacoes")
        self.earlier(document_id, "anotacoes")
        self.assertNotIn("Aplicar regras linguísticas", self.client.get(f"/envios/{document_id}").get_data(as_text=True))
        analyzed = self.earlier(document_id, "analises")
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Aplicar regras linguísticas", html)
        self.assertIn(f'name="analise_id" value="{analyzed["analise_id"]}"', html)
        self.assertNotIn("Baixar regras JSON", html)
        self.assertEqual(self.rows("rule_runs"), [])
        self.assertEqual(self.download(document_id).status_code, 404)

    def test_real_chain_is_preserved_and_model_is_not_run_again(self):
        document_id, prepared, segmented, annotated, analyzed = self.source(normalizar_crlf="on")
        before = self.snapshot()
        with patch("app.analisar_sintaxe_entidades") as analysis_model, patch("app.anotar_segmentacao") as annotation_model:
            ruled = self.run_rules(
                document_id, analise_id=analyzed["analise_id"],
                execucao_id="regras/estáveis", registrado_em=self.INSTANTE,
            )
        analysis_model.assert_not_called()
        annotation_model.assert_not_called()
        self.assertEqual(ruled["execucao_id"], "regras/estáveis")
        self.assertEqual(ruled["registrado_em"], self.INSTANTE)
        self.assertEqual(ruled["analise"], analyzed)
        self.assertEqual(ruled["analise"]["anotacao"], annotated)
        self.assertEqual(ruled["analise"]["anotacao"]["segmentacao"], segmented)
        self.assertEqual(ruled["analise"]["anotacao"]["segmentacao"]["preparacao"], prepared)
        for table in self.TABELAS[:-1]:
            self.assertEqual(self.rows(table), before[table])
        self.assertTrue(ruled["validacao"]["pronto_para_etapa_08"])
        downloaded = self.download(document_id, ruled["execucao_id"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_json(), ruled)
        self.assertEqual(downloaded.mimetype, "application/json")
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])

    def test_form_redirect_selects_exact_chain_and_html_escapes_original(self):
        document_id, _prepared, _segmented, _annotated, analyzed = self.source(
            content="João não chegou hoje. <script>alert('texto')</script>.",
        )
        response = self.client.post(f"/envios/{document_id}/regras", data={"analise_id": analyzed["analise_id"]})
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        for field in ("preparacao_id", "segmentacao_id", "anotacao_id", "analise_id"):
            self.assertEqual(query[field], [analyzed[field]])
        self.assertIn("execucao_id", query)
        detail = self.client.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200, detail.get_data(as_text=True))
        html = detail.get_data(as_text=True)
        for label in ("Regras linguísticas", "Orações", "Negação", "Modalidade", "Tempo", "Conectores", "Catálogo e estado", "Baixar regras JSON", "Evidências", "Alcance proposto"):
            self.assertIn(label, html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("não representam interpretações definitivas", html)

    def test_disabled_configuration_is_saved_without_false_readiness(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        ruled = self.run_rules(document_id, expected_status=422, regras_habilitadas=[])
        self.assertFalse(ruled["validacao"]["pronto_para_etapa_08"])
        self.assertEqual(ruled["ocorrencias"], [])
        self.assertTrue(all(item["estado"] == "desabilitada" for item in ruled["execucoes_regras"]))
        self.assertEqual(len(self.rows("rule_runs")), 1)
        downloaded = self.download(document_id, ruled["execucao_id"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_json(), ruled)
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Execução salva com diagnóstico incompleto", html)
        self.assertIn("Desabilitada", html)
        self.assertNotIn("Registro validado e pronto para a etapa 08", html)
        self.assertNotIn("Regra executada sem ocorrências", html)

    def test_zero_occurrences_are_distinct_from_disabled_rules(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source(content="Casa.")
        ruled = self.run_rules(document_id, regras_habilitadas=["negacao_nao"])
        self.assertEqual(ruled["ocorrencias"], [])
        self.assertTrue(ruled["validacao"]["pronto_para_etapa_08"])
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Regra executada sem ocorrências", html)
        self.assertIn("Nenhuma ocorrência nas regras executadas desta família", html)
        self.assertIn("Regras desta família desabilitadas", html)

    def test_failed_rule_is_persisted_and_readable_as_diagnostic(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        with patch("regras_linguisticas.detectar_regra", side_effect=RuntimeError("Falha controlada")):
            ruled = self.run_rules(document_id, expected_status=422, regras_habilitadas=["negacao_nao"])
        self.assertFalse(ruled["validacao"]["pronto_para_etapa_08"])
        failed = [execution for execution in ruled["execucoes_regras"] if execution["regra_id"] == "negacao_nao"]
        self.assertEqual(failed[0]["estado"], "falhou")
        self.assertEqual(failed[0]["ocorrencias_total"], 0)
        self.assertEqual(ruled["ocorrencias"], [])
        self.assertEqual(len(self.rows("rule_runs")), 1)
        self.assertEqual(self.download(document_id, ruled["execucao_id"]).get_json(), ruled)
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Falha na execução", html)
        self.assertIn("Uma regra desta família falhou", html)
        self.assertIn("Execução salva com diagnóstico incompleto", html)
        self.assertNotIn("Nenhuma ocorrência nas regras executadas desta família", html)
        self.assertNotIn("Registro validado e pronto para a etapa 08", html)

    def test_failed_form_redirects_to_saved_diagnostic_without_success_message(self):
        document_id, _prepared, _segmented, _annotated, analyzed = self.source()
        with patch("regras_linguisticas.detectar_regra", side_effect=RuntimeError("Falha controlada")):
            response = self.client.post(f"/envios/{document_id}/regras", data={"analise_id": analyzed["analise_id"]})
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        self.assertEqual(len(self.rows("rule_runs")), 1)
        html = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Execução salva com diagnóstico incompleto", html)
        self.assertIn("Falha na execução", html)
        self.assertNotIn("Registro validado e pronto para a etapa 08", html)

    def test_missing_morphology_is_saved_as_insufficient_annotation(self):
        document_id, _prepared = self.submit(content="Ela chegou.")
        self.earlier(document_id, "segmentacoes")
        annotated = self.earlier(document_id, "anotacoes")
        for token in annotated["anotacoes_tokens"]:
            if token["pos"] in {"VERB", "AUX"}:
                token["morfologia"] = None
                token["campos_nao_resolvidos"] = [field for field in ("lema", "pos", "tag", "morfologia") if token[field] is None]
        annotated["validacao"]["pendencias"] = [
            {"token_id": token["token_id"], "campo": field, "motivo": "atributo_nao_produzido"}
            for token in annotated["anotacoes_tokens"] for field in token["campos_nao_resolvidos"]
        ]
        validar_anotacao(annotated)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE annotations SET record_json = ?", (json.dumps(annotated),))
        self.earlier(document_id, "analises")
        ruled = self.run_rules(document_id, expected_status=422, regras_habilitadas=["tempo_flexao"])
        execution = next(item for item in ruled["execucoes_regras"] if item["regra_id"] == "tempo_flexao")
        self.assertEqual(execution["estado"], "impedida")
        self.assertTrue(execution["pendencias"])
        self.assertEqual(execution["erro"], None)
        self.assertEqual(self.download(document_id, ruled["execucao_id"]).get_json(), ruled)
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Anotação insuficiente", html)
        self.assertIn("Há anotações insuficientes", html)
        self.assertNotIn("Falha na execução", html)

    def test_restart_and_old_execution_recover_original_chain_and_catalogue(self):
        document_id, prepared, segmented, annotated, analyzed = self.source()
        first = self.run_rules(document_id, execucao_id="primeira", registrado_em=self.INSTANTE)
        second = self.run_rules(document_id, execucao_id="segunda")
        self.assertEqual(self.download(document_id).get_json(), second)
        newer_prepared = self.earlier(document_id, "preparacoes", normalizar_crlf=True)
        newer_segmented = self.earlier(document_id, "segmentacoes")
        newer_annotated = self.earlier(document_id, "anotacoes")
        newer_analyzed = self.earlier(document_id, "analises")
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertNotIn("Baixar regras JSON", html)
        self.run_rules(document_id)
        restarted = create_app(self.config).test_client()
        self.assertEqual(self.download(document_id, first["execucao_id"], restarted).get_json(), first)
        detail = restarted.get(f"/envios/{document_id}", query_string={"execucao_id": first["execucao_id"]})
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        for field, value in (("preparacao_id", prepared["preparacao_id"]), ("segmentacao_id", segmented["segmentacao_id"]), ("anotacao_id", annotated["anotacao_id"]), ("analise_id", analyzed["analise_id"])):
            self.assertIn(f'name="{field}" value="{value}"', html)
        self.assertIn("Execuções de regras salvas (2)", html)
        self.assertIn("primeira", html)
        self.assertIn("segunda", html)
        self.assertIn(first["catalogo"]["versao"], html)
        for mismatch in ({"analise_id": newer_analyzed["analise_id"]}, {"anotacao_id": newer_annotated["anotacao_id"]}, {"segmentacao_id": newer_segmented["segmentacao_id"]}, {"preparacao_id": newer_prepared["preparacao_id"]}):
            with self.subTest(mismatch=mismatch):
                response = restarted.get(f"/envios/{document_id}", query_string={"execucao_id": first["execucao_id"], **mismatch})
                self.assertEqual(response.status_code, 400)
        old_again = self.run_rules(document_id, analise_id=analyzed["analise_id"])
        self.assertEqual(old_again["analise"], analyzed)

    def test_duplicate_identifier_never_overwrites_any_stage(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        self.run_rules(document_id, execucao_id="única")
        before = self.snapshot()
        response = self.client.post(f"/envios/{document_id}/regras", json={"execucao_id": "única"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_payload_dates_and_configuration_do_not_write(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        before = self.snapshot()
        payloads = (
            [], "texto", None, {"texto": "substituir"}, {"analise_id": None}, {"analise_id": 1},
            {"analise_id": " "}, {"execucao_id": ""}, {"execucao_id": 1}, {"registrado_em": None},
            {"registrado_em": "2026-10-07"}, {"regras_habilitadas": None}, {"regras_habilitadas": "todas"},
            {"regras_habilitadas": ["inexistente"]}, {"regras_habilitadas": [1]},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(f"/envios/{document_id}/regras", data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        self.assertEqual(self.client.post(f"/envios/{document_id}/regras", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_form_and_execution_query_require_single_explicit_identifier(self):
        document_id, _prepared, _segmented, _annotated, analyzed = self.source()
        before = self.snapshot()
        forms = ({}, {"analise_id": ""}, {"analise_id": analyzed["analise_id"], "execucao_id": "extra"}, MultiDict([("analise_id", analyzed["analise_id"]), ("analise_id", analyzed["analise_id"])]))
        for form in forms:
            with self.subTest(form=form):
                response = self.client.post(f"/envios/{document_id}/regras", data=form)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)
        for query in (MultiDict([("execucao_id", "x"), ("execucao_id", "x")]), {"execucao_id": ""}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/envios/{document_id}", query_string=query).status_code, 400)
                self.assertEqual(self.client.get(f"/envios/{document_id}/regras.json", query_string=query).status_code, 400)

    def test_missing_documents_sources_and_runs_return_404(self):
        self.assertEqual(self.client.post("/envios/999/regras", json={}).status_code, 404)
        self.assertEqual(self.download(999).status_code, 404)
        document_id, _prepared = self.submit()
        self.assertEqual(self.client.post(f"/envios/{document_id}/regras", json={}).status_code, 404)
        self.earlier(document_id, "segmentacoes")
        self.earlier(document_id, "anotacoes")
        self.assertEqual(self.client.post(f"/envios/{document_id}/regras", json={}).status_code, 404)
        self.earlier(document_id, "analises")
        self.assertEqual(self.client.post(f"/envios/{document_id}/regras", json={"analise_id": "ausente"}).status_code, 404)
        self.assertEqual(self.download(document_id, "ausente").status_code, 404)
        self.assertEqual(self.client.get(f"/envios/{document_id}", query_string={"execucao_id": "ausente"}).status_code, 404)

    def test_analysis_and_execution_from_other_document_are_not_selected(self):
        first_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        second_id, _prepared, _segmented, _annotated, second_analysis = self.source()
        before = self.snapshot()
        response = self.client.post(f"/envios/{first_id}/regras", json={"analise_id": second_analysis["analise_id"]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)
        ruled = self.run_rules(second_id)
        self.assertEqual(self.download(first_id, ruled["execucao_id"]).status_code, 404)

    def test_malformed_saved_json_is_refused(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        ruled = self.run_rules(document_id)
        for malformed in ("{", "null", "[]"):
            with self.subTest(malformed=malformed):
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE rule_runs SET record_json = ?", (malformed,))
                self.assert_conflict(document_id, ruled["execucao_id"])

    def test_tampered_evidence_catalogue_and_readiness_are_refused(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        ruled = self.run_rules(document_id)
        self.assertTrue(ruled["ocorrencias"])
        mutations = (
            lambda item: item["ocorrencias"][0].__setitem__("nucleo_token_ids", ["inexistente"]),
            lambda item: item["ocorrencias"][0]["evidencias"][0].__setitem__("texto", "substituído"),
            lambda item: item["catalogo"]["regras"][0].__setitem__("versao", "incompatível"),
            lambda item: item["validacao"].__setitem__("pronto_para_etapa_08", False),
        )
        for number, mutation in enumerate(mutations):
            with self.subTest(number=number):
                changed = copy.deepcopy(ruled)
                mutation(changed)
                self.replace_json(ruled["execucao_id"], changed)
                self.assert_conflict(document_id, ruled["execucao_id"])

    def test_timestamp_identifier_and_source_must_match_stored_rows(self):
        document_id, _prepared, _segmented, _annotated, analyzed = self.source()
        ruled = self.run_rules(document_id)
        changed = copy.deepcopy(ruled)
        changed["registrado_em"] = self.INSTANTE
        validar_regras_linguisticas(changed)
        self.replace_json(ruled["execucao_id"], changed)
        self.assert_conflict(document_id, ruled["execucao_id"])
        self.replace_json(ruled["execucao_id"], ruled)
        changed = copy.deepcopy(ruled)
        changed["analise"]["anotacao"]["segmentacao"]["preparacao"]["metadados_origem"]["fonte"] = "metadado posterior"
        validar_regras_linguisticas(changed)
        self.replace_json(ruled["execucao_id"], changed)
        self.assert_conflict(document_id, ruled["execucao_id"])
        self.replace_json(ruled["execucao_id"], ruled)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE rule_runs SET execution_id = ?", ("substituído",))
        self.assertEqual(self.download(document_id, ruled["execucao_id"]).status_code, 404)
        self.assert_conflict(document_id, "substituído")
        self.assertEqual(ruled["analise_id"], analyzed["analise_id"])

    def test_database_foreign_key_cannot_redirect_or_reassign_execution(self):
        document_id, _prepared, _segmented, _annotated, analyzed = self.source()
        ruled = self.run_rules(document_id)
        newer_analysis = self.earlier(document_id, "analises")
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE rule_runs SET analysis_id = ?", (newer_analysis["analise_id"],))
        self.assert_conflict(document_id, ruled["execucao_id"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE rule_runs SET analysis_id = ?", (analyzed["analise_id"],))
        second_id, _prepared = self.submit()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE rule_runs SET submission_id = ?", (second_id,))
        self.assertEqual(self.download(document_id, ruled["execucao_id"]).status_code, 404)
        self.assert_conflict(second_id, ruled["execucao_id"])

    def test_corrupted_or_missing_source_blocks_reads_and_new_runs(self):
        document_id, _prepared, _segmented, _annotated, analyzed = self.source()
        ruled = self.run_rules(document_id)
        before = self.rows("rule_runs")
        changed = copy.deepcopy(analyzed)
        changed["sintaxe"][0]["cabeca_token_id"] = "inexistente"
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE analyses SET record_json = ?", (json.dumps(changed),))
        self.assert_conflict(document_id, ruled["execucao_id"])
        self.assertEqual(self.client.post(f"/envios/{document_id}/regras", json={}).status_code, 409)
        self.assertEqual(self.rows("rule_runs"), before)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM analyses")
        self.assert_conflict(document_id, ruled["execucao_id"])

    def test_additive_schema_upgrade_preserves_existing_database(self):
        document_id, _prepared, _segmented, _annotated, analyzed = self.source()
        before = {table: self.rows(table) for table in self.TABELAS[:-1]}
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE rule_runs")
        self.client = create_app(self.config).test_client()
        for table, rows in before.items():
            self.assertEqual(self.rows(table), rows)
        self.assertEqual(self.rows("rule_runs"), [])
        ruled = self.run_rules(document_id)
        self.assertEqual(ruled["analise"], analyzed)

    def test_failure_after_insert_rolls_back_without_fallback(self):
        document_id, _prepared, _segmented, _annotated, _analyzed = self.source()
        self.run_rules(document_id)
        before = self.snapshot()
        save = app_module.save_rule_run

        def insert_then_fail(connection, document, analyzed, **options):
            save(connection, document, analyzed, **options)
            raise ErroRegras("Falha depois da inserção.")

        with patch("app.save_rule_run", side_effect=insert_then_fail), patch("app.anotar_segmentacao") as fallback:
            response = self.client.post(f"/envios/{document_id}/regras", json={})
        self.assertEqual(response.status_code, 400)
        fallback.assert_not_called()
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
