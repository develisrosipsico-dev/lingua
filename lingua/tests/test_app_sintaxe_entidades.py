"""Integração da etapa 06 com o modelo real, SQLite e HTTP do Flask."""

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
from sintaxe_entidades import (
    ErroAlinhamento,
    ErroAnalise,
    ErroLimite,
    ErroModelo,
    ErroPrevisao,
    validar_analise_sintaxe_entidades,
)


class SyntaxEntityIntegrationTests(unittest.TestCase):
    INSTANTE = "2026-10-08T12:34:56-03:00"
    TABELAS = ("submissions", "preparations", "segmentations", "annotations", "analyses")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()
        self.original = "João chegou.\r\nMaria saiu.\r\n\r\nA guarda chegou."

    def rows(self, table):
        self.assertIn(table, self.TABELAS)
        with sqlite3.connect(self.database) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def snapshot(self):
        return {table: self.rows(table) for table in self.TABELAS}

    def submit(self, content=None, **options):
        response = self.client.post(
            "/envios", data={"content": self.original if content is None else content, **options},
        )
        self.assertEqual(response.status_code, 303)
        document_id = int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[-1])
        prepared = self.client.get(f"/envios/{document_id}/preparacao.json").get_json()
        return document_id, prepared

    def segment(self, document_id, **options):
        response = self.client.post(f"/envios/{document_id}/segmentacoes", json=options)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def annotate(self, document_id, **options):
        response = self.client.post(f"/envios/{document_id}/anotacoes", json=options)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def source(self, **options):
        document_id, prepared = self.submit(**options)
        segmented = self.segment(document_id)
        annotated = self.annotate(document_id)
        return document_id, prepared, segmented, annotated

    def analyze(self, document_id, **options):
        response = self.client.post(f"/envios/{document_id}/analises", json=options)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        record = response.get_json()
        self.assertTrue(validar_analise_sintaxe_entidades(record)["pronto_para_etapa_07"])
        return record

    def download(self, document_id, analysis_id=None, client=None):
        query = {} if analysis_id is None else {"analise_id": analysis_id}
        return (client or self.client).get(f"/envios/{document_id}/analise.json", query_string=query)

    def replace_json(self, analysis_id, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE analyses SET record_json = ? WHERE analysis_id = ?", (encoded, analysis_id),
            )

    def assert_conflict(self, document_id, analysis_id):
        response = self.download(document_id, analysis_id)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("erro", response.get_json())
        detail = self.client.get(f"/envios/{document_id}", query_string={"analise_id": analysis_id})
        self.assertEqual(detail.status_code, 409)

    def test_earlier_stages_do_not_automatically_run_analysis(self):
        document_id, _prepared = self.submit()
        for table in ("segmentations", "annotations", "analyses"):
            self.assertEqual(self.rows(table), [])
        self.assertNotIn("Analisar sintaxe e entidades", self.client.get(f"/envios/{document_id}").get_data(as_text=True))
        self.segment(document_id)
        self.assertEqual(self.rows("analyses"), [])
        self.assertNotIn("Analisar sintaxe e entidades", self.client.get(f"/envios/{document_id}").get_data(as_text=True))
        annotated = self.annotate(document_id)
        self.assertEqual(self.rows("analyses"), [])
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Analisar sintaxe e entidades", html)
        self.assertIn(f'name="anotacao_id" value="{annotated["anotacao_id"]}"', html)
        self.assertNotIn("Baixar análise JSON", html)
        self.assertEqual(self.download(document_id).status_code, 404)

    def test_real_model_preserves_complete_source_and_exports_token_relations(self):
        document_id, prepared, segmented, annotated = self.source(normalizar_crlf="on")
        before = self.snapshot()
        analyzed = self.analyze(
            document_id, anotacao_id=annotated["anotacao_id"],
            analise_id="análise/estável", registrado_em=self.INSTANTE,
        )
        self.assertEqual(analyzed["analise_id"], "análise/estável")
        self.assertEqual(analyzed["registrado_em"], self.INSTANTE)
        self.assertEqual(analyzed["anotacao"], annotated)
        for key in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id"):
            self.assertEqual(analyzed[key], annotated[key])
        for table in self.TABELAS[:-1]:
            self.assertEqual(self.rows(table), before[table])
        self.assertEqual(len(analyzed["sintaxe"]), len(segmented["tokens"]))
        token_ids = {token["id"] for token in segmented["tokens"]}
        for source_token, syntax in zip(segmented["tokens"], analyzed["sintaxe"]):
            with self.subTest(token=source_token["id"]):
                self.assertEqual(syntax["token_id"], source_token["id"])
                self.assertEqual(syntax["periodo_id"], source_token["periodo_id"])
                self.assertEqual(syntax["paragrafo_id"], source_token["paragrafo_id"])
                self.assertTrue(syntax["dependencia"])
                self.assertIn(syntax["cabeca_token_id"], token_ids)
        self.assertIsInstance(analyzed["entidades"], list)
        self.assertEqual(prepared["original"]["texto"], self.original)
        self.assertEqual(prepared["trabalho"]["texto"], self.original.replace("\r\n", "\n"))
        downloaded = self.download(document_id, analyzed["analise_id"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_json(), analyzed)
        self.assertEqual(downloaded.mimetype, "application/json")
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])

    def test_form_redirect_contains_exact_chain_and_user_content_is_escaped(self):
        document_id, _prepared, segmented, annotated = self.source(
            content="João chegou. Ação <script>alert('texto')</script>.",
        )
        response = self.client.post(
            f"/envios/{document_id}/analises", data={"anotacao_id": annotated["anotacao_id"]},
        )
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        for field in ("preparacao_id", "segmentacao_id", "anotacao_id"):
            self.assertEqual(query[field], [annotated[field]])
        analyzed = self.download(document_id, query["analise_id"][0]).get_json()
        self.assertEqual(analyzed["anotacao"], annotated)
        detail = self.client.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        self.assertIn("Baixar análise JSON", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn(f'name="segmentacao_id" value="{segmented["segmentacao_id"]}"', html)
        self.assertIn(f'name="anotacao_id" value="{annotated["anotacao_id"]}"', html)

    def test_analysis_history_and_download_survive_restart(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        first = self.analyze(document_id, analise_id="primeira")
        first_row = self.rows("analyses")[0]
        second = self.analyze(document_id, analise_id="segunda")
        self.assertEqual(len(self.rows("analyses")), 2)
        self.assertEqual(self.rows("analyses")[0], first_row)
        restarted = create_app(self.config).test_client()
        self.assertEqual(self.download(document_id, first["analise_id"], restarted).get_json(), first)
        self.assertEqual(self.download(document_id, client=restarted).get_json(), second)
        historical = restarted.get(f"/envios/{document_id}", query_string={"analise_id": first["analise_id"]})
        self.assertEqual(historical.status_code, 200)
        html = historical.get_data(as_text=True)
        self.assertIn("primeira", html)
        self.assertIn("segunda", html)

    def test_historical_analysis_selects_exact_annotation_segmentation_and_preparation(self):
        document_id, prepared, segmented, annotated = self.source()
        first = self.analyze(document_id, analise_id="análise-antiga")
        new_annotation = self.annotate(document_id, anotacao_id="anotacao-posterior")
        current_html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertNotIn("Baixar análise JSON", current_html)
        second = self.analyze(document_id)
        self.assertEqual(second["anotacao"], new_annotation)
        newer_segmentation = self.segment(document_id)
        self.annotate(document_id)
        self.analyze(document_id)
        response = self.client.post(
            f"/envios/{document_id}/preparacoes",
            json={"normalizar_crlf": True, "preparacao_id": "preparacao-posterior"},
        )
        self.assertEqual(response.status_code, 201)
        newer_preparation = response.get_json()
        self.segment(document_id)
        self.annotate(document_id)
        latest = self.analyze(document_id)
        self.assertEqual(latest["preparacao_id"], newer_preparation["preparacao_id"])
        self.assertEqual(self.download(document_id).get_json(), latest)
        self.assertEqual(self.download(document_id, first["analise_id"]).get_json(), first)
        detail = self.client.get(f"/envios/{document_id}", query_string={"analise_id": first["analise_id"]})
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        for field, value in (
            ("preparacao_id", prepared["preparacao_id"]),
            ("segmentacao_id", segmented["segmentacao_id"]),
            ("anotacao_id", annotated["anotacao_id"]),
        ):
            self.assertIn(f'name="{field}" value="{value}"', html)
        for extra in (
            {"anotacao_id": new_annotation["anotacao_id"]},
            {"segmentacao_id": newer_segmentation["segmentacao_id"]},
            {"preparacao_id": newer_preparation["preparacao_id"]},
        ):
            with self.subTest(extra=extra):
                mismatch = self.client.get(
                    f"/envios/{document_id}", query_string={"analise_id": first["analise_id"], **extra},
                )
                self.assertEqual(mismatch.status_code, 400)
        old_again = self.analyze(document_id, anotacao_id=annotated["anotacao_id"])
        self.assertEqual(old_again["anotacao"], annotated)

    def test_duplicate_identifier_does_not_overwrite_analysis_or_source(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        self.analyze(document_id, analise_id="id-unico")
        before = self.snapshot()
        response = self.client.post(f"/envios/{document_id}/analises", json={"analise_id": "id-unico"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("erro", response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_invalid_payloads_and_dates_are_explicit_and_do_not_change_records(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        before = self.snapshot()
        payloads = (
            [], "texto", None, {"texto": "substituir original"}, {"modelo": "inventado"},
            {"anotacao_id": None}, {"anotacao_id": 1}, {"anotacao_id": " "},
            {"analise_id": ""}, {"analise_id": 1}, {"analise_id": None},
            {"registrado_em": "2026-10-08"}, {"registrado_em": None},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/envios/{document_id}/analises", data=json.dumps(payload), content_type="application/json",
                )
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        malformed = self.client.post(f"/envios/{document_id}/analises", data="{", content_type="application/json")
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_form_requires_one_explicit_annotation_identifier(self):
        document_id, _prepared, _segmented, annotated = self.source()
        before = self.snapshot()
        forms = (
            {}, {"anotacao_id": ""}, {"anotacao_id": annotated["anotacao_id"], "analise_id": "extra"},
            MultiDict([("anotacao_id", annotated["anotacao_id"]), ("anotacao_id", annotated["anotacao_id"])]),
        )
        for form in forms:
            with self.subTest(form=form):
                response = self.client.post(f"/envios/{document_id}/analises", data=form)
                self.assertEqual(response.status_code, 400)
                self.assertIn("erro", response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_missing_document_annotation_or_analysis_returns_404(self):
        self.assertEqual(self.client.post("/envios/999/analises", json={}).status_code, 404)
        self.assertEqual(self.download(999).status_code, 404)
        document_id, _prepared = self.submit()
        self.assertEqual(self.client.post(f"/envios/{document_id}/analises", json={}).status_code, 404)
        self.segment(document_id)
        self.assertEqual(self.client.post(f"/envios/{document_id}/analises", json={}).status_code, 404)
        self.annotate(document_id)
        self.assertEqual(self.client.post(f"/envios/{document_id}/analises", json={"anotacao_id": "ausente"}).status_code, 404)
        self.assertEqual(self.download(document_id, "ausente").status_code, 404)
        self.assertEqual(self.client.get(f"/envios/{document_id}", query_string={"analise_id": "ausente"}).status_code, 404)

    def test_sources_and_analyses_cannot_be_taken_from_another_document(self):
        first_id, _prepared, _segmented, _annotated = self.source()
        second_id, _prepared, _segmented, second_annotation = self.source()
        before = self.snapshot()
        response = self.client.post(
            f"/envios/{first_id}/analises", json={"anotacao_id": second_annotation["anotacao_id"]},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)
        analyzed = self.analyze(second_id)
        self.assertEqual(self.download(first_id, analyzed["analise_id"]).status_code, 404)

    def test_inference_alignment_tree_and_limit_failures_never_fall_back(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        before = self.snapshot()
        failures = (
            (ErroModelo("Modelo português indisponível."), 503),
            (ErroAlinhamento("Os tokens não correspondem à origem."), 422),
            (ErroPrevisao("A previsão não forma uma árvore válida."), 422),
            (ErroLimite("O texto excede o limite da análise."), 413),
        )
        for failure, expected_status in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("app.analisar_sintaxe_entidades", side_effect=failure), patch("app.anotar_segmentacao") as fallback:
                    response = self.client.post(f"/envios/{document_id}/analises", json={})
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.get_json()["erro"], str(failure))
                fallback.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_malformed_saved_json_is_not_delivered_as_ready(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        analyzed = self.analyze(document_id)
        for malformed in ("{", "null", "[]"):
            with self.subTest(malformed=malformed):
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE analyses SET record_json = ?", (malformed,))
                self.assert_conflict(document_id, analyzed["analise_id"])

    def test_tampered_relations_or_ready_flags_are_refused_on_read(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        analyzed = self.analyze(document_id)
        mutations = (
            lambda item: item["sintaxe"][0].__setitem__("token_id", "token-inexistente"),
            lambda item: item["sintaxe"][0].__setitem__("cabeca_token_id", "cabeca-inexistente"),
            lambda item: item["sintaxe"][0].__setitem__("periodo_id", "periodo-inexistente"),
            lambda item: item["sintaxe"].pop(),
            lambda item: item["anotacao"]["anotacoes_tokens"][0].__setitem__("token_id", "token-inexistente"),
            lambda item: item["validacao"].__setitem__("pronto_para_etapa_07", False),
        )
        for number, mutation in enumerate(mutations):
            with self.subTest(mutation=number):
                changed = copy.deepcopy(analyzed)
                mutation(changed)
                self.replace_json(analyzed["analise_id"], changed)
                self.assert_conflict(document_id, analyzed["analise_id"])

    def test_saved_timestamp_and_identifier_must_match_valid_json(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        analyzed = self.analyze(document_id)
        changed = copy.deepcopy(analyzed)
        changed["registrado_em"] = self.INSTANTE
        validar_analise_sintaxe_entidades(changed)
        self.replace_json(analyzed["analise_id"], changed)
        self.assert_conflict(document_id, analyzed["analise_id"])
        self.replace_json(analyzed["analise_id"], analyzed)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE analyses SET analysis_id = ?", ("identificador-substituído",))
        self.assertEqual(self.download(document_id, analyzed["analise_id"]).status_code, 404)
        self.assert_conflict(document_id, "identificador-substituído")

    def test_source_metadata_must_match_saved_chain_even_when_both_records_are_valid(self):
        document_id, _prepared, _segmented, annotated = self.source()
        analyzed = self.analyze(document_id)
        changed = copy.deepcopy(analyzed)
        changed["anotacao"]["segmentacao"]["preparacao"]["metadados_origem"]["fonte"] = "metadado posterior"
        validar_anotacao(changed["anotacao"])
        validar_analise_sintaxe_entidades(changed)
        self.replace_json(analyzed["analise_id"], changed)
        self.assert_conflict(document_id, analyzed["analise_id"])
        self.assertEqual(changed["anotacao"]["anotacao_id"], annotated["anotacao_id"])

    def test_foreign_key_cannot_redirect_analysis_to_another_annotation(self):
        document_id, _prepared, _segmented, annotated = self.source()
        analyzed = self.analyze(document_id)
        second = self.annotate(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE analyses SET annotation_id = ? WHERE annotation_id = ?",
                (second["anotacao_id"], annotated["anotacao_id"]),
            )
        self.assert_conflict(document_id, analyzed["analise_id"])

    def test_analysis_cannot_be_reassigned_to_another_document(self):
        first_id, _prepared, _segmented, _annotated = self.source()
        analyzed = self.analyze(first_id)
        second_id, _prepared, _segmented, _annotated = self.source()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE analyses SET submission_id = ?", (second_id,))
        self.assertEqual(self.download(first_id, analyzed["analise_id"]).status_code, 404)
        self.assert_conflict(second_id, analyzed["analise_id"])

    def test_corrupted_annotation_blocks_creation_and_download_without_new_rows(self):
        document_id, _prepared, _segmented, annotated = self.source()
        analyzed = self.analyze(document_id)
        before = self.rows("analyses")
        annotated["anotacoes_tokens"][0]["token_id"] = "inexistente"
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE annotations SET record_json = ?", (json.dumps(annotated),))
        self.assert_conflict(document_id, analyzed["analise_id"])
        response = self.client.post(f"/envios/{document_id}/analises", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.rows("analyses"), before)

    def test_changed_original_or_missing_annotation_invalidates_saved_analysis(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        analyzed = self.analyze(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content = ?", ("Original substituído.",))
        self.assert_conflict(document_id, analyzed["analise_id"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content = ?", (self.original,))
            # Corrupção externa simulada: esta conexão não habilita chaves estrangeiras.
            connection.execute("DELETE FROM annotations")
        self.assert_conflict(document_id, analyzed["analise_id"])

    def test_failure_after_insert_rolls_back_and_preserves_existing_history(self):
        document_id, _prepared, _segmented, _annotated = self.source()
        self.analyze(document_id)
        before = self.snapshot()
        save = app_module.save_analysis

        def insert_then_fail(connection, document, annotated, **options):
            save(connection, document, annotated, **options)
            raise ErroAnalise("Falha depois da inserção.")

        with patch("app.save_analysis", side_effect=insert_then_fail):
            response = self.client.post(f"/envios/{document_id}/analises", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
