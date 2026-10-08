"""Seleção independente e resistência à adulteração da etapa 08."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import unidades_contexto as contexto
from unidades_contexto import (
    ErroConfiguracao, ErroContexto, ErroEntrada, ErroLimite,
    construir_unidades_contexto, consultar_unidade_contexto, validar_unidades_contexto,
)
from regras_linguisticas import validar_regras_linguisticas
from preparacao import ler_texto_original
from fixtures_regras import CASOS, construir_analise
from fixtures_contexto import (
    HASHES, INSTANTE, INTERVALOS_PADRAO, JANELAS_AMPLAS, JANELAS_ASSIMETRICAS,
    JANELAS_CRUZADAS, JANELAS_PADRAO, LIMITES_PADRAO, LINHAS, PARAGRAFOS,
    PERIODOS, TEXTO, TOKENS_FOCO, construir_pendencias, construir_regras,
    construir_regras_entidade, detector_vinculo_documental, renomear_processamentos,
)


def intervalo(par):
    return {"inicio": par[0], "fim": par[1]}


def digest(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def selecao_esperada(foco, selecionados, *, raio_anterior=1, raio_seguinte=1,
                     atravessar_paragrafos=False, texto=TEXTO):
    """Contrato e limites do gabarito; não acessa o construtor sob teste."""
    return {
        "versao": "1.0.0",
        "documento": {"documento_id": "documento-fixture-07", "sha256_original": digest(texto)},
        "foco": {"original": intervalo(PERIODOS[foco]),
                 "paragrafo_original": intervalo(PARAGRAFOS[0 if foco < 3 else 1])},
        "periodos": [{"original": intervalo(PERIODOS[i]),
                      "paragrafo_original": intervalo(PARAGRAFOS[0 if i < 3 else 1])}
                     for i in selecionados],
        "regras_segmentacao": {"identificacao": "segmentacao_portugues_por_regras", "versao": "1.0.0"},
        "politica_contexto": {"identificacao": "janela_periodos_consecutivos", "versao": "1.0.0",
                              "raio_anterior": raio_anterior, "raio_seguinte": raio_seguinte,
                              "atravessar_paragrafos": atravessar_paragrafos},
    }


class BaseContexto:
    @classmethod
    def setUpClass(cls):
        cls.fonte = construir_regras()

    def executar(self, *, fonte=None, **opcoes):
        argumentos = {"execucao_id": "contexto-fixture-08", "registrado_em": INSTANTE}
        argumentos.update(opcoes)
        return construir_unidades_contexto(self.fonte if fonte is None else fonte, **argumentos)

    def conferir_selecao(self, registro, gabarito):
        fontes = registro["regras"]["analise"]["anotacao"]["segmentacao"]["periodos"]
        self.assertEqual(len(registro["unidades"]), 5)
        for i, (unidade, esperados) in enumerate(zip(registro["unidades"], gabarito)):
            with self.subTest(foco=i):
                self.assertEqual(unidade["ordem"], i)
                self.assertEqual(unidade["periodo_foco_id"], fontes[i]["id"])
                self.assertEqual(unidade["paragrafo_foco_id"], fontes[i]["paragrafo_id"])
                self.assertEqual(unidade["janela"]["periodo_ids"], [fontes[j]["id"] for j in esperados])
                self.assertEqual(unidade["anteriores"], [fontes[j]["id"] for j in esperados if j < i])
                self.assertEqual(unidade["seguintes"], [fontes[j]["id"] for j in esperados if j > i])
                self.assertEqual(unidade["janela"]["periodo_ids"].count(fontes[i]["id"]), 1)

    def adulterar(self, mutacao):
        registro = self.executar()
        mutacao(registro)
        with self.assertRaises(ErroContexto):
            validar_unidades_contexto(registro)


class SelecaoContextoTests(BaseContexto, unittest.TestCase):
    def test_fixture_tem_limites_previamente_anotados(self):
        seg = self.fonte["analise"]["anotacao"]["segmentacao"]
        self.assertEqual([p["trabalho"] for p in seg["periodos"]], list(map(intervalo, PERIODOS)))
        self.assertEqual([p["trabalho"] for p in seg["paragrafos"]], list(map(intervalo, PARAGRAFOS)))

    def test_padrao_cobre_todos_focos_sem_atravessar_paragrafos(self):
        registro = self.executar()
        self.conferir_selecao(registro, JANELAS_PADRAO)
        self.assertEqual(registro["configuracao"], {"raio_anterior": 1, "raio_seguinte": 1,
                                                 "atravessar_paragrafos": False})
        self.assertTrue(validar_unidades_contexto(registro)["pronto_para_etapa_09"])

    def test_intervalos_e_textos_exatos_do_gabarito(self):
        registro = self.executar()
        for unidade, foco, janela in zip(registro["unidades"], PERIODOS, INTERVALOS_PADRAO):
            with self.subTest(foco=foco):
                for nome, limites in (("foco", foco), ("janela", janela)):
                    bloco = unidade[nome]
                    self.assertEqual(bloco["trabalho"], intervalo(limites))
                    self.assertEqual(bloco["original"], intervalo(limites))
                    self.assertEqual(bloco["texto"], TEXTO[slice(*limites)])
                    self.assertEqual(bloco["texto_original"], TEXTO[slice(*limites)])

    def test_cruzar_paragrafos_preserva_crlf_e_paragrafos_distintos(self):
        registro = self.executar(atravessar_paragrafos=True)
        self.conferir_selecao(registro, JANELAS_CRUZADAS)
        unidade = registro["unidades"][2]
        self.assertEqual(unidade["janela"]["texto"], "Casa azul! \t Céu.\r\n\r\nOutro.")
        self.assertEqual(unidade["janela"]["trabalho"], intervalo((17, 44)))
        paragrafos = registro["regras"]["analise"]["anotacao"]["segmentacao"]["paragrafos"]
        self.assertEqual(unidade["janela"]["paragrafo_ids"], [p["id"] for p in paragrafos])
        self.assertEqual(unidade["paragrafo_foco_id"], paragrafos[0]["id"])

    def test_raios_zero_mantem_apenas_foco(self):
        registro = self.executar(raio_anterior=0, raio_seguinte=0)
        self.conferir_selecao(registro, [[0], [1], [2], [3], [4]])
        for unidade in registro["unidades"]:
            self.assertEqual(unidade["foco"]["texto"], unidade["janela"]["texto"])
            for lado in ("anterior", "seguinte"):
                self.assertEqual(unidade["limites"][lado], {"raio_solicitado": 0,
                                                          "quantidade_selecionada": 0,
                                                          "motivo": "raio_zero"})

    def test_raios_assimetricos_preservam_ordem_de_leitura(self):
        self.conferir_selecao(self.executar(raio_anterior=2, raio_seguinte=0), JANELAS_ASSIMETRICAS)

    def test_raios_grandes_nao_preenchem_nem_omitem_focos(self):
        registro = self.executar(raio_anterior=100, raio_seguinte=100)
        self.conferir_selecao(registro, JANELAS_AMPLAS)
        self.conferir_selecao(self.executar(raio_anterior=100, raio_seguinte=100,
                                           atravessar_paragrafos=True), [[0, 1, 2, 3, 4]] * 5)

    def test_limites_diferenciam_documento_paragrafo_e_raio(self):
        registro = self.executar()
        for i, (anterior, seguinte) in enumerate(LIMITES_PADRAO):
            for lado, esperado, quantidade in (("anterior", anterior, int(i in (1, 2, 4))),
                                               ("seguinte", seguinte, int(i in (0, 1, 3)))):
                with self.subTest(foco=i, lado=lado):
                    self.assertEqual(registro["unidades"][i]["limites"][lado],
                                     {"raio_solicitado": 1, "quantidade_selecionada": quantidade,
                                      "motivo": esperado})

    def test_limites_amplos_atendem_primeiro_obstaculo_disponivel(self):
        registro = self.executar(raio_anterior=100, raio_seguinte=100)
        esperados = [("inicio_documento", "fronteira_paragrafo")] * 3 + [
            ("fronteira_paragrafo", "fim_documento")] * 2
        for unidade, (antes, depois) in zip(registro["unidades"], esperados):
            self.assertEqual(unidade["limites"]["anterior"]["motivo"], antes)
            self.assertEqual(unidade["limites"]["seguinte"]["motivo"], depois)

    def test_periodo_isolado_mantem_limites_do_documento(self):
        fonte = construir_regras(texto=CASOS["nominal"]["texto"], linhas=CASOS["nominal"]["tokens"])
        registro = self.executar(fonte=fonte)
        unidade, = registro["unidades"]
        self.assertEqual(unidade["janela"]["texto"], "O estudo.")
        self.assertEqual(unidade["anteriores"], [])
        self.assertEqual(unidade["seguintes"], [])
        self.assertEqual(unidade["limites"]["anterior"]["motivo"], "inicio_documento")
        self.assertEqual(unidade["limites"]["seguinte"]["motivo"], "fim_documento")

    def test_documento_sem_periodos_produz_lista_vazia_valida(self):
        fonte = construir_regras(texto=" \t\r\n\r\n ", linhas=[])
        registro = self.executar(fonte=fonte)
        self.assertEqual(registro["unidades"], [])
        self.assertTrue(validar_unidades_contexto(registro)["pronto_para_etapa_09"])

    def test_periodos_sem_regras_ou_entidades_recebem_contexto(self):
        registro = self.executar()
        for unidade in registro["unidades"][:4]:
            self.assertEqual(unidade["foco"]["anotacoes"]["entidades"], [])
            self.assertEqual(unidade["foco"]["anotacoes"]["regras"], [])
        self.assertTrue(registro["unidades"][4]["foco"]["anotacoes"]["regras"])

    def test_hesitacao_nao_altera_segmentacao_recebida(self):
        from fixtures_regras import ponto, token, verbo
        linhas = [token("Eu", "eu", "PRON", 2, "nsubj"), ponto(2, "..."),
                  verbo("voltei", "voltar", 2, tempo="Past"), ponto(2),
                  token("Fim", "fim", "NOUN", 4, "ROOT"), ponto(4)]
        fonte = construir_regras(texto="Eu...  voltei.  Fim.", linhas=linhas)
        registro = self.executar(fonte=fonte)
        self.assertEqual([u["foco"]["texto"] for u in registro["unidades"]], ["Eu...  voltei.", "Fim."])
        self.assertEqual(registro["regras"], fonte)

    def test_unicode_combinante_e_emoji_permanecem_exatos(self):
        caso = CASOS["unicode_crlf"]
        fonte = construir_regras(texto=caso["texto"], linhas=caso["tokens"])
        registro = self.executar(fonte=fonte, atravessar_paragrafos=True)
        segundo = registro["unidades"][1]["foco"]
        self.assertEqual(segundo["texto"], "Amanhã  João e\u0301 😀 ficará.")
        self.assertEqual(segundo["sha256_texto"], digest("Amanhã  João e\u0301 😀 ficará."))
        self.assertEqual(registro["unidades"][0]["janela"]["texto"],
                         "Ela\t\tnão saiu.\r\n\r\nAmanhã  João e\u0301 😀 ficará.")


class IntegridadeContextoTests(BaseContexto, unittest.TestCase):
    def test_hashes_constantes_independentes_para_foco_e_janela(self):
        registro = self.executar()
        self.assertEqual(registro["processamento"]["integridade"],
                         {"algoritmo": "sha256", "codificacao": "utf-8"})
        for unidade, foco, janela in zip(registro["unidades"], PERIODOS, INTERVALOS_PADRAO):
            for nome, limites in (("foco", foco), ("janela", janela)):
                self.assertEqual(unidade[nome]["sha256_texto"], HASHES[limites])
                self.assertEqual(unidade[nome]["sha256_texto_original"], HASHES[limites])
        cruzado = self.executar(atravessar_paragrafos=True)
        self.assertEqual(cruzado["unidades"][2]["janela"]["sha256_texto"], HASHES[(17, 44)])

    def test_descricao_e_identidade_canonicas_iguais_ao_gabarito(self):
        registro = self.executar()
        for i, unidade in enumerate(registro["unidades"]):
            esperado = selecao_esperada(i, JANELAS_PADRAO[i])
            self.assertEqual(unidade["selecao_logica"], esperado)
            serializado = json.dumps(esperado, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False)
            self.assertEqual(unidade["janela_logica_id"], digest(serializado))

    def test_ids_e_datas_da_execucao_nao_afetam_hashes_nem_identidade(self):
        primeiro = self.executar()
        segundo = self.executar(execucao_id="outro-contexto", registrado_em="2026-10-09T00:00:00Z")
        self.assertNotEqual(primeiro["execucao_id"], segundo["execucao_id"])
        for a, b in zip(primeiro["unidades"], segundo["unidades"]):
            self.assertNotEqual(a["id"], b["id"])
            self.assertEqual(a["janela_logica_id"], b["janela_logica_id"])
            self.assertEqual(a["foco"]["sha256_texto"], b["foco"]["sha256_texto"])
            self.assertEqual(a["janela"]["sha256_texto"], b["janela"]["sha256_texto"])
        self.assertTrue(validar_unidades_contexto(primeiro)["pronto_para_etapa_09"])

    def test_novos_ids_da_cadeia_nao_mudam_identidade_logica(self):
        fonte = renomear_processamentos(self.fonte)
        self.assertTrue(validar_regras_linguisticas(fonte)["pronto_para_etapa_08"])
        primeiro, segundo = self.executar(), self.executar(fonte=fonte)
        for a, b in zip(primeiro["unidades"], segundo["unidades"]):
            self.assertNotEqual(a["periodo_foco_id"], b["periodo_foco_id"])
            self.assertEqual(a["janela_logica_id"], b["janela_logica_id"])

    def test_focos_diferentes_com_mesma_janela_tem_identidades_distintas(self):
        a, b = self.executar()["unidades"][3:]
        self.assertEqual(a["janela"]["sha256_texto"], b["janela"]["sha256_texto"])
        self.assertNotEqual(a["janela_logica_id"], b["janela_logica_id"])

    def test_politicas_distintas_com_mesma_selecao_tem_identidades_distintas(self):
        a = self.executar(raio_anterior=50, raio_seguinte=50)
        b = self.executar(raio_anterior=51, raio_seguinte=50)
        for ua, ub in zip(a["unidades"], b["unidades"]):
            self.assertEqual(ua["janela"]["periodo_ids"], ub["janela"]["periodo_ids"])
            self.assertNotEqual(ua["janela_logica_id"], ub["janela_logica_id"])

    def test_mudar_selecao_altera_identidade(self):
        a = self.executar()["unidades"][2]
        b = self.executar(atravessar_paragrafos=True)["unidades"][2]
        self.assertNotEqual(a["selecao_logica"]["periodos"], b["selecao_logica"]["periodos"])
        self.assertNotEqual(a["janela_logica_id"], b["janela_logica_id"])

    def test_mudar_documento_fora_da_janela_altera_identidade(self):
        outro = construir_regras(texto=TEXTO + "\t", linhas=LINHAS)
        a, b = self.executar()["unidades"][0], self.executar(fonte=outro)["unidades"][0]
        self.assertEqual(a["janela"]["texto"], b["janela"]["texto"])
        self.assertNotEqual(a["janela_logica_id"], b["janela_logica_id"])

    def test_mudar_limites_originais_altera_identidade(self):
        outro = construir_regras(texto=" " + TEXTO, linhas=LINHAS)
        a, b = self.executar()["unidades"][0], self.executar(fonte=outro)["unidades"][0]
        self.assertEqual(a["janela"]["texto"], b["janela"]["texto"])
        self.assertNotEqual(a["selecao_logica"]["foco"]["original"], b["selecao_logica"]["foco"]["original"])
        self.assertNotEqual(a["janela_logica_id"], b["janela_logica_id"])

    def test_literal_normalizado_compartilham_selecao_original_e_identidade(self):
        literal = self.executar(atravessar_paragrafos=True)
        normalizado = self.executar(fonte=construir_regras(normalizar=True), atravessar_paragrafos=True)
        for a, b in zip(literal["unidades"], normalizado["unidades"]):
            self.assertEqual(a["janela"]["original"], b["janela"]["original"])
            self.assertEqual(a["foco"]["original"], b["foco"]["original"])
            self.assertEqual(a["janela_logica_id"], b["janela_logica_id"])
            self.assertEqual(a["janela"]["sha256_texto_original"], b["janela"]["sha256_texto_original"])
        a, b = literal["unidades"][2], normalizado["unidades"][2]
        self.assertNotEqual(a["janela"]["sha256_texto"], b["janela"]["sha256_texto"])
        self.assertEqual(b["janela"]["texto"], "Casa azul! \t Céu.\n\nOutro.")
        self.assertNotEqual(literal["unidades"][4]["foco"]["trabalho"],
                            normalizado["unidades"][4]["foco"]["trabalho"])

    def test_copia_defensiva_preserva_origem_completa(self):
        original = deepcopy(self.fonte)
        registro = self.executar()
        self.assertEqual(self.fonte, original)
        self.assertEqual(registro["regras"], original)
        registro["regras"]["catalogo"]["regras"][0]["id"] = "alteracao-local"
        self.assertEqual(self.fonte, original)

    def test_serializacao_json_preserva_identidade_e_validacao(self):
        registro = self.executar(atravessar_paragrafos=True)
        carregado = json.loads(json.dumps(registro, ensure_ascii=False, allow_nan=False))
        self.assertEqual(registro, carregado)
        self.assertTrue(validar_unidades_contexto(carregado)["pronto_para_etapa_09"])

    def test_leitor_existente_preserva_crlf_antes_de_construir_e_ler_json(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "origem-unicode.txt"
            caminho.write_bytes(TEXTO.encode("utf-8"))
            lido = ler_texto_original(caminho)
        self.assertEqual(lido, TEXTO)
        registro = self.executar(fonte=construir_regras(texto=lido), atravessar_paragrafos=True)
        carregado = json.loads(json.dumps(registro, ensure_ascii=False))
        self.assertEqual(carregado["unidades"][2]["janela"]["sha256_texto"], HASHES[(17, 44)])
        self.assertTrue(validar_unidades_contexto(carregado)["pronto_para_etapa_09"])

    def test_hash_adulterado_rejeitado(self):
        for bloco in ("foco", "janela"):
            for campo in ("sha256_texto", "sha256_texto_original"):
                with self.subTest(bloco=bloco, campo=campo):
                    self.adulterar(lambda r: r["unidades"][0][bloco].update({campo: "0" * 64}))

    def test_texto_adulterado_rejeitado_com_ou_sem_novo_hash(self):
        for bloco in ("foco", "janela"):
            for recalcular in (False, True):
                with self.subTest(bloco=bloco, recalcular=recalcular):
                    def mudar(r):
                        b = r["unidades"][0][bloco]
                        b["texto"] += "!"
                        if recalcular:
                            b["sha256_texto"] = digest(b["texto"])
                    self.adulterar(mudar)

    def test_intervalo_e_original_adulterados_rejeitados(self):
        for bloco in ("foco", "janela"):
            for campo in ("trabalho", "original"):
                with self.subTest(bloco=bloco, campo=campo):
                    self.adulterar(lambda r: r["unidades"][0][bloco][campo].update(inicio=1))
            self.adulterar(lambda r: r["unidades"][0][bloco].update(texto_original="outro"))

    def test_identidade_e_descricao_logica_adulteradas_rejeitadas(self):
        self.adulterar(lambda r: r["unidades"][0].update(janela_logica_id="0" * 64))
        self.adulterar(lambda r: r["unidades"][0]["selecao_logica"]["foco"]["original"].update(inicio=1))
        def recomputar(r):
            unidade = r["unidades"][0]
            unidade["selecao_logica"]["politica_contexto"]["raio_anterior"] = 10
            unidade["janela_logica_id"] = digest(json.dumps(unidade["selecao_logica"], ensure_ascii=False,
                                                           sort_keys=True, separators=(",", ":")))
        self.adulterar(recomputar)


class ContratoContextoTests(BaseContexto, unittest.TestCase):
    def test_raios_rejeitam_booleanos_negativos_float_e_texto(self):
        for lado in ("raio_anterior", "raio_seguinte"):
            for valor in (True, False, -1, 1.0, "1", None, [], {}):
                with self.subTest(lado=lado, valor=valor), self.assertRaises(ErroConfiguracao):
                    self.executar(**{lado: valor})

    def test_raios_extremos_respeitam_serializacao_json_sem_alterar_limite_global(self):
        registro = self.executar(raio_anterior=10 ** 500, raio_seguinte=10 ** 500)
        self.conferir_selecao(registro, JANELAS_AMPLAS)
        for lado in ("raio_anterior", "raio_seguinte"):
            with self.subTest(lado=lado), self.assertRaises(ErroConfiguracao):
                self.executar(**{lado: 10 ** 5000})

    def test_atravessar_paragrafos_requer_booleano_exato(self):
        for valor in (0, 1, "false", "true", None, [], {}):
            with self.subTest(valor=valor), self.assertRaises(ErroConfiguracao):
                self.executar(atravessar_paragrafos=valor)

    def test_ids_e_datas_fornecidos_sao_preservados(self):
        registro = self.executar()
        self.assertEqual(registro["execucao_id"], "contexto-fixture-08")
        self.assertEqual(registro["registrado_em"], INSTANTE)
        self.assertEqual(registro["regras_execucao_id"], self.fonte["execucao_id"])
        self.assertEqual([u["id"] for u in registro["unidades"]],
                         [f"contexto-fixture-08:unidade:{i}" for i in range(5)])

    def test_identificador_gerado_e_data_com_fuso(self):
        registro = construir_unidades_contexto(self.fonte)
        UUID(registro["execucao_id"])
        self.assertIsNotNone(datetime.fromisoformat(registro["registrado_em"]).utcoffset())
        data = datetime(2026, 10, 8, tzinfo=timezone.utc)
        self.assertEqual(self.executar(registrado_em=data)["registrado_em"], data.isoformat())

    def test_identificadores_vazios_e_datas_sem_fuso_rejeitados(self):
        for valor in ("", "  ", 12, [], {}):
            with self.subTest(id=valor), self.assertRaises(ErroContexto):
                self.executar(execucao_id=valor)
        for valor in ("2026-10-08T09:00:00", "2026-10-08", "incorreta", 12,
                      datetime(2026, 10, 8)):
            with self.subTest(data=valor), self.assertRaises(ErroContexto):
                self.executar(registrado_em=valor)

    def test_origem_diagnostica_valida_nao_e_promovida(self):
        fonte = construir_regras(regras_habilitadas=[])
        self.assertFalse(validar_regras_linguisticas(fonte)["pronto_para_etapa_08"])
        with self.assertRaises(ErroEntrada):
            self.executar(fonte=fonte)
        fonte["validacao"]["pronto_para_etapa_08"] = True
        with self.assertRaises(ErroEntrada):
            self.executar(fonte=fonte)

    def test_origem_invalida_ou_ausente_rejeitada(self):
        for fonte in (None, {}, [], "json"):
            with self.subTest(fonte=fonte), self.assertRaises(ErroEntrada):
                construir_unidades_contexto(fonte)
        fonte = deepcopy(self.fonte)
        fonte["analise"]["anotacao"]["segmentacao"]["tokens"][0]["texto"] = "adulterado"
        with self.assertRaises(ErroEntrada):
            self.executar(fonte=fonte)

    def test_cobertura_parcial_de_regras_e_preservada(self):
        fonte = construir_regras(regras_habilitadas=["oracao_predicado"])
        registro = self.executar(fonte=fonte)
        self.assertEqual(registro["regras"]["configuracao"], fonte["configuracao"])
        self.assertEqual(registro["regras"]["execucoes_regras"], fonte["execucoes_regras"])
        self.assertTrue(validar_unidades_contexto(registro)["pronto_para_etapa_09"])

    def test_consulta_por_identificador_ou_foco(self):
        registro = self.executar()
        for unidade in registro["unidades"]:
            self.assertEqual(consultar_unidade_contexto(registro, unidade_id=unidade["id"]), unidade)
            self.assertEqual(consultar_unidade_contexto(registro, periodo_id=unidade["periodo_foco_id"]), unidade)

    def test_consulta_nao_entrega_referencia_mutavel(self):
        registro = self.executar()
        copia = consultar_unidade_contexto(registro, unidade_id=registro["unidades"][0]["id"])
        copia["foco"]["texto"] = "alterado"
        self.assertEqual(registro["unidades"][0]["foco"]["texto"], "Árvore\tverde.")

    def test_consulta_seletores_invalidos_ou_desconhecidos(self):
        registro = self.executar()
        for seletores in ({}, {"unidade_id": "ausente"}, {"periodo_id": "ausente"},
                          {"unidade_id": registro["unidades"][0]["id"],
                           "periodo_id": registro["unidades"][0]["periodo_foco_id"]}):
            with self.subTest(seletores=seletores), self.assertRaises(ErroContexto):
                consultar_unidade_contexto(registro, **seletores)

    def test_consulta_foco_em_historicos_com_politicas_distintas(self):
        primeiro = self.executar()
        segundo = self.executar(execucao_id="execucao-ampla", atravessar_paragrafos=True)
        foco = primeiro["unidades"][2]["periodo_foco_id"]
        a = consultar_unidade_contexto(primeiro, periodo_id=foco)
        b = consultar_unidade_contexto(segundo, periodo_id=foco)
        self.assertEqual(a["janela"]["texto"], "Casa azul! \t Céu.")
        self.assertEqual(b["janela"]["texto"], "Casa azul! \t Céu.\r\n\r\nOutro.")

    def test_sem_reexecucao_de_modelos_nem_consulta_legada_por_foco(self):
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("modelo proibido")), \
             patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("modelo proibido")), \
             patch("segmentacao.contexto_periodo", side_effect=AssertionError("consulta repetida proibida")):
            registro = self.executar()
        self.assertFalse(registro["processamento"]["modelo_reexecutado"])

    def test_origem_validada_uma_vez_e_mapeamento_em_lote(self):
        with patch("unidades_contexto.validar_regras_linguisticas", wraps=validar_regras_linguisticas) as validador, \
             patch("unidades_contexto.mapear_intervalos", wraps=contexto.mapear_intervalos) as mapeamento:
            self.executar()
        self.assertEqual(validador.call_count, 1)
        self.assertEqual(mapeamento.call_count, 1)

    def test_limites_de_recursos_recusam_expansao_sem_resultado_parcial(self):
        original = deepcopy(self.fonte)
        for constante, limite in (("MAX_UNIDADES", 4), ("MAX_REFERENCIAS_PERIODOS", 1),
                                  ("MAX_CARACTERES_TEXTUAIS", 1), ("MAX_REFERENCIAS", 1)):
            with self.subTest(constante=constante), patch.object(contexto, constante, limite), \
                 self.assertRaises(ErroLimite):
                self.executar()
            self.assertEqual(self.fonte, original)

    def test_execucao_contextual_precisa_id_distinto_da_origem(self):
        with self.assertRaises(ErroContexto):
            self.executar(execucao_id=self.fonte["execucao_id"])

    def test_validacao_rejeita_focos_omitidos_duplicados_ou_reordenados(self):
        for acao in (lambda r: r["unidades"].pop(),
                     lambda r: r["unidades"].__setitem__(1, deepcopy(r["unidades"][0])),
                     lambda r: r["unidades"].reverse()):
            with self.subTest(acao=acao):
                self.adulterar(acao)

    def test_validacao_rejeita_vizinhos_errados_com_mesma_contagem(self):
        def mudar(r):
            unidade = r["unidades"][1]
            ultimo = r["unidades"][4]["periodo_foco_id"]
            unidade["seguintes"][0] = ultimo
            unidade["janela"]["periodo_ids"][2] = ultimo
        self.adulterar(mudar)

    def test_validacao_rejeita_janela_reordenada_e_foco_duplicado(self):
        self.adulterar(lambda r: r["unidades"][1]["janela"]["periodo_ids"].reverse())
        self.adulterar(lambda r: r["unidades"][1]["janela"]["periodo_ids"].__setitem__(
            0, r["unidades"][1]["periodo_foco_id"]))

    def test_validacao_rejeita_associacoes_limites_e_politica_adulterados(self):
        self.adulterar(lambda r: r["unidades"][0].update(paragrafo_foco_id=r["unidades"][4]["paragrafo_foco_id"]))
        self.adulterar(lambda r: r["unidades"][0]["limites"]["anterior"].update(motivo="raio_atendido"))
        self.adulterar(lambda r: r["configuracao"].update(atravessar_paragrafos=True))
        self.adulterar(lambda r: r["politica"].update(versao="2.0.0"))
        self.adulterar(lambda r: r["validacao"].update(pronto_para_etapa_09=False))

    def test_validacao_rejeita_referencias_token_e_regra_de_outro_registro(self):
        self.adulterar(lambda r: r["unidades"][0]["foco"]["token_ids"].__setitem__(0, "outro:token:0"))
        self.adulterar(lambda r: r["unidades"][0]["foco"]["anotacoes"]["morfologia"][0].update(
            anotacao_id="outra-anotacao"))
        self.adulterar(lambda r: r["unidades"][0]["foco"]["anotacoes"]["sintaxe"][0].update(
            analise_id="outra-analise"))
        self.adulterar(lambda r: r["unidades"][4]["foco"]["anotacoes"]["regras"][0].update(
            regras_execucao_id="outras-regras"))

    def test_mesmo_texto_em_outro_periodo_nao_autoriza_trocar_token(self):
        caso = CASOS["repeticao"]
        fonte = construir_regras(texto=caso["texto"], linhas=caso["tokens"])
        registro = self.executar(fonte=fonte)
        a, b = registro["unidades"]
        self.assertEqual(a["foco"]["texto"], b["foco"]["texto"])
        a["foco"]["anotacoes"]["morfologia"][0] = deepcopy(b["foco"]["anotacoes"]["morfologia"][0])
        with self.assertRaises(ErroContexto):
            validar_unidades_contexto(registro)

    def test_json_nao_estrito_rejeitado(self):
        for valor in (float("nan"), float("inf"), (1, 2), {1: "chave"}):
            with self.subTest(valor=valor):
                self.adulterar(lambda r: r.update(extra=valor))


class AnotacoesPendenciasTests(BaseContexto, unittest.TestCase):
    def test_morfologia_e_sintaxe_referenciam_tokens_do_proprietario(self):
        registro = self.executar()
        seg = self.fonte["analise"]["anotacao"]["segmentacao"]
        for i, unidade in enumerate(registro["unidades"]):
            esperados = [seg["tokens"][j]["id"] for j in TOKENS_FOCO[i]]
            self.assertEqual(unidade["foco"]["token_ids"], esperados)
            self.assertEqual(unidade["foco"]["anotacoes"]["morfologia"],
                             [{"anotacao_id": self.fonte["anotacao_id"], "token_id": t} for t in esperados])
            self.assertEqual(unidade["foco"]["anotacoes"]["sintaxe"],
                             [{"analise_id": self.fonte["analise_id"], "token_id": t} for t in esperados])

    def test_anotacoes_vizinhas_nao_sao_transferidas_ao_foco(self):
        registro = self.executar()
        unidade = registro["unidades"][3]
        self.assertEqual(unidade["foco"]["anotacoes"]["regras"], [])
        vizinho, = unidade["anotacoes_vizinhos"]
        self.assertEqual(vizinho["periodo_id"], registro["unidades"][4]["periodo_foco_id"])
        self.assertTrue(vizinho["anotacoes"]["regras"])
        self.assertEqual(vizinho["anotacoes"], registro["unidades"][4]["foco"]["anotacoes"])

    def test_anotacao_de_outro_periodo_rejeitada_mesmo_com_contagem_correta(self):
        self.adulterar(lambda r: r["unidades"][0]["foco"]["anotacoes"].update(
            morfologia=deepcopy(r["unidades"][1]["foco"]["anotacoes"]["morfologia"])))

    def test_pendencias_textuais_preservam_origem_e_nao_inferem_localizacao(self):
        fonte = construir_pendencias()
        registro = self.executar(fonte=fonte, raio_anterior=0, raio_seguinte=0)
        por_id = {o["id"]: o for o in fonte["ocorrencias"]}
        total_esperado = sum(len(o[campo]) for o in fonte["ocorrencias"]
                             for campo in ("ambiguidades", "necessidade_contexto"))
        pendencias = [p for u in registro["unidades"] for p in u["pendencias"]]
        self.assertEqual(len(pendencias), total_esperado)
        self.assertGreater(len(pendencias), 0)
        for pendencia in pendencias:
            origem = pendencia["origem"]
            ocorrencia = por_id[origem["ocorrencia_id"]]
            self.assertEqual(pendencia["texto"], ocorrencia[origem["campo"]][origem["indice"]])
            self.assertEqual(origem["regras_execucao_id"], fonte["execucao_id"])
            self.assertEqual(origem["periodo_id"], ocorrencia["periodo_id"])
            self.assertEqual(pendencia["papel"], "foco")
            self.assertEqual(pendencia["localizacao"], "localizacao_indeterminada")
            self.assertEqual(pendencia["contexto_disponivel"]["documento_id"], fonte["documento_id"])
            self.assertNotIn("resolvida", pendencia)

    def test_janela_ampla_nao_resolve_ambiguidades_nem_referencia_temporal_externa(self):
        fonte = construir_pendencias()
        registro = self.executar(fonte=fonte, raio_anterior=10, raio_seguinte=10,
                                 atravessar_paragrafos=True)
        for unidade in registro["unidades"]:
            self.assertTrue(unidade["pendencias"])
            for p in unidade["pendencias"]:
                self.assertEqual(p["localizacao"], "localizacao_indeterminada")
                self.assertEqual(p["contexto_disponivel"]["periodo_ids"],
                                 unidade["janela"]["periodo_ids"])
        self.assertTrue(any("refer" in p["texto"].lower() or "data" in p["texto"].lower()
                            for p in registro["unidades"][2]["pendencias"]))

    def test_pendencias_do_vizinho_preservam_propriedade(self):
        registro = self.executar(fonte=construir_pendencias())
        unidade = registro["unidades"][1]
        self.assertTrue(unidade["pendencias"])
        self.assertTrue(all(p["papel"] == "vizinho" for p in unidade["pendencias"]))
        self.assertTrue(all(p["origem"]["periodo_id"] != unidade["periodo_foco_id"]
                            for p in unidade["pendencias"]))

    def test_necessidade_e_localizacao_adulteradas_rejeitadas(self):
        for campo, valor in (("texto", "novo significado"), ("localizacao", "na_janela")):
            registro = self.executar(fonte=construir_pendencias())
            registro["unidades"][0]["pendencias"][0][campo] = valor
            with self.assertRaises(ErroContexto):
                validar_unidades_contexto(registro)

    def test_evidencias_descontinuas_e_vinculos_tipados_preservados(self):
        fonte = construir_regras(texto=CASOS["negacao_principal"]["texto"],
                                 linhas=CASOS["negacao_principal"]["tokens"])
        registro = self.executar(fonte=fonte)
        negacao, = [o for o in registro["regras"]["ocorrencias"] if o["regra"]["id"] == "negacao_nao"]
        self.assertEqual([t["texto"] for t in negacao["alcance"]["trechos"]], ["Não pensei", "."])
        self.assertEqual(registro["regras"], fonte)
        unidade, = registro["unidades"]
        self.assertTrue(unidade["vinculos_disponiveis"])
        self.assertTrue(all(v["vinculo"]["alvo_tipo"] in ("token", "periodo", "ocorrencia", "entidade")
                            for v in unidade["vinculos_disponiveis"]))

    def test_referencias_completas_para_documento_e_paragrafo(self):
        for unidade in self.executar()["unidades"]:
            self.assertEqual(unidade["referencias"],
                             {**{c: self.fonte[c] for c in ("documento_id", "preparacao_id", "segmentacao_id",
                                                          "anotacao_id", "analise_id")},
                              "regras_execucao_id": self.fonte["execucao_id"],
                              "paragrafo_id": unidade["paragrafo_foco_id"]})

    def test_exemplos_reais_preservam_entidades_e_limitacoes_anteriores(self):
        caminho = Path(__file__).resolve().parents[1] / "examples/regras_linguisticas_literal.json"
        fonte = json.loads(caminho.read_text(encoding="utf-8"))
        registro = self.executar(fonte=fonte)
        self.assertEqual(registro["regras"], fonte)
        entidades = fonte["analise"]["entidades"]
        refs = [r for u in registro["unidades"] for r in u["foco"]["anotacoes"]["entidades"]]
        self.assertEqual(refs, [{"analise_id": fonte["analise_id"], "entidade_id": e["id"]} for e in entidades])

    def test_entidade_manual_pertence_ao_vizinho_sem_transferir_propriedade(self):
        fonte = construir_regras_entidade()
        registro = self.executar(fonte=fonte)
        self.assertEqual(len(fonte["analise"]["entidades"]), 1)
        entidade = fonte["analise"]["entidades"][0]
        esperado = [{"analise_id": fonte["analise_id"], "entidade_id": entidade["id"]}]
        self.assertEqual(registro["unidades"][3]["foco"]["anotacoes"]["entidades"], [])
        self.assertEqual(registro["unidades"][3]["anotacoes_vizinhos"][0]["anotacoes"]["entidades"], esperado)
        self.assertEqual(registro["unidades"][4]["foco"]["anotacoes"]["entidades"], esperado)
        self.assertEqual(entidade["texto"], "João")
        self.assertTrue(validar_unidades_contexto(registro)["pronto_para_etapa_09"])
        registro["unidades"][4]["foco"]["anotacoes"]["entidades"][0]["analise_id"] = "outra-analise-mesmo-texto"
        with self.assertRaises(ErroContexto):
            validar_unidades_contexto(registro)

    def test_por_isso_entre_periodos_nao_inventa_alvo_para_necessidade(self):
        caso = CASOS["por_isso"]
        fonte = construir_regras(texto=caso["texto"], linhas=caso["tokens"])
        conector, = [o for o in fonte["ocorrencias"] if o["regra"]["id"] == "conector_por_isso"]
        a = self.executar(fonte=fonte, raio_anterior=0, raio_seguinte=0)
        b = self.executar(fonte=fonte)
        for registro in (a, b):
            pendencias = [p for p in registro["unidades"][1]["pendencias"]
                          if p["origem"]["ocorrencia_id"] == conector["id"]]
            self.assertTrue(pendencias)
            self.assertTrue(all(p["localizacao"] == "localizacao_indeterminada" for p in pendencias))
            self.assertFalse(any(v["periodo_alvo_id"] == registro["unidades"][0]["periodo_foco_id"]
                                 for v in registro["unidades"][1]["vinculos_disponiveis"]
                                 if v["ocorrencia_id"] == conector["id"]))

    def test_vinculo_explicito_de_contrato_localiza_alvo_sem_resolver_mensagem(self):
        """Catálogo atual não propõe antecedentes entre períodos; fonte é manual."""
        caso = CASOS["por_isso"]
        with patch("regras_linguisticas.detectar_regra", side_effect=detector_vinculo_documental):
            fonte = construir_regras(texto=caso["texto"], linhas=caso["tokens"],
                                     regras_habilitadas=["oracao_predicado"])
            estreita = self.executar(fonte=fonte, raio_anterior=0, raio_seguinte=0)
            ampla = self.executar(fonte=fonte)
            for registro, localizacao in ((estreita, "fora_da_janela"), (ampla, "na_janela")):
                unidade = registro["unidades"][0]
                vinculos = unidade["vinculos_disponiveis"]
                self.assertEqual({v["vinculo"]["alvo_tipo"] for v in vinculos}, {"token", "periodo", "ocorrencia"})
                self.assertTrue(all(v["periodo_alvo_id"] == registro["unidades"][1]["periodo_foco_id"]
                                    for v in vinculos))
                self.assertTrue(all(v["localizacao"] == localizacao for v in vinculos))
                self.assertEqual(unidade["pendencias"][0]["localizacao"], "localizacao_indeterminada")
                self.assertTrue(validar_unidades_contexto(registro)["pronto_para_etapa_09"])
            estreita["unidades"][0]["vinculos_disponiveis"][0]["localizacao"] = "na_janela"
            with self.assertRaises(ErroContexto):
                validar_unidades_contexto(estreita)


if __name__ == "__main__":
    unittest.main()
