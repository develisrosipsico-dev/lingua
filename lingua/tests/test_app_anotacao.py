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
from anotacao import (
    ErroAlinhamento,
    ErroAnotacao,
    ErroLimite,
    ErroModelo,
    validar_anotacao,
)
from segmentacao import validar_segmentacao


class AnnotationIntegrationTests(unittest.TestCase):
    INSTANTE = "2026-10-07T12:34:56-03:00"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()
        self.original = "As meninas estudaram.\r\nOs meninos estudarão.\r\n\r\nOlá, ação! 📝"

    def rows(self, table):
        self.assertIn(table, {"submissions", "preparations", "segmentations", "annotations"})
        with sqlite3.connect(self.database) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def snapshot(self):
        return {
            table: self.rows(table)
            for table in ("submissions", "preparations", "segmentations", "annotations")
        }

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
        record = response.get_json()
        self.assertTrue(validar_anotacao(record)["pronto_para_etapa_06"])
        return record

    def source(self, **options):
        document_id, prepared = self.submit(**options)
        segmented = self.segment(document_id)
        return document_id, prepared, segmented

    def download(self, document_id, annotation_id=None, client=None):
        query = {} if annotation_id is None else {"anotacao_id": annotation_id}
        return (client or self.client).get(f"/envios/{document_id}/anotacao.json", query_string=query)

    def replace_json(self, annotation_id, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE annotations SET record_json = ? WHERE annotation_id = ?",
                (encoded, annotation_id),
            )

    def assert_conflict(self, document_id, annotation_id):
        response = self.download(document_id, annotation_id)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("erro", response.get_json())
        detail = self.client.get(f"/envios/{document_id}", query_string={"anotacao_id": annotation_id})
        self.assertEqual(detail.status_code, 409)

    def test_registration_preparation_and_segmentation_do_not_automatically_annotate(self):
        document_id, _prepared = self.submit()
        self.assertEqual(self.rows("segmentations"), [])
        self.assertEqual(self.rows("annotations"), [])
        self.assertEqual(self.download(document_id).status_code, 404)
        before_segmenting = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertNotIn("Anotar morfologia e vocabulário", before_segmenting)
        segmented = self.segment(document_id)
        self.assertEqual(self.rows("annotations"), [])
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Anotar morfologia e vocabulário", html)
        self.assertIn(f'name="segmentacao_id" value="{segmented["segmentacao_id"]}"', html)
        self.assertNotIn("Baixar anotações JSON", html)

    def test_real_model_annotation_preserves_all_sources_and_exposes_full_token_fields(self):
        document_id, prepared, segmented = self.source(normalizar_crlf="on")
        before = self.snapshot()
        annotated = self.annotate(
            document_id, segmentacao_id=segmented["segmentacao_id"],
            anotacao_id="anotação/estável", registrado_em=self.INSTANTE,
        )
        self.assertEqual(annotated["anotacao_id"], "anotação/estável")
        self.assertEqual(annotated["registrado_em"], self.INSTANTE)
        self.assertEqual(annotated["documento_id"], document_id)
        self.assertEqual(annotated["preparacao_id"], prepared["preparacao_id"])
        self.assertEqual(annotated["segmentacao_id"], segmented["segmentacao_id"])
        self.assertEqual(annotated["segmentacao"], segmented)
        for table in ("submissions", "preparations", "segmentations"):
            self.assertEqual(self.rows(table), before[table])
        self.assertEqual(len(annotated["anotacoes_tokens"]), len(segmented["tokens"]))
        token_fields = {
            "token_id", "periodo_id", "paragrafo_id", "texto", "trabalho", "original",
            "lema", "pos", "tag", "morfologia", "origem", "campos_nao_resolvidos",
        }
        for source_token, token_annotation in zip(segmented["tokens"], annotated["anotacoes_tokens"]):
            with self.subTest(token=source_token["id"]):
                self.assertTrue(token_fields.issubset(token_annotation))
                self.assertEqual(token_annotation["token_id"], source_token["id"])
                for field in ("texto", "periodo_id", "paragrafo_id", "trabalho", "original"):
                    self.assertEqual(token_annotation[field], source_token[field])
        self.assertTrue(any(token["lema"] is not None and token["pos"] is not None for token in annotated["anotacoes_tokens"]))
        self.assertEqual(prepared["original"]["texto"], self.original)
        self.assertEqual(prepared["trabalho"]["texto"], self.original.replace("\r\n", "\n"))
        downloaded = self.download(document_id, annotated["anotacao_id"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_json(), annotated)
        self.assertEqual(downloaded.mimetype, "application/json")
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])

    def test_vocabulary_references_the_saved_token_annotations(self):
        document_id, _prepared, _segmented = self.source()
        annotated = self.annotate(document_id)
        token_ids = {token["token_id"] for token in annotated["anotacoes_tokens"]}
        self.assertTrue(annotated["vocabulario"])
        seen = []
        for entry in annotated["vocabulario"]:
            self.assertTrue({"id", "ordem", "lema", "pos", "formas", "quantidade", "token_ids"}.issubset(entry))
            self.assertEqual(entry["quantidade"], len(entry["token_ids"]))
            self.assertTrue(set(entry["token_ids"]).issubset(token_ids))
            seen.extend(entry["token_ids"])
        self.assertEqual(len(seen), len(set(seen)))

    def test_form_redirect_selects_exact_annotation_and_html_escapes_user_text(self):
        document_id, _prepared = self.submit(content="Ação <script>alert('texto')</script>. As meninas estudaram.")
        segmented = self.segment(document_id)
        response = self.client.post(
            f"/envios/{document_id}/anotacoes", data={"segmentacao_id": segmented["segmentacao_id"]},
        )
        self.assertEqual(response.status_code, 303)
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        annotated = self.download(document_id, query["anotacao_id"][0]).get_json()
        self.assertEqual(query["segmentacao_id"], [segmented["segmentacao_id"]])
        self.assertEqual(query["preparacao_id"], [segmented["preparacao_id"]])
        self.assertEqual(annotated["segmentacao"], segmented)
        detail = self.client.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        self.assertIn("Baixar anotações JSON", html)
        self.assertIn("Vocabulário", html)
        self.assertIn("Lema", html)
        self.assertIn("Ação", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn(f'name="segmentacao_id" value="{segmented["segmentacao_id"]}"', html)

    def test_annotation_and_download_persist_after_restart(self):
        document_id, _prepared, _segmented = self.source()
        annotated = self.annotate(document_id)
        restarted = create_app(self.config).test_client()
        self.assertEqual(self.download(document_id, annotated["anotacao_id"], restarted).get_json(), annotated)

    def test_multiple_annotations_append_and_preserve_previous_versions(self):
        document_id, _prepared, _segmented = self.source()
        first = self.annotate(document_id, anotacao_id="primeira")
        first_row = self.rows("annotations")[0]
        second = self.annotate(document_id, anotacao_id="segunda")
        self.assertEqual(len(self.rows("annotations")), 2)
        self.assertEqual(self.rows("annotations")[0], first_row)
        self.assertEqual(self.download(document_id).get_json(), second)
        self.assertEqual(self.download(document_id, first["anotacao_id"]).get_json(), first)

    def test_historical_annotation_selects_its_exact_preparation_and_segmentation(self):
        document_id, prepared, segmented = self.source()
        first = self.annotate(document_id, anotacao_id="anotacao-antiga")
        other_segmented = self.segment(document_id, segmentacao_id="segmentacao-posterior")
        current_detail = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertNotIn("Baixar anotações JSON", current_detail)
        second = self.annotate(document_id, anotacao_id="anotacao-posterior")
        self.assertEqual(second["segmentacao"], other_segmented)
        response = self.client.post(
            f"/envios/{document_id}/preparacoes", json={"normalizar_crlf": True, "preparacao_id": "preparacao-posterior"},
        )
        self.assertEqual(response.status_code, 201)
        latest_prepared = response.get_json()
        latest_segmented = self.segment(document_id)
        latest = self.annotate(document_id)
        self.assertEqual(latest["segmentacao"], latest_segmented)
        self.assertEqual(latest["preparacao_id"], latest_prepared["preparacao_id"])
        self.assertEqual(self.download(document_id).get_json(), latest)
        self.assertEqual(self.download(document_id, first["anotacao_id"]).get_json(), first)
        detail = self.client.get(f"/envios/{document_id}", query_string={"anotacao_id": first["anotacao_id"]})
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        self.assertIn(f'name="preparacao_id" value="{prepared["preparacao_id"]}"', html)
        self.assertIn(f'name="segmentacao_id" value="{segmented["segmentacao_id"]}"', html)
        for extra in [{"segmentacao_id": other_segmented["segmentacao_id"]}, {"preparacao_id": latest_prepared["preparacao_id"]}]:
            with self.subTest(extra=extra):
                mismatch = self.client.get(
                    f"/envios/{document_id}", query_string={"anotacao_id": first["anotacao_id"], **extra},
                )
                self.assertEqual(mismatch.status_code, 400)
        explicit_old = self.annotate(document_id, segmentacao_id=segmented["segmentacao_id"], anotacao_id="outra-antiga")
        self.assertEqual(explicit_old["segmentacao"], segmented)
        self.assertEqual(self.download(document_id, first["anotacao_id"]).get_json(), first)

    def test_duplicate_identifier_cannot_overwrite_existing_annotation(self):
        document_id, _prepared, _segmented = self.source()
        self.annotate(document_id, anotacao_id="id-unico")
        before = self.snapshot()
        response = self.client.post(f"/envios/{document_id}/anotacoes", json={"anotacao_id": "id-unico"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("erro", response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_invalid_json_and_options_return_explicit_400_without_changing_records(self):
        document_id, _prepared, _segmented = self.source()
        before = self.snapshot()
        payloads = [
            [], "texto", None, {"texto": "substituir original"}, {"modelo": "inventado"},
            {"segmentacao_id": None}, {"segmentacao_id": 1}, {"segmentacao_id": " "},
            {"anotacao_id": ""}, {"anotacao_id": 1}, {"anotacao_id": None},
            {"registrado_em": "2026-10-07"}, {"registrado_em": None},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/envios/{document_id}/anotacoes", data=json.dumps(payload), content_type="application/json",
                )
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        malformed = self.client.post(f"/envios/{document_id}/anotacoes", data="{", content_type="application/json")
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_form_requires_one_explicit_segmentation_identifier(self):
        document_id, _prepared, segmented = self.source()
        before = self.snapshot()
        forms = [
            {}, {"segmentacao_id": ""}, {"segmentacao_id": segmented["segmentacao_id"], "anotacao_id": "extra"},
            MultiDict([("segmentacao_id", segmented["segmentacao_id"]), ("segmentacao_id", segmented["segmentacao_id"])]),
        ]
        for form in forms:
            with self.subTest(form=form):
                response = self.client.post(f"/envios/{document_id}/anotacoes", data=form)
                self.assertEqual(response.status_code, 400)
                self.assertIn("erro", response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_missing_documents_segmentations_and_annotations_return_404(self):
        self.assertEqual(self.client.post("/envios/999/anotacoes", json={}).status_code, 404)
        self.assertEqual(self.download(999).status_code, 404)
        document_id, _prepared = self.submit()
        self.assertEqual(self.client.post(f"/envios/{document_id}/anotacoes", json={}).status_code, 404)
        self.assertEqual(self.download(document_id, "ausente").status_code, 404)
        self.assertEqual(self.client.get(f"/envios/{document_id}", query_string={"anotacao_id": "ausente"}).status_code, 404)
        self.segment(document_id)
        self.assertEqual(
            self.client.post(f"/envios/{document_id}/anotacoes", json={"segmentacao_id": "ausente"}).status_code, 404,
        )

    def test_segmentation_from_another_document_cannot_be_substituted(self):
        first_id, _first_prepared, _first_segmented = self.source()
        second_id, _second_prepared, second_segmented = self.source()
        before = self.snapshot()
        response = self.client.post(
            f"/envios/{first_id}/anotacoes", json={"segmentacao_id": second_segmented["segmentacao_id"]},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)
        annotated = self.annotate(second_id)
        self.assertEqual(self.download(first_id, annotated["anotacao_id"]).status_code, 404)

    def test_model_alignment_and_size_failures_are_explicit_and_never_fall_back(self):
        document_id, _prepared, _segmented = self.source()
        before = self.snapshot()
        failures = [
            (ErroModelo("Modelo português indisponível."), 503),
            (ErroAlinhamento("Os tokens do modelo não correspondem à segmentação."), 422),
            (ErroLimite("O texto excede o limite da anotação."), 413),
        ]
        for failure, expected_status in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("app.anotar_segmentacao", side_effect=failure), patch("app.segmentar_preparacao") as fallback:
                    response = self.client.post(f"/envios/{document_id}/anotacoes", json={})
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.get_json()["erro"], str(failure))
                fallback.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_malformed_saved_json_is_not_delivered_as_ready(self):
        document_id, _prepared, _segmented = self.source()
        annotated = self.annotate(document_id)
        for malformed in ["{", "null", "[]"]:
            with self.subTest(malformed=malformed):
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE annotations SET record_json = ?", (malformed,))
                self.assert_conflict(document_id, annotated["anotacao_id"])

    def test_tampered_token_references_vocabulary_maps_or_ready_flags_are_refused(self):
        document_id, _prepared, _segmented = self.source(normalizar_crlf="on")
        annotated = self.annotate(document_id)
        mutations = [
            lambda item: item["anotacoes_tokens"][0].__setitem__("token_id", "token-inexistente"),
            lambda item: item["anotacoes_tokens"][0]["trabalho"].__setitem__("inicio", 999),
            lambda item: item["anotacoes_tokens"][0].__setitem__("periodo_id", "periodo-inexistente"),
            lambda item: item["anotacoes_tokens"].pop(),
            lambda item: item["vocabulario"][0]["token_ids"].append("token-inexistente"),
            lambda item: item["vocabulario"][0].__setitem__("quantidade", 999),
            lambda item: item["vocabulario"][0]["formas"].clear(),
            lambda item: item["segmentacao"]["preparacao"]["mapa_trechos"].clear(),
            lambda item: item["validacao"]["cobertura"].__setitem__("tokens_anotados", 999),
            lambda item: item["validacao"].__setitem__("pronto_para_etapa_06", False),
            lambda item: item["validacao"].__setitem__("estado", "pronto_para_etapa_05"),
        ]
        for number, mutation in enumerate(mutations):
            with self.subTest(mutation=number):
                changed = copy.deepcopy(annotated)
                mutation(changed)
                self.replace_json(annotated["anotacao_id"], changed)
                self.assert_conflict(document_id, annotated["anotacao_id"])

    def test_annotation_timestamp_must_match_sqlite_even_when_record_is_valid(self):
        document_id, _prepared, _segmented = self.source()
        annotated = self.annotate(document_id)
        changed = copy.deepcopy(annotated)
        changed["registrado_em"] = self.INSTANTE
        validar_anotacao(changed)
        self.replace_json(annotated["anotacao_id"], changed)
        self.assert_conflict(document_id, annotated["anotacao_id"])

    def test_sqlite_identifier_must_match_the_valid_annotation_json(self):
        document_id, _prepared, _segmented = self.source()
        annotated = self.annotate(document_id)
        validar_anotacao(annotated)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE annotations SET annotation_id = ?", ("identificador-substituído",))
        self.assertEqual(self.download(document_id, annotated["anotacao_id"]).status_code, 404)
        self.assert_conflict(document_id, "identificador-substituído")

    def test_source_metadata_must_match_even_when_individual_records_remain_valid(self):
        document_id, _prepared, segmented = self.source()
        annotated = self.annotate(document_id)
        segmented["preparacao"]["metadados_origem"]["fonte"] = "metadado posterior"
        validar_segmentacao(segmented)
        changed = copy.deepcopy(annotated)
        changed["segmentacao"] = segmented
        validar_anotacao(changed)
        self.replace_json(annotated["anotacao_id"], changed)
        self.assert_conflict(document_id, annotated["anotacao_id"])

    def test_foreign_key_reference_cannot_redirect_annotation_to_other_segmentation(self):
        document_id, _prepared, segmented = self.source()
        annotated = self.annotate(document_id)
        second = self.segment(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE annotations SET segmentation_id = ? WHERE segmentation_id = ?",
                (second["segmentacao_id"], segmented["segmentacao_id"]),
            )
        self.assert_conflict(document_id, annotated["anotacao_id"])

    def test_annotation_cannot_be_reassigned_to_another_document(self):
        first_id, _prepared, _segmented = self.source()
        annotated = self.annotate(first_id)
        second_id, _second_prepared, _second_segmented = self.source()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE annotations SET submission_id = ?", (second_id,))
        self.assertEqual(self.download(first_id, annotated["anotacao_id"]).status_code, 404)
        self.assert_conflict(second_id, annotated["anotacao_id"])

    def test_tampered_source_segmentation_blocks_creation_and_saved_annotation_download(self):
        document_id, _prepared, segmented = self.source()
        annotated = self.annotate(document_id)
        before_annotations = self.rows("annotations")
        segmented["tokens"][0]["trabalho"]["inicio"] = 999
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE segmentations SET record_json = ?", (json.dumps(segmented),))
        self.assert_conflict(document_id, annotated["anotacao_id"])
        response = self.client.post(f"/envios/{document_id}/anotacoes", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.rows("annotations"), before_annotations)

    def test_changed_original_or_missing_segmentation_cannot_leave_ready_annotation(self):
        document_id, _prepared, _segmented = self.source()
        annotated = self.annotate(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content = ?", ("Original substituído.",))
        self.assert_conflict(document_id, annotated["anotacao_id"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content = ?", (self.original,))
            # Simula corrupção externa: esta conexão não habilita as chaves estrangeiras.
            connection.execute("DELETE FROM segmentations")
        self.assert_conflict(document_id, annotated["anotacao_id"])

    def test_failure_after_insert_rolls_back_new_annotation_and_preserves_history(self):
        document_id, _prepared, _segmented = self.source()
        self.annotate(document_id)
        before = self.snapshot()
        save = app_module.save_annotation

        def insert_then_fail(connection, document, segmented, **options):
            save(connection, document, segmented, **options)
            raise ErroAnotacao("Falha depois da inserção.")

        with patch("app.save_annotation", side_effect=insert_then_fail):
            response = self.client.post(f"/envios/{document_id}/anotacoes", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
