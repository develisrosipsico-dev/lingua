"""Mapa gramatical conferido sobre árvores anotadas manualmente."""

from copy import deepcopy
import unittest

from fixtures_regras import construir_analise, ponto, token, verbo
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

    def test_syntax_colors_follow_the_function_of_the_phrase_without_changing_word_roles(self):
        source = self.source("copula")
        rows = construir_visualizacao_segmentacao(*source)["paragraphs"][0]["periods"][0]["clauses"][0]["tokens"]
        self.assertEqual([t["syntax_tone"] for t in rows], [1, 2, 2, None])
        self.assertEqual([t["dependency_label"] for t in rows], ["Sujeito", "Cópula", "Raiz sintática", "Pontuação"])

    def test_discontinuous_clause_does_not_include_the_subordinate_scope(self):
        view = construir_visualizacao_segmentacao(*self.source())
        first, second = view["paragraphs"][0]["periods"][0]["clauses"]
        self.assertEqual(first["fragments"], ["Ela quer", "."])
        self.assertEqual(first["text"], "Ela quer […] .")
        self.assertEqual("".join(t["reading_prefix"] + t["reading_text"] for t in first["tokens"]), first["text"])
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

    def test_clause_order_follows_the_text_even_when_main_verb_comes_last(self):
        lines = [token("O", "o", "DET", 1, "det"), token("homem", "homem", "NOUN", 4, "nsubj"),
                 token("que", "que", "PRON", 3, "nsubj"),
                 verbo("chegou", "chegar", 1, "acl:relcl", tempo="Past"),
                 verbo("saiu", "sair", 4, tempo="Past"), ponto(4)]
        source = self.source(texto="O homem que chegou saiu.", linhas=lines)
        before = deepcopy(source)
        clauses = construir_visualizacao_segmentacao(*source)["paragraphs"][0]["periods"][0]["clauses"]
        self.assertEqual([c["nucleus_text"] for c in clauses], ["saiu", "chegou"])
        self.assertEqual([c["classification"] for c in clauses], ["Oração principal", "Oração subordinada"])
        self.assertEqual([t["syntax_tone"] for t in clauses[0]["tokens"]], [1, 1, 2, None])
        self.assertEqual([c["number"] for c in clauses], [1, 2])
        self.assertEqual(clauses[1]["relations"][0]["anchor"], clauses[0]["anchor"])
        self.assertEqual(source, before)

    def test_subordinate_before_main_and_coordinated_clauses_are_classified(self):
        for name, expected in (("condicao", ["Oração subordinada", "Oração principal"]),
                               ("coordenacao", ["Oração coordenada", "Oração coordenada"]),
                               ("auxiliar", ["Oração absoluta"])):
            with self.subTest(name=name):
                clauses = construir_visualizacao_segmentacao(*self.source(name))["paragraphs"][0]["periods"][0]["clauses"]
                self.assertEqual([c["classification"] for c in clauses], expected)

    def test_colored_periods_reconstruct_literal_paragraph_including_spaces(self):
        source = self.source("repeticao", texto="Ela não saiu.\t  Ela não saiu.")
        group = construir_visualizacao_segmentacao(*source)["paragraphs"][0]
        self.assertEqual("".join(p["text"] for p in group["original_parts"]), group["original_text"])
        colored = [p for p in group["original_parts"] if p["period_number"]]
        self.assertEqual([p["period_number"] for p in colored], [1, 2])
        self.assertNotEqual(colored[0]["tone"], colored[1]["tone"])
        self.assertEqual([p["tone"] for p in colored], [p["tone"] for p in group["periods"]])

    def test_clause_highlights_preserve_full_period_and_match_clause_colors(self):
        source = self.source()
        period = construir_visualizacao_segmentacao(*source)["paragraphs"][0]["periods"][0]
        self.assertEqual("".join(p["text"] for p in period["clause_parts"]), "Ela quer sair.")
        clauses = {c["id"]: c for c in period["clauses"]}
        for part in period["clause_parts"]:
            if part["clause_id"]:
                clause = clauses[part["clause_id"]]
                self.assertEqual((part["tone"], part["anchor"]), (clause["tone"], clause["anchor"]))
        colored = [p for p in period["clause_parts"] if p["clause_id"]]
        self.assertEqual([p["text"] for p in colored], ["Ela quer", "sair", "."])
        self.assertEqual(colored[0]["tone"], colored[2]["tone"])
        self.assertNotEqual(colored[0]["tone"], colored[1]["tone"])
        pending = construir_visualizacao_segmentacao(source[0])["paragraphs"][0]["periods"][0]
        self.assertEqual(pending["clause_parts"][0]["text"], "Ela quer sair.")
        self.assertIsNone(pending["clause_parts"][0]["clause_id"])

    def test_clickable_word_reading_preserves_spaces_and_distinct_occurrences(self):
        source = self.source("repeticao", texto="Ela\t não saiu.  Ela não saiu.")
        periods = construir_visualizacao_segmentacao(*source)["paragraphs"][0]["periods"]
        readings = ["".join(t["reading_prefix"] + t["reading_text"] for t in p["tokens"]) for p in periods]
        self.assertEqual(readings, ["Ela\t não saiu.", "Ela não saiu."])
        self.assertNotEqual(periods[0]["tokens"][0]["id"], periods[1]["tokens"][0]["id"])


if __name__ == "__main__":
    unittest.main()
