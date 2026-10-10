"""Regras com gabaritos manuais; integração real verifica somente integridade."""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import UUID

from anotacao import anotar_segmentacao
from preparacao import mapear_intervalo, preparar_texto
from segmentacao import segmentar_preparacao
from sintaxe_entidades import analisar_sintaxe_entidades
from catalogo_regras import CATALOGO
import regras_linguisticas as regras
from regras_linguisticas import (
    ErroConfiguracao, ErroEntrada, ErroRegras,
    aplicar_regras_linguisticas, validar_regras_linguisticas,
)
from fixtures_regras import CASOS, INSTANTE, construir_analise, ids


class BaseRegras:
    def executar(self, nome="negacao_principal", *, habilitadas=None, fonte=None, **opcoes):
        fonte = construir_analise(nome) if fonte is None else fonte
        argumentos = {"execucao_id": "execucao-fixture-07", "registrado_em": INSTANTE}
        if habilitadas is not None:
            argumentos["regras_habilitadas"] = habilitadas
        argumentos.update(opcoes)
        return aplicar_regras_linguisticas(fonte, **argumentos)

    def por_regra(self, registro, identificador):
        return [o for o in registro["ocorrencias"] if o["regra"]["id"] == identificador]

    def conferir_gabarito(self, registro, ocorrencia, gabarito):
        fonte = registro["analise"]
        for esperado, campo in (("marcador", "marcador_token_ids"), ("nucleo", "nucleo_token_ids")):
            if esperado in gabarito:
                self.assertEqual(ocorrencia[campo], ids(fonte, gabarito[esperado]))
        if "categoria" in gabarito:
            self.assertEqual(ocorrencia["categoria"], gabarito["categoria"])
        if "alcance" in gabarito:
            if gabarito["alcance"] is None:
                self.assertIsNone(ocorrencia["alcance"])
            else:
                self.assertEqual(ocorrencia["alcance"]["token_ids"], ids(fonte, gabarito["alcance"]))


