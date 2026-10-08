import copy
import json
import unittest

import anotacao
from anotacao import anotar_segmentacao
from avaliacao_morfologia import REFERENCIA, avaliar_referencia, comparar_anotacoes, formatar_markdown, validar_referencia
from preparacao import preparar_texto
from segmentacao import segmentar_preparacao


class MorphologyEvaluationTests(unittest.TestCase):
    def referencia(self):
        return json.loads(REFERENCIA.read_text(encoding="utf-8"))

    def test_referencia_tem_cinco_frases_e_distingue_guarda(self):
        referencia = self.referencia()
        validar_referencia(referencia)
        self.assertEqual(len(referencia["casos"]),5)
        self.assertEqual(sum(len(c["alvos"]) for c in referencia["casos"]),15)
        guardas = [(a["lema"],a["pos"]) for c in referencia["casos"] for a in c["alvos"] if a["texto"] == "guarda"]
        self.assertEqual(guardas,[("guardar","VERB"),("guarda","NOUN")])

    def test_rejeita_limites_adulterados(self):
        referencia=self.referencia()
        referencia["casos"][0]["alvos"][0]["original"]["fim"]=999
        with self.assertRaises(ValueError):
            validar_referencia(referencia)

    def test_avaliacao_real_com_contagens_calculadas_sem_exigir_acerto_total(self):
        referencia=self.referencia()
        snapshot=copy.deepcopy(referencia)
        resultado=avaliar_referencia(referencia)
        self.assertEqual(referencia,snapshot)
        linhas=[r for c in resultado["casos"] for r in c["resultados"]]
        self.assertEqual(resultado["resumo"]["tokens_avaliados"],len(linhas))
        self.assertEqual(resultado["resumo"]["acertos_lema"],sum(r["acerto_lema"] for r in linhas))
        self.assertEqual(resultado["resumo"]["acertos_pos"],sum(r["acerto_pos"] for r in linhas))
        self.assertIn("Lemas:",formatar_markdown(resultado))

    def test_comparador_reporta_divergencia_sem_modificar_previsao(self):
        caso=self.referencia()["casos"][0]
        registro=anotar_segmentacao(segmentar_preparacao(preparar_texto(caso["texto"],1)))
        # Perturbação deliberada somente no teste para conferir o comparador.
        registro["anotacoes_tokens"][1]["lema"]="divergencia_controlada"
        registro["vocabulario"]=anotacao._vocabulario(registro["anotacoes_tokens"],registro["anotacao_id"])
        snapshot=copy.deepcopy(registro)
        resultado=comparar_anotacoes(registro,caso)
        self.assertFalse(resultado[1]["acerto_lema"])
        self.assertEqual(resultado[1]["obtido"]["lema"],"divergencia_controlada")
        self.assertEqual(registro,snapshot)

    def test_limite_incompativel_do_alvo_nao_e_alinhado_silenciosamente(self):
        caso=copy.deepcopy(self.referencia()["casos"][0])
        registro=anotar_segmentacao(segmentar_preparacao(preparar_texto(caso["texto"],1)))
        caso["alvos"][0]["original"]["fim"]=1
        with self.assertRaises(ValueError):
            comparar_anotacoes(registro,caso)


if __name__ == "__main__":
    unittest.main()
