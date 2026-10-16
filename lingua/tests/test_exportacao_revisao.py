"""Pacote de revisão: histórico fiel, diagnóstico e isolamento do documento.

As árvores linguísticas são manuais e os vetores vêm do adaptador controlado.
O exportador é executado com SQLite somente leitura e modelos bloqueados.
"""

from contextlib import ExitStack
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app import create_app
from exportacao_revisao import gerar_pacote_revisao
from fixtures_contexto import TEXTO
import test_app_vetorizacao as origem_controlada


class ReviewPackageTests(unittest.TestCase):
    INSTANTE = origem_controlada.VectorAppTests.INSTANTE
    STAGES = {
        "03": "preparations", "04": "segmentations", "05": "annotations",
        "06": "analyses", "07": "rule_runs", "08": "context_runs",
    }

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / "textos.sqlite3"
        self.client = create_app({
            "TESTING": True, "DATABASE": str(self.database),
            "VECTORS_AUTO_START": False,
        }).test_client()
        self.code = self.directory / "codigo"
        self.code.mkdir()
        (self.code / "app.py").write_text("# fonte de revisão: português\n", encoding="utf-8")

    def source(self, **options):
        return origem_controlada.VectorAppTests.source(self, **options)

    def enqueue(self, document_id, **options):
        return origem_controlada.VectorAppTests.enqueue(self, document_id, **options)

    def process(self, job, **options):
        return origem_controlada.VectorAppTests.process(self, job, **options)

    def rows(self, query, params=()):
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params)]

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return tuple(connection.iterdump())

    def register(self, text=TEXTO):
        response = self.client.post("/envios", data={"content": text})
        self.assertEqual(response.status_code, 303)
        return int(response.headers["Location"].rsplit("/", 1)[1])

    def export(self, document_id, **options):
        before = self.snapshot()
        with sqlite3.connect(f"file:{self.database}?mode=ro", uri=True) as connection, ExitStack() as stack:
            connection.row_factory = sqlite3.Row
            document = dict(connection.execute("SELECT * FROM submissions WHERE id = ?", (document_id,)).fetchone())
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("Exportar não executa inferência")))
                      for target in ("anotacao._carregar_modelo", "sintaxe_entidades._carregar_modelo",
                                     "modelo_embeddings.criar_adaptador", "app.iniciar_trabalho")]
            stream = gerar_pacote_revisao(connection, document, codigo_raiz=self.code, **options)
            self.assertIsInstance(stream, BytesIO)
            self.assertEqual(stream.tell(), 0)
            self.assertEqual(connection.total_changes, 0)
            for guard in guards:
                guard.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        archive = ZipFile(stream)
        self.addCleanup(archive.close)
        self.assertIsNone(archive.testzip())
        return archive, json.loads(archive.read("processamento_completo.json"))

    def test_every_history_and_failed_vector_job_belongs_to_the_selected_document(self):
        document_id, old_context = self.source(normalizar=True)
        completed = self.enqueue(document_id, contexto_execucao_id=old_context["execucao_id"],
                                 execucao_id="vetores-alvo-concluidos")
        self.process(completed)
        expected_vector = self.client.get(f"/envios/{document_id}/vetorizacao.json").get_json()
        _, new_context = self.source(document_id=document_id, raio_anterior=0, raio_seguinte=0)
        failed = self.enqueue(document_id, contexto_execucao_id=new_context["execucao_id"],
                              execucao_id="vetores-alvo-com-falha")
        self.process(failed, fail=True)
        other_id, other_context = self.source()
        other_job = self.enqueue(other_id, execucao_id="VETOR-EXCLUSIVO-OUTRO-DOCUMENTO",
                                 perfil_id="e5_simetrico",
                                 opcoes={"formato_armazenamento": "float64"})
        self.process(other_job)

        archive, bundle = self.export(document_id)
        self.assertEqual(bundle["documento"], self.rows("SELECT * FROM submissions WHERE id = ?", (document_id,))[0])
        self.assertEqual(set(bundle["etapas"]), {*self.STAGES, "09"})
        for stage, table in self.STAGES.items():
            with self.subTest(stage=stage):
                expected = self.rows(f"SELECT * FROM {table} WHERE submission_id = ? ORDER BY id", (document_id,))
                records = bundle["etapas"][stage]["registros"]
                self.assertEqual(bundle["etapas"][stage]["estado"], "disponivel")
                self.assertEqual(len(records), len(expected))
                self.assertGreaterEqual(len(records), 2)
                self.assertTrue(all(item["validacao"]["estado"] == "valido" for item in records))
                self.assertEqual([item["banco"] for item in records],
                                 [{key: value for key, value in row.items() if key != "record_json"} for row in expected])
                self.assertEqual([item["registro_bruto"] for item in records], [row["record_json"] for row in expected])
                self.assertEqual([item["registro"] for item in records], [json.loads(row["record_json"]) for row in expected])
        self.assertEqual([item["registro"] for item in bundle["etapas"]["08"]["registros"]], [old_context, new_context])

        vectors = bundle["etapas"]["09"]
        jobs = {item["banco"]["execution_id"]: item for item in vectors["trabalhos"]}
        self.assertEqual(set(jobs), {completed["execucao_id"], failed["execucao_id"]})
        self.assertEqual(jobs[completed["execucao_id"]]["banco"]["state"], "concluida")
        self.assertEqual(jobs[failed["execucao_id"]]["banco"]["state"], "falhou")
        for row in self.rows("SELECT * FROM vector_jobs WHERE submission_id = ? ORDER BY id", (document_id,)):
            item = jobs[row["execution_id"]]
            self.assertEqual(item["banco"]["attempts"], 1)
            self.assertNotIn("claim_token", item["banco"])
            for field in ("options_json", "profile_json", "progress_json", "error_json", "adapter_json"):
                self.assertEqual(item["campos_json"][field]["bruto"], row[field])
                self.assertEqual(item["campos_json"][field]["valor"],
                                 None if row[field] is None else json.loads(row[field]))
        self.assertIn("Falha controlada", jobs[failed["execucao_id"]]["campos_json"]["error_json"]["valor"]["mensagem"])
        expected_events = self.rows("""SELECT e.id FROM vector_job_events e JOIN vector_jobs j
            ON j.execution_id = e.execution_id WHERE j.submission_id = ? ORDER BY e.id""", (document_id,))
        self.assertEqual([event["banco"]["id"] for event in vectors["eventos"]], [row["id"] for row in expected_events])
        self.assertEqual({event["banco"]["operation"] for event in vectors["eventos"]},
                         {"enfileirar", "iniciar", "concluir", "falhar"})
        self.assertEqual(len(vectors["execucoes"]), 1)
        self.assertEqual(vectors["execucoes"][0]["resultado_completo"], expected_vector)
        for key, table, column, order in (("representacoes", "vector_representations", "representation_json", "ordinal"),
                                          ("cache", "vector_cache", "artifact_json", "cache_key")):
            expected = self.rows(f"""SELECT x.* FROM {table} x JOIN vector_jobs j
                ON j.execution_id = x.execution_id WHERE j.submission_id = ? ORDER BY x.{order}""", (document_id,))
            self.assertEqual(len(vectors[key]), len(expected), key)
            self.assertTrue(expected, key)
            for item, row in zip(vectors[key], expected):
                self.assertEqual(item["banco"], {field: value for field, value in row.items() if field != column})
                self.assertEqual(item["registro_bruto"], row[column])
                self.assertEqual(item["registro"], json.loads(row[column]))
        expected_blobs = self.rows("""SELECT DISTINCT b.*
            FROM vector_blobs b JOIN vector_cache c ON c.blob_id = b.blob_id
            JOIN vector_jobs j ON j.execution_id = c.execution_id WHERE j.submission_id = ?""", (document_id,))
        self.assertTrue(expected_blobs)
        self.assertEqual(len(vectors["blobs"]), len(expected_blobs))
        blobs = {item["banco"]["blob_id"]: item for item in vectors["blobs"]}
        self.assertEqual(set(blobs), {row["blob_id"] for row in expected_blobs})
        for row in expected_blobs:
            item = blobs[row["blob_id"]]
            self.assertEqual(item["banco"], {key: value for key, value in row.items() if key != "data"})
            self.assertEqual(item["caminho"], f"vetores/blobs/{row['sha256_bytes']}.bin")
            self.assertEqual(archive.read(item["caminho"]), row["data"])
            self.assertTrue(item["referencias"])
            self.assertEqual({reference["execucao_id"] for reference in item["referencias"]}, {completed["execucao_id"]})
        binary_files = {name: archive.read(name) for name in archive.namelist() if name.startswith("vetores/blobs/")}
        self.assertEqual(binary_files, {f"vetores/blobs/{row['sha256_bytes']}.bin": row["data"] for row in expected_blobs})
        serialized = archive.read("processamento_completo.json").decode("utf-8")
        self.assertNotIn(other_context["execucao_id"], serialized)
        self.assertNotIn(other_job["execucao_id"], serialized)

    def test_literal_original_positions_and_all_grammatical_fields_are_preserved(self):
        document_id, context = self.source(normalizar=True)
        archive, bundle = self.export(document_id)
        self.assertEqual(archive.read("texto_original.txt"), TEXTO.encode("utf-8"))
        exported_context = bundle["etapas"]["08"]["registros"][0]["registro"]
        self.assertEqual(exported_context, context)
        analysis = exported_context["regras"]["analise"]
        annotation = analysis["anotacao"]
        segmentation = annotation["segmentacao"]
        preparation = segmentation["preparacao"]
        self.assertEqual(preparation["original"]["texto"], TEXTO)
        self.assertEqual(preparation["trabalho"]["texto"], TEXTO.replace("\r\n", "\n"))
        self.assertTrue(preparation["alteracoes"])
        self.assertEqual(bundle["etapas"]["05"]["registros"][0]["registro"], annotation)
        self.assertEqual(bundle["etapas"]["06"]["registros"][0]["registro"], analysis)
        for item in annotation["anotacoes_tokens"]:
            self.assertTrue({"token_id", "lema", "pos", "tag", "morfologia", "origem",
                             "texto", "original", "trabalho", "campos_nao_resolvidos"} <= item.keys())
            start, end = item["original"]["inicio"], item["original"]["fim"]
            self.assertEqual(TEXTO[start:end], item["texto"])
        for item in analysis["sintaxe"]:
            self.assertTrue({"token_id", "dependencia", "cabeca_token_id", "origem",
                             "original", "trabalho"} <= item.keys())
        self.assertTrue(any(item["morfologia"] for item in annotation["anotacoes_tokens"]))
        self.assertTrue(exported_context["regras"]["ocorrencias"])
        self.assertTrue(exported_context["unidades"])

    def test_corrupt_records_and_job_columns_keep_raw_evidence_and_diagnostics(self):
        document_id, context = self.source()
        job = self.enqueue(document_id, execucao_id="trabalho-com-json-corrompido")
        malformed = '{"evidencia": "não perder",'
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE annotations SET record_json = ? WHERE annotation_id = ?", (malformed, context["anotacao_id"]))
            connection.execute("UPDATE preparations SET record_json = '[]' WHERE preparation_id = ?", (context["preparacao_id"],))
            connection.execute("UPDATE vector_jobs SET options_json = ?, error_json = ? WHERE execution_id = ?",
                               (malformed, malformed, job["execucao_id"]))
            connection.execute("UPDATE vector_job_events SET error_json = ? WHERE execution_id = ?", (malformed, job["execucao_id"]))
        archive, bundle = self.export(document_id)
        broken = bundle["etapas"]["05"]["registros"][0]
        self.assertEqual(broken["registro_bruto"], malformed)
        self.assertIsNone(broken["registro"])
        self.assertEqual(broken["validacao"]["estado"], "invalido")
        self.assertTrue(broken["validacao"]["erros"])
        self.assertEqual(bundle["etapas"]["05"]["estado"], "com_erros")
        wrong_shape = next(item for item in bundle["etapas"]["03"]["registros"]
                           if item["banco"]["preparation_id"] == context["preparacao_id"])
        self.assertEqual(wrong_shape["registro"], [])
        self.assertEqual(wrong_shape["validacao"]["estado"], "invalido")
        self.assertTrue(wrong_shape["validacao"]["erros"])
        self.assertEqual(bundle["etapas"]["08"]["registros"][0]["registro"], context)
        for item, column in ((bundle["etapas"]["09"]["trabalhos"][0], "options_json"),
                             (bundle["etapas"]["09"]["trabalhos"][0], "error_json"),
                             (bundle["etapas"]["09"]["eventos"][0], "error_json")):
            with self.subTest(column=column):
                field = item["campos_json"][column]
                self.assertEqual(field["bruto"], malformed)
                self.assertIsNone(field["valor"])
                self.assertTrue(field["erro"])
        self.assertEqual(bundle["etapas"]["09"]["estado"], "com_erros")
        self.assertEqual(archive.read("texto_original.txt"), TEXTO.encode("utf-8"))

    def test_manifest_hashes_cover_the_exact_bytes_of_every_other_file(self):
        document_id, _ = self.source()
        self.process(self.enqueue(document_id))
        archive, _ = self.export(document_id)
        names = archive.namelist()
        self.assertTrue(any(name.startswith("vetores/blobs/") for name in names))
        self.assertEqual(len(names), len(set(names)))
        manifest = json.loads(archive.read("manifesto.json"))
        listed = {item["caminho"]: item for item in manifest["arquivos"]}
        self.assertEqual(set(listed), set(names) - {"manifesto.json"})
        for name, item in listed.items():
            with self.subTest(file=name):
                payload = archive.read(name)
                self.assertEqual(item["tamanho_bytes"], len(payload))
                self.assertEqual(item["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertFalse(PurePosixPath(name).is_absolute())
                self.assertNotIn("..", PurePosixPath(name).parts)

    def test_source_allowlist_excludes_secrets_other_project_and_external_symlinks(self):
        allowed = {
            "app.py": "# fonte de revisão: português\n",
            "modulo.py": "VALOR = 'revisão'\n",
            "requirements.txt": "Flask==3.1.2\n",
            "requirements-vetorizacao.txt": "numpy==2.3.3\n",
            "templates/detalhe.html": "<p>Revisão</p>\n",
            "static/mapa.js": "const revisão = true;\n",
            "static/mapa.css": "p { color: blue; }\n",
        }
        forbidden = (".env", ".env.local", "instance/segredo.py", "agente_analista/privado.py",
                     ".git/config.py", ".venv/pacote.py", "static/segredo.json")
        secret = "SEGREDO-QUE-NAO-PODE-ENTRAR-NO-ZIP"
        for name, contents in allowed.items():
            path = self.code / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        for name in forbidden:
            path = self.code / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secret, encoding="utf-8")
        outside = self.directory / "externo.py"
        outside.write_text(secret, encoding="utf-8")
        for name in ("vazamento.py", "templates/externo.html", "static/externo.js"):
            (self.code / name).symlink_to(outside)
        archive, _ = self.export(self.register())
        actual = {name.removeprefix("codigo/") for name in archive.namelist() if name.startswith("codigo/")}
        self.assertEqual(actual, set(allowed))
        for name, contents in allowed.items():
            self.assertEqual(archive.read(f"codigo/{name}"), contents.encode("utf-8"))
        self.assertFalse(any(secret.encode("utf-8") in archive.read(name) for name in archive.namelist()))

    def test_missing_stages_remain_explicit_without_inference_or_synthetic_records(self):
        document_id = self.register("  Só registrado.\r\n  ")
        processing = {"estado": "falhou", "etapa": "05", "erro": {"mensagem": "Modelo indisponível"}}
        archive, bundle = self.export(document_id, processamento=processing)
        self.assertEqual(bundle["processamento_solicitado"], processing)
        self.assertEqual(len(bundle["etapas"]["03"]["registros"]), 1)
        for stage in ("04", "05", "06", "07", "08"):
            self.assertEqual(bundle["etapas"][stage]["estado"], "ausente")
            self.assertEqual(bundle["etapas"][stage]["registros"], [])
        self.assertEqual(bundle["etapas"]["09"]["estado"], "ausente")
        self.assertEqual(bundle["etapas"]["09"]["trabalhos"], [])
        self.assertEqual(archive.read("texto_original.txt"), "  Só registrado.\r\n  ".encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
