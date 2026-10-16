"""Avisos e hierarquia sobre previsões controladas, sem carregar modelos."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from flask import Flask, render_template

from fixtures_regras import construir_analise, ponto, token, verbo
from diagnosticos_gramaticais import diagnosticar_gramatica
from regras_linguisticas import aplicar_regras_linguisticas
from visualizacao_segmentacao import construir_visualizacao_segmentacao


class GrammarDiagnosticsTests(unittest.TestCase):
    def source(self, name="desejo", **options):
        analysis = construir_analise(name, **options)
        annotation = analysis["anotacao"]
        return annotation["segmentacao"], annotation, analysis, aplicar_regras_linguisticas(analysis)

    def period(self, source):
        return construir_visualizacao_segmentacao(*source)["paragraphs"][0]["periods"][0]

    def test_nominal_copular_and_regular_conditional_have_no_invented_defect(self):
        for case in ("nominal", "copula", "condicao", "auxiliar", "coordenacao"):
            with self.subTest(case=case):
                report = diagnosticar_gramatica(*self.source(case))
                self.assertEqual(report["estado"], "sem_avisos_detectados")
                self.assertEqual(report["avisos"], [])

    def test_partial_chain_is_diagnosed_without_mutation_or_inference(self):
        source = self.source()
        before = deepcopy(source)
        self.assertEqual(diagnosticar_gramatica(source[0])["estado"], "pendente")
        report = diagnosticar_gramatica(*source)
        self.assertEqual(source, before)
        self.assertEqual(json.loads(json.dumps(report)), report)
        self.assertEqual(report["origem_ids"]["execucao_id"], source[3]["execucao_id"])
        self.assertEqual(diagnosticar_gramatica(*source[:3])["etapas_pendentes"], ["regras"])

    def test_nonverbal_root_with_finite_verbs_and_lost_words_is_explicit(self):
        rows = [token("Caso", "caso", "SCONJ", 2, "case"), token("a", "o", "DET", 2, "det"),
                token("premissa", "premissa", "DET", 2, "ROOT"),
                verbo("continue", "continuar", 2, "acl"),
                token("orientando", "orientar", "VERB", 3, "xcomp", {"VerbForm": "Ger"}), ponto(2)]
        source = self.source(texto="Caso a premissa continue orientando.", linhas=rows)
        before = deepcopy(source)
        report = diagnosticar_gramatica(*source)
        by_code = {a["codigo"]: a for a in report["avisos"]}
        self.assertIn("raiz_nao_predicativa_com_verbo_finito", by_code)
        self.assertIn("marcador_subordinativo_em_raiz", by_code)
        outside = by_code["tokens_relevantes_fora_das_oracoes"]
        tokens = {t["id"]: t["texto"] for t in source[0]["tokens"]}
        self.assertEqual([tokens[i] for i in outside["token_ids"]], ["Caso", "a", "premissa"])
        self.assertNotIn(".", [tokens[i] for a in report["avisos"] for i in a["token_ids"]])
        self.assertEqual(source, before)
        self.assertTrue(source[3]["validacao"]["pronto_para_etapa_08"])

    def test_root_with_concessive_marker_is_not_displayed_as_certain_main(self):
        rows = [token("Embora", "embora", "SCONJ", 2, "mark"),
                token("ela", "ela", "PRON", 2, "nsubj"), verbo("saia", "sair", 2), ponto(2, ","),
                token("ele", "ele", "PRON", 5, "nsubj"), verbo("fica", "ficar", 2, "ccomp"), ponto(2)]
        source = self.source(texto="Embora ela saia, ele fica.", linhas=rows)
        period = self.period(source)
        first, second = period["clauses"]
        self.assertEqual(first["classification"], "Classificação requer revisão")
        self.assertTrue(first["requires_review"])
        self.assertEqual(second["classification"], "Oração subordinada")
        self.assertEqual(period["grammar_diagnostics"][0]["words"], "Embora, saia")

        application = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        application.add_url_rule("/envios/<int:record_id>/analise", endpoint="segmentation_clause_analysis", build_only=True)
        with application.test_request_context():
            html = render_template("_segmentacao_periodo.html", period_view=period,
                                   segmentation_view=construir_visualizacao_segmentacao(*source),
                                   ruled=source[3], record={"id": 1}, segmented=source[0])
        self.assertIn("Pontos para revisão gramatical", html)
        self.assertIn("Classificação requer revisão", html)
        self.assertIn("Rege a", html)
        self.assertIn("Análise morfológica", html)
        self.assertIn("A ausência de avisos não garante correção gramatical", html)

    def test_subordinate_governing_another_stays_subordinate_in_global_position(self):
        rows = [token("Ela", "ela", "PRON", 1, "nsubj"), verbo("disse", "dizer", 1),
                token("que", "que", "SCONJ", 3, "mark"), verbo("sabe", "saber", 1, "ccomp"),
                token("que", "que", "SCONJ", 5, "mark"), verbo("chegou", "chegar", 3, "ccomp"), ponto(1)]
        source = self.source(texto="Ela disse que sabe que chegou.", linhas=rows)
        main, middle, last = self.period(source)["clauses"]
        self.assertEqual([c["classification"] for c in (main, middle, last)],
                         ["Oração principal", "Oração subordinada", "Oração subordinada"])
        self.assertEqual(middle["hierarchy"]["subordinada_a"], [main["id"]])
        self.assertEqual(middle["hierarchy"]["rege"], [last["id"]])
        self.assertTrue(any(r["label"] == "Rege a" and r["anchor"] == last["anchor"] for r in middle["relations"]))
        self.assertNotIn("principal", middle["classification"])

    def test_coordination_preserves_subordinate_level_and_explicit_shared_parent(self):
        rows = [token("Ela", "ela", "PRON", 1, "nsubj"), verbo("disse", "dizer", 1),
                token("que", "que", "SCONJ", 3, "mark"), verbo("entrou", "entrar", 1, "ccomp"),
                token("e", "e", "CCONJ", 5, "cc"), verbo("saiu", "sair", 3, "conj"), ponto(1)]
        source = self.source(texto="Ela disse que entrou e saiu.", linhas=rows)
        main, first, second = self.period(source)["clauses"]
        self.assertEqual(first["classification"], "Oração subordinada e coordenada")
        self.assertEqual(second["classification"], "Oração subordinada e coordenada")
        self.assertEqual(second["hierarchy"]["subordinada_a"], [main["id"]])
        self.assertTrue(any(r["label"] == "Compartilha subordinação a" and r["anchor"] == main["anchor"]
                            for r in second["relations"]))

    def test_contradictory_cycle_requests_review_without_changing_saved_links(self):
        source = self.source()
        clauses = [o for o in source[3]["ocorrencias"] if o["familia"] == "oracoes"]
        clauses[0]["vinculos"].append({"tipo": "subordinacao", "alvo_tipo": "ocorrencia", "alvo_id": clauses[1]["id"]})
        before = deepcopy(source)
        report = diagnosticar_gramatica(*source)
        conflicts = [a for a in report["avisos"] if a["codigo"] == "hierarquia_oracional_conflitante"]
        self.assertEqual(len(conflicts), 2)
        self.assertTrue(all(c["requires_review"] for c in self.period(source)["clauses"]))
        self.assertEqual(source, before)

    def test_concessive_word_used_nominally_does_not_trigger_marker_warning(self):
        rows = [token("O", "o", "DET", 1, "det"), token("embora", "embora", "NOUN", 4, "nsubj"),
                verbo("é", "ser", 4, "cop", pos="AUX"), token("uma", "um", "DET", 4, "det"),
                token("palavra", "palavra", "NOUN", 4, "ROOT"), ponto(4)]
        report = diagnosticar_gramatica(*self.source(texto="O embora é uma palavra.", linhas=rows))
        self.assertEqual(report["avisos"], [])


if __name__ == "__main__":
    unittest.main()
