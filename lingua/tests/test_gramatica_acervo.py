"""Contratos do pacote; inferência controlada, sem baixar ou simular teste E5 real."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

from gramatica_acervo import (
    ErroAcervo, exportar_acervo, validar_acervo, vetorizar_fragmentos,
)


class TokenizadorControlado:
    def encode(self, texto, **opcoes):
        if opcoes != {"add_special_tokens": True, "truncation": False}:
            raise AssertionError(opcoes)
        return list(range(len(texto.split()) + 2))


class ModeloControlado:
    """Vetores unitários artificiais para testar persistência, nunca recuperação."""

    tokenizer = TokenizadorControlado()
    max_seq_length = 512
    dtype = "float32"

    def __init__(self, falhar_na=None, limite_lote=None, defeito=None):
        self.chamadas = []
        self.falhar_na = falhar_na
        self.limite_lote = limite_lote
        self.defeito = defeito
        self.limpezas = 0

    def get_sentence_embedding_dimension(self):
        return 1024

    def limpar_cache_cuda(self):
        self.limpezas += 1

    def encode(self, entradas, **opcoes):
        self.chamadas.append((entradas[:], opcoes))
        assert opcoes["prompt"] == ""
        assert opcoes["normalize_embeddings"] is True
        assert opcoes["convert_to_numpy"] is True
        if self.falhar_na == len(self.chamadas):
            raise RuntimeError("Interrupção controlada; não é OOM.")
        if self.limite_lote is not None and len(entradas) > self.limite_lote:
            raise RuntimeError("CUDA out of memory. Teste controlado sem GPU.")
        matriz = np.zeros((len(entradas), 1024), dtype=np.float32)
        for linha, entrada in enumerate(entradas):
            indice = int(hashlib.sha256(entrada.encode()).hexdigest()[:8], 16) % 1024
            matriz[linha, indice] = 1
        if self.defeito == "nan":
            matriz[0, 0] = np.nan
        elif self.defeito == "zero":
            matriz[0] = 0
        elif self.defeito == "dimensao":
            matriz = matriz[:, :20]
        return matriz


class AcervoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pasta = Path(self.temp.name) / "acervo"
        self.pasta.mkdir()
        self.perfil = {"pdf_sha256": "a" * 64}
        self.fragmentos = []
        for numero in range(5):
            texto = f"Definição gramatical número {numero}."
            entrada = "passage: " + texto
            self.fragmentos.append({
                "id": f"fragmento-{numero}", "linha_vetor": numero,
                "texto": texto, "texto_embedding": entrada,
                "tokens_embedding": len(entrada.split()) + 2,
                "sha256_texto": hashlib.sha256(texto.encode()).hexdigest(),
                "sha256_texto_embedding": hashlib.sha256(entrada.encode()).hexdigest(),
                "secao": ["Morfologia"],
                "origem": [{"pagina_pdf": numero + 1, "pagina_rotulo": str(numero + 1)}],
            })
        (self.pasta / "obra.json").write_text(json.dumps({
            "titulo": "Gramática fictícia de teste", "pdf_sha256": "a" * 64,
        }), encoding="utf-8")
        (self.pasta / "relatorio_extracao.json").write_text(
            '{"paginas": 5, "avisos": []}', encoding="utf-8")

    def vetorizar(self, modelo=None, **opcoes):
        return vetorizar_fragmentos(self.fragmentos, modelo or ModeloControlado(),
                                   self.pasta, self.perfil, **opcoes)

    def test_vetores_linhas_e_entrada_exata(self):
        modelo = ModeloControlado()
        progresso = []
        caminho = self.vetorizar(modelo, lote_inicial=2, progresso=progresso.append)
        matriz = np.load(caminho, allow_pickle=False)
        self.assertEqual(matriz.shape, (5, 1024))
        self.assertEqual(matriz.dtype, np.dtype("float32"))
        self.assertTrue(np.allclose(np.linalg.norm(matriz, axis=1), 1))
        entradas = [texto for chamada, _ in modelo.chamadas for texto in chamada]
        self.assertEqual(entradas, [f["texto_embedding"] for f in self.fragmentos])
        self.assertEqual(progresso[-1]["concluidos"], 5)
        self.assertTrue(validar_acervo(self.pasta)["valido"])

    def test_interrupcao_retoma_somente_lotes_restantes(self):
        with self.assertRaisesRegex(RuntimeError, "Interrupção"):
            self.vetorizar(ModeloControlado(falhar_na=2), lote_inicial=2)
        self.assertFalse((self.pasta / "vetores.npy").exists())
        self.assertFalse(validar_acervo(self.pasta)["valido"])
        self.assertEqual(len(list((self.pasta / "checkpoints").glob("lote-*.json"))), 1)
        modelo = ModeloControlado()
        progresso = []
        self.vetorizar(modelo, lote_inicial=2, progresso=progresso.append)
        self.assertEqual(sum(len(entradas) for entradas, _ in modelo.chamadas), 3)
        self.assertEqual(progresso[0]["concluidos"], 2)
        self.assertTrue(progresso[0]["retomado"])
        self.assertTrue(validar_acervo(self.pasta)["valido"])

    def test_retomada_completa_nao_chama_inferencia(self):
        self.vetorizar(lote_inicial=2)
        modelo = ModeloControlado(falhar_na=1)
        self.vetorizar(modelo, lote_inicial=3)
        self.assertEqual(modelo.chamadas, [])

    def test_oom_reduz_lote_sem_perder_fragmentos(self):
        modelo = ModeloControlado(limite_lote=2)
        self.vetorizar(modelo, lote_inicial=8)
        self.assertEqual([opcoes["batch_size"] for _, opcoes in modelo.chamadas],
                         [8, 4, 2, 2, 2])
        self.assertEqual(modelo.limpezas, 2)
        self.assertTrue(validar_acervo(self.pasta)["valido"])

    def test_perfil_e_fragmentos_diferentes_nao_reutilizados(self):
        self.vetorizar(lote_inicial=2)
        original = (self.pasta / "vetores.npy").read_bytes()
        self.perfil["pdf_sha256"] = "b" * 64
        with self.assertRaisesRegex(ErroAcervo, "outro perfil"):
            self.vetorizar()
        self.perfil["pdf_sha256"] = "a" * 64
        self.fragmentos[0]["secao"] = ["Outra seção"]
        with self.assertRaisesRegex(ErroAcervo, "outros fragmentos"):
            self.vetorizar()
        self.assertEqual((self.pasta / "vetores.npy").read_bytes(), original)

    def test_checkpoint_corrompido_rejeitado(self):
        with self.assertRaises(RuntimeError):
            self.vetorizar(ModeloControlado(falhar_na=2), lote_inicial=2)
        lote = next((self.pasta / "checkpoints").glob("lote-*.npy"))
        lote.write_bytes(b"checkpoint alterado")
        modelo = ModeloControlado()
        with self.assertRaisesRegex(ErroAcervo, "SHA256 do checkpoint"):
            self.vetorizar(modelo)
        self.assertEqual(modelo.chamadas, [])

    def test_tokenizacao_real_conferida_sem_truncamento(self):
        self.fragmentos[0]["tokens_embedding"] -= 1
        modelo = ModeloControlado()
        with self.assertRaisesRegex(ErroAcervo, "tokens incompatíveis"):
            self.vetorizar(modelo)
        self.assertEqual(modelo.chamadas, [])

    def test_limite_490_preservado_com_retomada_e_sem_truncamento(self):
        self.perfil["limite_tokens"] = 490
        modelo = ModeloControlado(falhar_na=2)
        modelo.max_seq_length = 490
        with self.assertRaisesRegex(RuntimeError, "Interrupção"):
            self.vetorizar(modelo, lote_inicial=2)
        retomada = ModeloControlado()
        retomada.max_seq_length = 490
        self.vetorizar(retomada, lote_inicial=2)
        perfil = json.loads((self.pasta / "perfil_embeddings.json").read_text())
        self.assertEqual(perfil["limite_tokens"], 490)
        self.assertEqual(sum(len(entradas) for entradas, _ in retomada.chamadas), 3)
        self.assertTrue(validar_acervo(self.pasta)["valido"])

    def test_limites_invalidos_rejeitados_antes_de_inferencia(self):
        for limite in (True, "490", 15, 513, 490.0):
            with self.subTest(limite=limite):
                self.perfil["limite_tokens"] = limite
                modelo = ModeloControlado()
                with self.assertRaisesRegex(ErroAcervo, "limite_tokens"):
                    self.vetorizar(modelo)
                self.assertEqual(modelo.chamadas, [])

    def test_fragmento_acima_limite_perfil_rejeitado_mesmo_modelo_512(self):
        texto = " ".join(["palavra"] * 488)
        entrada = "passage: " + texto
        self.fragmentos[0].update(
            texto=texto, texto_embedding=entrada,
            tokens_embedding=len(entrada.split()) + 2,
            sha256_texto=hashlib.sha256(texto.encode()).hexdigest(),
            sha256_texto_embedding=hashlib.sha256(entrada.encode()).hexdigest(),
        )
        self.assertEqual(self.fragmentos[0]["tokens_embedding"], 491)
        self.perfil["limite_tokens"] = 490
        modelo = ModeloControlado()
        with self.assertRaisesRegex(ErroAcervo, "contagem de tokens"):
            self.vetorizar(modelo)
        self.assertEqual(modelo.chamadas, [])

    def test_limite_e_versoes_diferentes_nao_misturam_checkpoints(self):
        self.perfil.update(limite_tokens=490, ambiente={
            "python": "3.12", "bibliotecas": {"torch": "2.9.0"},
        })
        with self.assertRaisesRegex(RuntimeError, "Interrupção"):
            self.vetorizar(ModeloControlado(falhar_na=2), lote_inicial=2)
        for mudanca in ({"limite_tokens": 480}, {"ambiente": {
                "python": "3.12", "bibliotecas": {"torch": "2.10.0"}}}):
            with self.subTest(mudanca=mudanca):
                perfil = {**self.perfil, **mudanca}
                modelo = ModeloControlado()
                with self.assertRaisesRegex(ErroAcervo, "outro perfil"):
                    vetorizar_fragmentos(self.fragmentos, modelo, self.pasta, perfil)
                self.assertEqual(modelo.chamadas, [])

    def test_vetores_defeituosos_nao_geram_matriz_final(self):
        for defeito in ("nan", "zero", "dimensao"):
            with self.subTest(defeito=defeito):
                with self.assertRaises(ErroAcervo):
                    self.vetorizar(ModeloControlado(defeito=defeito))
                self.assertFalse((self.pasta / "vetores.npy").exists())
                self.assertFalse(validar_acervo(self.pasta)["valido"])

    def test_armazenamento_float32_nao_disfarca_inferencia_float16(self):
        modelo = ModeloControlado()
        modelo.dtype = "torch.float16"
        with self.assertRaisesRegex(ErroAcervo, "pesos em float32"):
            self.vetorizar(modelo)

    def test_exportacao_integra_com_texto_metadados_e_codigo_sem_checkpoints(self):
        self.vetorizar(lote_inicial=2)
        codigo = self.pasta / "codigo"
        codigo.mkdir()
        (codigo / "preparacao.py").write_text("# código de teste\n", encoding="utf-8")
        destino = exportar_acervo(self.pasta)
        with zipfile.ZipFile(destino) as pacote:
            nomes = pacote.namelist()
            manifesto = json.loads(pacote.read("manifesto.json"))
            self.assertEqual(manifesto["schema"], "gramatica.acervo.v1")
            self.assertEqual(manifesto["estado"], "completo")
            self.assertNotIn("manifesto.json", manifesto["arquivos"])
            self.assertFalse(any(nome.startswith("checkpoints/") for nome in nomes))
            self.assertIn("codigo/preparacao.py", nomes)
            for nome, metadados in manifesto["arquivos"].items():
                dados = pacote.read(nome)
                self.assertEqual(hashlib.sha256(dados).hexdigest(), metadados["sha256"])
                self.assertEqual(len(dados), metadados["bytes"])
            fragmentos = [json.loads(linha) for linha in pacote.read("fragmentos.jsonl").splitlines()]
            self.assertEqual(fragmentos, self.fragmentos)

    def test_exporta_anexos_de_revisao_e_historico_com_hashes(self):
        self.vetorizar()
        anexos = {
            "config_notebook.json": '{"limite_tokens":490}\n',
            "extracao.json": '{"paginas":[]}\n',
            "preparacao.json": '{"conferida":true}\n',
            "auditoria_fragmentos.json": '{"aprovado":true}\n',
            "historico_execucao.jsonl": '{"evento":"inicio"}\n{"evento":"fim"}\n',
        }
        for nome, conteudo in anexos.items():
            (self.pasta / nome).write_text(conteudo, encoding="utf-8")
        with zipfile.ZipFile(exportar_acervo(self.pasta)) as pacote:
            manifesto = json.loads(pacote.read("manifesto.json"))
            for nome, conteudo in anexos.items():
                dados = conteudo.encode("utf-8")
                self.assertEqual(pacote.read(nome), dados)
                self.assertEqual(manifesto["arquivos"][nome]["sha256"],
                                 hashlib.sha256(dados).hexdigest())

    def test_anexo_opcional_com_link_simbolico_rejeitado(self):
        self.vetorizar()
        (self.pasta / "extracao.json").symlink_to(self.pasta / "obra.json")
        with self.assertRaisesRegex(ErroAcervo, "link simbólico"):
            exportar_acervo(self.pasta)

    def test_matriz_alterada_mesmo_normalizada_impede_exportacao(self):
        self.vetorizar()
        matriz = np.load(self.pasta / "vetores.npy", allow_pickle=False)
        np.save(self.pasta / "vetores.npy", np.roll(matriz, 1, axis=1), allow_pickle=False)
        self.assertFalse(validar_acervo(self.pasta)["valido"])
        with self.assertRaisesRegex(ErroAcervo, "incompleto"):
            exportar_acervo(self.pasta)
        self.assertFalse(self.pasta.with_name(self.pasta.name + ".zip").exists())

    def test_metadados_obrigatorios_e_destino_nao_podem_destruir_matriz(self):
        self.vetorizar()
        with self.assertRaisesRegex(ErroAcervo, "substituir"):
            exportar_acervo(self.pasta, self.pasta / "vetores.npy")
        self.assertTrue(validar_acervo(self.pasta)["valido"])
        (self.pasta / "obra.json").unlink()
        with self.assertRaisesRegex(ErroAcervo, "obra.json"):
            exportar_acervo(self.pasta)


if __name__ == "__main__":
    unittest.main()
