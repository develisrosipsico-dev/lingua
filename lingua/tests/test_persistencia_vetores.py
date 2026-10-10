"""Contratos SQLite/fila com adaptador controlado exclusivo destes testes."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fixtures_contexto import construir_regras, INSTANTE
from unidades_contexto import construir_unidades_contexto
from persistencia_vetores import (
    ErroPersistenciaVetores, inicializar_vetores, obter_vetorizacao,
)
from trabalhos_vetorizacao import (
    ErroTrabalhoVetorizacao, enfileirar_vetorizacao, obter_trabalho,
    listar_trabalhos, processar_trabalho, retomar_trabalho,
    interromper_trabalho, iniciar_trabalho,
)


class AdaptadorControlado:
    """Sem rede/pesos; números determinísticos somente para contratos."""
    def __init__(self, perfil, *, dimensao=7, falhar_apos=None, durante_geracao=None):
        self.perfil = deepcopy(perfil)
        self.dimensao = dimensao
        self.falhar_apos = falhar_apos
        self.durante_geracao = durante_geracao
        self.chamadas = 0

    def descrever(self):
        return {"backend": self.perfil["backend"], "versao_backend": "teste-contrato-1",
                "modelo": self.perfil["modelo"], "tokenizador": self.perfil["tokenizador"],
                "dimensao": self.dimensao, "limite_tokens": 512,
                "pooling": self.perfil["pooling"], "dispositivo": "cpu",
                "precisao_inferencia": "float32", "bibliotecas": {"fixture": "1.0.0"}}

    def tokenizar(self, texto):
        return {"input_ids": [1] + [ord(c) + 3 for c in texto] + [2],
                "attention_mask": [1] * (len(texto) + 2),
                "special_tokens_mask": [1] + [0] * len(texto) + [1],
                "offsets": [[0, 0]] + [[i, i + 1] for i in range(len(texto))] + [[0, 0]]}

    def gerar(self, entradas):
        self.chamadas += 1
        if self.durante_geracao:
            self.durante_geracao(self)
        if self.falhar_apos is not None and self.chamadas > self.falhar_apos:
            raise ValueError("Falha controlada após checkpoint.")
        return [[float(((sum(ord(c) for c in texto) + i * 19) % 107) + 1)
                 for i in range(self.dimensao)] for texto in entradas]


class PersistenciaVetoresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexto = construir_unidades_contexto(construir_regras(),
            execucao_id="contexto-fixture-storage-09", registrado_em=INSTANTE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "vetores.sqlite3"
        self.documento_id = self.contexto["documento_id"]
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("CREATE TABLE submissions (id TEXT PRIMARY KEY, content TEXT)")
        self.connection.execute("""CREATE TABLE context_runs (id INTEGER PRIMARY KEY,
            execution_id TEXT UNIQUE, rule_execution_id TEXT, submission_id TEXT REFERENCES submissions(id),
            registered_at TEXT, record_json TEXT)""")
        self.connection.execute("INSERT INTO submissions VALUES (?, ?)", (self.documento_id, "original preservado"))
        self.connection.execute("INSERT INTO context_runs VALUES (1, ?, ?, ?, ?, ?)", (
            self.contexto["execucao_id"], self.contexto["regras_execucao_id"], self.documento_id,
            self.contexto["registrado_em"], json.dumps(self.contexto, ensure_ascii=False)))
        self.connection.commit()
        inicializar_vetores(self.connection)
        self.connection.commit()

    def enfileirar(self, identificador="vetor-fixture-storage-09", **opcoes):
        result = enfileirar_vetorizacao(self.connection, self.documento_id, self.contexto,
            execucao_id=identificador, registrado_em=INSTANTE, opcoes=opcoes or None)
        self.connection.commit()
        return result

    def processar(self, identificador="vetor-fixture-storage-09", **kwargs):
        return processar_trabalho(self.database, identificador,
                                 fabrica_adaptador=kwargs.pop("fabrica_adaptador", AdaptadorControlado), **kwargs)

    def contar(self, tabela):
        self.assertIn(tabela, {"vector_jobs", "vector_runs", "vector_blobs", "vector_cache", "vector_representations"})
        return self.connection.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]

    def test_inicializacao_aditiva_idempotente_e_foreign_keys(self):
        inicializar_vetores(self.connection)
        self.assertEqual(self.connection.execute("SELECT content FROM submissions").fetchone()[0], "original preservado")
        self.assertEqual(self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("INSERT INTO vector_cache VALUES ('inexistente','chave','blob','{}')")

    def test_enfileirar_nao_carrega_modelo_e_preserva_opcoes(self):
        with patch("modelo_embeddings.criar_adaptador", side_effect=AssertionError("Não carregar")):
            job = self.enfileirar(formato_armazenamento="float64", limite_tokens=200)
            self.assertEqual(obter_trabalho(self.connection, job["execucao_id"]), job)
        self.assertEqual(job["estado"], "enfileirada")
        self.assertEqual(job["tentativas"], 0)
        self.assertEqual(job["opcoes"]["formato_armazenamento"], "float64")
        self.assertIsNone(obter_trabalho(self.connection, job["execucao_id"], documento_id="outro"))

    def test_entrada_invalida_nao_cria_trabalho(self):
        for kwargs in ({"execucao_id": ""}, {"execucao_id": self.contexto["execucao_id"]},
                       {"registrado_em": "2026-10-08"}, {"perfil_id": "desconhecido"},
                       {"opcoes": {"limite_tokens": True}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                enfileirar_vetorizacao(self.connection, self.documento_id, self.contexto, **kwargs)
        with self.assertRaises(ValueError):
            enfileirar_vetorizacao(self.connection, "outro", self.contexto)
        self.assertEqual(self.contar("vector_jobs"), 0)

    def test_tres_formatos_roundtrip_manifesto_e_associacoes(self):
        from vetorizacao import validar_vetorizacao
        for formato, tamanho in (("float16", 2), ("float32", 4), ("float64", 8)):
            with self.subTest(formato=formato):
                identificador = "vetor-" + formato
                self.enfileirar(identificador, formato_armazenamento=formato)
                self.assertEqual(self.processar(identificador)["estado"], "concluida")
                registro = obter_vetorizacao(self.connection, self.documento_id, execucao_id=identificador)
                validar_vetorizacao(json.loads(json.dumps(registro, ensure_ascii=False)))
                self.assertEqual(registro["contexto"], self.contexto)
                self.assertEqual(len(registro["representacoes"]), 2 * len(self.contexto["unidades"]) + 1)
                manifesto = json.loads(self.connection.execute("SELECT manifest_json FROM vector_runs WHERE execution_id = ?", (identificador,)).fetchone()[0])
                self.assertNotIn("contexto", manifesto)
                self.assertTrue(all("valores" not in a and "_blob_id" in a for a in manifesto["artefatos"]))
                for dimensão, nbytes in self.connection.execute("SELECT dimension, length(data) FROM vector_blobs WHERE format = ?", (formato,)):
                    self.assertEqual(nbytes, dimensão * tamanho)

    def test_experimentos_preservam_anteriores_e_deduplicam_bytes(self):
        self.enfileirar("experimento-um")
        self.processar("experimento-um")
        antigo = obter_vetorizacao(self.connection, self.documento_id, execucao_id="experimento-um")
        blobs = self.contar("vector_blobs")
        self.enfileirar("experimento-dois")
        self.processar("experimento-dois")
        self.assertEqual(self.contar("vector_runs"), 2)
        self.assertEqual(self.contar("vector_blobs"), blobs)
        self.assertEqual(obter_vetorizacao(self.connection, self.documento_id, execucao_id="experimento-um"), antigo)
        self.assertEqual([j["execucao_id"] for j in listar_trabalhos(self.connection, self.documento_id)], ["experimento-dois", "experimento-um"])

    def test_trabalho_concluido_nao_reexecuta_modelo(self):
        self.enfileirar()
        self.processar()
        self.assertEqual(self.processar(fabrica_adaptador=lambda _: self.fail("Modelo reexecutado"))["estado"], "concluida")
        self.assertEqual(self.contar("vector_runs"), 1)

    def test_falha_mantem_cache_sem_resultado_e_retomada_reusa(self):
        self.enfileirar(lote=1)
        adapters = []
        def fabrica(perfil):
            result = AdaptadorControlado(perfil, falhar_apos=1)
            adapters.append(result)
            return result
        falhou = self.processar(fabrica_adaptador=fabrica)
        self.assertEqual(falhou["estado"], "falhou")
        self.assertEqual(self.contar("vector_runs"), 0)
        self.assertGreater(self.contar("vector_cache"), 0)
        self.assertIsNone(obter_vetorizacao(self.connection, self.documento_id, execucao_id=falhou["execucao_id"]))
        retomar_trabalho(self.connection, falhou["execucao_id"])
        self.connection.commit()
        retomado = self.processar()
        self.assertEqual(retomado["estado"], "concluida")
        self.assertEqual(retomado["tentativas"], 2)
        self.assertEqual(self.contar("vector_runs"), 1)
        diagnosticos = [e for e in retomado["historico"] if e["operacao"] == "falhar"]
        self.assertEqual(len(diagnosticos), 1)
        self.assertIn("Falha controlada", diagnosticos[0]["erro"]["mensagem"])

    def test_retomada_recusa_adaptador_com_dimensao_diferente(self):
        self.enfileirar(lote=1)
        falhou = self.processar(fabrica_adaptador=lambda p: AdaptadorControlado(p, falhar_apos=1))
        retomar_trabalho(self.connection, falhou["execucao_id"])
        self.connection.commit()
        final = self.processar(fabrica_adaptador=lambda p: AdaptadorControlado(p, dimensao=8))
        self.assertEqual(final["estado"], "falhou")
        self.assertIn("mudou", final["erro"]["mensagem"])
        self.assertEqual(self.contar("vector_runs"), 0)

    def test_inferencia_libera_escrita_no_banco(self):
        self.enfileirar()
        def gravar(_):
            with sqlite3.connect(self.database, timeout=0.5) as outro:
                outro.execute("INSERT OR IGNORE INTO submissions VALUES ('escrita-durante-inferencia', 'ok')")
        result = self.processar(fabrica_adaptador=lambda p: AdaptadorControlado(p, durante_geracao=gravar))
        self.assertEqual(result["estado"], "concluida", result["erro"])
        self.assertEqual(self.connection.execute("SELECT content FROM submissions WHERE id = 'escrita-durante-inferencia'").fetchone()[0], "ok")

    def test_origem_alterada_durante_inferencia_impede_publicacao(self):
        self.enfileirar()
        nova = construir_unidades_contexto(self.contexto["regras"], execucao_id=self.contexto["execucao_id"],
            registrado_em=self.contexto["registrado_em"], raio_anterior=0)
        def adulterar(_):
            with sqlite3.connect(self.database) as outro:
                outro.execute("UPDATE context_runs SET record_json = ?", (json.dumps(nova, ensure_ascii=False),))
        result = self.processar(fabrica_adaptador=lambda p: AdaptadorControlado(p, durante_geracao=adulterar))
        self.assertEqual(result["estado"], "falhou")
        self.assertEqual(self.contar("vector_runs"), 0)
        self.assertIn("origem", result["erro"]["mensagem"].lower())

    def test_blob_corrompido_impede_download(self):
        self.enfileirar()
        self.processar()
        self.connection.execute("UPDATE vector_blobs SET data = zeroblob(length(data))")
        with self.assertRaises(ErroPersistenciaVetores):
            obter_vetorizacao(self.connection, self.documento_id, execucao_id="vetor-fixture-storage-09")

    def test_associacao_corrompida_impede_download(self):
        self.enfileirar()
        self.processar()
        self.connection.execute("UPDATE vector_representations SET representation_json = '{}' WHERE ordinal = 0")
        with self.assertRaises(ErroPersistenciaVetores):
            obter_vetorizacao(self.connection, self.documento_id, execucao_id="vetor-fixture-storage-09")

    def test_checkpoint_corrompido_nao_e_reutilizado(self):
        self.enfileirar(lote=1)
        falhou = self.processar(fabrica_adaptador=lambda p: AdaptadorControlado(p, falhar_apos=1))
        self.connection.execute("UPDATE vector_blobs SET data = zeroblob(length(data))")
        retomar_trabalho(self.connection, falhou["execucao_id"])
        self.connection.commit()
        result = self.processar()
        self.assertEqual(result["estado"], "falhou")
        self.assertEqual(self.contar("vector_runs"), 0)

    def test_claim_impede_execucao_dupla_e_interrupcao_invalida_worker(self):
        self.enfileirar()
        entrou, liberar = threading.Event(), threading.Event()
        def pausar(_):
            entrou.set()
            if not liberar.wait(10):
                raise AssertionError("Espera limitada da fixture expirou")
        future = iniciar_trabalho(self.database, "vetor-fixture-storage-09",
            fabrica_adaptador=lambda p: AdaptadorControlado(p, durante_geracao=pausar))
        self.assertTrue(entrou.wait(10))
        try:
            segundo = self.processar(fabrica_adaptador=lambda _: self.fail("Segundo worker gerou"))
            self.assertEqual(segundo["estado"], "executando")
            interromper_trabalho(self.connection, segundo["execucao_id"])
            self.connection.commit()
        finally:
            liberar.set()
        self.assertEqual(future.result(timeout=10)["estado"], "interrompida")
        self.assertEqual(self.contar("vector_runs"), 0)
        retomar_trabalho(self.connection, "vetor-fixture-storage-09")
        self.connection.commit()
        self.assertEqual(self.processar()["estado"], "concluida")

    def test_retomar_nao_aceita_pendente_ou_concluido(self):
        self.enfileirar()
        with self.assertRaises(ErroTrabalhoVetorizacao):
            retomar_trabalho(self.connection, "vetor-fixture-storage-09")
        self.processar()
        with self.assertRaises(ErroTrabalhoVetorizacao):
            retomar_trabalho(self.connection, "vetor-fixture-storage-09")

    def test_falha_na_associacao_reverte_publicacao_inteira(self):
        self.enfileirar()
        self.connection.execute("""CREATE TRIGGER fixture_falha_associacao BEFORE INSERT ON vector_representations
            WHEN NEW.ordinal = 1 BEGIN SELECT RAISE(ABORT, 'Falha controlada na segunda associação'); END""")
        self.connection.commit()
        result = self.processar()
        self.assertEqual(result["estado"], "falhou")
        self.assertEqual(self.contar("vector_runs"), 0)
        self.assertEqual(self.contar("vector_representations"), 0)
        self.assertGreater(self.contar("vector_cache"), 0)

    def test_conclusao_sem_resultado_e_progresso_invalido_sao_recusados(self):
        self.enfileirar()
        self.connection.execute("UPDATE vector_jobs SET state = 'concluida'")
        with self.assertRaises(ErroPersistenciaVetores):
            obter_trabalho(self.connection, "vetor-fixture-storage-09")
        self.connection.execute("UPDATE vector_jobs SET state = 'enfileirada', progress_json = ?",
            (json.dumps({"concluidos": True, "total": 1, "etapa": "falso"}),))
        with self.assertRaises(ErroTrabalhoVetorizacao):
            obter_trabalho(self.connection, "vetor-fixture-storage-09")


if __name__ == "__main__":
    unittest.main()
