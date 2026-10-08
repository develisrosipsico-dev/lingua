"""CLI escreve JSON real sem sobrescrever; inferência controlada só no teste."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fixtures_contexto import construir_regras, INSTANTE
from fixtures_vetorizacao import AdaptadorControlado
from perfis_vetorizacao import carregar_perfil
from unidades_contexto import construir_unidades_contexto
from vetorizacao import vetorizar_contexto, validar_vetorizacao
import vetorizar


class CliVetorizacaoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexto = construir_unidades_contexto(construir_regras(),
            execucao_id="contexto-cli-fixture-09", registrado_em=INSTANTE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.entrada = Path(self.tmp.name) / "contexto.json"
        self.saida = Path(self.tmp.name) / "vetores.json"
        self.entrada.write_text(json.dumps(self.contexto, ensure_ascii=False), encoding="utf-8")
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def chamar(self, *extras):
        args = [str(self.entrada), "--saida", str(self.saida),
                "--execucao-id", "vetorizacao-cli-fixture-09", "--registrado-em", INSTANTE, *extras]
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            return vetorizar.main(args)

    @staticmethod
    def gerar_controlado(contexto, **kwargs):
        perfil = carregar_perfil(kwargs["perfil_id"], opcoes=kwargs["opcoes"])
        return vetorizar_contexto(contexto, adaptador=AdaptadorControlado(perfil), **kwargs)

    def test_json_completo_preserva_unicode_origem_e_configuracao(self):
        with patch("vetorizar.vetorizar_contexto", side_effect=self.gerar_controlado) as gerar:
            status = self.chamar("--perfil", "e5_conteudo", "--opcoes", '{"formato_armazenamento":"float64"}')
        self.assertEqual(status, 0, self.stderr.getvalue())
        texto = self.saida.read_text(encoding="utf-8")
        self.assertIn("Árvore", texto)
        self.assertIn("João", texto)
        self.assertNotIn("\\u00c1", texto)
        registro = json.loads(texto)
        validar_vetorizacao(registro)
        self.assertEqual(registro["contexto"], self.contexto)
        self.assertEqual(registro["execucao_id"], "vetorizacao-cli-fixture-09")
        self.assertEqual(registro["registrado_em"], INSTANTE)
        self.assertEqual(registro["perfil"]["formato_armazenamento"], "float64")
        self.assertEqual(registro["perfil"]["prefixo"], "passage: ")
        self.assertEqual(self.stdout.getvalue(), str(self.saida) + "\n")
        self.assertNotIn("Erro:", self.stderr.getvalue())
        gerar.assert_called_once()

    def test_saida_existente_preservada_sem_chamar_modelo(self):
        anterior = "Experimento anterior: café ☕\r\n".encode("utf-8")
        self.saida.write_bytes(anterior)
        with patch("vetorizar.vetorizar_contexto") as gerar:
            self.assertEqual(self.chamar(), 1)
            gerar.assert_not_called()
        self.assertEqual(self.saida.read_bytes(), anterior)
        self.assertIn("saída já existe", self.stderr.getvalue())

    def test_json_invalido_ou_arquivo_ausente_sem_modelo_e_sem_saida(self):
        for conteudo in ("{invalido", None):
            with self.subTest(conteudo=conteudo):
                if conteudo is None:
                    self.entrada.unlink()
                else:
                    self.entrada.write_text(conteudo, encoding="utf-8")
                with patch("vetorizar.vetorizar_contexto") as gerar:
                    self.assertEqual(self.chamar(), 1)
                    gerar.assert_not_called()
                self.assertFalse(self.saida.exists())

    def test_origem_08_invalida_retorna_erro_sem_arquivo(self):
        adulterado = deepcopy(self.contexto)
        adulterado["unidades"][0]["foco"]["texto"] += "alterado"
        self.entrada.write_text(json.dumps(adulterado, ensure_ascii=False), encoding="utf-8")
        with patch("vetorizar.vetorizar_contexto", side_effect=self.gerar_controlado):
            self.assertEqual(self.chamar(), 1)
        self.assertFalse(self.saida.exists())
        self.assertIn("Erro:", self.stderr.getvalue())

    def test_opcoes_com_json_invalido_nao_chama_modelo(self):
        with patch("vetorizar.vetorizar_contexto") as gerar:
            self.assertEqual(self.chamar("--opcoes", "{invalido"), 1)
            gerar.assert_not_called()
        self.assertFalse(self.saida.exists())

    def test_criacao_concorrente_da_saida_nao_e_sobrescrita(self):
        anterior = "Resultado do outro processo: ação".encode("utf-8")
        def gerar_e_criar(contexto, **kwargs):
            registro = self.gerar_controlado(contexto, **kwargs)
            self.saida.write_bytes(anterior)
            return registro
        with patch("vetorizar.vetorizar_contexto", side_effect=gerar_e_criar):
            self.assertEqual(self.chamar(), 1)
        self.assertEqual(self.saida.read_bytes(), anterior)


if __name__ == "__main__":
    unittest.main()
