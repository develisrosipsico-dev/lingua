"""Subtipos conferidos com árvores manuais e preservação da evidência literal."""

from copy import deepcopy
from importlib.util import find_spec
import json
import unittest
from unittest.mock import patch

from anotacao import anotar_segmentacao
from classificacao_oracoes import classificar_oracoes
from fixtures_regras import construir_analise, ids, ponto, token, verbo
from preparacao import preparar_texto
from regras_linguisticas import aplicar_regras_linguisticas
from segmentacao import segmentar_preparacao
from sintaxe_entidades import analisar_sintaxe_entidades


TEXTO_USUARIO = (
    "Eu ainda guardo no bolso do casaco as meias de lã que minha avó tricotava "
    "para os netos, embora nunca tivesse um neto mulher."
)


def fonte_usuario():
    """A vírgula abre a concessiva; não delimita o início da relativa."""
    return construir_analise(texto=TEXTO_USUARIO, linhas=[
        token("Eu", "eu", "PRON", 2, "nsubj"),
        token("ainda", "ainda", "ADV", 2, "advmod"),
        verbo("guardo", "guardar", 2),
        token("no", "em o", "ADP", 4, "case"),
        token("bolso", "bolso", "NOUN", 2, "obl"),
        token("do", "de o", "ADP", 6, "case"),
        token("casaco", "casaco", "NOUN", 4, "nmod"),
        token("as", "o", "DET", 8, "det"),
        token("meias", "meia", "NOUN", 2, "obj"),
        token("de", "de", "ADP", 10, "case"),
        token("lã", "lã", "NOUN", 8, "nmod"),
        token("que", "que", "PRON", 14, "obj", {"PronType": "Rel"}),
        token("minha", "meu", "DET", 13, "det"),
        token("avó", "avó", "NOUN", 14, "nsubj"),
        verbo("tricotava", "tricotar", 8, "acl:relcl", tempo="Imp"),
        token("para", "para", "ADP", 17, "case"),
        token("os", "o", "DET", 17, "det"),
        token("netos", "neto", "NOUN", 14, "obl"),
        ponto(21, ","),
        token("embora", "embora", "SCONJ", 21, "mark"),
        token("nunca", "nunca", "ADV", 21, "advmod"),
        token("tivesse", "ter", "VERB", 14, "advcl",
              {"VerbForm": "Fin", "Mood": "Sub", "Tense": "Imp"}),
        token("um", "um", "DET", 23, "det"),
        token("neto", "neto", "NOUN", 21, "obj"),
        token("mulher", "mulher", "NOUN", 23, "nmod"),
        ponto(2),
    ])


