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
from preparacao import validar_registro
from segmentacao import ErroSegmentacao, validar_segmentacao


class SegmentationIntegrationTests(unittest.TestCase):
    INSTANTE = "2026-10-07T12:34:56-03:00"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()
        self.original = "Primeiro período.\r\nSegundo período!\r\n\r\nTerceiro período?"

    def rows(self, table):
        self.assertIn(table, {"submissions", "preparations", "segmentations"})
        with sqlite3.connect(self.database) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def snapshot(self):
        return {table: self.rows(table) for table in ("submissions", "preparations", "segmentations")}

    def submit(self, content=None, **options):
        response = self.client.post(
            "/envios", data={"content": self.original if content is None else content, **options},
        )
        self.assertEqual(response.status_code, 303)
        document_id = int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[-1])
        preparation = self.client.get(f"/envios/{document_id}/preparacao.json").get_json()
        return document_id, preparation

    def segment(self, document_id, **options):
        response = self.client.post(f"/envios/{document_id}/segmentacoes", json=options)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        record = response.get_json()
        self.assertTrue(validar_segmentacao(record)["pronto_para_etapa_05"])
        return record

    def download(self, document_id, segmentation_id=None, client=None):
        query = {} if segmentation_id is None else {"segmentacao_id": segmentation_id}
        return (client or self.client).get(
            f"/envios/{document_id}/segmentacao.json", query_string=query,
        )

    def replace_json(self, segmentation_id, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE segmentations SET record_json = ? WHERE segmentation_id = ?",
                (encoded, segmentation_id),
            )

    def assert_conflict(self, document_id, segmentation_id):
        response = self.download(document_id, segmentation_id)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("erro", response.get_json())
        detail = self.client.get(
            f"/envios/{document_id}", query_string={"segmentacao_id": segmentation_id},
        )
        self.assertEqual(detail.status_code, 409)
        context = self.client.get(
            f"/envios/{document_id}/contexto-periodo.json",
            query_string={"segmentacao_id": segmentation_id, "periodo_id": "periodo-qualquer"},
        )
        self.assertEqual(context.status_code, 409)

    def test_new_submission_creates_preparation_without_automatic_segmentation(self):
        document_id, prepared = self.submit()
        self.assertEqual(len(self.rows("submissions")), 1)
        self.assertEqual(len(self.rows("preparations")), 1)
        self.assertEqual(self.rows("segmentations"), [])
        self.assertEqual(self.download(document_id).status_code, 404)
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Tokenizar e segmentar", html)
        self.assertIn(f'name="preparacao_id" value="{prepared["preparacao_id"]}"', html)
        self.assertNotIn("Baixar segmentação JSON", html)

    def test_json_execution_preserves_original_and_preparation_and_supplied_metadata(self):
        document_id, prepared = self.submit(normalizar_crlf="on")
        submissions = self.rows("submissions")
        preparations = self.rows("preparations")
        segmented = self.segment(
            document_id, preparacao_id=prepared["preparacao_id"],
            segmentacao_id="segmentacao-estavel", registrado_em=self.INSTANTE,
        )
        self.assertEqual(segmented["segmentacao_id"], "segmentacao-estavel")
        self.assertEqual(segmented["registrado_em"], self.INSTANTE)
        self.assertEqual(segmented["documento_id"], document_id)
        self.assertEqual(segmented["preparacao_id"], prepared["preparacao_id"])
        self.assertEqual(segmented["preparacao"], prepared)
        self.assertEqual(self.rows("submissions"), submissions)
        self.assertEqual(self.rows("preparations"), preparations)
        self.assertEqual(prepared["original"]["texto"], self.original)
        self.assertEqual(prepared["trabalho"]["texto"], self.original.replace("\r\n", "\n"))
        downloaded = self.download(document_id)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_json(), segmented)
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        self.assertEqual(downloaded.mimetype, "application/json")

    def test_form_button_redirects_to_exact_execution_with_counts_and_escaped_periods(self):
        original = "<script>alert('texto')</script>. Outra frase!"
        document_id, prepared = self.submit(content=original)
        response = self.client.post(
            f"/envios/{document_id}/segmentacoes", data={"preparacao_id": prepared["preparacao_id"]},
        )
        self.assertEqual(response.status_code, 303)
        location = urlsplit(response.headers["Location"])
        query = parse_qs(location.query)
        self.assertEqual(query["preparacao_id"], [prepared["preparacao_id"]])
        segmented = self.download(document_id, query["segmentacao_id"][0]).get_json()
        html = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Baixar segmentação JSON", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        for units in ("paragrafos", "periodos", "tokens"):
            self.assertIn(f"<dd>{len(segmented[units])}</dd>", html)
        self.assertEqual(self.rows("submissions")[0][1], original)

    def test_context_returns_previous_current_next_and_boundary_nulls(self):
        document_id, _prepared = self.submit()
        segmented = self.segment(document_id)
        periods = segmented["periodos"]
        self.assertEqual(len(periods), 3)
        for position, period in enumerate(periods):
            with self.subTest(position=position):
                response = self.client.get(
                    f'/envios/{document_id}/segmentacoes/{segmented["segmentacao_id"]}'
                    f'/periodos/{period["id"]}/contexto.json',
                )
                self.assertEqual(response.status_code, 200)
                context = response.get_json()
                self.assertEqual(context["atual"], period)
                self.assertEqual(context["anterior"], periods[position - 1] if position else None)
                self.assertEqual(context["seguinte"], periods[position + 1] if position + 1 < len(periods) else None)

    def test_context_query_preserves_arbitrary_textual_identifiers(self):
        document_id, _prepared = self.submit()
        segmented = self.segment(document_id, segmentacao_id="execução/1?versão#%")
        periods = segmented["periodos"]
        response = self.client.get(
            f"/envios/{document_id}/contexto-periodo.json",
            query_string={"segmentacao_id": segmented["segmentacao_id"], "periodo_id": periods[1]["id"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"anterior": periods[0], "atual": periods[1], "seguinte": periods[2]})
        unknown = self.client.get(
            f"/envios/{document_id}/contexto-periodo.json",
            query_string={"segmentacao_id": segmented["segmentacao_id"], "periodo_id": "ausente"},
        )
        self.assertEqual(unknown.status_code, 404)

    def test_context_query_requires_both_identifiers_once_without_blank_values(self):
        document_id, _prepared = self.submit()
        segmented = self.segment(document_id)
        segmentation_id, period_id = segmented["segmentacao_id"], segmented["periodos"][0]["id"]
        queries = [
            {}, {"segmentacao_id": segmentation_id}, {"periodo_id": period_id},
            {"segmentacao_id": "", "periodo_id": period_id},
            {"segmentacao_id": segmentation_id, "periodo_id": " "},
            MultiDict([("segmentacao_id", segmentation_id), ("segmentacao_id", segmentation_id), ("periodo_id", period_id)]),
            MultiDict([("segmentacao_id", segmentation_id), ("periodo_id", period_id), ("periodo_id", period_id)]),
        ]
        for query in queries:
            with self.subTest(query=query):
                response = self.client.get(f"/envios/{document_id}/contexto-periodo.json", query_string=query)
                self.assertEqual(response.status_code, 400)
                self.assertIn("erro", response.get_json())

    def test_execution_survives_app_restart(self):
        document_id, _prepared = self.submit()
        segmented = self.segment(document_id)
        restarted = create_app(self.config).test_client()
        self.assertEqual(
            self.download(document_id, segmented["segmentacao_id"], client=restarted).get_json(), segmented,
        )

    def test_new_executions_append_and_old_versions_remain_available(self):
        document_id, prepared = self.submit()
        first = self.segment(document_id, segmentacao_id="primeira")
        first_row = self.rows("segmentations")[0]
        second = self.segment(document_id, segmentacao_id="segunda")
        self.assertNotEqual(first["segmentacao_id"], second["segmentacao_id"])
        self.assertEqual(self.rows("segmentations")[0], first_row)
        self.assertEqual(self.download(document_id).get_json(), second)
        self.assertEqual(self.download(document_id, "primeira").get_json(), first)
        html = self.client.get(
            f"/envios/{document_id}", query_string={"preparacao_id": prepared["preparacao_id"]},
        ).get_data(as_text=True)
        self.assertIn("Segmentações salvas (2)", html)
        self.assertIn("segmentacao_id=primeira", html)
        self.assertIn("segmentacao_id=segunda", html)

    def test_old_preparation_and_segmentation_are_not_replaced_by_latest_versions(self):
        document_id, first_prepared = self.submit()
        first = self.segment(document_id, segmentacao_id="segmentacao-antiga")
        response = self.client.post(
            f"/envios/{document_id}/preparacoes",
            json={"normalizar_crlf": True, "preparacao_id": "preparacao-nova"},
        )
        self.assertEqual(response.status_code, 201)
        second_prepared = response.get_json()
        html_before_segmenting = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertNotIn("Baixar segmentação JSON", html_before_segmenting)
        second = self.segment(document_id, segmentacao_id="segmentacao-nova")
        self.assertEqual(second["preparacao"], second_prepared)
        self.assertEqual(self.download(document_id).get_json(), second)
        self.assertEqual(self.download(document_id, first["segmentacao_id"]).get_json(), first)
        old_detail = self.client.get(
            f"/envios/{document_id}", query_string={"segmentacao_id": first["segmentacao_id"]},
        )
        self.assertEqual(old_detail.status_code, 200)
        self.assertIn(
            f'name="preparacao_id" value="{first_prepared["preparacao_id"]}"',
            old_detail.get_data(as_text=True),
        )
        mismatch = self.client.get(
            f"/envios/{document_id}",
            query_string={"segmentacao_id": first["segmentacao_id"], "preparacao_id": second_prepared["preparacao_id"]},
        )
        self.assertEqual(mismatch.status_code, 400)
        explicit_old = self.segment(
            document_id, preparacao_id=first_prepared["preparacao_id"], segmentacao_id="outra-antiga",
        )
        self.assertEqual(explicit_old["preparacao"], first_prepared)
        self.assertEqual(self.download(document_id, first["segmentacao_id"]).get_json(), first)

    def test_duplicate_execution_id_cannot_overwrite_an_existing_execution(self):
        document_id, _prepared = self.submit()
        self.segment(document_id, segmentacao_id="id-unico")
        before = self.snapshot()
        response = self.client.post(
            f"/envios/{document_id}/segmentacoes", json={"segmentacao_id": "id-unico"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("erro", response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_invalid_json_options_are_explicit_errors_without_database_changes(self):
        document_id, _prepared = self.submit()
        self.segment(document_id)
        before = self.snapshot()
        payloads = [
            [], "texto", None, {"texto": "substituir original"}, {"normalizar_crlf": True},
            {"preparacao_id": None}, {"preparacao_id": 1}, {"preparacao_id": " "},
            {"segmentacao_id": ""}, {"segmentacao_id": 1}, {"registrado_em": "2026-10-07"},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/envios/{document_id}/segmentacoes",
                    data=json.dumps(payload), content_type="application/json",
                )
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        malformed = self.client.post(
            f"/envios/{document_id}/segmentacoes", data="{", content_type="application/json",
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_form_requires_one_explicit_preparation_identifier(self):
        document_id, prepared = self.submit()
        before = self.snapshot()
        forms = [
            {}, {"preparacao_id": ""}, {"preparacao_id": prepared["preparacao_id"], "segmentacao_id": "extra"},
            MultiDict([("preparacao_id", prepared["preparacao_id"]), ("preparacao_id", prepared["preparacao_id"])]),
        ]
        for form in forms:
            with self.subTest(form=form):
                response = self.client.post(f"/envios/{document_id}/segmentacoes", data=form)
                self.assertEqual(response.status_code, 400)
                self.assertIn("erro", response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_missing_documents_preparations_executions_and_periods_return_404(self):
        self.assertEqual(self.client.post("/envios/999/segmentacoes", json={}).status_code, 404)
        self.assertEqual(self.download(999).status_code, 404)
        document_id, _prepared = self.submit()
        self.assertEqual(self.download(document_id, "ausente").status_code, 404)
        self.assertEqual(
            self.client.post(f"/envios/{document_id}/segmentacoes", json={"preparacao_id": "ausente"}).status_code, 404,
        )
        self.assertEqual(
            self.client.get(f"/envios/{document_id}", query_string={"preparacao_id": "ausente"}).status_code, 404,
        )
        self.assertEqual(
            self.client.get(f"/envios/{document_id}", query_string={"segmentacao_id": "ausente"}).status_code, 404,
        )
        segmented = self.segment(document_id)
        base = f'/envios/{document_id}/segmentacoes/{segmented["segmentacao_id"]}/periodos'
        self.assertEqual(self.client.get(base + "/ausente/contexto.json").status_code, 404)
        self.assertEqual(
            self.client.get(f"/envios/{document_id}/segmentacoes/ausente/periodos/ausente/contexto.json").status_code, 404,
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO submissions (id, content, created_at) VALUES (?, ?, ?)",
                (21, "Documento legado.", "2026-10-06T12:00:00+00:00"),
            )
        self.assertEqual(self.client.post("/envios/21/segmentacoes", json={}).status_code, 404)

    def test_preparation_from_another_document_cannot_be_substituted(self):
        first_id, _first_prepared = self.submit()
        second_id, second_prepared = self.submit()
        before = self.snapshot()
        response = self.client.post(
            f"/envios/{first_id}/segmentacoes", json={"preparacao_id": second_prepared["preparacao_id"]},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)
        second_segmented = self.segment(second_id)
        self.assertEqual(self.download(first_id, second_segmented["segmentacao_id"]).status_code, 404)

    def test_malformed_saved_json_is_not_delivered_as_ready(self):
        document_id, _prepared = self.submit()
        segmented = self.segment(document_id)
        for malformed in ["{", "null", "[]"]:
            with self.subTest(malformed=malformed):
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE segmentations SET record_json = ?", (malformed,))
                self.assert_conflict(document_id, segmented["segmentacao_id"])

    def test_tampered_units_relations_preparation_map_or_ready_flag_are_refused(self):
        document_id, _prepared = self.submit(normalizar_crlf="on")
        segmented = self.segment(document_id)
        mutations = [
            lambda item: item["tokens"][0]["trabalho"].__setitem__("inicio", 999),
            lambda item: item["tokens"][0].__setitem__("periodo_id", "periodo-inexistente"),
            lambda item: item["periodos"][0].__setitem__("paragrafo_id", "paragrafo-inexistente"),
            lambda item: item["periodos"][0]["tokens"].clear(),
            lambda item: item["paragrafos"][0]["periodos"].clear(),
            lambda item: item["preparacao"]["mapa_trechos"].clear(),
            lambda item: item["validacao"].__setitem__("pronto_para_etapa_05", False),
            lambda item: item["validacao"].__setitem__("estado", "pronto_para_etapa_04"),
        ]
        for number, mutation in enumerate(mutations):
            with self.subTest(mutation=number):
                changed = copy.deepcopy(segmented)
                mutation(changed)
                self.replace_json(segmented["segmentacao_id"], changed)
                self.assert_conflict(document_id, segmented["segmentacao_id"])

    def test_execution_timestamp_must_match_sqlite_even_when_record_is_valid(self):
        document_id, _prepared = self.submit()
        segmented = self.segment(document_id)
        changed = copy.deepcopy(segmented)
        changed["registrado_em"] = self.INSTANTE
        validar_segmentacao(changed)
        self.replace_json(segmented["segmentacao_id"], changed)
        self.assert_conflict(document_id, segmented["segmentacao_id"])

    def test_preparation_metadata_must_match_stored_source_even_when_each_record_is_valid(self):
        document_id, prepared = self.submit()
        segmented = self.segment(document_id)
        prepared["metadados_origem"]["fonte"] = "metadado posterior"
        validar_registro(prepared)
        validar_segmentacao(segmented)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE preparations SET record_json = ? WHERE preparation_id = ?",
                (json.dumps(prepared), prepared["preparacao_id"]),
            )
        self.assert_conflict(document_id, segmented["segmentacao_id"])

    def test_foreign_key_reference_cannot_redirect_execution_to_new_preparation(self):
        document_id, prepared = self.submit()
        segmented = self.segment(document_id)
        response = self.client.post(
            f"/envios/{document_id}/preparacoes", json={"preparacao_id": "preparacao-posterior"},
        )
        self.assertEqual(response.status_code, 201)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE segmentations SET preparation_id = ? WHERE preparation_id = ?",
                ("preparacao-posterior", prepared["preparacao_id"]),
            )
        self.assert_conflict(document_id, segmented["segmentacao_id"])

    def test_execution_cannot_be_reassigned_to_another_document(self):
        first_id, _prepared = self.submit()
        segmented = self.segment(first_id)
        second_id, _second_prepared = self.submit()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE segmentations SET submission_id = ?", (second_id,))
        self.assertEqual(self.download(first_id, segmented["segmentacao_id"]).status_code, 404)
        self.assert_conflict(second_id, segmented["segmentacao_id"])

    def test_tampered_preparation_blocks_both_creation_and_existing_execution_download(self):
        document_id, prepared = self.submit()
        segmented = self.segment(document_id)
        before_segmentations = self.rows("segmentations")
        prepared["metadados_origem"]["recebido_em"] = "1900-01-01T00:00:00+00:00"
        validar_registro(prepared)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE preparations SET record_json = ?", (json.dumps(prepared),))
        self.assert_conflict(document_id, segmented["segmentacao_id"])
        response = self.client.post(f"/envios/{document_id}/segmentacoes", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.rows("segmentations"), before_segmentations)

    def test_changed_original_or_missing_preparation_cannot_leave_ready_execution(self):
        document_id, _prepared = self.submit()
        segmented = self.segment(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content = ?", ("Original substituído.",))
        self.assert_conflict(document_id, segmented["segmentacao_id"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content = ?", (self.original,))
            # Simula corrupção externa: esta conexão não habilita as chaves estrangeiras.
            connection.execute("DELETE FROM preparations")
        self.assert_conflict(document_id, segmented["segmentacao_id"])

    def test_segmenter_failure_does_not_insert_or_change_records(self):
        document_id, _prepared = self.submit()
        self.segment(document_id)
        before = self.snapshot()
        with patch("app.segmentar_preparacao", side_effect=ErroSegmentacao("Falha explícita de segmentação.")):
            response = self.client.post(f"/envios/{document_id}/segmentacoes", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Falha explícita", response.get_json()["erro"])
        self.assertEqual(self.snapshot(), before)

    def test_failure_after_insert_rolls_back_new_execution_and_preserves_history(self):
        document_id, _prepared = self.submit()
        self.segment(document_id)
        before = self.snapshot()
        save = app_module.save_segmentation

        def insert_then_fail(connection, document, prepared, **options):
            save(connection, document, prepared, **options)
            raise ErroSegmentacao("Falha depois da inserção.")

        with patch("app.save_segmentation", side_effect=insert_then_fail):
            response = self.client.post(f"/envios/{document_id}/segmentacoes", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
