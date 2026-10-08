"""Regressões manuais da integração, sem usar previsões como gabarito.

Os atributos e os alcances esperados abaixo são definidos diretamente.
A etapa 06 é montada pelo leitor de fixtures já existente; as verificações
exercitam a API pública da etapa 07 e a preservação da origem.
"""

from copy import deepcopy
import json
import unittest

from fixtures_regras import (
    INSTANTE, construir_analise, ids, infinitivo, ponto, token, verbo,
)
from preparacao import mapear_intervalo
from regras_detectores import construir_contexto, detectar_regra
from regras_linguisticas import aplicar_regras_linguisticas, validar_regras_linguisticas


class IntegracaoCitacaoRegrasTests(unittest.TestCase):
    def executar(self, fonte):
        anterior = deepcopy(fonte)
        registro = aplicar_regras_linguisticas(
            fonte, execucao_id="regressao-citacao", registrado_em=INSTANTE)
        self.assertEqual(fonte, anterior)
        self.assertEqual(registro["analise"], anterior)
        self.assertTrue(validar_regras_linguisticas(registro)["pronto_para_etapa_08"])
        self.assertEqual(
            validar_regras_linguisticas(json.loads(json.dumps(registro, ensure_ascii=False))),
            registro["validacao"])
        preparacao = fonte["anotacao"]["segmentacao"]["preparacao"]
        for ocorrencia in registro["ocorrencias"]:
            trechos = list(ocorrencia["evidencias"])
            if ocorrencia["alcance"] is not None:
                trechos.extend(ocorrencia["alcance"]["trechos"])
            for trecho in trechos:
                inicio, fim = trecho["trabalho"]["inicio"], trecho["trabalho"]["fim"]
                self.assertEqual(trecho["texto"], preparacao["trabalho"]["texto"][inicio:fim])
                self.assertEqual(trecho["original"], mapear_intervalo(preparacao, inicio, fim))
        return registro

    def por_regra(self, registro, regra):
        return [o for o in registro["ocorrencias"] if o["regra"]["id"] == regra]

    def fonte_citacao(self, *, pos_aspa="PUNCT"):
        return construir_analise(texto='Ela disse "não saio".', linhas=[
            token("Ela", "ela", "PRON", 1, "nsubj"),
            verbo("disse", "dizer", 1, tempo="Past"),
            token('"', '"', pos_aspa, 4, "dep"),
            token("não", "não", "ADV", 4, "advmod"),
            verbo("saio", "sair", 1, "ccomp"),
            ponto(4, '"'), ponto(1),
        ])

    def test_delimitador_nao_vira_nucleo_mesmo_com_pos_verbal(self):
        for pos in ("VERB", "AUX", "PROPN"):
            with self.subTest(pos=pos):
                fonte = self.fonte_citacao(pos_aspa=pos)
                registro = self.executar(fonte)
                oracoes = self.por_regra(registro, "oracao_predicado")
                self.assertEqual([o["nucleo_token_ids"] for o in oracoes],
                                 [ids(fonte, [1]), ids(fonte, [4])])
                self.assertEqual([o["alcance"]["token_ids"] for o in oracoes],
                                 [ids(fonte, [0, 1, 6]), ids(fonte, [3, 4])])
                negacao, = self.por_regra(registro, "negacao_nao")
                self.assertEqual(negacao["alcance"]["token_ids"], ids(fonte, [3, 4]))
                self.assertTrue(negacao["ambiguidades"])
                self.assertEqual(fonte["anotacao"]["anotacoes_tokens"][2]["pos"], pos)

    def test_delimitador_com_dependente_copular_nao_vira_predicado(self):
        fonte = construir_analise(texto='Está "não".', linhas=[
            verbo("Está", "estar", 1, "cop", pos="AUX"),
            token('"', '"', "ADJ", 1, "ROOT"),
            token("não", "não", "ADV", 1, "advmod"),
            ponto(1, '"'), ponto(1),
        ])
        registro = self.executar(fonte)
        self.assertEqual(self.por_regra(registro, "oracao_predicado"), [])

    def test_negacao_externa_nao_inclui_mencao_citada(self):
        fonte = construir_analise(texto='Não saio "não".', linhas=[
            token("Não", "não", "ADV", 1, "advmod"),
            verbo("saio", "sair", 1), ponto(1, '"'),
            token("não", "não", "ADV", 1, "advmod"),
            ponto(1, '"'), ponto(1),
        ])
        registro = self.executar(fonte)
        externa, citada = self.por_regra(registro, "negacao_nao")
        self.assertEqual(externa["alcance"]["token_ids"], ids(fonte, [0, 1, 5]))
        self.assertEqual([t["texto"] for t in externa["alcance"]["trechos"]], ["Não saio", "."])
        self.assertIsNone(citada["alcance"])
        self.assertTrue(citada["ambiguidades"])
        self.assertEqual(self.por_regra(registro, "negacao_multiplos"), [])

    def test_delimitador_nao_sustenta_nucleo_de_constituinte_negado(self):
        fonte = construir_analise(texto='Não "saio".', linhas=[
            token("Não", "não", "ADV", 1, "advmod"),
            token('"', '"', "ADJ", 1, "ROOT"),
            verbo("saio", "sair", 1, "xcomp"), ponto(1, '"'), ponto(1),
        ])
        registro = self.executar(fonte)
        negacao, = self.por_regra(registro, "negacao_nao")
        self.assertEqual(negacao["nucleo_token_ids"], [])
        self.assertIsNone(negacao["alcance"])
        self.assertTrue(negacao["necessidade_contexto"])

    def test_regioes_citadas_distintas_nao_se_misturam(self):
        fonte = construir_analise(texto='"Não saio" "não fico".', linhas=[
            ponto(2, '"'), token("Não", "não", "ADV", 2, "advmod"),
            verbo("saio", "sair", 2), ponto(2, '"'), ponto(6, '"'),
            token("não", "não", "ADV", 6, "advmod"),
            verbo("fico", "ficar", 2, "conj"), ponto(6, '"'), ponto(2),
        ])
        registro = self.executar(fonte)
        negacoes = self.por_regra(registro, "negacao_nao")
        self.assertEqual([o["alcance"]["token_ids"] for o in negacoes],
                         [ids(fonte, [1, 2]), ids(fonte, [5, 6])])

    def test_modal_verbal_manual_filtra_citacao_sem_vincular_negacao_citada(self):
        for auxiliar in (False, True):
            with self.subTest(auxiliar=auxiliar):
                fonte = construir_analise(texto='Ela pode dizer "não saio".', linhas=[
                    token("Ela", "ela", "PRON", 2 if auxiliar else 1, "nsubj"),
                    verbo("pode", "poder", 2 if auxiliar else 1,
                          "aux" if auxiliar else "ROOT", pos="AUX" if auxiliar else "VERB"),
                    infinitivo("dizer", "dizer", 2 if auxiliar else 1,
                               "ROOT" if auxiliar else "xcomp"),
                    ponto(5, '"'), token("não", "não", "ADV", 5, "advmod"),
                    verbo("saio", "sair", 2, "ccomp"), ponto(5, '"'),
                    ponto(2 if auxiliar else 1),
                ])
                registro = self.executar(fonte)
                modal, = self.por_regra(registro, "modalidade_verbal")
                esperado = [0, 2, 7] if auxiliar else [2]
                self.assertEqual(modal["alcance"]["token_ids"], ids(fonte, esperado))
                self.assertFalse(any(v["tipo"] == "negacao_no_conteudo" for v in modal["vinculos"]))
                self.assertFalse(any("contém negação" in a for a in modal["ambiguidades"]))

    def test_modal_copular_manual_filtra_citacao(self):
        fonte = construir_analise(texto='É necessário dizer "não saio".', linhas=[
            verbo("É", "ser", 1, "cop", pos="AUX"),
            token("necessário", "necessário", "ADJ", 1, "ROOT"),
            infinitivo("dizer", "dizer", 1), ponto(5, '"'),
            token("não", "não", "ADV", 5, "advmod"),
            verbo("saio", "sair", 2, "ccomp"), ponto(5, '"'), ponto(1),
        ])
        registro = self.executar(fonte)
        modal, = self.por_regra(registro, "modalidade_lexical")
        self.assertEqual(modal["alcance"]["token_ids"], ids(fonte, [2]))
        self.assertFalse(any(v["tipo"] == "negacao_no_conteudo" for v in modal["vinculos"]))

    def test_mapa_original_permanece_equivalente_com_crlf_opcional(self):
        texto = 'Não saio "não".\r\n\r\nEla pode dizer "não saio".'
        linhas = [
            token("Não", "não", "ADV", 1, "advmod"), verbo("saio", "sair", 1),
            ponto(1, '"'), token("não", "não", "ADV", 1, "advmod"), ponto(1, '"'), ponto(1),
            token("Ela", "ela", "PRON", 7, "nsubj"), verbo("pode", "poder", 7),
            infinitivo("dizer", "dizer", 7), ponto(11, '"'),
            token("não", "não", "ADV", 11, "advmod"), verbo("saio", "sair", 8, "ccomp"),
            ponto(11, '"'), ponto(7),
        ]
        resultados = []
        for normalizar in (False, True):
            fonte = construir_analise(texto=texto, linhas=linhas, normalizar=normalizar)
            registro = self.executar(fonte)
            self.assertEqual(fonte["anotacao"]["segmentacao"]["preparacao"]["original"]["texto"], texto)
            modal, = self.por_regra(registro, "modalidade_verbal")
            trecho, = modal["alcance"]["trechos"]
            self.assertEqual(trecho["texto"], "dizer")
            self.assertEqual(trecho["original"], {"inicio": 28, "fim": 33})
            self.assertEqual(trecho["trabalho"],
                             {"inicio": 26 if normalizar else 28, "fim": 31 if normalizar else 33})
            resultados.append([[t["original"] for t in o["evidencias"]]
                               for o in registro["ocorrencias"]])
        self.assertEqual(resultados[0], resultados[1])

    def test_cadeia_modal_preserva_complementos_xcomp(self):
        fonte = construir_analise(texto="Ela deve poder sair.", linhas=[
            token("Ela", "ela", "PRON", 1, "nsubj"), verbo("deve", "dever", 1),
            infinitivo("poder", "poder", 1), infinitivo("sair", "sair", 2), ponto(1),
        ])
        registro = self.executar(fonte)
        modais = self.por_regra(registro, "modalidade_verbal")
        self.assertEqual([m["alcance"]["token_ids"] for m in modais],
                         [ids(fonte, [2, 3]), ids(fonte, [3])])
        self.assertEqual(len(self.por_regra(registro, "oracao_predicado")), 3)

    def test_contexto_historico_mantem_comportamento_original(self):
        fonte = self.fonte_citacao(pos_aspa="VERB")
        historico = construir_contexto(fonte, versao="1.0.0")
        atual = construir_contexto(fonte)
        self.assertEqual(historico.versao, "1.0.0")
        self.assertEqual(atual.versao, "1.1.0")
        anterior = detectar_regra("oracao_predicado", fonte, contexto=historico)
        novo = detectar_regra("oracao_predicado", fonte, contexto=atual)
        self.assertEqual([o["nucleo_token_ids"] for o in anterior["ocorrencias"]],
                         [ids(fonte, [1]), ids(fonte, [2]), ids(fonte, [4])])
        self.assertEqual([o["nucleo_token_ids"] for o in novo["ocorrencias"]],
                         [ids(fonte, [1]), ids(fonte, [4])])
        with self.assertRaises(ValueError):
            construir_contexto(fonte, versao="inexistente")


if __name__ == "__main__":
    unittest.main()