class ClassificacaoOracoesTests(unittest.TestCase):
    def source(self, name="desejo", *, analysis=None, **options):
        analysis = analysis if analysis is not None else construir_analise(name, **options)
        annotation = analysis["anotacao"]
        return annotation["segmentacao"], annotation, analysis, aplicar_regras_linguisticas(analysis)

    def classify(self, source):
        before = deepcopy(source)
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("Sem reanotação")), \
             patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("Sem reanálise")):
            result = classificar_oracoes(*source)
        self.assertEqual(source, before)
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False)), result)
        return result

    def predicate(self, result, source, word):
        ident = next(t["id"] for t in source[0]["tokens"] if t["texto"] == word)
        return next(o for o in result["oracoes"] if ident in o["nucleo_token_ids"])

    def test_exemplo_usuario_distingue_restritiva_de_concessiva_e_antecedente(self):
        source = self.source(analysis=fonte_usuario())
        result = self.classify(source)
        relative = self.predicate(result, source, "tricotava")
        concession = self.predicate(result, source, "tivesse")
        self.assertEqual((relative["classe"], relative["subtipo"], relative["estado"]),
                         ("adjetiva", "restritiva", "classificada"))
        self.assertEqual(relative["rotulo"], "Oração subordinada adjetiva restritiva")
        self.assertEqual(relative["referencia"]["token_id"], ids(source[2], [8])[0])
        self.assertEqual(relative["referencia"]["texto"], "meias")
        self.assertEqual(relative["marcador_token_ids"], ids(source[2], [11]))
        self.assertEqual((concession["classe"], concession["subtipo"], concession["estado"]),
                         ("adverbial", "concessiva", "classificada"))
        self.assertEqual(concession["rotulo"], "Oração subordinada adverbial concessiva")
        self.assertEqual(concession["marcador_token_ids"], ids(source[2], [19]))
        self.assertIn(relative["oracao_id"], concession["hierarquia"]["subordinada_a"])

    def test_relativa_explicativa_delimitada_por_duas_virgulas(self):
        source = self.source(texto="Eu guardo as meias, que minha avó tricotava, no casaco.", linhas=[
            token("Eu", "eu", "PRON", 1, "nsubj"), verbo("guardo", "guardar", 1),
            token("as", "o", "DET", 3, "det"), token("meias", "meia", "NOUN", 1, "obj"),
            ponto(8, ","), token("que", "que", "PRON", 8, "obj", {"PronType": "Rel"}),
            token("minha", "meu", "DET", 7, "det"), token("avó", "avó", "NOUN", 8, "nsubj"),
            verbo("tricotava", "tricotar", 3, "acl:relcl", tempo="Imp"), ponto(8, ","),
            token("no", "em o", "ADP", 11, "case"), token("casaco", "casaco", "NOUN", 1, "obl"), ponto(1),
        ])
        relative = self.predicate(self.classify(source), source, "tricotava")
        self.assertEqual((relative["classe"], relative["subtipo"]), ("adjetiva", "explicativa"))
        self.assertEqual(relative["rotulo"], "Oração subordinada adjetiva explicativa")

    def test_virgulas_internas_na_relativa_nao_a_tornam_explicativa(self):
        source = self.source(texto="Eu guardo as meias que minha avó, muito paciente, tricotava.", linhas=[
            token("Eu", "eu", "PRON", 1, "nsubj"), verbo("guardo", "guardar", 1),
            token("as", "o", "DET", 3, "det"), token("meias", "meia", "NOUN", 1, "obj"),
            token("que", "que", "PRON", 11, "obj", {"PronType": "Rel"}),
            token("minha", "meu", "DET", 6, "det"), token("avó", "avó", "NOUN", 11, "nsubj"),
            ponto(9, ","), token("muito", "muito", "ADV", 9, "advmod"),
            token("paciente", "paciente", "ADJ", 6, "amod"), ponto(9, ","),
            verbo("tricotava", "tricotar", 3, "acl:relcl", tempo="Imp"), ponto(1),
        ])
        relative = self.predicate(self.classify(source), source, "tricotava")
        self.assertEqual(relative["subtipo"], "restritiva")

    def test_que_integrante_nao_se_transforma_em_pronome_relativo(self):
        source = self.source("saber_proposicao")
        subordinate = self.predicate(self.classify(source), source, "saiu")
        self.assertNotEqual(subordinate["classe"], "adjetiva")
        self.assertNotIn(subordinate["subtipo"], {"restritiva", "explicativa"})
        self.assertEqual(subordinate["referencia"]["texto"], "Sei")

    def test_embora_em_raiz_conflitante_indica_revisao_sem_inventar_principal(self):
        source = self.source(texto="Embora ela saia, ele fica.", linhas=[
            token("Embora", "embora", "SCONJ", 2, "mark"),
            token("ela", "ela", "PRON", 2, "nsubj"), verbo("saia", "sair", 2), ponto(2, ","),
            token("ele", "ele", "PRON", 5, "nsubj"), verbo("fica", "ficar", 2, "ccomp"), ponto(2),
        ])
        concessive = self.predicate(self.classify(source), source, "saia")
        self.assertEqual(concessive["estado"], "requer_revisao")
        self.assertIsNone(concessive["rotulo"])

    def test_embora_adverbial_ou_nominal_nao_e_concessiva_so_por_sua_grafia(self):
        cases = [
            ("Ela foi embora.", [token("Ela", "ela", "PRON", 1, "nsubj"),
                                  verbo("foi", "ir", 1, tempo="Past"),
                                  token("embora", "embora", "ADV", 1, "advmod"), ponto(1)]),
            ("O embora é uma palavra.", [token("O", "o", "DET", 1, "det"),
                                         token("embora", "embora", "NOUN", 4, "nsubj"),
                                         verbo("é", "ser", 4, "cop", pos="AUX"),
                                         token("uma", "um", "DET", 4, "det"),
                                         token("palavra", "palavra", "NOUN", 4, "ROOT"), ponto(4)]),
        ]
        for text, rows in cases:
            with self.subTest(text=text):
                result = self.classify(self.source(texto=text, linhas=rows))
                self.assertTrue(all(o["subtipo"] != "concessiva" for o in result["oracoes"]))

    def test_raiz_nominal_sem_relativo_nao_autoriza_subtipo_adjetivo(self):
        source = self.source(texto="A premissa continue orientando.", linhas=[
            token("A", "o", "DET", 1, "det"), token("premissa", "premissa", "DET", 1, "ROOT"),
            verbo("continue", "continuar", 1, "acl"),
            token("orientando", "orientar", "VERB", 2, "xcomp", {"VerbForm": "Ger"}), ponto(1),
        ])
        result = self.classify(source)
        self.assertTrue(result["oracoes"])
        self.assertTrue(all(o["subtipo"] is None for o in result["oracoes"]))
        self.assertTrue(all(o["estado"] != "classificada" for o in result["oracoes"]))

    def test_ciclo_oracional_exige_revisao_sem_reescrever_os_vinculos(self):
        source = self.source()
        clauses = [o for o in source[3]["ocorrencias"] if o["familia"] == "oracoes"]
        clauses[0]["vinculos"].append({"tipo": "subordinacao", "alvo_tipo": "ocorrencia", "alvo_id": clauses[1]["id"]})
        result = self.classify(source)
        self.assertTrue(all(o["estado"] == "requer_revisao" for o in result["oracoes"]))
        self.assertTrue(all(o["subtipo"] is None for o in result["oracoes"]))

    def test_evidencias_preservam_ids_offsets_literais_e_origem_sem_inferencia(self):
        source = self.source(analysis=fonte_usuario())
        before = deepcopy(source)
        result = self.classify(source)
        self.assertEqual(result["schema_version"], "lingua.classificacao_oracoes.v1")
        self.assertEqual(result["estado"], "disponivel")
        self.assertEqual(result["origem_ids"]["execucao_id"], source[3]["execucao_id"])
        by_id = {t["id"]: t for t in source[0]["tokens"]}
        original = source[0]["preparacao"]["original"]["texto"]
        work = source[0]["preparacao"]["trabalho"]["texto"]
        for clause in result["oracoes"]:
            self.assertTrue(clause["explicacao"])
            if clause["estado"] == "classificada":
                self.assertTrue(clause["evidencias"])
            for evidence in clause["evidencias"] + ([clause["referencia"]] if clause["referencia"] else []):
                literal = by_id[evidence["token_id"]]
                self.assertEqual(evidence["original"], literal["original"])
                self.assertEqual(evidence["trabalho"], literal["trabalho"])
                self.assertEqual(evidence["texto"], original[evidence["original"]["inicio"]:evidence["original"]["fim"]])
                self.assertEqual(evidence["texto"], work[evidence["trabalho"]["inicio"]:evidence["trabalho"]["fim"]])
        relative = self.predicate(result, source, "tricotava")
        relative["referencia"]["original"]["inicio"] = -1
        relative["evidencias"][0]["trabalho"]["fim"] = -1
        self.assertEqual(source, before)

    def test_cadeia_incompleta_ou_periodo_nominal_nao_inventa_oracoes(self):
        source = self.source("nominal")
        for size in range(1, 4):
            with self.subTest(stages=size):
                result = classificar_oracoes(*source[:size])
                self.assertEqual(result["estado"], "pendente")
                self.assertEqual(result["oracoes"], [])
        self.assertEqual(self.classify(source)["oracoes"], [])


