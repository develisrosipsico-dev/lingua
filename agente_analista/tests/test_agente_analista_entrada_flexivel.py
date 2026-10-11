"""Relatos com quantidade variável de parágrafos conservam limites e origem."""

import unittest

from agente_analista.entrada import ErroEntrada, validar_relato


class EntradaFlexivelTests(unittest.TestCase):
    def test_um_paragrafo_com_quebra_simples_preserva_texto_original(self):
        texto = " \tLembrei da conversa 😀.\r\nAinda não respondi.  "
        relato = validar_relato(texto)
        self.assertEqual(relato["texto"], texto)
        self.assertEqual(relato["palavras"], len(texto.split()))
        self.assertEqual(len(relato["paragrafos"]), 1)
        paragrafo = relato["paragrafos"][0]
        self.assertEqual(paragrafo["id"], "P1")
        self.assertEqual(paragrafo["inicio"], 2)
        self.assertEqual(paragrafo["texto"], "Lembrei da conversa 😀.\r\nAinda não respondi.")
        self.assertEqual(texto[paragrafo["inicio"]:paragrafo["fim"]], paragrafo["texto"])

    def test_varios_paragrafos_tem_ids_sequenciais_e_offsets_unicode(self):
        texto = "  Não voltei 😀.\r\n \t\r\nRecordei a carta.\n\n\n  Não respondi.\r\r Esperei.  "
        relato = validar_relato(texto)
        self.assertEqual(relato["texto"], texto)
        self.assertEqual([p["id"] for p in relato["paragrafos"]], ["P1", "P2", "P3", "P4"])
        self.assertEqual([p["texto"] for p in relato["paragrafos"]],
                         ["Não voltei 😀.", "Recordei a carta.", "Não respondi.", "Esperei."])
        fim_anterior = 0
        for paragrafo in relato["paragrafos"]:
            self.assertGreaterEqual(paragrafo["inicio"], fim_anterior)
            self.assertEqual(texto[paragrafo["inicio"]:paragrafo["fim"]], paragrafo["texto"])
            fim_anterior = paragrafo["fim"]

    def test_limite_400_vale_para_um_ou_varios_paragrafos(self):
        for separador in (" ", "\n\n"):
            texto = separador.join(["lembrança"] * 400)
            with self.subTest(separador=separador):
                self.assertEqual(validar_relato(texto)["palavras"], 400)
                with self.assertRaisesRegex(ErroEntrada, "401"):
                    validar_relato(texto + " outra")

    def test_vazio_permanece_invalido(self):
        for texto in ("", " \t\r\n\r\n ", "\u00a0\u2003"):
            with self.subTest(texto=texto), self.assertRaisesRegex(ErroEntrada, "Cole"):
                validar_relato(texto)

    def test_contagem_de_palavras_usa_whitespace_unicode(self):
        texto = "não\u00a0sei\u2003por\u2028quê"
        self.assertEqual(validar_relato(texto)["palavras"], 4)
        self.assertEqual(validar_relato(texto)["texto"], texto)


if __name__ == "__main__":
    unittest.main()
