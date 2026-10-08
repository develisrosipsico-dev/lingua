import copy
import unittest
from unittest.mock import patch

from preparacao import (
    ErroPreparacao,
    mapear_intervalo,
    mapear_intervalos,
    preparar_texto,
    validar_registro,
)


class MapaLoteTests(unittest.TestCase):
    def preparar(self, texto, normalizar_crlf=False):
        return preparar_texto(
            texto,
            "documento-mapa-lote",
            preparacao_id="preparacao-mapa-lote",
            registrado_em="2026-10-08T00:00:00+00:00",
            normalizar_crlf=normalizar_crlf,
        )

    def test_equivalencia_em_todas_as_fronteiras(self):
        # Compara todos os intervalos, inclusive vazios e fronteiras de CRLF.
        textos = ("", "😀e\u0301\r\n\tA\r\n\r\nB\r\n", "\r\n\r\n", "\rA\n\u2028\u2029")
        for texto in textos:
            for normalizar in (False, True):
                with self.subTest(texto=texto, normalizar=normalizar):
                    registro = self.preparar(texto, normalizar)
                    tamanho = registro["trabalho"]["comprimento"]
                    intervalos = [
                        (inicio, fim)
                        for inicio in range(tamanho + 1)
                        for fim in range(inicio, tamanho + 1)
                    ]
                    esperado = [mapear_intervalo(registro, *par) for par in intervalos]
                    self.assertEqual(mapear_intervalos(registro, intervalos), esperado)

    def test_exemplo_e_intersecao_crlf(self):
        registro = self.preparar("Eu não queria sair.\r\nMas fiquei.", True)
        self.assertEqual(
            mapear_intervalos(registro, [(20, 31), (18, 21), (19, 20), (19, 19), (20, 20)]),
            [
                {"inicio": 21, "fim": 32},
                {"inicio": 18, "fim": 22},
                {"inicio": 19, "fim": 21},
                {"inicio": 19, "fim": 19},
                {"inicio": 21, "fim": 21},
            ],
        )

    def test_ordem_duplicatas_e_pares_em_lista_ou_tupla(self):
        registro = self.preparar("A\r\nB", True)
        self.assertEqual(
            mapear_intervalos(registro, ([2, 3], (0, 1), [2, 3])),
            [{"inicio": 3, "fim": 4}, {"inicio": 0, "fim": 1}, {"inicio": 3, "fim": 4}],
        )

    def test_lote_vazio_e_texto_vazio(self):
        for normalizar in (False, True):
            with self.subTest(normalizar=normalizar):
                registro = self.preparar("", normalizar)
                self.assertEqual(mapear_intervalos(registro, []), [])
                self.assertEqual(mapear_intervalos(registro, [(0, 0)]), [{"inicio": 0, "fim": 0}])

    def test_valida_registro_uma_vez_por_lote(self):
        registro = self.preparar("A\r\nB", True)
        for intervalos in ([], [(0, 1), (1, 2), (2, 3), (0, 3)]):
            with self.subTest(intervalos=intervalos):
                with patch("preparacao.validar_registro", wraps=validar_registro) as validar:
                    mapear_intervalos(registro, intervalos)
                    validar.assert_called_once_with(registro)

    def test_nao_modifica_entradas_e_resultados_sao_independentes(self):
        registro = self.preparar("😀\r\ne\u0301", True)
        intervalos = [[0, 1], [1, 2], [0, 1]]
        original_registro, original_intervalos = copy.deepcopy(registro), copy.deepcopy(intervalos)
        resultados = mapear_intervalos(registro, intervalos)
        self.assertEqual(registro, original_registro)
        self.assertEqual(intervalos, original_intervalos)
        resultados[0]["inicio"] = 99
        self.assertEqual(resultados[2], {"inicio": 0, "fim": 1})
        self.assertEqual(registro, original_registro)

    def test_rejeita_lote_ou_pares_malformados(self):
        registro = self.preparar("ABC")
        invalidos = (
            None, "01", b"01", 1, {}, iter([(0, 1)]),
            [None], ["01"], [{"inicio": 0, "fim": 1}], [(0,)], [(0, 1, 2)],
            [(False, 1)], [(0, True)], [(0.0, 1)], [(0, "1")],
            [(-1, 0)], [(0, -1)], [(2, 1)], [(0, 4)], [(4, 4)],
        )
        for intervalos in invalidos:
            with self.subTest(intervalos=intervalos):
                with self.assertRaises(ErroPreparacao):
                    mapear_intervalos(registro, intervalos)

    def test_rejeita_registro_corrompido_mesmo_com_lote_vazio(self):
        registro = self.preparar("A\r\nB", True)
        registro["original"]["texto"] = "original alterado"
        for intervalos in ([], [(0, 1)]):
            with self.subTest(intervalos=intervalos):
                with self.assertRaises(ErroPreparacao):
                    mapear_intervalos(registro, intervalos)

    def test_valida_todos_os_pares_antes_de_mapear(self):
        registro = self.preparar("A\r\nB", True)
        with patch("preparacao.bisect_right", side_effect=AssertionError("Mapeamento prematuro")):
            with self.assertRaises(ErroPreparacao):
                mapear_intervalos(registro, [(0, 1), (3, 2)])


if __name__ == "__main__":
    unittest.main()