class ClassificacaoModeloRealTests(unittest.TestCase):
    """A árvore real deve ser preservada; os casos manuais são o gabarito."""

    source = ClassificacaoOracoesTests.source
    classify = ClassificacaoOracoesTests.classify
    predicate = ClassificacaoOracoesTests.predicate

    def check_model(self, name):
        prep = preparar_texto(TEXTO_USUARIO, "documento-subtipos-real")
        seg = segmentar_preparacao(prep)
        annotation = anotar_segmentacao(seg, modelo=name)
        analysis = analisar_sintaxe_entidades(annotation)
        source = self.source(analysis=analysis)
        result = self.classify(source)
        relative = self.predicate(result, source, "tricotava")
        concessive = self.predicate(result, source, "tivesse")
        self.assertEqual((relative["classe"], relative["subtipo"]), ("adjetiva", "restritiva"))
        predicted = next(s for s in analysis["sintaxe"] if s["texto"] == "tricotava")
        self.assertEqual(relative["referencia"]["token_id"], predicted["cabeca_token_id"])
        self.assertEqual((concessive["classe"], concessive["subtipo"]), ("adverbial", "concessiva"))

    @unittest.skipUnless(find_spec("pt_core_news_sm"), "Modelo português pequeno não instalado")
    def test_modelo_pequeno_real_exemplo_usuario(self):
        self.check_model("pt_core_news_sm")

    @unittest.skipUnless(find_spec("pt_core_news_md"), "Modelo português médio opcional não instalado")
    def test_modelo_medio_real_exemplo_usuario(self):
        self.check_model("pt_core_news_md")


if __name__ == "__main__":
    unittest.main()