class ReferenciasLinguisticasTests(BaseRegras, unittest.TestCase):
    def test_referencias_manualmente_anotadas_validas_sem_modelo(self):
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("modelo proibido")), \
             patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("modelo proibido")):
            for nome in CASOS:
                with self.subTest(nome=nome):
                    self.assertTrue(construir_analise(nome)["validacao"]["pronto_para_etapa_07"])

    def test_oracoes_auxiliar_copula_complemento_e_coordenacao(self):
        for nome in ("auxiliar", "copula", "desejo", "coordenacao"):
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                encontradas = self.por_regra(registro, "oracao_predicado")
                esperadas = CASOS[nome]["oracoes"]
                self.assertEqual(len(encontradas), len(esperadas))
                for encontrada, esperada in zip(encontradas, esperadas):
                    self.conferir_gabarito(registro, encontrada, esperada)
                if nome in {"desejo", "coordenacao"}:
                    self.assertTrue(any(o["vinculos"] for o in encontradas))

    def test_predicado_nominal_sem_copula_nao_inventa_oracao(self):
        registro = self.executar("nominal", habilitadas=["oracao_predicado"])
        self.assertEqual(registro["ocorrencias"], [])
        self.assertTrue(registro["validacao"]["pronto_para_etapa_08"])

    def test_subordinada_condicional_fica_no_periodo_e_vincula_oracoes(self):
        registro = self.executar("condicao")
        oracoes = self.por_regra(registro, "oracao_predicado")
        self.assertEqual(len(oracoes), 2)
        self.assertEqual(len({o["periodo_id"] for o in oracoes}), 1)
        self.assertTrue(any(o["vinculos"] for o in oracoes))
        conector, = self.por_regra(registro, "conector_simples")
        self.conferir_gabarito(registro, conector, CASOS["condicao"]["conector"])

    def test_diferenca_de_alcance_negacao_principal_e_subordinada(self):
        for nome in ("negacao_principal", "negacao_subordinada"):
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                ocorrencia, = self.por_regra(registro, "negacao_nao")
                self.conferir_gabarito(registro, ocorrencia, CASOS[nome]["negacao"])
                self.assertTrue(ocorrencia["vinculos"])

    def test_alcance_descontinuo_nao_inclui_complemento_excluido(self):
        registro = self.executar("negacao_principal")
        negacao, = self.por_regra(registro, "negacao_nao")
        trechos = negacao["alcance"]["trechos"]
        self.assertEqual([t["texto"] for t in trechos], ["Não pensei", "."])
        self.assertNotIn("sair", " ".join(t["texto"] for t in trechos))
        self.assertEqual([i for t in trechos for i in t["token_ids"]], negacao["alcance"]["token_ids"])

    def test_nao_so_mas_tambem_tem_prioridade_sobre_negacao_e_contraste(self):
        registro = self.executar("nao_so")
        correlativa, = self.por_regra(registro, "negacao_correlativa")
        self.conferir_gabarito(registro, correlativa, CASOS["nao_so"]["correlativa"])
        self.assertEqual(correlativa["categoria"], "adicao_correlativa")
        self.assertEqual(self.por_regra(registro, "negacao_nao"), [])
        self.assertFalse(any(o["categoria"] == "contraste" for o in registro["ocorrencias"]))

    def test_correlativa_preserva_segundo_nao_independente(self):
        registro = self.executar("nao_so_outro_nao")
        correlativa, = self.por_regra(registro, "negacao_correlativa")
        simples, = self.por_regra(registro, "negacao_nao")
        self.conferir_gabarito(registro, correlativa, CASOS["nao_so_outro_nao"]["correlativa"])
        self.conferir_gabarito(registro, simples, CASOS["nao_so_outro_nao"]["negacao"])

    def test_correlativa_incompleta_nao_dispara_regra_especial(self):
        registro = self.executar("nao_so_incompleta")
        self.assertEqual(self.por_regra(registro, "negacao_correlativa"), [])
        self.assertEqual(len(self.por_regra(registro, "negacao_nao")), 1)

    def test_nunca_e_nem_possuem_regras_e_marcadores_proprios(self):
        for nome, regra_id in (("nunca", "negacao_nunca"), ("nem", "negacao_nem")):
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                ocorrencia, = self.por_regra(registro, regra_id)
                self.conferir_gabarito(registro, ocorrencia, CASOS[nome]["negacao"])

    def test_multiplos_negativos_preservam_regras_simples_sem_calcular_polaridade(self):
        registro = self.executar("multiplos")
        multiples, = self.por_regra(registro, "negacao_multiplos")
        self.conferir_gabarito(registro, multiples, CASOS["multiplos"]["multiplos"])
        self.assertEqual(len(self.por_regra(registro, "negacao_nao")), 1)
        self.assertEqual(len(self.por_regra(registro, "negacao_nunca")), 1)
        self.assertNotIn("polaridade", multiples)

    def test_palavra_nao_nominal_nao_e_negacao_de_oracao(self):
        registro = self.executar("negacao_metalinguistica")
        self.assertEqual(self.por_regra(registro, "negacao_nao"), [])

    def test_negacao_sem_nucleo_preserva_marcador_e_explica_alcance_ausente(self):
        registro = self.executar("marcador_sem_nucleo")
        ocorrencia, = self.por_regra(registro, "negacao_nao")
        self.conferir_gabarito(registro, ocorrencia, CASOS["marcador_sem_nucleo"]["negacao"])
        self.assertTrue(ocorrencia["necessidade_contexto"] or ocorrencia["ambiguidades"])

    def test_citacao_e_discurso_relatado_nao_ampliam_negacao_ao_narrador(self):
        for nome in ("citacao", "discurso_relatado"):
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                ocorrencia, = self.por_regra(registro, "negacao_nao")
                self.conferir_gabarito(registro, ocorrencia, CASOS[nome]["negacao"])
                nucleo_elocucao = ids(registro["analise"], [1])[0]
                self.assertNotIn(nucleo_elocucao, ocorrencia["alcance"]["token_ids"])
                self.assertTrue(ocorrencia["necessidade_contexto"] or ocorrencia["ambiguidades"])

    def test_pode_preserva_possibilidade_permissao_capacidade_como_alternativas(self):
        registro = self.executar("pode")
        ocorrencia, = self.por_regra(registro, "modalidade_verbal")
        self.conferir_gabarito(registro, ocorrencia, CASOS["pode"]["modal"])
        self.assertEqual(set(ocorrencia["alternativas"]), {"possibilidade", "permissao", "capacidade"})
        self.assertTrue(ocorrencia["ambiguidades"])
        self.assertTrue(registro["validacao"]["pronto_para_etapa_08"])

    def test_modalidade_verbal_necessidade_obrigacao_capacidade_desejo(self):
        for nome in ("necessidade", "obrigacao", "capacidade", "desejo"):
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                ocorrencia, = self.por_regra(registro, "modalidade_verbal")
                self.conferir_gabarito(registro, ocorrencia, CASOS[nome]["modal"])
                self.assertIn(ids(registro["analise"], [2])[0], ocorrencia["alcance"]["token_ids"])
                if nome == "obrigacao":
                    self.assertIn("inferencia_epistemica", ocorrencia["alternativas"])

    def test_pode_nominal_nao_dispara_modalidade_verbal(self):
        registro = self.executar("pode_nominal")
        self.assertEqual(self.por_regra(registro, "modalidade_verbal"), [])

    def test_saber_com_infinitivo_indica_capacidade_mas_saber_proposicao_nao(self):
        registro = self.executar("saber_capacidade")
        ocorrencia, = self.por_regra(registro, "modalidade_verbal")
        self.conferir_gabarito(registro, ocorrencia, CASOS["saber_capacidade"]["modal"])
        conhecimento = self.executar("saber_proposicao")
        self.assertFalse(any(o["categoria"] == "capacidade" for o in conhecimento["ocorrencias"]))

    def test_talvez_marca_duvida_lexical(self):
        registro = self.executar("duvida")
        ocorrencia, = self.por_regra(registro, "modalidade_lexical")
        self.conferir_gabarito(registro, ocorrencia, CASOS["duvida"]["modal"])

    def test_adjetivo_predicativo_flexionado_usa_lema_para_modalidade(self):
        registro = self.executar("necessidade_predicativa")
        ocorrencia, = self.por_regra(registro, "modalidade_lexical")
        self.conferir_gabarito(registro, ocorrencia, CASOS["necessidade_predicativa"]["modal"])

    def test_desejo_negado_vincula_modalidade_e_negacao(self):
        registro = self.executar("desejo_negado")
        modal, = self.por_regra(registro, "modalidade_verbal")
        negativa, = self.por_regra(registro, "negacao_nao")
        self.conferir_gabarito(registro, modal, CASOS["desejo_negado"]["modal"])
        self.assertTrue(any(v["alvo_id"] == negativa["id"] for v in modal["vinculos"]))
        self.assertTrue(modal["necessidade_contexto"] or modal["ambiguidades"])

    def test_tempo_relativo_e_flexao_permanecem_evidencias_distintas_sem_data(self):
        registro = self.executar("tempo_relativo")
        relativos = self.por_regra(registro, "tempo_relativo")
        flexoes = self.por_regra(registro, "tempo_flexao")
        self.assertEqual(len(relativos), 3)
        self.assertEqual(len(flexoes), 3)
        for ocorrencia, gabarito in zip(relativos, CASOS["tempo_relativo"]["tempos"]):
            self.conferir_gabarito(registro, ocorrencia, gabarito)
            self.assertEqual(ocorrencia["categoria"], "referencia_relativa")
            self.assertTrue(ocorrencia["necessidade_contexto"])
            self.assertFalse({"data", "data_absoluta", "data_calendario"} & set(ocorrencia))
        self.assertEqual([ids(registro["analise"], [i]) for i in (1, 4, 7)],
                         [o["marcador_token_ids"] for o in flexoes])

    def test_amanha_nominal_nao_dispara_referencia_temporal(self):
        registro = self.executar("tempo_nominal")
        self.assertEqual(self.por_regra(registro, "tempo_relativo"), [])

    def test_quando_sustenta_conector_e_tempo_sem_eliminar_sobreposicao(self):
        registro = self.executar("quando")
        conector, = self.por_regra(registro, "conector_simples")
        self.conferir_gabarito(registro, conector, CASOS["quando"]["conector"])
        temporais = [o for o in self.por_regra(registro, "tempo_relativo")
                     if o["categoria"] == "construcao_temporal"]
        self.assertEqual(len(temporais), 1)
        self.assertEqual(conector["marcador_token_ids"], temporais[0]["marcador_token_ids"])
        self.assertNotEqual(conector["id"], temporais[0]["id"])

    def test_se_reflexivo_e_interrogativo_nao_disparam_condicao(self):
        for nome in ("se_reflexivo", "se_interrogativo"):
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                self.assertFalse(any(o["categoria"] == "condicao" for o in registro["ocorrencias"]))

    def test_contraste_causa_consequencia_possuem_condicoes_sintaticas(self):
        for nome, regra_id in (("contraste", "conector_simples"), ("causa", "conector_simples"),
                               ("por_isso", "conector_por_isso")):
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                ocorrencia, = self.por_regra(registro, regra_id)
                self.conferir_gabarito(registro, ocorrencia, CASOS[nome]["conector"])
                self.assertTrue(ocorrencia["vinculos"])
                if nome == "por_isso":
                    self.assertTrue(ocorrencia["necessidade_contexto"])

    def test_mas_nominal_nao_dispara_contraste(self):
        registro = self.executar("conector_nominal")
        self.assertEqual(self.por_regra(registro, "conector_simples"), [])

    def test_coordenacao_nominal_com_mas_nao_inventa_duas_oracoes(self):
        registro = self.executar("contraste_nominal")
        self.assertEqual(self.por_regra(registro, "conector_simples"), [])
        self.assertEqual(self.por_regra(registro, "oracao_predicado"), [])

    def test_por_isso_complemento_posposto_nao_asserta_relacao_de_consequencia(self):
        registro = self.executar("por_isso_argumento")
        for ocorrencia in self.por_regra(registro, "conector_por_isso"):
            self.assertTrue(ocorrencia["ambiguidades"])
            self.assertTrue(ocorrencia["alternativas"])
            self.assertTrue(ocorrencia["necessidade_contexto"])

    def test_ocorrencias_repetidas_preservam_tokens_e_identidades_distintas(self):
        registro = self.executar("repeticao")
        ocorrencias = self.por_regra(registro, "negacao_nao")
        self.assertEqual(len(ocorrencias), 2)
        for ocorrencia, esperado in zip(ocorrencias, CASOS["repeticao"]["negacoes"]):
            self.conferir_gabarito(registro, ocorrencia, esperado)
        self.assertNotEqual(ocorrencias[0]["id"], ocorrencias[1]["id"])
        self.assertNotEqual(ocorrencias[0]["periodo_id"], ocorrencias[1]["periodo_id"])

    def test_limites_e_textos_de_evidencia_foram_anotados_previamente(self):
        casos = (("auxiliar", "oracao_predicado"), ("negacao_principal", "negacao_nao"),
                 ("pode", "modalidade_verbal"), ("quando", "conector_simples"),
                 ("contraste", "conector_simples"))
        for nome, regra_id in casos:
            with self.subTest(nome=nome):
                registro = self.executar(nome)
                ocorrencia, = self.por_regra(registro, regra_id)
                esperados = CASOS[nome]["evidencia_literal"]
                self.assertEqual([(t["texto"], t["trabalho"]["inicio"], t["trabalho"]["fim"])
                                  for t in ocorrencia["evidencias"]], esperados)
                self.assertEqual([(t["texto"], t["original"]["inicio"], t["original"]["fim"])
                                  for t in ocorrencia["evidencias"]], esperados)


