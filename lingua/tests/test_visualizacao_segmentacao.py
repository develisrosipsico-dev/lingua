"""Mapa gramatical conferido sobre árvores anotadas manualmente."""

from copy import deepcopy
import unittest

from fixtures_regras import CASOS, construir_analise
from regras_linguisticas import aplicar_regras_linguisticas
from visualizacao_segmentacao import construir_visualizacao_segmentacao


class SegmentationMapTests(unittest.TestCase):
    def source(self, name="desejo", **options):
        analysis = construir_analise(name, **options)
        annotation = analysis["anotacao"]
        return annotation["segmentacao"], annotation, analysis, aplicar_regras_linguisticas(analysis)

    def test_paragraph_period_clause_hierarchy_preserves_crlf_and_source(self):
        source = self.source("repeticao", texto="Ela não saiu.\r\n\r\nEla não saiu.", normalizar=True)
        before = deepcopy(source)
        view = construir_visualizacao_segmentacao(*source)
        self.assertEqual(source, before)
        self.assertEqual(len(view["paragraphs"]), 2)
        self.assertEqual([g["original_text"] for g in view["paragraphs"]], ["Ela não saiu."] * 2)
        self.assertEqual([len(g["periods"]) for g in view["paragraphs"]], [1, 1])
        self.assertEqual(view["clauses_total"], 2)
        for group in view["paragraphs"]:
            for period in group["periods"]:
                self.assertEqual(len(period["clauses"]), 1)
                self.assertTrue(period["clauses"][0]["tokens"])

    def test_discontinuous_clause_does_not_include_the_subordinate_scope(self):
        view = construir_visualizacao_segmentacao(*self.source())
        first, second = view["paragraphs"][0]["periods"][0]["clauses"]
        self.assertEqual(first["fragments"], ["Ela quer", "."])
        self.assertEqual(first["text"], "Ela quer […] .")
        self.assertEqual([t["text"] for t in first["tokens"]], ["Ela", "quer", "."])
        self.assertEqual([t["text"] for t in second["tokens"]], ["sair"])
        self.assertTrue(second["tokens"][0]["head_outside_clause"])
        self.assertEqual(second["tokens"][0]["head_text"], "quer")
        self.assertTrue(second["relations"])

    def test_grammatical_labels_keep_raw_values_and_lemmas(self):
        view = construir_visualizacao_segmentacao(*self.source())
        rows = view["paragraphs"][0]["periods"][0]["clauses"][0]["tokens"]
        self.assertEqual(rows[0]["dependency_label"], "Sujeito")
        self.assertEqual(rows[1]["lemma"], "querer")
        self.assertEqual((rows[1]["pos"], rows[1]["pos_label"]), ("VERB", "Verbo"))
        mood = next(a for a in rows[1]["morphology"] if a["field"] == "Mood")
        self.assertEqual((mood["raw_value"], mood["value"]), ("Ind", "Indicativo"))

    def test_pending_stages_and_nominal_period_have_no_invented_clauses(self):
        seg, annotation, analysis, rules = self.source("nominal")
        for args, expected in (((seg,), "morfologia"), ((seg, annotation), "sintaxe"),
                               ((seg, annotation, analysis), "regras")):
            with self.subTest(expected=expected):
                view = construir_visualizacao_segmentacao(*args)
                self.assertEqual(view["next_stage"], expected)
                self.assertEqual(view["clauses_total"], 0)
        view = construir_visualizacao_segmentacao(seg, annotation, analysis, rules)
        period = view["paragraphs"][0]["periods"][0]
        self.assertEqual(period["clauses"], [])
        self.assertEqual([t["text"] for t in period["tokens"]], ["O", "estudo", "."])

    def test_period_filter_does_not_mix_paragraphs(self):
        source = self.source("repeticao", texto="Ela não saiu.\r\n\r\nEla não saiu.")
        target = source[0]["periodos"][1]
        view = construir_visualizacao_segmentacao(*source, periodo_id=target["id"])
        self.assertEqual(len(view["paragraphs"]), 1)
        self.assertEqual(view["paragraphs"][0]["paragraph"]["ordem"], 1)
        self.assertEqual(view["paragraphs"][0]["periods"][0]["period"], target)


if __name__ == "__main__":
    unittest.main()
