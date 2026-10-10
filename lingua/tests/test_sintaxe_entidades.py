"""Etapa 06: invariantes determinísticas e integração separada com o modelo.

As árvores e entidades do processador controlado são dados sintéticos apenas
para testar o contrato. Não são referências linguísticas nem exemplos de
previsões produzidas pelo spaCy. Os testes reais conferem integridade, sem
transformar as previsões obtidas em seu próprio gabarito linguístico.
"""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from anotacao import anotar_segmentacao, validar_anotacao
from preparacao import ler_texto_original, mapear_intervalo, preparar_texto
from segmentacao import segmentar_preparacao
import sintaxe_entidades as sintaxe
from sintaxe_entidades import (
    ErroAlinhamento, ErroAnalise, ErroEntrada, ErroLimite, ErroModelo,
    ErroPrevisao, analisar_sintaxe_entidades, validar_analise_sintaxe_entidades,
)


class FonteAnalise:
    INSTANTE = "2026-10-07T18:20:00-03:00"

    @classmethod
    def setUpClass(cls):
        registro_real = analisar_sintaxe_entidades(
            cls.fonte("Eu guardei o dinheiro."), analise_id="modelo-contrato",
            registrado_em=cls.INSTANTE,
        )
        cls.processamento_real = registro_real["processamento"]
        cls.origem_real = copy.deepcopy(registro_real["sintaxe"][0]["origem"])

    @classmethod
    def fonte(cls, texto="Eu guardei o dinheiro.", *, normalizar=False):
        preparacao = preparar_texto(
            texto, "documento-teste-06", normalizar_crlf=normalizar,
            preparacao_id="preparacao-teste-06", registrado_em=cls.INSTANTE,
            metadados={"fonte": "teste", "autoria": {"etiquetas": ["literal"]}},
        )
        segmentacao = segmentar_preparacao(
            preparacao, segmentacao_id="segmentacao-teste-06",
            registrado_em=cls.INSTANTE,
        )
        return anotar_segmentacao(
            segmentacao, anotacao_id="anotacao-teste-06", registrado_em=cls.INSTANTE,
        )

    def previsoes_controladas(self, fonte):
        """Uma árvore artificial em estrela por período, sem gabarito de NLP."""
        resultado = []
        raizes = {}
        for token in fonte["segmentacao"]["tokens"]:
            raiz = raizes.setdefault(token["periodo_id"], token["id"])
            anotacao = {campo: copy.deepcopy(token[campo]) for campo in (
                "texto", "periodo_id", "paragrafo_id", "trabalho", "original",
            )}
            anotacao.update(
                token_id=token["id"], dependencia="ROOT" if token["id"] == raiz else "dep",
                cabeca_token_id=raiz, origem=copy.deepcopy(self.origem_real),
            )
            resultado.append(anotacao)
        return resultado

    def entidade_controlada(self, fonte, ordens, *, rotulo="PER"):
        tokens = fonte["segmentacao"]["tokens"]
        primeiro, ultimo = tokens[ordens[0]], tokens[ordens[-1]]
        inicio, fim = primeiro["trabalho"]["inicio"], ultimo["trabalho"]["fim"]
        preparacao = fonte["segmentacao"]["preparacao"]
        return {
            "texto": preparacao["trabalho"]["texto"][inicio:fim], "rotulo": rotulo,
            "token_ids": [tokens[i]["id"] for i in ordens],
            "periodo_id": primeiro["periodo_id"], "paragrafo_id": primeiro["paragrafo_id"],
            "trabalho": {"inicio": inicio, "fim": fim},
            "original": mapear_intervalo(preparacao, inicio, fim),
            "origem": copy.deepcopy(self.origem_real),
        }

    def analisar_controlado(self, fonte=None, *, entidades=None, relacoes=None, **opcoes):
        fonte = self.fonte() if fonte is None else fonte
        retorno = (
            self.previsoes_controladas(fonte) if relacoes is None else relacoes,
            [] if entidades is None else entidades,
            copy.deepcopy(self.processamento_real),
        )
        argumentos = {"analise_id": "analise-teste-06", "registrado_em": self.INSTANTE}
        argumentos.update(opcoes)
        with patch("sintaxe_entidades._executar", return_value=retorno):
            return analisar_sintaxe_entidades(fonte, **argumentos)

    def rejeitar_previsoes(self, fonte, *, entidades=None, relacoes=None):
        with self.assertRaises(ErroPrevisao):
            self.analisar_controlado(fonte, entidades=entidades, relacoes=relacoes)