class ContratoRegrasTests(BaseRegras, unittest.TestCase):
    def test_schema_catalogo_configuracao_cadeia_e_relatorio(self):
        fonte = construir_analise()
        registro = self.executar(fonte=fonte)
        self.assertEqual(registro["schema_version"], "1.0.0")
        self.assertEqual(registro["etapa"], "07_regras_linguisticas")
        self.assertEqual(registro["execucao_id"], "execucao-fixture-07")
        self.assertEqual(registro["registrado_em"], INSTANTE)
        self.assertEqual(registro["analise"], fonte)
        self.assertEqual(registro["catalogo"], CATALOGO)
        for campo in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "analise_id"):
            self.assertEqual(registro[campo], fonte[campo])
        self.assertEqual([e["regra_id"] for e in registro["execucoes_regras"]],
                         [r["id"] for r in sorted(CATALOGO["regras"],
                                                  key=lambda regra: (regra["prioridade"], regra["id"]))])
        self.assertTrue(all(e["estado"] == "executada" for e in registro["execucoes_regras"]))
        self.assertEqual(validar_regras_linguisticas(registro), registro["validacao"])
        self.assertTrue(registro["validacao"]["pronto_para_etapa_08"])

    def test_familias_sem_achados_sao_executadas_e_nao_desabilitadas(self):
        registro = self.executar("nominal")
        self.assertEqual(registro["ocorrencias"], [])
        self.assertEqual({r["familia"] for r in registro["catalogo"]["regras"]},
                         {"oracoes", "negacao", "tempo", "modalidade", "conectores"})
        self.assertTrue(all(e["estado"] == "executada" and e["ocorrencias_total"] == 0
                            for e in registro["execucoes_regras"]))
        self.assertTrue(registro["validacao"]["pronto_para_etapa_08"])

    def test_cada_regra_tem_negativo_independente_sem_falso_positivo(self):
        for regra_id in (r["id"] for r in CATALOGO["regras"]):
            with self.subTest(regra_id=regra_id):
                registro = self.executar("nominal", habilitadas=[regra_id])
                self.assertEqual(registro["ocorrencias"], [])
                execucao, = [e for e in registro["execucoes_regras"] if e["regra_id"] == regra_id]
                self.assertEqual(execucao["estado"], "executada")
                self.assertEqual(execucao["ocorrencias_total"], 0)

    def test_desabilitar_regra_especial_nao_suprime_marcadores_simples(self):
        registro = self.executar("nao_so", habilitadas=["negacao_nao", "conector_simples"])
        self.assertEqual(self.por_regra(registro, "negacao_correlativa"), [])
        self.assertEqual(len(self.por_regra(registro, "negacao_nao")), 1)
        self.assertEqual([o["categoria"] for o in self.por_regra(registro, "conector_simples")], ["contraste"])

    def test_duplicata_exata_de_proposta_e_eliminada_sem_apagar_ocorrencia_distinta(self):
        original = regras.detectar_regra

        def com_duplicatas(*argumentos, **opcoes):
            retorno = original(*argumentos, **opcoes)
            return {"ocorrencias": retorno["ocorrencias"] + copy.deepcopy(retorno["ocorrencias"]),
                    "pendencias": retorno["pendencias"]}

        with patch("regras_linguisticas.detectar_regra", side_effect=com_duplicatas):
            registro = self.executar("repeticao", habilitadas=["negacao_nao"])
        self.assertEqual(len(registro["ocorrencias"]), 2)
        self.assertEqual([o["marcador_token_ids"] for o in registro["ocorrencias"]],
                         [ids(registro["analise"], [1]), ids(registro["analise"], [5])])
        validar_regras_linguisticas(registro)

    def test_ordem_de_configuracao_do_chamador_e_canonicalizada_pela_prioridade(self):
        selecionadas = ["negacao_nao", "negacao_correlativa", "oracao_predicado", "conector_simples"]
        primeiro = self.executar("nao_so", habilitadas=selecionadas)
        segundo = self.executar("nao_so", habilitadas=list(reversed(selecionadas)))
        self.assertEqual(primeiro, segundo)
        self.assertEqual(primeiro["configuracao"]["ordem_execucao"][0], "negacao_correlativa")

    def test_vazio_e_brancos_validos_sem_tokens_ou_ocorrencias(self):
        for texto in ("", "  \t\r\n\r\n", "\u00a0\u2028"):
            with self.subTest(texto=repr(texto)):
                fonte = construir_analise(texto=texto, linhas=[])
                registro = self.executar(fonte=fonte)
                self.assertEqual(registro["ocorrencias"], [])
                self.assertTrue(registro["validacao"]["pronto_para_etapa_08"])

    def test_desabilitar_todas_nao_declarara_prontidao(self):
        registro = self.executar(habilitadas=[])
        self.assertEqual(registro["configuracao"]["regras_habilitadas"], [])
        self.assertEqual(registro["ocorrencias"], [])
        self.assertTrue(all(e["estado"] == "desabilitada" for e in registro["execucoes_regras"]))
        self.assertFalse(registro["validacao"]["pronto_para_etapa_08"])
        validar_regras_linguisticas(registro)

    def test_configuracao_limitada_distingue_regra_sem_achado_e_desabilitada(self):
        registro = self.executar("nominal", habilitadas=["negacao_nao"])
        estados = {e["regra_id"]: e["estado"] for e in registro["execucoes_regras"]}
        self.assertEqual(estados["negacao_nao"], "executada")
        self.assertTrue(all(estado == "desabilitada" for regra_id, estado in estados.items()
                            if regra_id != "negacao_nao"))
        self.assertEqual(registro["ocorrencias"], [])
        self.assertTrue(registro["validacao"]["pronto_para_etapa_08"])

    def test_falha_operacional_registrada_nao_se_confunde_com_zero_achados(self):
        with patch("regras_linguisticas.detectar_regra", side_effect=RuntimeError("falha controlada")):
            registro = self.executar(habilitadas=["negacao_nao"])
        execucao, = [e for e in registro["execucoes_regras"] if e["regra_id"] == "negacao_nao"]
        self.assertEqual(execucao["estado"], "falhou")
        self.assertIsInstance(execucao["erro"], dict)
        self.assertFalse(registro["validacao"]["pronto_para_etapa_08"])
        validar_regras_linguisticas(registro)

    def test_falha_no_indice_compartilhado_preserva_diagnosticos_das_regras_ativas(self):
        with patch("regras_linguisticas.construir_contexto", side_effect=RuntimeError("índice indisponível")):
            registro = self.executar(habilitadas=["negacao_nao", "oracao_predicado"])
        self.assertEqual(registro["ocorrencias"], [])
        self.assertFalse(registro["validacao"]["pronto_para_etapa_08"])
        for execucao in registro["execucoes_regras"]:
            if execucao["regra_id"] in {"negacao_nao", "oracao_predicado"}:
                self.assertEqual(execucao["estado"], "falhou")
                self.assertIsInstance(execucao["erro"], dict)
                self.assertTrue(execucao["erro"]["mensagem"])
            else:
                self.assertEqual(execucao["estado"], "desabilitada")
                self.assertIsNone(execucao["erro"])
        validar_regras_linguisticas(registro)

    def test_anotacao_pos_ausente_distingue_impedimento_de_ausencia_de_achado(self):
        linhas = copy.deepcopy(CASOS["pode"]["tokens"])
        forma, lema, pos, cabeca, dep, morfo = linhas[1]
        linhas[1] = (forma, lema, None, cabeca, dep, morfo)
        fonte = construir_analise("pode", linhas=linhas)
        registro = self.executar(fonte=fonte, habilitadas=["modalidade_verbal"])
        execucao, = [e for e in registro["execucoes_regras"] if e["regra_id"] == "modalidade_verbal"]
        self.assertIn(execucao["estado"], {"impedida", "executada_com_pendencias"})
        self.assertTrue(execucao["pendencias"])
        self.assertIsNone(execucao["erro"])
        self.assertFalse(registro["validacao"]["pronto_para_etapa_08"])
        validar_regras_linguisticas(registro)

    def test_morfologia_ausente_impede_regra_temporal_sem_apagar_anotacao(self):
        linhas = copy.deepcopy(CASOS["pode"]["tokens"])
        forma, lema, pos, cabeca, dep, morfo = linhas[1]
        linhas[1] = (forma, lema, pos, cabeca, dep, None)
        fonte = construir_analise("pode", linhas=linhas)
        registro = self.executar(fonte=fonte, habilitadas=["tempo_flexao"])
        self.assertIsNone(registro["analise"]["anotacao"]["anotacoes_tokens"][1]["morfologia"])
        execucao, = [e for e in registro["execucoes_regras"] if e["regra_id"] == "tempo_flexao"]
        self.assertTrue(execucao["pendencias"])
        self.assertFalse(registro["validacao"]["pronto_para_etapa_08"])

    def test_lema_ausente_em_adjetivo_predicativo_impede_negativo_silencioso(self):
        linhas = copy.deepcopy(CASOS["necessidade_predicativa"]["tokens"])
        forma, lema, pos, cabeca, dep, morfo = linhas[1]
        linhas[1] = (forma, None, pos, cabeca, dep, morfo)
        fonte = construir_analise("necessidade_predicativa", linhas=linhas)
        registro = self.executar(fonte=fonte, habilitadas=["modalidade_lexical"])
        execucao, = [e for e in registro["execucoes_regras"] if e["regra_id"] == "modalidade_lexical"]
        self.assertTrue(execucao["pendencias"])
        self.assertFalse(registro["validacao"]["pronto_para_etapa_08"])
        self.assertIsNone(execucao["erro"])

    def test_pos_ausente_no_nucleo_ou_fronteira_nao_produz_alcance_inventado(self):
        casos = (("negacao_principal", 1, "negacao_nao"),
                 ("negacao_principal", 3, "negacao_nao"),
                 ("nao_so", 7, "negacao_correlativa"),
                 ("tempo_relativo", 1, "tempo_relativo"))
        for nome, indice, regra_id in casos:
            with self.subTest(nome=nome, indice=indice, regra_id=regra_id):
                linhas = copy.deepcopy(CASOS[nome]["tokens"])
                forma, lema, pos, cabeca, dep, morfo = linhas[indice]
                linhas[indice] = (forma, lema, None, cabeca, dep, morfo)
                fonte = construir_analise(nome, linhas=linhas)
                registro = self.executar(fonte=fonte, habilitadas=[regra_id])
                execucao, = [e for e in registro["execucoes_regras"] if e["regra_id"] == regra_id]
                self.assertTrue(execucao["pendencias"])
                self.assertIn(execucao["estado"], {"impedida", "executada_com_pendencias"})
                self.assertFalse(registro["validacao"]["pronto_para_etapa_08"])
                self.assertIsNone(execucao["erro"])
                validar_regras_linguisticas(registro)

    def test_id_e_data_fornecidos_reproduzem_registro_integral(self):
        fonte = construir_analise()
        primeiro = self.executar(fonte=fonte)
        self.assertEqual(primeiro, self.executar(fonte=fonte))
        automatico = aplicar_regras_linguisticas(fonte)
        self.assertEqual(UUID(automatico["execucao_id"]).version, 4)
        self.assertIsNotNone(datetime.fromisoformat(automatico["registrado_em"]).utcoffset())
        data = datetime(2026, 10, 7, tzinfo=timezone.utc)
        self.assertEqual(self.executar(fonte=fonte, registrado_em=data)["registrado_em"], data.isoformat())
        for opcoes in ({"execucao_id": ""}, {"execucao_id": True}, {"registrado_em": "2026-10-07"},
                        {"registrado_em": datetime(2026, 10, 7)}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroRegras):
                self.executar(fonte=fonte, **opcoes)

    def test_configuracoes_invalidas_produzem_erros_explicitamente(self):
        for habilitadas in ("negacao_nao", ["nao_existe"], ["negacao_nao", "negacao_nao"],
                            [True], {"negacao_nao"}, {"negacao_nao": True}):
            with self.subTest(habilitadas=habilitadas), self.assertRaises(ErroConfiguracao):
                self.executar(habilitadas=habilitadas)

    def test_registros_ausentes_incompletos_ou_com_tipos_nao_json_sao_recusados(self):
        for valor in (None, [], {}, "texto", True):
            with self.subTest(valor=valor), self.assertRaises(ErroRegras):
                validar_regras_linguisticas(valor)
        registro = self.executar()
        for campo in registro:
            alterado = copy.deepcopy(registro)
            del alterado[campo]
            with self.subTest(campo=campo), self.assertRaises(ErroRegras):
                validar_regras_linguisticas(alterado)
        for valor in (float("nan"), float("inf"), (1, 2), {1, 2}, datetime(2026, 10, 7)):
            alterado = copy.deepcopy(registro)
            alterado["campo_adicional"] = valor
            with self.subTest(tipo=type(valor).__name__), self.assertRaises(ErroRegras):
                validar_regras_linguisticas(alterado)

    def test_preservacao_unicode_separadores_metadados_e_copia_independente(self):
        fonte = construir_analise("unicode_crlf")
        snapshot = copy.deepcopy(fonte)
        registro = self.executar(fonte=fonte)
        self.assertEqual(fonte, snapshot)
        self.assertEqual(registro["analise"], snapshot)
        preparar = registro["analise"]["anotacao"]["segmentacao"]["preparacao"]
        self.assertEqual(preparar["original"]["texto"], CASOS["unicode_crlf"]["texto"])
        registro["analise"]["anotacao"]["segmentacao"]["tokens"][0]["texto"] = "alterado"
        registro["catalogo"]["regras"][0]["descricao"] = "alterado"
        self.assertEqual(fonte, snapshot)
        self.assertNotEqual(registro["catalogo"], CATALOGO)

    def test_crlf_literal_normalizado_tem_mesmos_intervalos_no_original(self):
        literal = self.executar(fonte=construir_analise("unicode_crlf", normalizar=False))
        normalizado = self.executar(fonte=construir_analise("unicode_crlf", normalizar=True))
        self.assertEqual(len(literal["ocorrencias"]), len(normalizado["ocorrencias"]))
        for esquerda, direita in zip(literal["ocorrencias"], normalizado["ocorrencias"]):
            self.assertEqual(esquerda["regra"], direita["regra"])
            self.assertEqual(esquerda["marcador_token_ids"], direita["marcador_token_ids"])
            for campo in ("evidencias",):
                self.assertEqual([t["original"] for t in esquerda[campo]], [t["original"] for t in direita[campo]])
            if esquerda["alcance"] is not None:
                self.assertEqual([t["original"] for t in esquerda["alcance"]["trechos"]],
                                 [t["original"] for t in direita["alcance"]["trechos"]])
        for registro in (literal, normalizado):
            preparacao = registro["analise"]["anotacao"]["segmentacao"]["preparacao"]
            for ocorrencia in registro["ocorrencias"]:
                trechos = ocorrencia["evidencias"] + ([] if ocorrencia["alcance"] is None
                                                   else ocorrencia["alcance"]["trechos"])
                for trecho in trechos:
                    self.assertEqual(trecho["original"], mapear_intervalo(preparacao,
                                     trecho["trabalho"]["inicio"], trecho["trabalho"]["fim"]))
                    self.assertEqual(trecho["texto"], preparacao["trabalho"]["texto"][
                                     trecho["trabalho"]["inicio"]:trecho["trabalho"]["fim"]])

    def test_intervalos_unicode_crlf_conferidos_contra_limites_manuais(self):
        registro = self.executar(fonte=construir_analise("unicode_crlf", normalizar=True))
        relativa, = [o for o in self.por_regra(registro, "tempo_relativo")
                     if o["categoria"] == "referencia_relativa"]
        self.assertEqual([(t["texto"], t["trabalho"], t["original"]) for t in relativa["evidencias"]],
                         [("Amanhã", {"inicio": 18, "fim": 24}, {"inicio": 20, "fim": 26}),
                          ("ficará", {"inicio": 36, "fim": 42}, {"inicio": 38, "fim": 44})])

    def test_json_estrito_validacao_nao_muta_e_leitura_sem_spacy(self):
        registro = self.executar("unicode_crlf")
        recuperado = json.loads(json.dumps(registro, ensure_ascii=False, allow_nan=False))
        snapshot = copy.deepcopy(recuperado)
        validar_regras_linguisticas(recuperado)
        self.assertEqual(recuperado, snapshot)
        codigo = '''import builtins, json, sys
original = builtins.__import__
def protegido(name, *args, **kwargs):
    if name == "spacy" or name.startswith("spacy.") or name == "pt_core_news_sm":
        raise ImportError("Modelo indisponível por escolha do teste")
    return original(name, *args, **kwargs)
builtins.__import__ = protegido
from regras_linguisticas import aplicar_regras_linguisticas, validar_regras_linguisticas
registro = json.load(sys.stdin)
assert validar_regras_linguisticas(registro)["pronto_para_etapa_08"] is True
reproduzido = aplicar_regras_linguisticas(registro["analise"], execucao_id=registro["execucao_id"],
    registrado_em=registro["registrado_em"], regras_habilitadas=registro["configuracao"]["regras_habilitadas"])
assert reproduzido == registro
'''
        resultado = subprocess.run([sys.executable, "-c", codigo], input=json.dumps(registro), text=True,
                                  cwd=Path(__file__).resolve().parents[1], capture_output=True)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_entrada_malformada_ou_cadeia_adulterada_rejeitada_antes_de_detectar(self):
        for valor in (None, [], {}, "texto", True):
            with self.subTest(valor=valor), self.assertRaises(ErroEntrada):
                aplicar_regras_linguisticas(valor)
        alteracoes = [
            lambda r: r["anotacao"]["segmentacao"]["preparacao"]["original"].__setitem__("sha256", "0" * 64),
            lambda r: r["anotacao"]["segmentacao"]["tokens"][0].__setitem__("texto", "adulterado"),
            lambda r: r["anotacao"]["anotacoes_tokens"][0].__setitem__("token_id", "ausente"),
            lambda r: r["sintaxe"][0].__setitem__("cabeca_token_id", "ausente"),
        ]
        for numero, alterar in enumerate(alteracoes):
            with self.subTest(numero=numero):
                fonte = construir_analise()
                alterar(fonte)
                with patch("regras_linguisticas.detectar_regra") as detectar, self.assertRaises(ErroEntrada):
                    aplicar_regras_linguisticas(fonte)
                detectar.assert_not_called()

    def test_rejeita_corrupcoes_de_catalogo_id_ordem_tokens_texto_mapa_e_vinculos(self):
        def negativa(registro):
            return self.por_regra(registro, "negacao_nao")[0]

        alteracoes = [
            lambda r: r.__setitem__("analise_id", "outra-analise"),
            lambda r: r.__setitem__("schema_version", "99.0.0"),
            lambda r: r["catalogo"].__setitem__("versao", "99.0.0"),
            lambda r: negativa(r)["regra"].__setitem__("id", "nao_existe"),
            lambda r: negativa(r)["regra"].__setitem__("versao", "99.0.0"),
            lambda r: negativa(r).__setitem__("ordem", 999),
            lambda r: negativa(r).__setitem__("id", "id-incoerente"),
            lambda r: negativa(r).__setitem__("marcador_token_ids", ["token-ausente"]),
            lambda r: negativa(r)["evidencias"][0].__setitem__("texto", "adulterado"),
            lambda r: negativa(r)["evidencias"][0]["trabalho"].__setitem__("inicio", True),
            lambda r: negativa(r)["evidencias"][0]["original"].__setitem__("fim", 999),
            lambda r: negativa(r).__setitem__("periodo_id", "periodo-ausente"),
            lambda r: negativa(r).__setitem__("paragrafo_id", "paragrafo-ausente"),
            lambda r: negativa(r)["vinculos"].append({"tipo": "oracao", "alvo_tipo": "ocorrencia", "alvo_id": "ausente"}),
            lambda r: r["ocorrencias"].append(copy.deepcopy(r["ocorrencias"][0])),
            lambda r: r["validacao"].__setitem__("pronto_para_etapa_08", False),
            lambda r: r["execucoes_regras"][0].__setitem__("ocorrencias_total", 999),
        ]
        for numero, alterar in enumerate(alteracoes):
            with self.subTest(numero=numero):
                registro = self.executar()
                alterar(registro)
                with self.assertRaises(ErroRegras):
                    validar_regras_linguisticas(registro)

    def test_rejeita_predicados_semanticos_incoerentes_mesmo_com_tokens_existentes(self):
        alteracoes = [
            lambda o, fonte: o.__setitem__("categoria", "condicao"),
            lambda o, fonte: o.__setitem__("marcador_token_ids", ids(fonte, [2])),
            lambda o, fonte: o.__setitem__("nucleo_token_ids", ids(fonte, [3])),
            lambda o, fonte: o.__setitem__("explicacao", "Não deriva da condição executada."),
            lambda o, fonte: o["alcance"]["token_ids"].append(ids(fonte, [3])[0]),
        ]
        for numero, alterar in enumerate(alteracoes):
            with self.subTest(numero=numero):
                registro = self.executar()
                ocorrencia, = self.por_regra(registro, "negacao_nao")
                alterar(ocorrencia, registro["analise"])
                with self.assertRaises(ErroRegras):
                    validar_regras_linguisticas(registro)

    def test_trecho_descontinuo_nao_pode_ser_fundido_atravessando_tokens_excluidos(self):
        registro = self.executar()
        ocorrencia, = self.por_regra(registro, "negacao_nao")
        trechos = ocorrencia["alcance"]["trechos"]
        inicio, fim = trechos[0]["trabalho"]["inicio"], trechos[-1]["trabalho"]["fim"]
        preparacao = registro["analise"]["anotacao"]["segmentacao"]["preparacao"]
        falso = copy.deepcopy(trechos[0])
        falso.update(token_ids=ocorrencia["alcance"]["token_ids"],
                     texto=preparacao["trabalho"]["texto"][inicio:fim], trabalho={"inicio": inicio, "fim": fim},
                     original=mapear_intervalo(preparacao, inicio, fim))
        ocorrencia["alcance"]["trechos"] = [falso]
        with self.assertRaises(ErroRegras):
            validar_regras_linguisticas(registro)


class IntegracaoModeloRealRegrasTests(BaseRegras, unittest.TestCase):
    def test_modelo_real_alinhamento_preservacao_sem_gabarito_linguistico(self):
        """Previsões reais são entradas observadas, nunca referências das regras."""
        preparacao = preparar_texto("João não queria sair.\r\nMas amanhã poderá voltar.",
                                   "documento-integracao-07", registrado_em=INSTANTE,
                                   normalizar_crlf=True)
        segmentacao = segmentar_preparacao(preparacao, registrado_em=INSTANTE)
        anotacao = anotar_segmentacao(segmentacao, registrado_em=INSTANTE)
        fonte = analisar_sintaxe_entidades(anotacao, registrado_em=INSTANTE)
        snapshot = copy.deepcopy(fonte)
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("reanotação proibida")), \
             patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("reanálise proibida")):
            registro = self.executar(fonte=fonte)
            validar_regras_linguisticas(registro)
        self.assertEqual(registro["analise"], snapshot)
        self.assertEqual(fonte, snapshot)
        self.assertEqual(json.loads(json.dumps(registro, ensure_ascii=False, allow_nan=False)), registro)


if __name__ == "__main__":
    unittest.main()
