"""Compatibilidade dos registros recebidos e evolução explícita da etapa 07.

Os quatro arquivos recebidos permanecem exemplos históricos da versão 1.0.0.
Novas execuções usam a versão 1.1.0 e preservam a cadeia anterior. O teste de
SQLite importa cópias dos exemplos com a identidade numérica exigida pela
aplicação; não altera os arquivos nem refaz previsões linguísticas.
"""

from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import connect_database, create_app
from regras_linguisticas import ErroRegras, aplicar_regras_linguisticas, validar_regras_linguisticas
from unidades_contexto import construir_unidades_contexto, validar_unidades_contexto


EXEMPLOS = Path(__file__).resolve().parents[1] / "examples"
HASHES_RECEBIDOS = {
    "regras_linguisticas_literal.json": "790961cb3d6e86fc2218efba4643f406009f474b9edca730645ce4ba97912696",
    "regras_linguisticas_normalizada.json": "1cf0f52d5102cf482c41744d90eacfabc3e1a58dede093bb2a5e833ae13ef3ba",
    "unidades_contexto_literal.json": "f1865cae4a8f074971fb1b73ff4405b4fdbb447a698465b85d70d94758db1077",
    "unidades_contexto_normalizada.json": "7f280b3d1ca58c965b746bd830aa1800a50864ec7a7851604e26bfb331a182b2",
}
INSTANTE = "2026-10-10T10:30:00+00:00"
RECEBIDO_EM = "2026-10-07T15:00:00+00:00"
TABELAS = ("submissions", "preparations", "segmentations", "annotations", "analyses", "rule_runs", "context_runs")


def ler_exemplo(nome):
    return json.loads((EXEMPLOS / nome).read_bytes())


def serializar(valor):
    return json.dumps(valor, ensure_ascii=False, allow_nan=False)


