"""Integridade do índice e recuperação com vetores externos rastreáveis."""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from agente_analista.busca import Buscador, ErroBusca, _BM25
from agente_analista.corpus import Corpus, ErroCorpus


RAIZ = Path(__file__).resolve().parents[2]
REVISAO = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"


def _sha(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _escrever(pasta, fragmentos):
    (pasta / "fragmentos.jsonl").write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in fragmentos), encoding="utf-8")


def _fixture(pasta):
    manifesto = {"modelo": "intfloat/multilingual-e5-large", "revisao": REVISAO,
                 "prefixo_passagens": "passage: ", "prefixo_consultas": "query: ",
                 "tokens_maximo": 64, "margem_tokens": 8, "dimensao": 3,
                 "normalizacao": "L2", "tipo_vetor": "float32", "blocos": 2, "fragmentos": 3,
                 "referencias_arquivo": "fragmentos.jsonl", "vetores_arquivo": "vetores.npy"}
    fragmentos = []
    for bloco, textos in (("sonhos#1", ["O sonho retorna. ", "O desejo insiste."]), ("viagem#1", ["Viagem a Paris."])):
        origem = "".join(textos)
        inicio = 0
        for i, texto in enumerate(textos):
            f = {"id": f"{bloco}@{i}", "bloco_id": bloco, "obra": bloco.split("#")[0], "volume": 1,
                 "secao": "", "fragmento_no_bloco": i, "inicio": inicio, "fim": inicio + len(texto),
                 "paginas": [12 + i], "texto": texto, "cabecalho": "Sonhos" if bloco.startswith("sonhos") else "Viagem",
                 "texto_origem_sha256": _sha(origem), "tokens_entrada": 30}
            f["entrada_sha256"] = _sha("passage: " + f["cabecalho"] + "\n" + texto)
            inicio = f["fim"]
            fragmentos.append(f)
    (pasta / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    _escrever(pasta, fragmentos)
    np.save(pasta / "vetores.npy", np.array([[1, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32))
    return fragmentos


def _relato(p1="Sonho e desejo.", p2="Volto a pensar no sonho."):
    separador = "\n \t\n"
    texto = p1 + separador + p2
    inicio2 = len(p1) + len(separador)
    return {"texto": texto, "palavras": len(texto.split()), "paragrafos": [
        {"id": "P1", "inicio": 0, "fim": len(p1), "texto": p1},
        {"id": "P2", "inicio": inicio2, "fim": len(texto), "texto": p2}]}


def _importado(relato=None, vetor=None):
    relato = relato or _relato()
    vetor = [1.0, 0.0, 0.0] if vetor is None else vetor
    consultas = []
    for tipo in ("periodo", "contextual"):
        for paragrafo in relato["paragrafos"]:
            consultas.append({
                "id": f"{tipo}-{paragrafo['id']}", "tipo": tipo,
                "unidade_id": f"U-{paragrafo['id']}", "periodo_id": paragrafo["id"],
                "artefato_id": f"A-{tipo}-{paragrafo['id']}",
                "origem": {"campo": "unidades.foco.texto" if tipo == "periodo" else "unidades.janela.texto",
                           "original": {k: paragrafo[k] for k in ("inicio", "fim")}},
                "inicio": paragrafo["inicio"], "fim": paragrafo["fim"], "texto": paragrafo["texto"],
                "texto_vetorizado": paragrafo["texto"], "vetor": list(vetor),
            })
    consultas.append({"id": "documento", "tipo": "documento", "unidade_id": None, "periodo_id": None,
                      "artefato_id": "A-documento", "origem": {"campo": "preparacao.original.texto"},
                      "inicio": 0, "fim": len(relato["texto"]), "texto": relato["texto"],
                      "texto_vetorizado": relato["texto"], "vetor": list(vetor)})
    return {"relato": deepcopy(relato), "consultas": consultas, "fonte": {
        "schema_version": "1.0.0", "execucao_id": "vetorizacao-fixture", "contexto_execucao_id": "contexto-fixture",
        "documento_id": "documento-fixture", "espaco_vetorial_id": "espaco-fixture",
        "modelo": "intfloat/multilingual-e5-large", "revisao": REVISAO,
        "tokenizador": {"identificacao": "intfloat/multilingual-e5-large", "revisao": REVISAO},
        "dimensao": 3, "pooling": "mean", "normalizacao": "l2", "prefixo": "query: ",
        "formato_vetor": "float32",
    }}


def _importado_analista():
    importado = _importado()
    importado["fonte"].update(perfil="e5_analista", estrategia_consultas="paragrafos_documento", agregacao="nenhuma")
    importado["consultas"] = importado["consultas"][:2] + importado["consultas"][-1:]
    for consulta, paragrafo in zip(importado["consultas"][:2], importado["relato"]["paragrafos"]):
        consulta.update(tipo="paragrafo", paragrafo_id=paragrafo["id"])
    return importado


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporario.cleanup)
        self.pasta = Path(self.temporario.name)
        self.fragmentos = _fixture(self.pasta)

    def test_reconstroi_texto_e_referencia_sem_inventar_bibliografia(self):
        corpus = Corpus(self.pasta)
        self.assertIsInstance(corpus.vetores, np.memmap)
        bloco = corpus.blocos["sonhos#1"]
        self.assertEqual(bloco["texto"], "O sonho retorna. O desejo insiste.")
        self.assertEqual(bloco["fragmentos_ids"], ["sonhos#1@0", "sonhos#1@1"])
        self.assertEqual(bloco["referencia"]["paginas"], [12, 13])
        self.assertIn("numeração impressa não conferida", bloco["referencia"]["nota_paginas"])
        self.assertIn("edição", bloco["referencia"]["pendencias"])

    def test_rejeita_identificador_duplicado(self):
        self.fragmentos[1]["id"] = self.fragmentos[0]["id"]
        _escrever(self.pasta, self.fragmentos)
        with self.assertRaisesRegex(ErroCorpus, "duplicado"):
            Corpus(self.pasta)

    def test_rejeita_lacuna_e_sobreposicao(self):
        for deslocamento in (-1, 1):
            with self.subTest(deslocamento=deslocamento):
                fs = deepcopy(self.fragmentos)
                fs[1]["inicio"] += deslocamento
                fs[1]["fim"] += deslocamento
                _escrever(self.pasta, fs)
                with self.assertRaisesRegex(ErroCorpus, "lacuna|sobreposição conflitante"):
                    Corpus(self.pasta)

    def test_une_sobreposicao_consistente_como_uma_fonte(self):
        origem = "O sonho retorna. O desejo insiste."
        f = self.fragmentos[1]
        f["inicio"] = 8
        f["texto"] = origem[f["inicio"]:]
        f["fim"] = len(origem)
        f["entrada_sha256"] = _sha("passage: " + f["cabecalho"] + "\n" + f["texto"])
        _escrever(self.pasta, self.fragmentos)
        corpus = Corpus(self.pasta)
        self.assertEqual(corpus.blocos["sonhos#1"]["texto"], origem)
        resultado = Buscador(corpus).buscar(_importado())
        self.assertEqual(sum(c["bloco_id"] == "sonhos#1" for c in resultado["candidatos"]), 1)

    def test_rejeita_texto_alterado_mesmo_com_hash_da_entrada_recalculado(self):
        f = self.fragmentos[0]
        f["texto"] = f["texto"].replace("sonho", "medos")
        f["entrada_sha256"] = _sha("passage: " + f["cabecalho"] + "\n" + f["texto"])
        _escrever(self.pasta, self.fragmentos)
        with self.assertRaisesRegex(ErroCorpus, "bloco reconstruído"):
            Corpus(self.pasta)

    def test_rejeita_hash_da_entrada_incorreto(self):
        self.fragmentos[0]["entrada_sha256"] = "0" * 64
        _escrever(self.pasta, self.fragmentos)
        with self.assertRaisesRegex(ErroCorpus, "entrada E5"):
            Corpus(self.pasta)

    def test_rejeita_matriz_incompativel_nan_ou_nao_normalizada(self):
        for matriz in (np.ones((3, 4), dtype=np.float32), np.eye(3, dtype=np.float64),
                       np.array([[np.nan, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
                       np.zeros((3, 3), dtype=np.float32)):
            with self.subTest(shape=matriz.shape, dtype=matriz.dtype):
                np.save(self.pasta / "vetores.npy", matriz)
                with self.assertRaises(ErroCorpus):
                    Corpus(self.pasta)

    def test_nao_le_arquivos_fora_da_pasta_do_corpus(self):
        caminho = self.pasta / "manifesto.json"
        manifesto = json.loads(caminho.read_text())
        manifesto["referencias_arquivo"] = "../fragmentos.jsonl"
        caminho.write_text(json.dumps(manifesto))
        with self.assertRaisesRegex(ErroCorpus, "diretamente na pasta"):
            Corpus(self.pasta)


class BuscaTests(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporario.cleanup)
        pasta = Path(self.temporario.name)
        _fixture(pasta)
        self.corpus = Corpus(pasta)
        self.buscador = Buscador(self.corpus)

    def test_agrupa_fontes_e_cada_bloco_contribui_uma_vez_por_ranking(self):
        etapas = []
        resultado = self.buscador.buscar(_importado(), etapas.append)
        self.assertEqual({c["tipo"] for c in resultado["consultas"]}, {"periodo", "contextual", "documento"})
        blocos = resultado["candidatos"]
        self.assertEqual(len({c["bloco_id"] for c in blocos}), len(blocos))
        primeiro = blocos[0]
        self.assertEqual(primeiro["bloco_id"], "sonhos#1")
        self.assertEqual(primeiro["fragmentos_ids"], ["sonhos#1@0", "sonhos#1@1"])
        contribuicoes = defaultdict(list)
        for p in primeiro["pontuacoes"]:
            contribuicoes[(p["consulta_id"], p["metodo"])].append(p["contribuicao_rrf"])
        self.assertTrue(all(sum(c > 0 for c in contrib) == 1 for contrib in contribuicoes.values()))
        self.assertAlmostEqual(primeiro["rrf"], sum(sum(c) for c in contribuicoes.values()))
        self.assertEqual(resultado["metodo_fusao"]["consultas_por_tipo"], {"periodo": 2, "contextual": 2, "documento": 1})
        for pontuacao in primeiro["pontuacoes"]:
            consulta = next(c for c in resultado["consultas"] if c["id"] == pontuacao["consulta_id"])
            self.assertEqual(pontuacao["artefato_id"], consulta["artefato_id"])
            self.assertEqual(pontuacao["periodo_id"], consulta["periodo_id"])
            self.assertAlmostEqual(pontuacao["contribuicao_rrf"],
                                   pontuacao["contribuicao_rrf_bruta"] / (3 * pontuacao["consultas_do_tipo"]))
        self.assertTrue(etapas)

    def test_analista_restaura_soma_rrf_original_de_p1_p2_e_relato(self):
        resultado = self.buscador.buscar(_importado_analista())
        self.assertEqual([c["tipo"] for c in resultado["consultas"]], ["paragrafo", "paragrafo", "documento"])
        primeiro, segundo = resultado["candidatos"]
        self.assertEqual(primeiro["bloco_id"], "sonhos#1")
        # Antigo ranking: três consultas, lexical+E5; uma contribuição por bloco.
        self.assertAlmostEqual(primeiro["rrf"], 6 / 61)
        self.assertAlmostEqual(segundo["rrf"], 3 / 62)
        self.assertIsNone(resultado["metodo_fusao"]["pesos_por_tipo"])
        self.assertEqual(resultado["metodo_fusao"]["consultas_por_tipo"], {"paragrafo": 2, "documento": 1})
        for p in primeiro["pontuacoes"]:
            self.assertEqual(p["contribuicao_rrf"], p["contribuicao_rrf_bruta"])

    def test_analista_fragmentos_independentes_contribuem_como_antes(self):
        importado = _importado_analista()
        recorte = deepcopy(importado["consultas"][0])
        recorte["id"] += ":fragmento:2"
        importado["consultas"].append(recorte)
        resultado = self.buscador.buscar(importado)
        self.assertAlmostEqual(resultado["candidatos"][0]["rrf"], 8 / 61)

    def test_reutiliza_consultas_sem_recortar_inferir_ou_exportar_vetores(self):
        relato = _relato("  Não apago o sonho 🧠.\t" * 7, "A lembrança retorna.\r\nMesmo assim há dúvida.  ")
        importado = _importado(relato)
        original = deepcopy(importado)
        with patch.dict(sys.modules, {"embeddings_e5": None, "torch": None, "transformers": None}):
            resultado = self.buscador.buscar(importado)
        self.assertEqual(importado, original)
        self.assertEqual(len(resultado["consultas"]), len(importado["consultas"]))
        self.assertEqual(resultado["fonte_vetorizacao"], importado["fonte"])
        for consulta, recebida in zip(resultado["consultas"], importado["consultas"]):
            self.assertEqual(consulta, {k: v for k, v in recebida.items() if k != "vetor"})
            self.assertEqual(relato["texto"][consulta["inicio"]:consulta["fim"]], consulta["texto"])

    def test_muitas_janelas_nao_aumentam_o_peso_da_granularidade(self):
        importado = _importado()
        antes = self.buscador.buscar(importado)
        janela = next(c for c in importado["consultas"] if c["tipo"] == "contextual")
        for numero in range(20):
            importado["consultas"].append({**deepcopy(janela), "id": f"janela-extra-{numero}"})
        depois = self.buscador.buscar(importado)
        self.assertEqual([c["bloco_id"] for c in antes["candidatos"]], [c["bloco_id"] for c in depois["candidatos"]])
        for primeiro, segundo in zip(antes["candidatos"], depois["candidatos"]):
            self.assertAlmostEqual(primeiro["rrf"], segundo["rrf"])

    def test_busca_semantica_usa_vetor_importado_em_todas_as_linhas(self):
        relato = _relato("Caderno verde.", "Parede azul.")
        resultado = self.buscador.buscar(_importado(relato, vetor=[0, 1, 0]))
        self.assertEqual(resultado["candidatos"][0]["bloco_id"], "viagem#1")
        pontos = [p for c in resultado["candidatos"] for p in c["pontuacoes"]]
        self.assertTrue(all(p["metodo"] == "E5" for p in pontos))
        self.assertEqual({p["fragmento_id"] for p in pontos}, {f["id"] for f in self.corpus.fragmentos})

    def test_nao_aceita_vetorizacao_de_outra_revisao(self):
        importado = _importado()
        importado["fonte"]["revisao"] = "0" * 40
        with self.assertRaises(ErroBusca):
            self.buscador.buscar(importado)

    def test_nao_aceita_vetor_zero_ou_dimensao_errada(self):
        for vetor in ([0, 0, 0], [1, 0], [float("nan"), 0, 0], [float("inf"), 0, 0], [2, 0, 0]):
            with self.subTest(vetor=vetor), self.assertRaisesRegex(ErroBusca, "vetor|Vetor"):
                self.buscador.buscar(_importado(vetor=vetor))

    def test_rejeita_tipo_desconhecido_e_identificador_repetido(self):
        for campo, valor in (("tipo", "paragrafo"), ("id", "documento")):
            importado = _importado()
            importado["consultas"][0][campo] = valor
            with self.subTest(campo=campo), self.assertRaises(ErroBusca):
                self.buscador.buscar(importado)


@unittest.skipUnless((RAIZ / "agente_analista/data/Vetor/vetores_fragmentos_e5.npy").is_file(),
                     "Matriz original do corpus não disponível neste ambiente")
class CorpusRealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(RAIZ / "agente_analista/data/Vetor")

    def test_recuperacao_lexical_do_indice_fornecido(self):
        lexical = _BM25(self.corpus.fragmentos)
        pontuacoes = lexical.pontuar("sonho lembrança desejo")
        indice = int(pontuacoes.argmax())
        fonte = self.corpus.fragmentos[indice]
        self.assertGreater(float(pontuacoes[indice]), 0)
        self.assertEqual(fonte["obra"], "interpretacao-sonhos")
        bloco = self.corpus.blocos[fonte["bloco_id"]]
        self.assertEqual(bloco["texto"][fonte["inicio"]:fonte["fim"]], fonte["texto"])
        self.assertEqual(len(self.corpus.fragmentos), 8087)
        self.assertEqual(len(self.corpus.blocos), 6249)

    @unittest.skipUnless(os.environ.get("AGENTE_ANALISTA_VETORIZACAO_REAL"), "Arquivo externo de vetorização real opcional")
    def test_busca_hibrida_com_vetorizacao_externa_real(self):
        from agente_analista.importacao import importar_vetorizacao
        caminho = Path(os.environ["AGENTE_ANALISTA_VETORIZACAO_REAL"])
        importado = importar_vetorizacao(json.loads(caminho.read_text(encoding="utf-8")))
        resultado = Buscador(self.corpus).buscar(importado)
        self.assertTrue(resultado["candidatos"])
        self.assertEqual(len(resultado["consultas"]), len(importado["consultas"]))
        for candidato in resultado["candidatos"]:
            self.assertIn(candidato["bloco_id"], self.corpus.blocos)
            self.assertTrue(any(p["metodo"] == "E5" for p in candidato["pontuacoes"]))


if __name__ == "__main__":
    unittest.main()
