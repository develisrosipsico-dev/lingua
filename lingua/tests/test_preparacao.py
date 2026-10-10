import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from preparacao import (
    ErroPreparacao,
    ler_texto_original,
    mapear_intervalo,
    preparar_texto,
    validar_registro,
)


class PreparacaoTests(unittest.TestCase):
    ORIGINAL = "Eu não queria sair.\r\nMas fiquei."
    INSTANTE = "2026-10-07T12:34:56-03:00"

    def preparar(self, texto=None, **opcoes):
        argumentos = {
            "preparacao_id": "preparacao-teste-001",
            "registrado_em": self.INSTANTE,
        }
        argumentos.update(opcoes)
        return preparar_texto(
            self.ORIGINAL if texto is None else texto,
            "documento-001",
            **argumentos,
        )

    def conferir_texto(self, bloco, texto, versao):
        self.assertEqual(bloco["texto"], texto)
        self.assertEqual(bloco["versao"], versao)
        self.assertEqual(bloco["comprimento"], len(texto))
        self.assertEqual(bloco["sha256"], hashlib.sha256(texto.encode("utf-8")).hexdigest())

    def conferir_validacao(self, validacao):
        self.assertEqual(validacao["estado"], "pronto_para_etapa_04")
        self.assertIs(validacao["pronto_para_etapa_04"], True)
        self.assertIsInstance(validacao["verificacoes"], list)
        self.assertTrue(validacao["verificacoes"])

    def conferir_mapa(self, registro):
        original = registro["original"]["texto"]
        trabalho = registro["trabalho"]["texto"]
        cursor_original = cursor_trabalho = 0
        alteracoes = {alteracao["id"]: alteracao for alteracao in registro["alteracoes"]}
        self.assertEqual(len(alteracoes), len(registro["alteracoes"]))
        alteracoes_mapeadas = []
        for trecho in registro["mapa_trechos"]:
            with self.subTest(trecho=trecho):
                intervalo_original = trecho["original"]
                intervalo_trabalho = trecho["trabalho"]
                self.assertEqual(intervalo_original["inicio"], cursor_original)
                self.assertEqual(intervalo_trabalho["inicio"], cursor_trabalho)
                self.assertGreater(intervalo_original["fim"], intervalo_original["inicio"])
                self.assertGreater(intervalo_trabalho["fim"], intervalo_trabalho["inicio"])
                self.assertLessEqual(intervalo_original["fim"], len(original))
                self.assertLessEqual(intervalo_trabalho["fim"], len(trabalho))
                antes = original[intervalo_original["inicio"]:intervalo_original["fim"]]
                depois = trabalho[intervalo_trabalho["inicio"]:intervalo_trabalho["fim"]]
                if trecho["tipo"] == "copia":
                    self.assertEqual(antes, depois)
                    self.assertIsNone(trecho["alteracao_id"])
                else:
                    self.assertEqual(trecho["tipo"], "transformacao_tecnica")
                    alteracao = alteracoes[trecho["alteracao_id"]]
                    self.assertEqual(alteracao["original"], intervalo_original)
                    self.assertEqual(alteracao["trabalho"], intervalo_trabalho)
                    self.assertEqual(alteracao["antes"], antes)
                    self.assertEqual(alteracao["depois"], depois)
                    self.assertEqual((antes, depois), ("\r\n", "\n"))
                    self.assertEqual(alteracao["operacao"], "crlf_para_lf")
                    self.assertEqual(alteracao["versao_regra"], "1.0.0")
                    self.assertIsInstance(alteracao["motivo"], str)
                    self.assertTrue(alteracao["motivo"].strip())
                    alteracoes_mapeadas.append(trecho["alteracao_id"])
                cursor_original = intervalo_original["fim"]
                cursor_trabalho = intervalo_trabalho["fim"]
        self.assertEqual(cursor_original, len(original))
        self.assertEqual(cursor_trabalho, len(trabalho))
        self.assertCountEqual(alteracoes_mapeadas, alteracoes)

    def test_preservacao_literal_de_unicode_e_formatacao(self):
        original = "\ufeff  EU, eu... hã?! Não; ação — 漢字 📝 😀 e\u0301 é\t\t\r\n\r\n\n\rFim\u2028\u2029  "
        registro = self.preparar(original)
        self.conferir_texto(registro["original"], original, 1)
        self.conferir_texto(registro["trabalho"], original, 1)
        self.assertEqual(registro["alteracoes"], [])
        self.conferir_mapa(registro)
        self.conferir_validacao(registro["validacao"])

    def test_caracteres_combinantes_nao_sao_normalizados(self):
        registro = self.preparar("é e\u0301")
        self.assertEqual(registro["trabalho"]["texto"], "é e\u0301")
        self.assertEqual(registro["trabalho"]["comprimento"], 4)
        self.assertEqual(mapear_intervalo(registro, 2, 4), {"inicio": 2, "fim": 4})

    def test_indices_contam_pontos_de_codigo_e_nao_bytes_ou_utf16(self):
        registro = self.preparar("A😀éB")
        self.assertEqual(registro["original"]["comprimento"], 4)
        self.assertEqual(mapear_intervalo(registro, 1, 2), {"inicio": 1, "fim": 2})
        self.assertEqual(registro["original"]["texto"][1:2], "😀")

    def test_esquema_e_identificadores_fornecidos(self):
        registro = self.preparar()
        campos = {
            "schema_version", "documento_id", "preparacao_id", "registrado_em",
            "original", "trabalho", "coordenadas", "politica", "mapa_trechos",
            "alteracoes", "validacao",
        }
        self.assertTrue(campos.issubset(registro))
        self.assertEqual(registro["schema_version"], "1.0.0")
        self.assertEqual(registro["documento_id"], "documento-001")
        self.assertEqual(registro["preparacao_id"], "preparacao-teste-001")
        self.assertEqual(datetime.fromisoformat(registro["registrado_em"]), datetime.fromisoformat(self.INSTANTE))
        self.assertEqual(registro["coordenadas"], {
            "unidade": "pontos_de_codigo_unicode", "base": 0, "fim_exclusivo": True,
        })
        self.assertIsInstance(registro["politica"]["identificacao"], str)
        self.assertTrue(registro["politica"]["identificacao"].strip())
        self.assertEqual(registro["politica"]["versao"], "1.0.0")
        self.assertEqual(registro["politica"]["opcoes"], {"normalizar_crlf": False})

    def test_caller_pode_reproduzir_registro(self):
        self.assertEqual(self.preparar(normalizar_crlf=True), self.preparar(normalizar_crlf=True))

    def test_data_datetime_com_fuso_e_aceita(self):
        instante = datetime(2026, 10, 7, 15, 34, 56, tzinfo=timezone.utc)
        registro = self.preparar(registrado_em=instante)
        self.assertEqual(datetime.fromisoformat(registro["registrado_em"]), instante)

    def test_data_com_z_e_aceita(self):
        registro = self.preparar(registrado_em="2026-10-07T15:34:56Z")
        instante = datetime.fromisoformat(registro["registrado_em"].replace("Z", "+00:00"))
        self.assertEqual(instante, datetime(2026, 10, 7, 15, 34, 56, tzinfo=timezone.utc))

    def test_identificador_e_data_reais_sao_gerados_por_padrao(self):
        registro = preparar_texto("Texto", 0)
        identificador = UUID(registro["preparacao_id"])
        self.assertEqual(identificador.version, 4)
        instante = datetime.fromisoformat(registro["registrado_em"].replace("Z", "+00:00"))
        self.assertIsNotNone(instante.utcoffset())
        self.assertEqual(registro["documento_id"], 0)

    def test_arquivo_preserva_crlf_cr_lf_bom_e_utf8(self):
        original = "\ufeffAção 😀\r\n\r\nE\rF\nG\t e\u0301"
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "original.txt"
            caminho.write_bytes(original.encode("utf-8"))
            self.assertEqual(ler_texto_original(caminho), original)

    def test_leitura_aceita_encoding_explicito(self):
        original = "Ação\r\nOutra\rlinha\n"
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "original.txt"
            caminho.write_bytes(original.encode("latin-1"))
            self.assertEqual(ler_texto_original(caminho, encoding="latin-1"), original)

    def test_crlf_e_preservado_quando_opcao_desativada(self):
        registro = self.preparar(normalizar_crlf=False)
        self.assertEqual(registro["trabalho"]["texto"], self.ORIGINAL)
        self.assertEqual(registro["alteracoes"], [])
        self.assertEqual(mapear_intervalo(registro, 21, 32), {"inicio": 21, "fim": 32})

    def test_exemplo_usuario_crlf_para_lf(self):
        registro = self.preparar(normalizar_crlf=True)
        self.assertEqual(registro["politica"]["opcoes"], {"normalizar_crlf": True})
        self.conferir_texto(registro["original"], self.ORIGINAL, 1)
        self.conferir_texto(registro["trabalho"], "Eu não queria sair.\nMas fiquei.", 2)
        self.assertEqual(len(registro["alteracoes"]), 1)
        self.conferir_mapa(registro)
        self.assertEqual(registro["trabalho"]["texto"][20:31], "Mas fiquei.")
        self.assertEqual(self.ORIGINAL[21:32], "Mas fiquei.")
        self.assertEqual(mapear_intervalo(registro, 20, 31), {"inicio": 21, "fim": 32})

    def test_mapeamento_atravessa_transformacao_completa(self):
        registro = self.preparar(normalizar_crlf=True)
        self.assertEqual(mapear_intervalo(registro, 18, 21), {"inicio": 18, "fim": 22})
        self.assertEqual(mapear_intervalo(registro, 19, 20), {"inicio": 19, "fim": 21})
        self.assertEqual(mapear_intervalo(registro, 0, 20), {"inicio": 0, "fim": 21})
        self.assertEqual(mapear_intervalo(registro, 19, 31), {"inicio": 19, "fim": 32})

    def test_multiplos_crlf_preservam_demais_caracteres(self):
        original = "\r\nA\rB\n\r\n\r\n😀e\u0301\t\u2028\u2029\r\n"
        trabalho = "\nA\rB\n\n\n😀e\u0301\t\u2028\u2029\n"
        registro = self.preparar(original, normalizar_crlf=True)
        self.conferir_texto(registro["original"], original, 1)
        self.conferir_texto(registro["trabalho"], trabalho, 2)
        self.assertEqual(len(registro["alteracoes"]), 4)
        self.conferir_mapa(registro)
        self.assertEqual(mapear_intervalo(registro, 0, len(trabalho)), {"inicio": 0, "fim": len(original)})

    def test_crlf_no_inicio_fim_e_toda_entrada(self):
        for original in ["\r\n", "\r\n\r\n", "\r\ntexto", "texto\r\n"]:
            with self.subTest(original=repr(original)):
                registro = self.preparar(original, normalizar_crlf=True)
                self.assertEqual(registro["trabalho"]["texto"], original.replace("\r\n", "\n"))
                self.conferir_mapa(registro)
                self.conferir_validacao(validar_registro(registro))

    def test_opcao_ativada_sem_crlf_cria_versao_sem_alteracoes(self):
        original = "A\rB\nC\u2028D\u2029"
        registro = self.preparar(original, normalizar_crlf=True)
        self.conferir_texto(registro["original"], original, 1)
        self.conferir_texto(registro["trabalho"], original, 2)
        self.assertEqual(registro["alteracoes"], [])
        self.conferir_mapa(registro)

    def test_texto_vazio_tem_cobertura_vazia_valida(self):
        for normalizar in [False, True]:
            with self.subTest(normalizar=normalizar):
                registro = self.preparar("", normalizar_crlf=normalizar)
                self.conferir_texto(registro["original"], "", 1)
                self.conferir_texto(registro["trabalho"], "", 2 if normalizar else 1)
                self.assertEqual(registro["mapa_trechos"], [])
                self.assertEqual(registro["alteracoes"], [])
                self.assertEqual(mapear_intervalo(registro, 0, 0), {"inicio": 0, "fim": 0})
                self.conferir_validacao(validar_registro(registro))

    def test_intervalos_vazios_em_bordas_e_trechos_copiados(self):
        registro = self.preparar(normalizar_crlf=True)
        for posicao, original in [(0, 0), (5, 5), (19, 19), (20, 21), (31, 32)]:
            with self.subTest(posicao=posicao):
                self.assertEqual(mapear_intervalo(registro, posicao, posicao), {"inicio": original, "fim": original})

    def test_json_roundtrip_com_unicode_e_hashes_exatos(self):
        original = "\ufeff NÃO! 😀 e\u0301\t\r\n\r\n終\r\n"
        for normalizar in [False, True]:
            with self.subTest(normalizar=normalizar):
                registro = self.preparar(original, normalizar_crlf=normalizar)
                for ascii_only in [False, True]:
                    recuperado = json.loads(json.dumps(registro, ensure_ascii=ascii_only))
                    self.assertEqual(recuperado, registro)
                    self.conferir_validacao(validar_registro(recuperado))
                    self.conferir_mapa(recuperado)

    def test_metadados_conferidos_reutilizam_versao_original(self):
        metadados = {
            "documento_id": "documento-001",
            "versao": 7,
            "comprimento": len(self.ORIGINAL),
            "sha256": hashlib.sha256(self.ORIGINAL.encode("utf-8")).hexdigest(),
        }
        for normalizar in [False, True]:
            with self.subTest(normalizar=normalizar):
                registro = self.preparar(metadados=metadados, normalizar_crlf=normalizar)
                self.assertEqual(registro["original"]["versao"], 7)
                self.assertEqual(registro["trabalho"]["versao"], 8 if normalizar else 7)
                self.conferir_validacao(validar_registro(registro))

    def test_metadados_desconhecidos_sao_preservados_sem_alias(self):
        metadados = {"fonte": {"nome": "formulário", "tags": ["original"]}, "numero": 42}
        snapshot = copy.deepcopy(metadados)
        registro = self.preparar(metadados=metadados)
        self.assertEqual(metadados, snapshot)
        self.assertEqual(registro["metadados_origem"]["fonte"], snapshot["fonte"])
        metadados["fonte"]["tags"].append("posterior")
        self.assertEqual(registro["metadados_origem"]["fonte"]["tags"], ["original"])
        registro["metadados_origem"]["fonte"]["tags"].append("registro")
        self.assertEqual(metadados["fonte"]["tags"], ["original", "posterior"])

    def test_documento_id_inteiro_e_aceito(self):
        registro = preparar_texto("Texto", 42, metadados={"documento_id": 42})
        self.assertEqual(registro["documento_id"], 42)

    def test_erro_preparacao_e_value_error(self):
        self.assertTrue(issubclass(ErroPreparacao, ValueError))

    def test_recusa_texto_invalido(self):
        for texto in [None, 1, True, b"texto", [], "\ud800"]:
            with self.subTest(texto=repr(texto)):
                with self.assertRaises(ErroPreparacao):
                    preparar_texto(texto, "doc")

    def test_recusa_identificador_documento_invalido(self):
        for identificador in [None, "", -1, True, False, 1.5, [], {}]:
            with self.subTest(identificador=identificador):
                with self.assertRaises(ErroPreparacao):
                    preparar_texto("Texto", identificador)

    def test_recusa_identificador_preparacao_invalido(self):
        for identificador in ["", 1, True, [], {}]:
            with self.subTest(identificador=identificador):
                with self.assertRaises(ErroPreparacao):
                    self.preparar(preparacao_id=identificador)

    def test_recusa_data_sem_fuso_ou_invalida(self):
        for instante in ["", "ontem", "2026-10-07", "2026-10-07T12:34:56", datetime(2026, 10, 7), 123, True]:
            with self.subTest(instante=instante):
                with self.assertRaises(ErroPreparacao):
                    self.preparar(registrado_em=instante)

    def test_recusa_opcao_crlf_nao_booleana(self):
        for opcao in [0, 1, None, "false", "true", [], {}]:
            with self.subTest(opcao=opcao):
                with self.assertRaises(ErroPreparacao):
                    self.preparar(normalizar_crlf=opcao)

    def test_recusa_metadados_inconsistentes(self):
        casos = [
            {"documento_id": "outro"},
            {"comprimento": len(self.ORIGINAL) - 1},
            {"comprimento": True},
            {"comprimento": "32"},
            {"sha256": hashlib.sha256(b"outro").hexdigest()},
            {"sha256": "invalido"},
            {"sha256": None},
            {"versao": 0},
            {"versao": -1},
            {"versao": True},
            {"versao": "1"},
        ]
        for metadados in casos:
            with self.subTest(metadados=metadados):
                with self.assertRaises(ErroPreparacao):
                    self.preparar(metadados=metadados)

    def test_recusa_metadados_nao_mapeamento_ou_nao_json(self):
        for metadados in [[], "texto", 42, {"extras": {1, 2}}, {"extras": object()}, {"extras": float("nan")}]:
            with self.subTest(metadados=repr(metadados)):
                with self.assertRaises(ErroPreparacao):
                    self.preparar(metadados=metadados)

    def test_recusa_intervalos_invalidos(self):
        registro = self.preparar(normalizar_crlf=True)
        for inicio, fim in [(-1, 0), (0, -1), (2, 1), (0, 32), (32, 32), (False, 1), (0, True), (0.0, 1), (0, "1"), (None, 1)]:
            with self.subTest(inicio=inicio, fim=fim):
                with self.assertRaises(ErroPreparacao):
                    mapear_intervalo(registro, inicio, fim)

    def test_validar_nao_muta_registro(self):
        registro = self.preparar(normalizar_crlf=True)
        snapshot = copy.deepcopy(registro)
        self.conferir_validacao(validar_registro(registro))
        self.assertEqual(registro, snapshot)

    def test_preparacoes_independentes_nao_sobrescrevem_original(self):
        primeira = self.preparar()
        snapshot = copy.deepcopy(primeira)
        segunda = self.preparar(normalizar_crlf=True, preparacao_id="preparacao-teste-002")
        self.assertEqual(primeira, snapshot)
        self.assertEqual(segunda["original"], primeira["original"])
        self.assertNotEqual(segunda["trabalho"]["versao"], primeira["trabalho"]["versao"])
        self.assertNotEqual(segunda["preparacao_id"], primeira["preparacao_id"])

    def test_validador_recusa_estrutura_invalida(self):
        for registro in [None, [], "texto", {}]:
            with self.subTest(registro=registro):
                with self.assertRaises(ErroPreparacao):
                    validar_registro(registro)
        for campo in ["schema_version", "documento_id", "preparacao_id", "registrado_em", "original", "trabalho", "coordenadas", "politica", "metadados_origem", "mapa_trechos", "alteracoes", "validacao"]:
            with self.subTest(campo=campo):
                registro = self.preparar()
                del registro[campo]
                with self.assertRaises(ErroPreparacao):
                    validar_registro(registro)

    def test_validador_recusa_hash_ou_comprimento_adulterado(self):
        for bloco in ["original", "trabalho"]:
            for campo, valor in [("sha256", "0" * 64), ("comprimento", 999), ("versao", 0), ("comprimento", True)]:
                with self.subTest(bloco=bloco, campo=campo):
                    registro = self.preparar(normalizar_crlf=True)
                    registro[bloco][campo] = valor
                    with self.assertRaises(ErroPreparacao):
                        validar_registro(registro)

    def test_validador_recusa_schema_incompativel(self):
        registro = self.preparar()
        registro["schema_version"] = "99.0.0"
        with self.assertRaises(ErroPreparacao):
            validar_registro(registro)

    def test_validador_recusa_coordenadas_incompativeis(self):
        for campo, valor in [("unidade", "bytes_utf8"), ("base", 1), ("base", False), ("fim_exclusivo", False)]:
            with self.subTest(campo=campo, valor=valor):
                registro = self.preparar()
                registro["coordenadas"][campo] = valor
                with self.assertRaises(ErroPreparacao):
                    validar_registro(registro)

    def test_validador_recusa_politica_ou_versao_inconsistente(self):
        modificacoes = [
            lambda registro: registro["politica"]["opcoes"].__setitem__("normalizar_crlf", False),
            lambda registro: registro["politica"]["opcoes"].__setitem__("normalizar_crlf", 1),
            lambda registro: registro["politica"].__setitem__("versao", "99.0.0"),
            lambda registro: registro["trabalho"].__setitem__("versao", 1),
            lambda registro: registro["metadados_origem"].__setitem__("documento_id", "outro"),
        ]
        for alterar in modificacoes:
            with self.subTest(alteracao=alterar):
                registro = self.preparar(normalizar_crlf=True)
                alterar(registro)
                with self.assertRaises(ErroPreparacao):
                    validar_registro(registro)

    def test_validador_recusa_cobertura_limites_e_ordem_invalidos(self):
        modificacoes = [
            lambda mapa: mapa.pop(),
            lambda mapa: mapa.reverse(),
            lambda mapa: mapa[0]["original"].__setitem__("inicio", 1),
            lambda mapa: mapa[0]["trabalho"].__setitem__("inicio", -1),
            lambda mapa: mapa[0]["trabalho"].__setitem__("fim", 999),
            lambda mapa: mapa[0]["trabalho"].__setitem__("fim", 18),
            lambda mapa: mapa[0]["trabalho"].__setitem__("inicio", False),
        ]
        for alterar in modificacoes:
            with self.subTest(alteracao=alterar):
                registro = self.preparar(normalizar_crlf=True)
                alterar(registro["mapa_trechos"])
                with self.assertRaises(ErroPreparacao):
                    validar_registro(registro)

    def test_validador_recusa_copia_com_conteudo_diferente_mesmo_com_hash_correto(self):
        registro = self.preparar("ABC")
        registro["trabalho"]["texto"] = "AXC"
        registro["trabalho"]["sha256"] = hashlib.sha256(b"AXC").hexdigest()
        with self.assertRaises(ErroPreparacao):
            validar_registro(registro)

    def test_validador_recusa_alteracao_sem_correspondencia(self):
        modificacoes = [
            lambda registro: registro["alteracoes"].clear(),
            lambda registro: registro["alteracoes"].append(copy.deepcopy(registro["alteracoes"][0])),
            lambda registro: registro["alteracoes"][0].__setitem__("id", "nao-vinculado"),
            lambda registro: registro["alteracoes"][0].__setitem__("antes", "\n"),
            lambda registro: registro["alteracoes"][0].__setitem__("depois", "\r"),
            lambda registro: registro["alteracoes"][0].__setitem__("operacao", "minusculas"),
            lambda registro: registro["alteracoes"][0].__setitem__("versao_regra", "99.0.0"),
            lambda registro: registro["alteracoes"][0]["original"].__setitem__("fim", 20),
            lambda registro: registro["mapa_trechos"][1].__setitem__("alteracao_id", "inexistente"),
            lambda registro: registro["mapa_trechos"][0].__setitem__("tipo", "desconhecido"),
            lambda registro: registro["mapa_trechos"][0].__setitem__("alteracao_id", "indevido"),
        ]
        for alterar in modificacoes:
            with self.subTest(alteracao=alterar):
                registro = self.preparar(normalizar_crlf=True)
                alterar(registro)
                with self.assertRaises(ErroPreparacao):
                    validar_registro(registro)

    def test_flag_pronto_nao_oculta_inconsistencia(self):
        registro = self.preparar()
        registro["original"]["sha256"] = "0" * 64
        registro["validacao"]["estado"] = "pronto_para_etapa_04"
        registro["validacao"]["pronto_para_etapa_04"] = True
        with self.assertRaises(ErroPreparacao):
            validar_registro(registro)

    def test_mapeamento_nao_aceita_registro_inconsistente(self):
        registro = self.preparar(normalizar_crlf=True)
        registro["mapa_trechos"] = []
        with self.assertRaises(ErroPreparacao):
            mapear_intervalo(registro, 0, 1)


if __name__ == "__main__":
    unittest.main()