def hash_logico(descricao):
    texto = json.dumps(descricao, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def exemplo_para_sqlite(modalidade):
    """Adapte somente a identidade documental da cópia-fixture ao SQLite.

    O documento e todas as previsões, regras, IDs de processamento, textos,
    posições e hashes textuais continuam iguais aos exemplos históricos.
    A identidade lógica contextual inclui documento_id e deve acompanhar a
    troca explícita da identidade documental desta fixture.
    """
    registro = ler_exemplo(f"unidades_contexto_{modalidade}.json")

    def localizar(item):
        if isinstance(item, dict):
            if "documento_id" in item:
                item["documento_id"] = 1
            for filho in item.values():
                localizar(filho)
        elif isinstance(item, list):
            for filho in item:
                localizar(filho)

    localizar(registro)
    preparacao = registro["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
    preparacao["metadados_origem"] = {"documento_id": 1, "recebido_em": RECEBIDO_EM}
    for unidade in registro["unidades"]:
        unidade["janela_logica_id"] = hash_logico(unidade["selecao_logica"])
    return registro


class CompatibilidadeRegistrosTests(unittest.TestCase):
    def conferir_legado(self, nome):
        conteudo = (EXEMPLOS / nome).read_bytes()
        self.assertEqual(hashlib.sha256(conteudo).hexdigest(), HASHES_RECEBIDOS[nome])
        registro = json.loads(conteudo)
        antes = deepcopy(registro)
        if nome.startswith("unidades_contexto"):
            self.assertTrue(validar_unidades_contexto(registro)["pronto_para_etapa_09"])
            regras = registro["regras"]
        else:
            self.assertTrue(validar_regras_linguisticas(registro)["pronto_para_etapa_08"])
            regras = registro
        self.assertEqual(regras["catalogo"]["versao"], "1.0.0")
        self.assertEqual(regras["processamento"]["modulo"]["versao"], "1.0.0")
        self.assertEqual(registro, antes)
        self.assertEqual((EXEMPLOS / nome).read_bytes(), conteudo)

    def test_regras_literal_recebidas_continuam_validas_e_intactas(self):
        self.conferir_legado("regras_linguisticas_literal.json")

    def test_regras_normalizadas_recebidas_continuam_validas_e_intactas(self):
        self.conferir_legado("regras_linguisticas_normalizada.json")

    def test_contexto_literal_recebido_continua_valido_e_intacto(self):
        self.conferir_legado("unidades_contexto_literal.json")

    def test_contexto_normalizado_recebido_continua_valido_e_intacto(self):
        self.conferir_legado("unidades_contexto_normalizada.json")

    def test_novas_execucoes_usam_versao_11_e_preservam_cadeia_sem_modelo(self):
        for modalidade in ("literal", "normalizada"):
            with self.subTest(modalidade=modalidade):
                legado = ler_exemplo(f"regras_linguisticas_{modalidade}.json")
                antes = deepcopy(legado)
                analise = legado["analise"]
                with patch("anotacao._carregar_modelo", side_effect=AssertionError("modelo não autorizado")), \
                     patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("modelo não autorizado")):
                    novas_regras = aplicar_regras_linguisticas(
                        analise, execucao_id=f"compatibilidade:07:11:{modalidade}", registrado_em=INSTANTE,
                    )
                    self.assertEqual(novas_regras["catalogo"]["versao"], "1.1.0")
                    self.assertEqual(novas_regras["processamento"]["modulo"]["versao"], "1.1.0")
                    self.assertTrue(validar_regras_linguisticas(novas_regras)["pronto_para_etapa_08"])
                    antes_regras = deepcopy(novas_regras)
                    contexto = construir_unidades_contexto(
                        novas_regras, execucao_id=f"compatibilidade:08:11:{modalidade}", registrado_em=INSTANTE,
                    )
                    restaurado = json.loads(serializar(contexto))
                    self.assertTrue(validar_unidades_contexto(restaurado)["pronto_para_etapa_09"])
                self.assertEqual(legado, antes)
                self.assertEqual(novas_regras, antes_regras)
                self.assertEqual(novas_regras["analise"], analise)
                self.assertEqual(restaurado["regras"], novas_regras)
                self.assertEqual(restaurado["regras"]["analise"], analise)
                self.assertEqual(restaurado, contexto)

    def test_contexto_novo_pode_preservar_origem_legada_sem_promove_la(self):
        for modalidade in ("literal", "normalizada"):
            with self.subTest(modalidade=modalidade):
                legado = ler_exemplo(f"regras_linguisticas_{modalidade}.json")
                antes = deepcopy(legado)
                contexto = construir_unidades_contexto(
                    legado, execucao_id=f"compatibilidade:08:origem-legada:{modalidade}", registrado_em=INSTANTE,
                )
                restaurado = json.loads(serializar(contexto))
                self.assertTrue(validar_unidades_contexto(restaurado)["pronto_para_etapa_09"])
                self.assertEqual(restaurado["regras"], antes)
                self.assertEqual(restaurado["regras"]["catalogo"]["versao"], "1.0.0")
                self.assertEqual(restaurado["regras"]["processamento"]["modulo"]["versao"], "1.0.0")
                self.assertEqual(legado, antes)

    def test_versao_desconhecida_nao_reinterpreta_registro_legado(self):
        for campo in ("modulo", "catalogo"):
            with self.subTest(campo=campo):
                registro = ler_exemplo("regras_linguisticas_literal.json")
                if campo == "modulo":
                    registro["processamento"]["modulo"]["versao"] = "99.0.0"
                else:
                    registro["catalogo"]["versao"] = "99.0.0"
                with self.assertRaises(ErroRegras):
                    validar_regras_linguisticas(registro)


class CompatibilidadeHistoricoSQLiteTests(unittest.TestCase):
    def setUp(self):
        diretorio = tempfile.TemporaryDirectory()
        self.addCleanup(diretorio.cleanup)
        self.database = Path(diretorio.name) / "historico.sqlite3"
        self.configuracao = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.configuracao).test_client()

    def snapshot(self):
        with closing(connect_database(self.database)) as conexao:
            return {tabela: [tuple(linha) for linha in conexao.execute(f"SELECT * FROM {tabela} ORDER BY id")]
                    for tabela in TABELAS}

    def importar_legado(self, contexto):
        validar_unidades_contexto(contexto)
        regras = contexto["regras"]
        analise = regras["analise"]
        anotacao = analise["anotacao"]
        segmentacao = anotacao["segmentacao"]
        preparacao = segmentacao["preparacao"]
        with closing(connect_database(self.database)) as conexao:
            conexao.execute(
                "INSERT OR IGNORE INTO submissions (id, content, created_at) VALUES (?, ?, ?)",
                (1, preparacao["original"]["texto"], RECEBIDO_EM),
            )
            conexao.execute(
                "INSERT INTO preparations (preparation_id, submission_id, record_json) VALUES (?, ?, ?)",
                (preparacao["preparacao_id"], 1, serializar(preparacao)),
            )
            for tabela, campo_id, campo_origem, identificador_origem, registro in (
                ("segmentations", "segmentation_id", "preparation_id", preparacao["preparacao_id"], segmentacao),
                ("annotations", "annotation_id", "segmentation_id", segmentacao["segmentacao_id"], anotacao),
                ("analyses", "analysis_id", "annotation_id", anotacao["anotacao_id"], analise),
                ("rule_runs", "execution_id", "analysis_id", analise["analise_id"], regras),
                ("context_runs", "execution_id", "rule_execution_id", regras["execucao_id"], contexto),
            ):
                campo_registro = {"segmentations": "segmentacao_id", "annotations": "anotacao_id",
                                  "analyses": "analise_id", "rule_runs": "execucao_id", "context_runs": "execucao_id"}[tabela]
                conexao.execute(
                    f"INSERT INTO {tabela} ({campo_id}, {campo_origem}, submission_id, registered_at, record_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (registro[campo_registro], identificador_origem, 1, registro["registrado_em"], serializar(registro)),
                )
            conexao.commit()

    def test_importacao_legada_e_novas_execucoes_preservam_download_e_historico_apos_reinicio(self):
        legados = {modalidade: exemplo_para_sqlite(modalidade) for modalidade in ("literal", "normalizada")}
        for contexto in legados.values():
            self.importar_legado(contexto)
        antes = self.snapshot()
        novas_execucoes = {}
        for modalidade, legado in legados.items():
            resposta = self.client.post("/envios/1/regras", json={
                "analise_id": legado["analise_id"], "execucao_id": f"sqlite:07:11:{modalidade}",
                "registrado_em": INSTANTE,
            })
            self.assertEqual(resposta.status_code, 201, resposta.get_data(as_text=True))
            regras = resposta.get_json()
            self.assertEqual(regras["catalogo"]["versao"], "1.1.0")
            self.assertEqual(regras["analise"], legado["regras"]["analise"])
            resposta = self.client.post("/envios/1/contextos", json={
                "regras_execucao_id": regras["execucao_id"], "execucao_id": f"sqlite:08:11:{modalidade}",
                "registrado_em": INSTANTE,
            })
            self.assertEqual(resposta.status_code, 201, resposta.get_data(as_text=True))
            novas_execucoes[modalidade] = resposta.get_json()
        depois = self.snapshot()
        for tabela in TABELAS[:5]:
            self.assertEqual(depois[tabela], antes[tabela])
        for tabela in TABELAS[5:]:
            self.assertEqual(depois[tabela][:len(antes[tabela])], antes[tabela])
            self.assertEqual(len(depois[tabela]), 4)

        reiniciado = create_app(self.configuracao).test_client()
        for modalidade in legados:
            for registro in (legados[modalidade], novas_execucoes[modalidade]):
                with self.subTest(modalidade=modalidade, execucao_id=registro["execucao_id"]):
                    resposta = reiniciado.get("/envios/1/contexto.json", query_string={
                        "contexto_execucao_id": registro["execucao_id"],
                    })
                    self.assertEqual(resposta.status_code, 200, resposta.get_data(as_text=True))
                    self.assertEqual(resposta.get_json(), registro)
                    resposta = reiniciado.get("/envios/1/regras.json", query_string={
                        "execucao_id": registro["regras_execucao_id"],
                    })
                    self.assertEqual(resposta.status_code, 200, resposta.get_data(as_text=True))
                    self.assertEqual(resposta.get_json(), registro["regras"])
                    resposta = reiniciado.get("/envios/1", query_string={
                        "contexto_execucao_id": registro["execucao_id"],
                    })
                    self.assertEqual(resposta.status_code, 200, resposta.get_data(as_text=True))
                    self.assertIn(registro["execucao_id"], resposta.get_data(as_text=True))
                    self.assertIn(registro["regras_execucao_id"], resposta.get_data(as_text=True))
        self.assertEqual(self.snapshot(), depois)


if __name__ == "__main__":
    unittest.main()
