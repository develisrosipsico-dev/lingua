"""Referências manuais de locuções e reprodução exata das versões históricas."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from fixtures_regras import INSTANTE, construir_analise, ids, infinitivo, ponto, token, verbo
from regras_linguisticas import aplicar_regras_linguisticas, validar_regras_linguisticas


def fonte_continuar(*, dependencia="xcomp", morfologia=None, sujeito_proprio=False, auxiliar=False):
    linhas = [token("Ela", "ela", "PRON", 2 if auxiliar else 1, "nsubj"),
              verbo("continua", "continuar", 2 if auxiliar else 1,
                    "aux" if auxiliar else "ROOT", pos="AUX" if auxiliar else "VERB"),
              token("orientando", "orientar", "VERB", 2 if auxiliar else 1,
                    "ROOT" if auxiliar else dependencia,
                    {"VerbForm": "Ger"} if morfologia is None else morfologia),
              token("a", "o", "DET", 4, "det"),
              token("equipe", "equipe", "NOUN", 2, "obj"), ponto(2 if auxiliar else 1)]
    if sujeito_proprio:
        linhas[0] = token("Ela", "ela", "PRON", 2, "nsubj")
    return construir_analise(texto="Ela continua orientando a equipe.", linhas=linhas)


def fonte_passar(*, elo=True):
    return construir_analise(texto="Ela passou a reconhecer o problema.", linhas=[
        token("Ela", "ela", "PRON", 1, "nsubj"), verbo("passou", "passar", 1, tempo="Past"),
        token("a", "a", "ADP", 3 if elo else 1, "mark"), infinitivo("reconhecer", "reconhecer", 1),
        token("o", "o", "DET", 5, "det"), token("problema", "problema", "NOUN", 3, "obj"), ponto(1),
    ])


class AgrupamentosAspectuaisTests(unittest.TestCase):
    def executar(self, fonte, **opcoes):
        snapshot = deepcopy(fonte)
        registro = aplicar_regras_linguisticas(
            fonte, execucao_id="execucao-fixture-07", registrado_em=INSTANTE, **opcoes)
        self.assertEqual(fonte, snapshot)
        self.assertEqual(registro["analise"], snapshot)
        self.assertTrue(validar_regras_linguisticas(registro)["pronto_para_etapa_08"])
        return registro, [o for o in registro["ocorrencias"] if o["familia"] == "oracoes"]

    def test_passar_a_infinitivo_preserva_componentes_elo_e_anotacoes(self):
        fonte = fonte_passar()
        registro, oracoes = self.executar(fonte)
        self.assertEqual(registro["processamento"]["modulo"]["versao"], "1.2.0")
        self.assertEqual(len(oracoes), 1)
        oracao = oracoes[0]
        self.assertEqual(oracao["nucleo_token_ids"], ids(fonte, [1, 3]))
        self.assertEqual(oracao["marcador_token_ids"], ids(fonte, [1, 2, 3]))
        self.assertEqual(oracao["alcance"]["token_ids"], ids(fonte, range(7)))
        self.assertEqual([e["texto"] for e in oracao["evidencias"]], ["passou a reconhecer"])
        self.assertEqual([e["dependencia"] for e in oracao["anotacoes_utilizadas"]], ["ROOT", "mark", "xcomp"])
        self.assertIn("passar a + infinitivo", oracao["explicacao"])
        self.assertIn({"tipo": "elo_aspectual", "alvo_tipo": "token", "alvo_id": ids(fonte, [2])[0]}, oracao["vinculos"])

    def test_continuar_gerundio_xcomp_e_aux_recebem_um_grupo(self):
        for auxiliar in (False, True):
            with self.subTest(auxiliar=auxiliar):
                fonte = fonte_continuar(auxiliar=auxiliar)
                _, oracoes = self.executar(fonte)
                self.assertEqual(len(oracoes), 1)
                self.assertEqual(oracoes[0]["nucleo_token_ids"], ids(fonte, [1, 2]))
                self.assertEqual(oracoes[0]["alcance"]["token_ids"], ids(fonte, range(6)))
                self.assertIn("continuar + gerúndio", oracoes[0]["explicacao"])

    def test_cadeia_aspectual_inteira_e_representada_sem_nucleos_duplicados(self):
        fonte = construir_analise(texto="Ela passa a continuar orientando a equipe.", linhas=[
            token("Ela", "ela", "PRON", 1, "nsubj"), verbo("passa", "passar", 1),
            token("a", "a", "ADP", 3, "mark"), infinitivo("continuar", "continuar", 1),
            token("orientando", "orientar", "VERB", 3, "xcomp", {"VerbForm": "Ger"}),
            token("a", "o", "DET", 6, "det"), token("equipe", "equipe", "NOUN", 4, "obj"), ponto(1),
        ])
        _, oracoes = self.executar(fonte)
        self.assertEqual(len(oracoes), 1)
        self.assertEqual(oracoes[0]["nucleo_token_ids"], ids(fonte, [1, 3, 4]))
        self.assertEqual(oracoes[0]["marcador_token_ids"], ids(fonte, [1, 2, 3, 4]))
        self.assertEqual(oracoes[0]["alcance"]["token_ids"], ids(fonte, range(8)))

    def test_negacao_no_componente_lexical_vincula_o_grupo_e_preserva_a_cabeca(self):
        fonte = construir_analise(texto="Ela continua não orientando a equipe.", linhas=[
            token("Ela", "ela", "PRON", 1, "nsubj"), verbo("continua", "continuar", 1),
            token("não", "não", "ADV", 3, "advmod"),
            token("orientando", "orientar", "VERB", 1, "xcomp", {"VerbForm": "Ger"}),
            token("a", "o", "DET", 5, "det"), token("equipe", "equipe", "NOUN", 3, "obj"), ponto(1),
        ])
        registro, oracoes = self.executar(fonte)
        negacao, = [o for o in registro["ocorrencias"] if o["regra"]["id"] == "negacao_nao"]
        self.assertEqual(negacao["alcance"]["token_ids"], oracoes[0]["alcance"]["token_ids"])
        self.assertEqual(negacao["anotacoes_utilizadas"][1]["cabeca_token_id"], ids(fonte, [3])[0])
        self.assertTrue(any(v["alvo_tipo"] == "ocorrencia" and v["alvo_id"] == oracoes[0]["id"]
                            for v in negacao["vinculos"]))

    def test_complementos_nao_aspectuais_mantem_duas_oracoes(self):
        fontes = [construir_analise("desejo"),
                  construir_analise(texto="Ela insiste em declarar.", linhas=[
                      token("Ela", "ela", "PRON", 1, "nsubj"), verbo("insiste", "insistir", 1),
                      token("em", "em", "ADP", 3, "mark"), infinitivo("declarar", "declarar", 1), ponto(1)]),
                  fonte_continuar(dependencia="ccomp"), fonte_continuar(dependencia="conj"),
                  fonte_continuar(morfologia={}), fonte_continuar(morfologia={"VerbForm": "Inf"}),
                  fonte_continuar(sujeito_proprio=True), fonte_passar(elo=False)]
        for numero, fonte in enumerate(fontes):
            with self.subTest(caso=numero):
                _, oracoes = self.executar(fonte)
                self.assertEqual(len(oracoes), 2)
                self.assertFalse(any(v["tipo"] == "componente_aspectual"
                                     for o in oracoes for v in o["vinculos"]))

    def test_passou_a_ponte_nao_fabrica_locucao(self):
        fonte = construir_analise(texto="Ela passou a ponte.", linhas=[
            token("Ela", "ela", "PRON", 1, "nsubj"), verbo("passou", "passar", 1, tempo="Past"),
            token("a", "o", "DET", 3, "det"), token("ponte", "ponte", "NOUN", 1, "obj"), ponto(1)])
        _, oracoes = self.executar(fonte)
        self.assertEqual(len(oracoes), 1)
        self.assertEqual(oracoes[0]["nucleo_token_ids"], ids(fonte, [1]))
        self.assertNotIn("componente_aspectual", [v["tipo"] for v in oracoes[0]["vinculos"]])

    def test_versoes_anteriores_nao_recebem_a_fusao_retroativamente(self):
        for versao in ("1.0.0", "1.1.0"):
            with self.subTest(versao=versao):
                _, oracoes = self.executar(fonte_passar(), versao=versao)
                self.assertEqual(len(oracoes), 2)


class HistoricoExatoTests(unittest.TestCase):
    # Capturados antes da revisão1.2, sobre o JSON inteiro canônico: inclui
    # catálogo, versões, alcances, evidências, vínculos e validações.
    HASHES = {
        "1.0.0": {
            "auxiliar": "1ee962ebbd883929dd330a603f8a78ea5e7d6c3d34df9b51901e3c37ed11e34d",
            "citacao": "a71f84ea09df80c847948f43b0c8c8505519e94c997a94719c1594a6d0858d72",
            "desejo": "4f75f64da8459ba78bbcce44544e90e54a42b2e1cb8808c6163ded068a14cd0f",
            "nao_so": "b0521e9897b810eb6845727e3ee9086f3468e47b62b6e1c23459d276cd518249",
            "unicode_crlf": "5b1e5877d7170b3938e5026af30b7650948bd319cdce90e24fd1dd05d3e1623f",
        },
        "1.1.0": {
            "auxiliar": "f05f896346486544360215c304dd5c8083e27cc2b247122606589bcaa549a853",
            "citacao": "c88da11bab003a8af2edb809c9546dde4f491f0402075164f82772e9de189ace",
            "desejo": "525742a96ce38d39fd52abe12e5b1f8363500aba37cd50c456284c320eacbea2",
            "nao_so": "307b261e7eff97cf981e21aa218f67294a3eb2cd719b23d120c6088b7946a52e",
            "unicode_crlf": "100eee5bacd64c92f52c6a313fe742f396ebec2203a11c5baeaaa1214845538e",
        },
    }

    def test_registros_completos_das_versoes_anteriores_continuam_identicos(self):
        for versao, casos in self.HASHES.items():
            for nome, esperado in casos.items():
                with self.subTest(versao=versao, nome=nome):
                    fonte = construir_analise(nome)
                    # A referência manual declara o tokenizador usado na
                    # captura; a versão instalada no computador não participa
                    # do hash de compatibilidade do mecanismo de regras.
                    segmentacao = fonte["anotacao"]["segmentacao"]
                    segmentacao["processamento"]["versao"] = "3.8.16"
                    segmentacao["processamento"]["tokenizador"]["versao"] = "3.8.16"
                    for etapa in (fonte, fonte["anotacao"]):
                        etapa["processamento"]["configuracoes"]["tokenizador"]["versao_origem"] = "3.8.16"
                    registro = aplicar_regras_linguisticas(
                        fonte, versao=versao,
                        execucao_id="execucao-fixture-07", registrado_em=INSTANTE)
                    canonico = json.dumps(registro, ensure_ascii=False, allow_nan=False,
                                          sort_keys=True, separators=(",", ":"))
                    self.assertEqual(hashlib.sha256(canonico.encode()).hexdigest(), esperado)
                    self.assertTrue(validar_regras_linguisticas(registro)["pronto_para_etapa_08"])

    def test_arquivos_historicos_publicados_revalidam_sem_mudar_os_bytes(self):
        exemplos = Path(__file__).resolve().parents[1] / "examples"
        for nome in ("regras_linguisticas_literal.json", "regras_linguisticas_normalizada.json", "regras_integradas.json"):
            dados = (exemplos / nome).read_bytes()
            registro = json.loads(dados)
            snapshot = deepcopy(registro)
            with self.subTest(nome=nome):
                self.assertTrue(validar_regras_linguisticas(registro)["pronto_para_etapa_08"])
                self.assertEqual(registro, snapshot)
                self.assertEqual((exemplos / nome).read_bytes(), dados)


if __name__ == "__main__":
    unittest.main()
