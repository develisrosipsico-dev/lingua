"""Etapa 09 no Flask: experimentos e origem histórica, sem inferência neural.

O adaptador determinístico é injetado apenas na configuração de testes.
As anotações linguísticas da origem são manuais e controladas.
"""

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from werkzeug.datastructures import MultiDict

from app import create_app, load_vector_context
from fixtures_contexto import LINHAS, TEXTO
from fixtures_regras import construir_analise
from regras_linguisticas import aplicar_regras_linguisticas
from unidades_contexto import construir_unidades_contexto, validar_unidades_contexto
from trabalhos_vetorizacao import processar_trabalho
from vetorizacao import validar_vetorizacao
from fixtures_vetorizacao import AdaptadorControlado




class VectorAppTests(unittest.TestCase):
    INSTANTE = "2026-10-08T14:15:00-03:00"
    EARLIER_TABLES = ("submissions", "preparations", "segmentations", "annotations", "analyses", "rule_runs", "context_runs")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database), "VECTORS_AUTO_START": False}
        self.client = create_app(self.config).test_client()

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return {table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() for table in self.EARLIER_TABLES}

    def source(self, *, document_id=None, normalizar=False, **context_options):
        if document_id is None:
            response = self.client.post("/envios", data={"content": TEXTO})
            self.assertEqual(response.status_code, 303)
            document_id = int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[1])
        with sqlite3.connect(self.database) as connection:
            created_at = connection.execute("SELECT created_at FROM submissions WHERE id = ?", (document_id,)).fetchone()[0]
        analyzed = construir_analise(texto=TEXTO, linhas=LINHAS, normalizar=normalizar)
        tag = str(uuid4())
        encoded = json.dumps(analyzed, ensure_ascii=False)
        for prefix in ("preparacao", "segmentacao", "anotacao", "analise"):
            encoded = encoded.replace(f"{prefix}-fixture-07", f"{prefix}-{tag}")
        analyzed = json.loads(encoded)

        def replace_document(value):
            if isinstance(value, dict):
                if "documento_id" in value:
                    value["documento_id"] = document_id
                for child in value.values():
                    replace_document(child)
            elif isinstance(value, list):
                for child in value:
                    replace_document(child)

        replace_document(analyzed)
        annotated = analyzed["anotacao"]
        segmented = annotated["segmentacao"]
        prepared = segmented["preparacao"]
        prepared["metadados_origem"].update(documento_id=document_id, recebido_em=created_at)
        ruled = aplicar_regras_linguisticas(analyzed, registrado_em=self.INSTANTE)
        contextualized = construir_unidades_contexto(ruled, registrado_em=self.INSTANTE, **context_options)
        validar_unidades_contexto(contextualized)
        dump = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False)
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("INSERT INTO preparations(preparation_id,submission_id,record_json) VALUES(?,?,?)", (prepared["preparacao_id"], document_id, dump(prepared)))
            connection.execute("INSERT INTO segmentations(segmentation_id,preparation_id,submission_id,registered_at,record_json) VALUES(?,?,?,?,?)", (segmented["segmentacao_id"], prepared["preparacao_id"], document_id, segmented["registrado_em"], dump(segmented)))
            connection.execute("INSERT INTO annotations(annotation_id,segmentation_id,submission_id,registered_at,record_json) VALUES(?,?,?,?,?)", (annotated["anotacao_id"], segmented["segmentacao_id"], document_id, annotated["registrado_em"], dump(annotated)))
            connection.execute("INSERT INTO analyses(analysis_id,annotation_id,submission_id,registered_at,record_json) VALUES(?,?,?,?,?)", (analyzed["analise_id"], annotated["anotacao_id"], document_id, analyzed["registrado_em"], dump(analyzed)))
            connection.execute("INSERT INTO rule_runs(execution_id,analysis_id,submission_id,registered_at,record_json) VALUES(?,?,?,?,?)", (ruled["execucao_id"], analyzed["analise_id"], document_id, ruled["registrado_em"], dump(ruled)))
            connection.execute("INSERT INTO context_runs(execution_id,rule_execution_id,submission_id,registered_at,record_json) VALUES(?,?,?,?,?)", (contextualized["execucao_id"], ruled["execucao_id"], document_id, contextualized["registrado_em"], dump(contextualized)))
        return document_id, contextualized

    def enqueue(self, document_id, **options):
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", json=options)
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        return response.get_json()

    def process(self, job, *, fail=False):
        return processar_trabalho(self.database, job["execucao_id"], fabrica_adaptador=lambda perfil: AdaptadorControlado(perfil, falhar=fail), carregar_contexto=load_vector_context)

    def download(self, document_id, execution_id=None, client=None):
        query = {} if execution_id is None else {"vetorizacao_execucao_id": execution_id}
        return (client or self.client).get(f"/envios/{document_id}/vetorizacao.json", query_string=query)

    def status(self, document_id, job, client=None):
        return (client or self.client).get(job["status_url"])

    def test_enqueue_returns_202_and_never_loads_model_or_changes_source(self):
        document_id, context = self.source()
        before = self.snapshot()
        with patch("app.iniciar_trabalho") as worker:
            job = self.enqueue(document_id, contexto_execucao_id=context["execucao_id"], execucao_id="experimento/um", registrado_em=self.INSTANTE)
        worker.assert_not_called()
        self.assertEqual(job["estado"], "enfileirada")
        self.assertEqual(job["contexto_execucao_id"], context["execucao_id"])
        self.assertEqual(job["registrado_em"], self.INSTANTE)
        self.assertEqual(self.status(document_id, job).status_code, 200)
        self.assertEqual(self.download(document_id, job["execucao_id"]).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client.get(job["detalhe_url"]).status_code, 200)

    def test_default_cpu_profile_and_form_redirect(self):
        document_id, context = self.source()
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", data={"contexto_execucao_id": context["execucao_id"], "perfil_id": "e5_simetrico", "dispositivo": "cpu", "lote": "3", "formato_armazenamento": "float64"})
        self.assertEqual(response.status_code, 303)
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(set(query), {"vetorizacao_execucao_id"})
        job_id = query["vetorizacao_execucao_id"][0]
        job = self.client.get(f"/envios/{document_id}/vetorizacoes/{job_id}").get_json()
        self.assertEqual(job["opcoes"], {"dispositivo": "cpu", "lote": 3, "formato_armazenamento": "float64"})
        self.assertEqual(self.client.get(response.headers["Location"]).status_code, 200)

    def test_completed_result_keeps_exact_source_and_survives_restart(self):
        document_id, context = self.source(normalizar=True)
        before = self.snapshot()
        job = self.enqueue(document_id, contexto_execucao_id=context["execucao_id"])
        self.process(job)
        response = self.download(document_id, job["execucao_id"])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        result = response.get_json()
        validar_vetorizacao(result)
        self.assertEqual(result["contexto"], context)
        self.assertEqual(result["documento_id"], document_id)
        self.assertEqual(len(result["representacoes"]), 2 * len(context["unidades"]) + 1)
        self.assertEqual(len([item for item in result["representacoes"] if item["tipo"] == "documento"]), 1)
        self.assertEqual(self.snapshot(), before)
        restarted = create_app(self.config).test_client()
        self.assertEqual(self.download(document_id, job["execucao_id"], restarted).get_json(), result)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(restarted.get(job["detalhe_url"]).status_code, 200)

    def test_individual_representation_and_unknown_id(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        self.process(job)
        result = self.download(document_id).get_json()
        selected = result["representacoes"][0]
        query = {"vetorizacao_execucao_id": job["execucao_id"], "representacao_id": selected["id"]}
        response = self.client.get(f"/envios/{document_id}/representacao-vetorial.json", query_string=query)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        bundle = response.get_json()
        self.assertEqual(bundle["contexto"], context)
        self.assertEqual(bundle["perfil"], result["perfil"])
        self.assertEqual(bundle["representacao"], selected)
        self.assertIn(selected["id"], response.get_data(as_text=True))
        query["representacao_id"] = "outra"
        self.assertEqual(self.client.get(f"/envios/{document_id}/representacao-vetorial.json", query_string=query).status_code, 404)

    def test_multiple_models_and_profiles_coexist_without_overwrite(self):
        document_id, context = self.source()
        first = self.enqueue(document_id, perfil_id="e5_simetrico")
        self.process(first)
        original = self.download(document_id, first["execucao_id"]).get_json()
        second = self.enqueue(document_id, perfil_id="e5_conteudo", opcoes={"modelo": "modelo-controlado/alternativo", "revisao": "a" * 40, "formato_armazenamento": "float16"})
        self.process(second)
        alternate = self.download(document_id, second["execucao_id"]).get_json()
        self.assertNotEqual(original["espaco_vetorial_id"], alternate["espaco_vetorial_id"])
        self.assertEqual(original["contexto"], alternate["contexto"])
        self.assertEqual(self.download(document_id, first["execucao_id"]).get_json(), original)
        self.assertEqual(self.download(document_id).get_json(), alternate)

    def test_historical_vector_selection_resolves_original_chain_and_conflicts(self):
        document_id, old_context = self.source()
        old_job = self.enqueue(document_id, contexto_execucao_id=old_context["execucao_id"])
        self.process(old_job)
        _, new_context = self.source(document_id=document_id, normalizar=True, raio_anterior=0, raio_seguinte=0)
        self.assertNotEqual(old_context["preparacao_id"], new_context["preparacao_id"])
        detail = self.client.get(old_job["detalhe_url"])
        self.assertEqual(detail.status_code, 200, detail.get_data(as_text=True))
        for field, wrong in (("contexto_execucao_id", new_context["execucao_id"]), ("execucao_id", new_context["regras_execucao_id"]), ("preparacao_id", new_context["preparacao_id"]), ("anotacao_id", new_context["anotacao_id"])):
            response = self.client.get(f"/envios/{document_id}", query_string={"vetorizacao_execucao_id": old_job["execucao_id"], field: wrong})
            self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(self.download(document_id, old_job["execucao_id"]).get_json()["contexto"], old_context)

    def test_failure_has_diagnostic_no_final_download_and_can_resume(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        self.process(job, fail=True)
        failed = self.status(document_id, job)
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.get_json()["estado"], "falhou")
        self.assertIn("Falha controlada", failed.get_json()["erro"]["mensagem"])
        self.assertEqual(self.download(document_id).status_code, 409)
        response = self.client.post(f"{job['status_url']}/retomar", json={})
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["estado"], "enfileirada")
        self.process(job)
        self.assertEqual(self.download(document_id).status_code, 200)
        self.assertEqual(self.status(document_id, job).get_json()["tentativas"], 2)

    def test_resume_rejects_pending_completed_and_configuration_change(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        self.assertEqual(self.client.post(f"{job['status_url']}/retomar", json={}).status_code, 409)
        self.assertEqual(self.client.post(f"{job['status_url']}/retomar", json={"perfil_id": "e5_conteudo"}).status_code, 400)
        self.process(job)
        self.assertEqual(self.client.post(f"{job['status_url']}/retomar", json={}).status_code, 409)

    def test_duplicate_execution_identifier_preserves_first_experiment(self):
        document_id, context = self.source()
        first = self.enqueue(document_id, execucao_id="identificador-repetido")
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={"execucao_id": first["execucao_id"], "perfil_id": "e5_conteudo"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.status(document_id, first).get_json()["perfil_id"], "e5_simetrico")

    def test_json_options_are_strict_and_cannot_register_test_backend(self):
        document_id, context = self.source()
        invalid = [[], None, {"desconhecido": True}, {"perfil_id": None}, {"perfil_id": "inexistente"}, {"opcoes": None}, {"opcoes": []}, {"opcoes": {"backend": "simulado"}}, {"opcoes": {"lote": True}}, {"opcoes": {"limite_tokens": 0}}, {"contexto_execucao_id": ""}, {"execucao_id": None}, {"registrado_em": "sem-fuso"}]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.client.post(f"/envios/{document_id}/vetorizacoes", data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))

    def test_invalid_form_duplicates_empty_profile_and_noninteger_lot(self):
        document_id, context = self.source()
        common = {"contexto_execucao_id": context["execucao_id"]}
        invalid = [{}, {**common, "perfil_id": ""}, {**common, "lote": "0"}, {**common, "lote": "-1"}, {**common, "lote": "1.5"}, {**common, "dispositivo": "kaggle"}, {**common, "backend": "simulado"}, MultiDict([("contexto_execucao_id", context["execucao_id"]), ("contexto_execucao_id", context["execucao_id"])])]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(f"/envios/{document_id}/vetorizacoes", data=payload).status_code, 400)

    def test_missing_document_context_job_and_cross_document_return_404(self):
        for path in ("/envios/999/vetorizacoes",):
            self.assertEqual(self.client.post(path, json={}).status_code, 404)
        self.assertEqual(self.client.get("/envios/999/vetorizacoes/ausente").status_code, 404)
        self.assertEqual(self.client.get("/envios/999/vetorizacao.json").status_code, 404)
        document_id, context = self.source()
        self.assertEqual(self.client.post(f"/envios/{document_id}/vetorizacoes", json={"contexto_execucao_id": "ausente"}).status_code, 404)
        self.assertEqual(self.download(document_id).status_code, 404)
        job = self.enqueue(document_id)
        other_id, _ = self.source()
        self.assertEqual(self.client.get(f"/envios/{other_id}/vetorizacoes/{job['execucao_id']}").status_code, 404)
        self.assertEqual(self.download(other_id, job["execucao_id"]).status_code, 404)
        self.assertEqual(self.client.post(f"/envios/{other_id}/vetorizacoes/{job['execucao_id']}/retomar", json={}).status_code, 404)

    def test_download_and_detail_selectors_reject_duplicates_and_empty_values(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        invalid = [{"vetorizacao_execucao_id": ""}, {"outro": "x"}, MultiDict([("vetorizacao_execucao_id", job["execucao_id"]), ("vetorizacao_execucao_id", job["execucao_id"])])]
        for query in invalid:
            self.assertEqual(self.client.get(f"/envios/{document_id}/vetorizacao.json", query_string=query).status_code, 400)
        for query in invalid[::2]:
            self.assertEqual(self.client.get(f"/envios/{document_id}", query_string=query).status_code, 400)
        self.assertEqual(self.client.get(f"/envios/{document_id}/representacao-vetorial.json", query_string={"vetorizacao_execucao_id": job["execucao_id"]}).status_code, 400)

    def test_source_corruption_is_blocked_for_status_download_and_resume(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        self.process(job)
        tampered = copy.deepcopy(context)
        tampered["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["original"]["texto"] += "alterado"
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET record_json=? WHERE execution_id=?", (json.dumps(tampered, ensure_ascii=False), context["execucao_id"]))
        for response in (self.status(document_id, job), self.download(document_id, job["execucao_id"]), self.client.get(job["detalhe_url"]), self.client.post(f"{job['status_url']}/retomar", json={})):
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))

    def test_database_foreign_keys_prevent_deleting_used_context(self):
        document_id, context = self.source()
        self.enqueue(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM context_runs WHERE execution_id=?", (context["execucao_id"],))

    def test_corrupted_blob_is_rejected_before_download_and_detail(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        self.process(job)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE vector_blobs SET data=? WHERE blob_id=(SELECT blob_id FROM vector_blobs LIMIT 1)", (b"corrompido",))
        for response in (self.download(document_id, job["execucao_id"]), self.client.get(job["detalhe_url"])):
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
            self.assertIn("erro", response.get_json())

    def test_job_snapshot_profile_and_origin_metadata_are_validated(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE vector_jobs SET context_sha256=? WHERE execution_id=?", ("0" * 64, job["execucao_id"]))
        self.assertEqual(self.status(document_id, job).status_code, 409)
        self.assertEqual(self.client.get(job["detalhe_url"]).status_code, 409)
        self.assertEqual(self.download(document_id).status_code, 409)

    def test_full_stored_source_chain_is_checked_beyond_embedded_context(self):
        document_id, context = self.source()
        job = self.enqueue(document_id)
        self.process(job)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE analyses SET registered_at=? WHERE analysis_id=?", ("2026-10-09T14:15:00-03:00", context["analise_id"]))
        for response in (self.status(document_id, job), self.download(document_id), self.client.get(job["detalhe_url"])):
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))

    def test_readiness_is_recalculated_instead_of_trusting_stored_flag(self):
        document_id, context = self.source()
        self.assertTrue(context["validacao"]["pronto_para_etapa_09"])
        with patch("app.validar_unidades_contexto", return_value={"pronto_para_etapa_09": False}):
            response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.download(document_id).status_code, 404)

    def test_empty_optional_form_fields_preserve_profile_defaults(self):
        document_id, context = self.source()
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", data={"contexto_execucao_id": context["execucao_id"], "perfil_id": "e5_legado", "dispositivo": "cpu", "lote": "", "formato_armazenamento": ""})
        self.assertEqual(response.status_code, 303)
        job_id = parse_qs(urlsplit(response.headers["Location"]).query)["vetorizacao_execucao_id"][0]
        job = self.client.get(f"/envios/{document_id}/vetorizacoes/{job_id}").get_json()
        self.assertEqual(job["perfil"]["formato_armazenamento"], "float16")
        self.assertEqual(job["opcoes"], {"dispositivo": "cpu"})

    def test_background_worker_starts_only_after_committed_job_with_source_callback(self):
        document_id, context = self.source()
        auto_client = create_app({**self.config, "VECTORS_AUTO_START": True, "VECTORS_MAX_WORKERS": 2}).test_client()
        with patch("app.iniciar_trabalho") as worker:
            response = auto_client.post(f"/envios/{document_id}/vetorizacoes", json={})
        self.assertEqual(response.status_code, 202)
        worker.assert_called_once()
        args, kwargs = worker.call_args
        self.assertEqual(args, (str(self.database), response.get_json()["execucao_id"]))
        self.assertIs(kwargs["carregar_contexto"], load_vector_context)
        self.assertEqual(kwargs["max_workers"], 2)
        self.assertEqual(self.status(document_id, response.get_json()).status_code, 200)


if __name__ == "__main__":
    unittest.main()
