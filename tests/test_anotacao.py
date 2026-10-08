import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import UUID

import anotacao
from anotacao import (
    ErroAlinhamento, ErroAnotacao, ErroLimite, ErroModelo,
    anotar_segmentacao, validar_anotacao,
)
from preparacao import preparar_texto
from segmentacao import segmentar_preparacao, validar_segmentacao


class AnnotationTests(unittest.TestCase):
    INSTANTE = "2026-10-07T12:00:00-03:00"

    def fonte(self, texto="Eu guardei o dinheiro.", normalizar=False):
        return segmentar_preparacao(preparar_texto(
            texto, 42, normalizar_crlf=normalizar,
            preparacao_id="preparacao-teste", registrado_em=self.INSTANTE,
        ), segmentacao_id="segmentacao-teste", registrado_em=self.INSTANTE)

    def anotar(self, texto="Eu guardei o dinheiro.", normalizar=False):
        return anotar_segmentacao(self.fonte(texto, normalizar), anotacao_id="anotacao-teste", registrado_em=self.INSTANTE)

    def test_modelo_real_sem_parser_entidades_ou_confianças_inventadas(self):
        registro = self.anotar()
        self.assertEqual(registro["processamento"]["componentes_executados"], ["tok2vec", "morphologizer", "lemmatizer"])
        self.assertEqual(registro["processamento"]["modelo"]["versao"], "3.8.0")
        self.assertTrue(all(t["lema"] and t["pos"] for t in registro["anotacoes_tokens"]))
        self.assertTrue(all(t["tag"] is None for t in registro["anotacoes_tokens"]))
        self.assertTrue(validar_anotacao(registro)["pronto_para_etapa_06"])
        self.assertTrue(registro["validacao"]["pendencias"])
        for t in registro["anotacoes_tokens"]:
            self.assertFalse({"confidence", "confianca", "dep", "head", "ent_type", "embedding"} & set(t))

    def test_preservacao_literal_e_unicode_em_diferentes_entradas(self):
        textos = [
            "  Olá!  Ação\t\tna\u0303o.\r\n\r\nFiquei... fiquei olhando.  ",
            "\ufeffEu não queria sair. 😀 👩\u200d💻 👩\u200d👩\u200d👧\u200d👦",
            "tô num lugar; vô sair, pq vc insistiu. erraado hã hã",
            "Ele disse: ‘não’.\n\n漢字 e\u0301 é.",
        ]
        for texto in textos:
            with self.subTest(texto=texto):
                fonte = self.fonte(texto)
                snapshot = copy.deepcopy(fonte)
                registro = anotar_segmentacao(fonte)
                self.assertEqual(fonte, snapshot)
                self.assertEqual(registro["segmentacao"], snapshot)
                self.assertEqual(registro["segmentacao"]["preparacao"]["original"]["texto"], texto)
                for origem, atual in zip(fonte["tokens"], registro["anotacoes_tokens"]):
                    self.assertEqual(atual["token_id"], origem["id"])
                    for campo in ("texto", "trabalho", "original", "periodo_id", "paragrafo_id"):
                        self.assertEqual(atual[campo], origem[campo])

    def test_mapeamento_crlf_herdado_sem_recalcular_deslocamentos(self):
        registro = self.anotar("Eu não queria sair.\r\nMas fiquei.", normalizar=True)
        token = next(t for t in registro["anotacoes_tokens"] if t["texto"] == "Mas")
        self.assertEqual(token["trabalho"], {"inicio":20,"fim":23})
        self.assertEqual(token["original"], {"inicio":21,"fim":24})
        self.assertEqual(registro["segmentacao"]["periodos"][1]["original"], {"inicio":21,"fim":32})

    def test_copias_independentes_nao_modificam_fonte(self):
        fonte = self.fonte()
        snapshot = copy.deepcopy(fonte)
        registro = anotar_segmentacao(fonte)
        registro["anotacoes_tokens"][0]["original"]["inicio"] = 999
        registro["segmentacao"]["tokens"][0]["texto"] = "alterado"
        self.assertEqual(fonte, snapshot)

    def test_vazio_e_brancos_sao_validos_sem_unidades_lexicais(self):
        for texto in ("", "  \t\r\n\r\n", "\u00a0\u2028"):
            with self.subTest(texto=repr(texto)):
                registro = self.anotar(texto)
                self.assertEqual(registro["anotacoes_tokens"], [])
                self.assertEqual(registro["vocabulario"], [])
                self.assertTrue(validar_anotacao(registro)["pronto_para_etapa_06"])

    def test_json_estrito_e_validacao_sem_mutar(self):
        registro = self.anotar("Ação e\u0301 😀!\r\nOutra linha.")
        recuperado = json.loads(json.dumps(registro, ensure_ascii=False, allow_nan=False))
        snapshot = copy.deepcopy(recuperado)
        validar_anotacao(recuperado)
        self.assertEqual(recuperado, registro)
        self.assertEqual(recuperado, snapshot)

    def test_leitura_sem_modelo_ou_spacy_importado(self):
        registro = self.anotar()
        codigo = '''import builtins, json, sys
original = builtins.__import__
def protegido(name, *args, **kwargs):
    if name == "spacy" or name.startswith("spacy.") or name == "pt_core_news_sm":
        raise ImportError("Biblioteca propositalmente indisponível")
    return original(name, *args, **kwargs)
builtins.__import__ = protegido
from anotacao import validar_anotacao
assert validar_anotacao(json.load(sys.stdin))["pronto_para_etapa_06"] is True
'''
        result = subprocess.run([sys.executable,"-c",codigo], input=json.dumps(registro), text=True,
                                cwd=Path(__file__).resolve().parents[1], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_identificadores_datas_reais_e_reproduciveis(self):
        primeiro = self.anotar()
        self.assertEqual(primeiro, self.anotar())
        automatico = anotar_segmentacao(self.fonte())
        self.assertEqual(UUID(automatico["anotacao_id"]).version, 4)
        self.assertIsNotNone(datetime.fromisoformat(automatico["registrado_em"]).utcoffset())
        instante = datetime(2026,10,7,tzinfo=timezone.utc)
        self.assertEqual(anotar_segmentacao(self.fonte(), registrado_em=instante)["registrado_em"], instante.isoformat())
        for options in ({"anotacao_id":""}, {"anotacao_id":True}, {"registrado_em":"2026-10-07"},
                        {"registrado_em":datetime(2026,10,7)}):
            with self.subTest(options=options), self.assertRaises(ErroAnotacao):
                anotar_segmentacao(self.fonte(), **options)

    def test_modelo_carregado_uma_vez(self):
        anotacao._carregar_modelo.cache_clear()
        import spacy
        with patch("spacy.load", wraps=spacy.load) as carregar:
            self.anotar("Um.")
            self.anotar("Dois.")
            self.assertEqual(carregar.call_count, 1)

    def test_modelo_indisponivel_ou_incompativel_nao_simula_saida(self):
        with patch("spacy.load", side_effect=OSError("ausente")), self.assertRaises(ErroModelo):
            anotacao._carregar_modelo.__wrapped__()
        modelo, versao = anotacao._carregar_modelo()
        with patch.dict(modelo.meta, {"version":"99.0.0"}), patch("spacy.load", return_value=modelo), self.assertRaises(ErroModelo):
            anotacao._carregar_modelo.__wrapped__()

    def test_fronteiras_incompativeis_interrompem_execucao(self):
        fonte = self.fonte("Eu fiquei.")
        # Válida estruturalmente, mas diferente da tokenização efetiva do spaCy.
        texto = fonte["preparacao"]["trabalho"]["texto"]
        fonte["tokens"][0]["trabalho"]["fim"] = 1
        fonte["tokens"][0]["original"]["fim"] = 1
        fonte["tokens"][0]["texto"] = "E"
        novo = copy.deepcopy(fonte["tokens"][0])
        novo.update(id="segmentacao-teste:token:1", ordem=1,texto="u", trabalho={"inicio":1,"fim":2},original={"inicio":1,"fim":2})
        fonte["tokens"].insert(1,novo)
        for ordem,token in enumerate(fonte["tokens"]):
            token.update(ordem=ordem,id=f"segmentacao-teste:token:{ordem}")
        fonte["periodos"][0]["tokens"] = [t["id"] for t in fonte["tokens"]]
        validar_segmentacao(fonte)
        with self.assertRaises(ErroAlinhamento):
            anotar_segmentacao(fonte)
        self.assertEqual(fonte["preparacao"]["trabalho"]["texto"], texto)

    def test_limites_explicitamente_testados_sem_truncamento(self):
        fonte = self.fonte("Um dois três.")
        with patch("anotacao.MAX_CARACTERES", 5), self.assertRaises(ErroLimite):
            anotar_segmentacao(fonte)
        with patch("anotacao.MAX_TOKENS", 2), self.assertRaises(ErroLimite):
            anotar_segmentacao(fonte)

    def test_validacao_da_fonte_tem_numero_constante_de_chamadas(self):
        fonte = self.fonte("palavra " * 100)
        with patch("anotacao.validar_segmentacao", wraps=validar_segmentacao) as conferir:
            anotar_segmentacao(fonte)
        self.assertEqual(conferir.call_count, 1)

    def test_vocabulario_preserva_stopwords_formas_e_ocorrencias(self):
        registro = self.anotar("Eu não queria sair. Eu não queria sair.")
        entradas = registro["vocabulario"]
        self.assertTrue(any(e["lema"] == "não" for e in entradas))
        tokens = {t["token_id"]:t for t in registro["anotacoes_tokens"]}
        incluidos = []
        for entry in entradas:
            self.assertEqual(entry["quantidade"], len(entry["token_ids"]))
            self.assertEqual(entry["formas"], list(dict.fromkeys(tokens[i]["texto"] for i in entry["token_ids"])))
            self.assertFalse(entry["pos"] in {"PUNCT","SYM"})
            incluidos.extend(entry["token_ids"])
        self.assertEqual(len(incluidos),len(set(incluidos)))

    def test_agrupamento_alternativo_nao_funde_desconhecidos(self):
        # Dados sintéticos somente para testar o agrupador, sem representar previsões reais.
        tokens = [{"token_id":str(i),"texto":t,"lema":l,"pos":p} for i,(t,l,p) in enumerate([
            ("Xablau",None,None),("blorf",None,None),("Xablau",None,None),
            ("guarda","guardar","VERB"),("guarda","guarda","NOUN"),
            ("e\u0301",None,"X"),("é",None,"X"),(".",".","PUNCT"),
        ])]
        entries = anotacao._vocabulario(tokens,"teste")
        self.assertEqual(len(entries),6)
        self.assertEqual(entries[0]["quantidade"],2)
        self.assertEqual(entries[0]["criterio_agrupamento"],"forma_pos")
        self.assertEqual(entries[0]["forma_agrupamento"],"Xablau")

    def test_morfologia_vazia_ausencia_e_lema_igual_ao_texto(self):
        registro = self.anotar("dinheiro.")
        self.assertEqual(registro["anotacoes_tokens"][0]["lema"], "dinheiro")
        token = registro["anotacoes_tokens"][-1]
        self.assertEqual(token["morfologia"], {})
        token["morfologia"] = None
        token["campos_nao_resolvidos"] = ["tag","morfologia"]
        registro["validacao"] = anotacao._relatorio(registro["anotacoes_tokens"])
        validar_anotacao(registro)

    def test_entradas_e_registros_malformados_sao_erros_explicitamente(self):
        for valor in (None, [], {}, "texto", True):
            with self.subTest(valor=valor):
                with self.assertRaises(ErroAnotacao):
                    anotar_segmentacao(valor)
                with self.assertRaises(ErroAnotacao):
                    validar_anotacao(valor)
        for campo in self.anotar():
            registro = self.anotar()
            del registro[campo]
            with self.subTest(campo=campo), self.assertRaises(ErroAnotacao):
                validar_anotacao(registro)

    def test_rejeita_anotacoes_vocabulario_e_relatorios_adulterados(self):
        alterar = [
            lambda r:r["segmentacao"]["preparacao"]["original"].__setitem__("sha256","0"*64),
            lambda r:r["anotacoes_tokens"][0].__setitem__("texto","errado"),
            lambda r:r["anotacoes_tokens"][0].__setitem__("origem",{}),
            lambda r:r["anotacoes_tokens"][0].__setitem__("pos","ERRADO"),
            lambda r:r["anotacoes_tokens"][0].__setitem__("morfologia",{"Gender":2}),
            lambda r:r["anotacoes_tokens"][0].__setitem__("campos_nao_resolvidos",[]),
            lambda r:r["anotacoes_tokens"][0]["original"].__setitem__("inicio",True),
            lambda r:r["vocabulario"][0].__setitem__("quantidade",999),
            lambda r:r["vocabulario"][0]["token_ids"].append("inexistente"),
            lambda r:r["processamento"]["componentes_executados"].append("parser"),
            lambda r:r["processamento"]["configuracoes"].__setitem__("embeddings_exportados",0),
            lambda r:r["validacao"].__setitem__("pronto_para_etapa_06",False),
        ]
        for numero, modificar in enumerate(alterar):
            with self.subTest(numero=numero):
                registro=self.anotar()
                modificar(registro)
                with self.assertRaises(ErroAnotacao):
                    validar_anotacao(registro)


if __name__ == "__main__":
    unittest.main()
