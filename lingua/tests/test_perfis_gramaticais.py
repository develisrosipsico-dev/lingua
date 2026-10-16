"""Compatibilidade histórica e escolha explícita do perfil gramatical.

Os testes de metadados sintéticos validam apenas o contrato. A integração
com o modelo medium é separada e só roda quando o pacote opcional existe.
"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import anotacao
from perfis_gramaticais import normalizar_modelo, perfil_gramatical
from preparacao import preparar_texto
from segmentacao import segmentar_preparacao
import sintaxe_entidades as sintaxe


class PerfisGramaticaisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.segmentacao = segmentar_preparacao(preparar_texto(
            "Silenciosamente, ela formulou uma premissa. Embora discordasse, continuou escrevendo.",
            "perfis-gramaticais",
        ))
        cls.anotacao_sm = anotacao.anotar_segmentacao(cls.segmentacao)
        cls.sintaxe_sm = sintaxe.analisar_sintaxe_entidades(cls.anotacao_sm)

    def como_md(self, fonte=None):
        """Converta apenas proveniência para exercitar validação sem inferência."""
        registro = deepcopy(self.anotacao_sm if fonte is None else fonte)
        registro["processamento"]["modelo"]["nome"] = "pt_core_news_md"
        registro["processamento"]["perfil_gramatical"] = perfil_gramatical("pt_core_news_md")
        for token in registro["anotacoes_tokens"]:
            token["origem"]["modelo"]["nome"] = "pt_core_news_md"
        return registro

    def test_default_historico_nao_recebe_campos_novos(self):
        self.assertEqual(normalizar_modelo(), "pt_core_news_sm")
        self.assertNotIn("perfil_gramatical", self.anotacao_sm["processamento"])
        self.assertNotIn("perfil_gramatical", self.sintaxe_sm["processamento"])
        self.assertEqual(self.anotacao_sm["schema_version"], "1.0.0")
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("Não carregar")), \
                patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("Não carregar")):
            anotacao.validar_anotacao(self.anotacao_sm)
            sintaxe.validar_analise_sintaxe_entidades(self.sintaxe_sm)

    def test_modelo_arbitrario_ou_versao_nao_suportada_rejeitados_antes_da_inferencia(self):
        for nome in ("", "pt_core_news_lg", "pt_core_news_md-3.7.0", "../modelo", True, [], {}):
            with self.subTest(nome=nome), patch("anotacao._carregar_modelo") as carregar:
                with self.assertRaises(anotacao.ErroModelo):
                    anotacao.anotar_segmentacao(self.segmentacao, modelo=nome)
                carregar.assert_not_called()
        for mudar in (
            lambda p: p.pop("perfil_gramatical"),
            lambda p: p["perfil_gramatical"].__setitem__("versao", "2.0.0"),
            lambda p: p["modelo"].__setitem__("versao", "3.7.0"),
            lambda p: p["modelo"].__setitem__("compatibilidade_spacy", ">=3.7.0"),
        ):
            registro = self.como_md()
            mudar(registro["processamento"])
            with self.subTest(processamento=registro["processamento"]), self.assertRaises(anotacao.ErroAnotacao):
                anotacao.validar_anotacao(registro)

    def test_md_validado_sem_importar_spacy_ou_ter_modelo_instalado(self):
        registro = self.como_md()
        codigo = '''import builtins, json, sys
original = builtins.__import__
def protegido(nome, *args, **kwargs):
    if nome == "spacy" or nome.startswith("spacy.") or nome.startswith("pt_core_news_"):
        raise ImportError("Modelo propositalmente indisponível")
    return original(nome, *args, **kwargs)
builtins.__import__ = protegido
from anotacao import validar_anotacao
assert validar_anotacao(json.load(sys.stdin))["pronto_para_etapa_06"]
'''
        processo = subprocess.run(
            [sys.executable, "-c", codigo], input=json.dumps(registro), text=True,
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
        )
        self.assertEqual(processo.returncode, 0, processo.stderr)

    def test_modelo_md_ausente_nao_cai_silenciosamente_para_sm(self):
        with patch("spacy.load", side_effect=OSError("não instalado")) as carregar:
            with self.assertRaisesRegex(anotacao.ErroModelo, "requirements-gramatica.txt"):
                anotacao._carregar_modelo.__wrapped__("pt_core_news_md")
            self.assertEqual(carregar.call_args.args[0], "pt_core_news_md")
        with patch("spacy.load", side_effect=OSError("não instalado")) as carregar:
            with self.assertRaisesRegex(sintaxe.ErroModelo, "requirements-gramatica.txt"):
                sintaxe._carregar_modelo.__wrapped__("pt_core_news_md")
            self.assertEqual(carregar.call_args.args[0], "pt_core_news_md")

    def test_sintaxe_rejeita_mistura_de_modelos_e_perfis(self):
        registro = deepcopy(self.sintaxe_sm)
        registro["anotacao"] = self.como_md()
        with self.assertRaises(sintaxe.ErroAnalise):
            sintaxe.validar_analise_sintaxe_entidades(registro)

    @unittest.skipUnless(importlib.util.find_spec("pt_core_news_md"), "Modelo medium opcional não instalado")
    def test_modelo_md_real_preserva_tokens_morfologia_e_fonte_na_sintaxe(self):
        fonte = deepcopy(self.segmentacao)
        morfologia = anotacao.anotar_segmentacao(fonte, modelo="pt_core_news_md")
        self.assertEqual(fonte, self.segmentacao)
        self.assertEqual(morfologia["processamento"]["modelo"]["nome"], "pt_core_news_md")
        self.assertEqual(morfologia["processamento"]["perfil_gramatical"], perfil_gramatical("pt_core_news_md"))
        carregar_original = sintaxe._carregar_modelo
        with patch("sintaxe_entidades._carregar_modelo", wraps=carregar_original) as carregar:
            analise = sintaxe.analisar_sintaxe_entidades(morfologia)
        carregar.assert_called_once_with("pt_core_news_md")
        self.assertEqual(analise["processamento"]["modelo"], morfologia["processamento"]["modelo"])
        self.assertEqual(analise["processamento"]["perfil_gramatical"], morfologia["processamento"]["perfil_gramatical"])
        self.assertEqual(analise["anotacao"], morfologia)
        self.assertEqual([t["texto"] for t in analise["sintaxe"]], [t["texto"] for t in fonte["tokens"]])
        self.assertTrue(sintaxe.validar_analise_sintaxe_entidades(analise)["pronto_para_etapa_07"])
        # A validação do registro não depende da permanência do pacote opcional.
        with patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("Não carregar")):
            sintaxe.validar_analise_sintaxe_entidades(analise)


if __name__ == "__main__":
    unittest.main()