class ContratoSintaxeEntidadesTests(FonteAnalise, unittest.TestCase):
    def test_esquema_e_prontidao_com_cobertura_integral(self):
        fonte = self.fonte("Eu fiquei. Ela saiu.")
        registro = self.analisar_controlado(fonte)
        self.assertEqual(registro["schema_version"], "1.0.0")
        self.assertEqual(registro["etapa"], "06_sintaxe_entidades")
        self.assertEqual(registro["analise_id"], "analise-teste-06")
        self.assertEqual(registro["anotacao"], fonte)
        for campo in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "coordenadas"):
            self.assertEqual(registro[campo], fonte[campo])
        validacao = validar_analise_sintaxe_entidades(registro)
        self.assertEqual(validacao["estado"], "pronto_para_etapa_07")
        self.assertIs(validacao["pronto_para_etapa_07"], True)
        self.assertEqual(validacao["cobertura"], {
            "tokens_total": len(fonte["segmentacao"]["tokens"]),
            "tokens_analisados": len(fonte["segmentacao"]["tokens"]),
            "periodos_total": len(fonte["segmentacao"]["periodos"]), "entidades_total": 0,
        })
        self.assertTrue(validacao["verificacoes"])

    def test_preserva_unicode_acentos_combinantes_emojis_e_separadores(self):
        textos = (
            "  Olá!  João\t\tficou.\r\n\r\nAção e\u0301 não é na\u0303o.  ",
            "\ufeffEu 😀 fiquei.\n\nEla 👩\u200d💻 saiu!",
            "não NÃO não... hã hã, tô erraado!\t\r\nOutra linha.",
        )
        for texto in textos:
            with self.subTest(texto=texto):
                fonte = self.fonte(texto)
                snapshot = copy.deepcopy(fonte)
                registro = self.analisar_controlado(fonte)
                self.assertEqual(fonte, snapshot)
                self.assertEqual(registro["anotacao"], snapshot)
                preparacao = registro["anotacao"]["segmentacao"]["preparacao"]
                self.assertEqual(preparacao["original"]["texto"], texto)
                for anterior, atual in zip(fonte["segmentacao"]["tokens"], registro["sintaxe"]):
                    self.assertEqual(atual["token_id"], anterior["id"])
                    for campo in ("texto", "trabalho", "original", "periodo_id", "paragrafo_id"):
                        self.assertEqual(atual[campo], anterior[campo])
                self.assertEqual(registro["anotacao"]["anotacoes_tokens"], snapshot["anotacoes_tokens"])
                self.assertEqual(registro["anotacao"]["segmentacao"]["separadores"], snapshot["segmentacao"]["separadores"])

    def test_leitura_literal_arquivo_e_serializacao_json(self):
        texto = "\ufeffOlá e\u0301!\r\n\r\n  João\tficou.\n"
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "original.txt"
            caminho.write_bytes(texto.encode("utf-8"))
            recebido = ler_texto_original(caminho)
            self.assertEqual(recebido, texto)
            registro = self.analisar_controlado(self.fonte(recebido))
            arquivo_json = Path(pasta) / "analise.json"
            arquivo_json.write_text(json.dumps(registro, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            recuperado = json.loads(arquivo_json.read_text(encoding="utf-8"))
        snapshot = copy.deepcopy(recuperado)
        validar_analise_sintaxe_entidades(recuperado)
        self.assertEqual(recuperado, registro)
        self.assertEqual(recuperado, snapshot)

    def test_json_lido_sem_spacy_ou_modelo_disponivel(self):
        registro = self.analisar_controlado()
        codigo = '''import builtins, json, sys
original = builtins.__import__
def protegido(name, *args, **kwargs):
    if name == "spacy" or name.startswith("spacy.") or name == "pt_core_news_sm":
        raise ImportError("Biblioteca propositalmente indisponível")
    return original(name, *args, **kwargs)
builtins.__import__ = protegido
from sintaxe_entidades import validar_analise_sintaxe_entidades
assert validar_analise_sintaxe_entidades(json.load(sys.stdin))["pronto_para_etapa_07"] is True
'''
        processo = subprocess.run(
            [sys.executable, "-c", codigo], input=json.dumps(registro), text=True,
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
        )
        self.assertEqual(processo.returncode, 0, processo.stderr)

    def test_crlf_literal_e_normalizado_mapeiam_mesmos_originais(self):
        texto = "Eu não queria sair.\r\nMas fiquei.\r\n\r\nJoão\r\nSilva chegou."
        registros = []
        for normalizar in (False, True):
            fonte = self.fonte(texto, normalizar=normalizar)
            tokens = fonte["segmentacao"]["tokens"]
            ordens = [i for i, token in enumerate(tokens) if token["texto"] in {"João", "Silva"}]
            registros.append(self.analisar_controlado(
                fonte, entidades=[self.entidade_controlada(fonte, ordens)],
            ))
        literal, normalizado = registros
        self.assertEqual([s["original"] for s in literal["sintaxe"]], [s["original"] for s in normalizado["sintaxe"]])
        self.assertEqual(literal["entidades"][0]["original"], normalizado["entidades"][0]["original"])
        mas = next(t for t in normalizado["sintaxe"] if t["texto"] == "Mas")
        self.assertEqual(mas["trabalho"], {"inicio": 20, "fim": 23})
        self.assertEqual(mas["original"], {"inicio": 21, "fim": 24})
        self.assertEqual(literal["entidades"][0]["texto"], "João\r\nSilva")
        self.assertEqual(normalizado["entidades"][0]["texto"], "João\nSilva")

    def test_copias_independentes_e_validacao_nao_mutam_fonte(self):
        fonte = self.fonte()
        snapshot = copy.deepcopy(fonte)
        registro = self.analisar_controlado(fonte)
        validar_analise_sintaxe_entidades(registro)
        registro["sintaxe"][0]["original"]["inicio"] = 999
        registro["anotacao"]["anotacoes_tokens"][0]["lema"] = "adulterado"
        registro["anotacao"]["segmentacao"]["preparacao"]["metadados_origem"]["fonte"] = "adulterado"
        self.assertEqual(fonte, snapshot)

    def test_vazio_e_apenas_brancos_tem_listas_vazias_validas(self):
        for texto in ("", "  \t\r\n\r\n", "\u00a0\u2028"):
            with self.subTest(texto=repr(texto)):
                registro = self.analisar_controlado(self.fonte(texto))
                self.assertEqual(registro["sintaxe"], [])
                self.assertEqual(registro["entidades"], [])
                self.assertIs(validar_analise_sintaxe_entidades(registro)["pronto_para_etapa_07"], True)

    def test_ids_e_data_com_fuso_reproduziveis(self):
        fonte = self.fonte()
        registro = self.analisar_controlado(fonte)
        self.assertEqual(registro, self.analisar_controlado(fonte))
        automatico = self.analisar_controlado(fonte, analise_id=None, registrado_em=None)
        self.assertEqual(UUID(automatico["analise_id"]).version, 4)
        self.assertIsNotNone(datetime.fromisoformat(automatico["registrado_em"]).utcoffset())
        instante = datetime(2026, 10, 7, tzinfo=timezone.utc)
        self.assertEqual(self.analisar_controlado(fonte, registrado_em=instante)["registrado_em"], instante.isoformat())
        for opcoes in ({"analise_id": ""}, {"analise_id": True}, {"registrado_em": "2026-10-07"},
                       {"registrado_em": datetime(2026, 10, 7)}, {"registrado_em": True}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroAnalise):
                self.analisar_controlado(fonte, **opcoes)

    def test_indicador_pronto_da_fonte_nao_substitui_validacao(self):
        fonte = self.fonte()
        fonte["segmentacao"]["preparacao"]["original"]["sha256"] = "0" * 64
        self.assertTrue(fonte["validacao"]["pronto_para_etapa_06"])
        with patch("sintaxe_entidades._executar") as executar, self.assertRaises(ErroEntrada):
            analisar_sintaxe_entidades(fonte)
        executar.assert_not_called()

    def test_fontes_malformadas_produzem_erro_explicito(self):
        for fonte in (None, [], {}, True, "texto"):
            with self.subTest(fonte=fonte), self.assertRaises(ErroAnalise):
                analisar_sintaxe_entidades(fonte)
        fonte = self.fonte()
        for modificar in (
            lambda r: r["validacao"].__setitem__("pronto_para_etapa_06", False),
            lambda r: r["anotacoes_tokens"][0].__setitem__("token_id", "ausente"),
            lambda r: r["segmentacao"]["tokens"][0].__setitem__("texto", "errado"),
        ):
            adulterado = copy.deepcopy(fonte)
            modificar(adulterado)
            with self.assertRaises(ErroEntrada):
                analisar_sintaxe_entidades(adulterado)

    def test_cobertura_tokens_ausentes_duplicados_ou_reordenados(self):
        fonte = self.fonte()
        relacoes = self.previsoes_controladas(fonte)
        for invalidas in (relacoes[:-1], relacoes + [copy.deepcopy(relacoes[-1])],
                          list(reversed(relacoes))):
            with self.subTest(ids=[r["token_id"] for r in invalidas]):
                self.rejeitar_previsoes(fonte, relacoes=invalidas)

    def test_cabeca_inexistente_ou_separador_sao_rejeitados(self):
        fonte = self.fonte("Eu  fiquei.")
        separador_id = fonte["segmentacao"]["separadores"][0]["id"]
        for cabeca in ("token-inexistente", separador_id, None, True):
            relacoes = self.previsoes_controladas(fonte)
            relacoes[1]["cabeca_token_id"] = cabeca
            with self.subTest(cabeca=cabeca):
                self.rejeitar_previsoes(fonte, relacoes=relacoes)

    def test_raiz_unica_rotulo_ROOT_e_cabeca_para_si(self):
        fonte = self.fonte("Eu fiquei aqui.")
        relacoes = self.previsoes_controladas(fonte)
        modificacoes = (
            lambda r: r[0].__setitem__("dependencia", "dep"),
            lambda r: r[0].__setitem__("cabeca_token_id", r[1]["token_id"]),
            lambda r: r[1].update(dependencia="ROOT", cabeca_token_id=r[1]["token_id"]),
            lambda r: r[1].__setitem__("cabeca_token_id", r[1]["token_id"]),
            lambda r: r[0].__setitem__("dependencia", "root"),
        )
        for numero, modificar in enumerate(modificacoes):
            invalidas = copy.deepcopy(relacoes)
            modificar(invalidas)
            with self.subTest(numero=numero):
                self.rejeitar_previsoes(fonte, relacoes=invalidas)

    def test_ciclo_com_raiz_existente_nao_atinge_raiz(self):
        fonte = self.fonte("Eu fiquei aqui.")
        relacoes = self.previsoes_controladas(fonte)
        relacoes[1]["cabeca_token_id"] = relacoes[2]["token_id"]
        relacoes[2]["cabeca_token_id"] = relacoes[1]["token_id"]
        self.rejeitar_previsoes(fonte, relacoes=relacoes)

    def test_cabecas_nao_atravessam_periodos_ou_paragrafos(self):
        for texto in ("Eu fiquei. Ela saiu.", "Eu fiquei.\n\nEla saiu."):
            with self.subTest(texto=texto):
                fonte = self.fonte(texto)
                relacoes = self.previsoes_controladas(fonte)
                segundo_periodo = fonte["segmentacao"]["periodos"][1]
                alvo = next(r for r in relacoes if r["periodo_id"] == segundo_periodo["id"] and r["dependencia"] != "ROOT")
                alvo["cabeca_token_id"] = relacoes[0]["token_id"]
                self.rejeitar_previsoes(fonte, relacoes=relacoes)

    def test_anotacoes_sintaticas_mantem_vinculos_textos_intervalos_origem(self):
        fonte = self.fonte()
        modificacoes = (
            lambda r: r[0].__setitem__("periodo_id", "outro-periodo"),
            lambda r: r[0].__setitem__("paragrafo_id", "outro-paragrafo"),
            lambda r: r[0].__setitem__("texto", "eu"),
            lambda r: r[0]["trabalho"].__setitem__("inicio", True),
            lambda r: r[0]["original"].__setitem__("fim", 999),
            lambda r: r[0].__setitem__("dependencia", ""),
            lambda r: r[0].__setitem__("dependencia", None),
            lambda r: r[0].__setitem__("origem", {}),
        )
        for numero, modificar in enumerate(modificacoes):
            relacoes = self.previsoes_controladas(fonte)
            modificar(relacoes)
            with self.subTest(numero=numero):
                self.rejeitar_previsoes(fonte, relacoes=relacoes)

    def test_entidade_com_espacos_internos_preserva_recorte_e_tokens_consecutivos(self):
        for texto in ("João   Silva chegou.", "João\t\tSilva chegou.", "João\nSilva chegou."):
            with self.subTest(texto=texto):
                fonte = self.fonte(texto)
                esperado = self.entidade_controlada(fonte, [0, 1])
                registro = self.analisar_controlado(fonte, entidades=[esperado])
                entidade = registro["entidades"][0]
                self.assertEqual(entidade["id"], "analise-teste-06:entidade:0")
                self.assertEqual(entidade["ordem"], 0)
                self.assertEqual(entidade["token_ids"], esperado["token_ids"])
                self.assertEqual(entidade["texto"], texto[:texto.index(" chegou")])
                self.assertEqual(entidade["trabalho"], esperado["trabalho"])
                self.assertEqual(entidade["original"], esperado["original"])
                validar_analise_sintaxe_entidades(registro)

    def test_entidades_repetidas_sao_ocorrencias_distintas(self):
        fonte = self.fonte("João viu João.")
        primeira = self.entidade_controlada(fonte, [0])
        segunda = self.entidade_controlada(fonte, [2])
        registro = self.analisar_controlado(fonte, entidades=[primeira, segunda])
        entidades = registro["entidades"]
        self.assertEqual([e["texto"] for e in entidades], ["João", "João"])
        self.assertEqual([e["ordem"] for e in entidades], [0, 1])
        self.assertNotEqual(entidades[0]["id"], entidades[1]["id"])
        self.assertNotEqual(entidades[0]["token_ids"], entidades[1]["token_ids"])
        self.assertNotEqual(entidades[0]["trabalho"], entidades[1]["trabalho"])

    def test_lista_vazia_de_entidades_e_valida_sem_inventar_genericos(self):
        fonte = self.fonte("O rapaz visitou o mercadinho.")
        registro = self.analisar_controlado(fonte, entidades=[])
        self.assertEqual(registro["entidades"], [])
        self.assertEqual(registro["validacao"]["cobertura"]["entidades_total"], 0)

    def test_entidades_sem_tokens_duplicadas_nao_consecutivas_ou_invertidas(self):
        fonte = self.fonte("João da Silva chegou.")
        entidade = self.entidade_controlada(fonte, [0, 1, 2])
        ids = entidade["token_ids"]
        for token_ids in ([], ["inexistente"], [ids[0], ids[0]], [ids[0], ids[2]], list(reversed(ids))):
            invalida = copy.deepcopy(entidade)
            invalida["token_ids"] = token_ids
            with self.subTest(token_ids=token_ids):
                self.rejeitar_previsoes(fonte, entidades=[invalida])

    def test_entidades_rejeitam_limites_texto_associacao_e_origem_incorretos(self):
        fonte = self.fonte("João Silva chegou.")
        entidade = self.entidade_controlada(fonte, [0, 1])
        modificar = (
            lambda e: e["trabalho"].__setitem__("inicio", 1),
            lambda e: e["trabalho"].__setitem__("fim", e["trabalho"]["fim"] - 1),
            lambda e: e["trabalho"].__setitem__("fim", 999),
            lambda e: e["trabalho"].__setitem__("inicio", True),
            lambda e: e["original"].__setitem__("fim", 999),
            lambda e: e.__setitem__("texto", "Joao Silva"),
            lambda e: e.__setitem__("periodo_id", "outro-periodo"),
            lambda e: e.__setitem__("paragrafo_id", "outro-paragrafo"),
            lambda e: e.__setitem__("rotulo", ""),
            lambda e: e.__setitem__("rotulo", "CATEGORIA_INVENTADA"),
            lambda e: e.__setitem__("origem", {}),
        )
        for numero, mudanca in enumerate(modificar):
            invalida = copy.deepcopy(entidade)
            mudanca(invalida)
            with self.subTest(numero=numero):
                self.rejeitar_previsoes(fonte, entidades=[invalida])

    def test_entidade_nao_atravessa_periodo_ou_paragrafo(self):
        for texto in ("João. Maria.", "João.\n\nMaria."):
            with self.subTest(texto=texto):
                fonte = self.fonte(texto)
                entidade = self.entidade_controlada(fonte, [0, 1, 2])
                self.rejeitar_previsoes(fonte, entidades=[entidade])

    def test_ordem_e_sobreposicao_de_entidades_sao_rejeitadas(self):
        fonte = self.fonte("João viu Maria.")
        primeira = self.entidade_controlada(fonte, [0])
        segunda = self.entidade_controlada(fonte, [2])
        self.rejeitar_previsoes(fonte, entidades=[segunda, primeira])
        self.rejeitar_previsoes(fonte, entidades=[primeira, copy.deepcopy(primeira)])
        registro = self.analisar_controlado(fonte, entidades=[primeira, segunda])
        for campo, valor in (("ordem", True), ("ordem", 9), ("id", "outro-id")):
            adulterado = copy.deepcopy(registro)
            adulterado["entidades"][0][campo] = valor
            with self.subTest(campo=campo, valor=valor), self.assertRaises(ErroAnalise):
                validar_analise_sintaxe_entidades(adulterado)

    def test_entidade_unicode_usa_pontos_de_codigo_e_mapa_original(self):
        fonte = self.fonte("😀 e\u0301 João chegou.\r\nOutra linha.", normalizar=True)
        tokens = fonte["segmentacao"]["tokens"]
        ordem = next(i for i, t in enumerate(tokens) if t["texto"] == "João")
        entidade = self.entidade_controlada(fonte, [ordem])
        registro = self.analisar_controlado(fonte, entidades=[entidade])
        self.assertEqual(registro["entidades"][0]["trabalho"], {"inicio": 5, "fim": 9})
        self.assertEqual(registro["entidades"][0]["original"], {"inicio": 5, "fim": 9})
        alterada = copy.deepcopy(entidade)
        alterada["trabalho"] = {"inicio": 6, "fim": 10}
        self.rejeitar_previsoes(fonte, entidades=[alterada])

    def test_registro_adulterado_campos_fonte_contagens_e_prontidao(self):
        registro = self.analisar_controlado()
        alterar = (
            lambda r: r.__setitem__("documento_id", "outro-documento"),
            lambda r: r.__setitem__("anotacao_id", "outra-anotacao"),
            lambda r: r["anotacao"]["segmentacao"]["preparacao"]["original"].__setitem__("sha256", "0" * 64),
            lambda r: r["anotacao"]["anotacoes_tokens"][0].__setitem__("texto", "alterado"),
            lambda r: r["coordenadas"].__setitem__("base", False),
            lambda r: r["sintaxe"][0].__setitem__("token_id", "inexistente"),
            lambda r: r["sintaxe"][0].__setitem__("dependencia", ""),
            lambda r: r["processamento"].__setitem__("componentes_executados", ["ner"]),
            lambda r: r["processamento"]["modelo"].__setitem__("versao", "99.0.0"),
            lambda r: r["validacao"].__setitem__("pronto_para_etapa_07", False),
            lambda r: r["validacao"]["cobertura"].__setitem__("tokens_total", 999),
            lambda r: r["validacao"]["cobertura"].__setitem__("entidades_total", False),
        )
        for numero, modificar in enumerate(alterar):
            adulterado = copy.deepcopy(registro)
            modificar(adulterado)
            with self.subTest(numero=numero), self.assertRaises(ErroAnalise):
                validar_analise_sintaxe_entidades(adulterado)

    def test_arquivo_malformado_campos_ausentes_e_json_invalido(self):
        registro = self.analisar_controlado()
        for valor in (None, [], {}, True, "registro"):
            with self.subTest(valor=valor), self.assertRaises(ErroAnalise):
                validar_analise_sintaxe_entidades(valor)
        for campo in registro:
            adulterado = copy.deepcopy(registro)
            del adulterado[campo]
            with self.subTest(campo=campo), self.assertRaises(ErroAnalise):
                validar_analise_sintaxe_entidades(adulterado)
        for invalido in (float("nan"), {1: "chave não textual"}, ("tupla",)):
            adulterado = copy.deepcopy(registro)
            adulterado["extra"] = invalido
            with self.subTest(invalido=repr(invalido)), self.assertRaises(ErroAnalise):
                validar_analise_sintaxe_entidades(adulterado)

    def test_limite_caracteres_sem_processamento_ou_truncamento(self):
        fonte = self.fonte("Um dois três.")
        snapshot = copy.deepcopy(fonte)
        with patch("sintaxe_entidades.MAX_CARACTERES", 5), patch("sintaxe_entidades._executar") as executar:
            with self.assertRaises(ErroLimite):
                analisar_sintaxe_entidades(fonte)
        executar.assert_not_called()
        self.assertEqual(fonte, snapshot)

    def test_fonte_e_validada_uma_vez_por_execucao(self):
        fonte = self.fonte("palavra " * 100)
        with patch("sintaxe_entidades.validar_anotacao", wraps=validar_anotacao) as conferir:
            self.analisar_controlado(fonte)
        self.assertEqual(conferir.call_count, 1)


class ModeloRealSintaxeEntidadesTests(FonteAnalise, unittest.TestCase):
    def modelo_com_alteracao_controlada(self, alterar):
        """Injete uma violação após o modelo real, somente dentro do teste."""
        modelo, versao = sintaxe._carregar_modelo()

        class ModeloAlterado:
            def __getattr__(self, nome):
                return getattr(modelo, nome)

            def __call__(self, documento):
                resultado = modelo(documento)
                alterar(resultado)
                return resultado

        return ModeloAlterado(), versao

    def test_modelo_real_cobertura_parser_ner_e_morfologia_preservada(self):
        fonte = self.fonte("João visitou São Paulo. Maria ficou em Lisboa.")
        snapshot = copy.deepcopy(fonte)
        registro = analisar_sintaxe_entidades(fonte)
        self.assertEqual(fonte, snapshot)
        self.assertEqual(registro["anotacao"], snapshot)
        self.assertEqual(registro["processamento"]["componentes_executados"], ["tok2vec", "parser", "ner"])
        self.assertEqual(registro["processamento"]["modelo"]["nome"], "pt_core_news_sm")
        self.assertEqual(registro["processamento"]["modelo"]["versao"], "3.8.0")
        self.assertTrue(all(t["dependencia"] for t in registro["sintaxe"]))
        self.assertTrue(registro["entidades"])
        self.assertIs(validar_analise_sintaxe_entidades(registro)["pronto_para_etapa_07"], True)
        for item in registro["sintaxe"] + registro["entidades"]:
            self.assertFalse({"confidence", "confianca", "embedding", "interpretacao"} & set(item))

    def test_modelo_real_periodos_consideram_tokens_de_conteudo_em_espacos_iniciais(self):
        for texto in ("  Eu fiquei. Ela saiu.", "\tEu fiquei.\r\nEla saiu.",
                      "Eu  fiquei.\n\nEla saiu."):
            with self.subTest(texto=repr(texto)):
                fonte = self.fonte(texto)
                registro = analisar_sintaxe_entidades(fonte)
                self.assertEqual(registro["anotacao"], fonte)
                self.assertEqual([r["token_id"] for r in registro["sintaxe"]],
                                 [t["id"] for t in fonte["segmentacao"]["tokens"]])
                validar_analise_sintaxe_entidades(registro)

    def test_modelo_real_limite_tokens_internos_sem_truncar(self):
        fonte = self.fonte("Um dois três.")
        snapshot = copy.deepcopy(fonte)
        with patch("sintaxe_entidades.MAX_TOKENS", 2), self.assertRaises(ErroLimite):
            analisar_sintaxe_entidades(fonte)
        self.assertEqual(fonte, snapshot)

    def test_modelo_ausente_ou_incompativel_produz_erro_explicito(self):
        with patch("spacy.load", side_effect=OSError("modelo ausente")), self.assertRaises(ErroModelo):
            sintaxe._carregar_modelo.__wrapped__()
        modelo, _ = sintaxe._carregar_modelo()
        with patch.dict(modelo.meta, {"version": "99.0.0"}), patch("spacy.load", return_value=modelo):
            with self.assertRaises(ErroModelo):
                sintaxe._carregar_modelo.__wrapped__()

    def test_modelo_e_carregado_uma_vez_e_nao_roda_novamente_no_validador(self):
        import spacy
        sintaxe._carregar_modelo.cache_clear()
        with patch("spacy.load", wraps=spacy.load) as carregar:
            primeiro = analisar_sintaxe_entidades(self.fonte("Eu fiquei."))
            segundo = analisar_sintaxe_entidades(self.fonte("Ela saiu."))
            self.assertEqual(carregar.call_count, 1)
            validar_analise_sintaxe_entidades(primeiro)
            validar_analise_sintaxe_entidades(segundo)
            self.assertEqual(carregar.call_count, 1)

    def test_tokenizador_incompativel_nao_ignora_alinhamento(self):
        from spacy.tokens import Doc
        from segmentacao import _tokenizador
        fonte = self.fonte("Eu fiquei.")
        tokenizador, _ = _tokenizador()
        # Processador controlado apenas para representar tokenização incompatível.
        diferente = Doc(tokenizador.vocab, words=["E", "u", "fiquei", "."], spaces=[False, True, False, False])
        with patch("segmentacao._tokenizador", return_value=(tokenizador, "3.8.16")):
            with patch.object(tokenizador, "make_doc", return_value=diferente), self.assertRaises(ErroAlinhamento):
                analisar_sintaxe_entidades(fonte)

    def test_componentes_nao_podem_alterar_fronteiras_ou_morfologia_herdadas(self):
        from spacy.attrs import SENT_START

        def alterar_fronteira(documento):
            dados = documento.to_array([SENT_START])
            primeiro = next(t.i for t in documento if not t.text.isspace())
            dados[primeiro] = 0
            documento.from_array([SENT_START], dados)

        def alterar_morfologia(documento):
            next(t for t in documento if not t.text.isspace()).lemma_ = "adulterado"

        fonte = self.fonte("  Eu fiquei. Ela saiu.")
        snapshot = copy.deepcopy(fonte)
        for alterar in (alterar_fronteira, alterar_morfologia):
            with self.subTest(alteracao=alterar.__name__):
                modelo = self.modelo_com_alteracao_controlada(alterar)
                with patch("sintaxe_entidades._carregar_modelo", return_value=modelo):
                    with self.assertRaises(ErroAlinhamento):
                        analisar_sintaxe_entidades(fonte)
        self.assertEqual(fonte, snapshot)

    def test_cabeca_interna_em_separador_e_erro_explicito_sem_reparar_previsao(self):
        def alterar_cabeca(documento):
            membro = next(t for t in documento if not t.text.isspace() and t.dep_ != "ROOT")
            membro.head = next(t for t in documento if t.text.isspace())

        fonte = self.fonte("Eu fiquei  aqui.")
        modelo = self.modelo_com_alteracao_controlada(alterar_cabeca)
        with patch("sintaxe_entidades._carregar_modelo", return_value=modelo):
            with self.assertRaisesRegex(ErroAlinhamento, "separador"):
                analisar_sintaxe_entidades(fonte)


if __name__ == "__main__":
    unittest.main()
