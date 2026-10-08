"""Métricas com posições e vetores controlados, sem inferência neural."""

from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from avaliacao_vetorizacao import ErroAvaliacao, avaliar_recuperacao, caracterizar_execucao
from fixtures_contexto import construir_regras, INSTANTE
from fixtures_vetorizacao import AdaptadorControlado
from perfis_vetorizacao import carregar_perfil
from unidades_contexto import construir_unidades_contexto
from vetorizacao import codificar_vetor, vetorizar_contexto


class AdaptadorRanking(AdaptadorControlado):
    """Direções anotadas manualmente para aferir os rankings esperados."""

    def gerar(self, entradas):
        vetores = {
            "Árvore\tverde.": [1.0, 0.0, 0.0, 0.0],
            "Casa azul!": [1.0, 1.0, 0.0, 0.0],
            "Céu.": [0.0, 1.0, 0.0, 0.0],
            "Outro.": [-1.0, 0.0, 0.0, 0.0],
            "João saiu.": [1.0, 0.0, 0.0, 0.0],
        }
        resultado = []
        for entrada in entradas:
            texto = entrada[len(self.perfil["prefixo"]):]
            vetor = vetores.get(texto, [1.0, 2.0, 3.0, 4.0])
            if self.perfil["id"] == "e5_consulta" and texto == "Casa azul!":
                vetor = [0.0, 1.0, 0.0, 0.0]
            resultado.append(vetor[:])
        return resultado


class AvaliacaoTestes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexto = construir_unidades_contexto(construir_regras(), execucao_id="contexto-avaliacao-controlada",
                                                  registrado_em=INSTANTE)
        cls.consultas = cls.gerar("e5_consulta", "consultas-controladas")
        cls.conteudos = cls.gerar("e5_conteudo", "conteudos-controlados")
        cls.q = [r["id"] for r in cls.consultas["representacoes"] if r["tipo"] == "periodo"]
        cls.c = [r["id"] for r in cls.conteudos["representacoes"] if r["tipo"] == "periodo"]

    @classmethod
    def gerar(cls, perfil_id, execucao_id, *, opcoes=None):
        opcoes = {"formato_armazenamento": "float16", **(opcoes or {})}
        perfil = carregar_perfil(perfil_id, opcoes=opcoes)
        return vetorizar_contexto(cls.contexto, perfil_id=perfil_id, opcoes=opcoes,
                                 adaptador=AdaptadorRanking(perfil), execucao_id=execucao_id,
                                 registrado_em=INSTANTE)

    def test_ranking_recall_mrr_and_ndcg_match_manual_positions(self):
        resultado = avaliar_recuperacao(self.consultas, self.conteudos, {self.q[0]: [self.c[1]]}, ks=(1, 3, 10))
        primeiro = resultado["resultados"][0]
        self.assertEqual([r["conteudo_id"] for r in primeiro["ranking"]], [self.c[0], self.c[4], self.c[1], self.c[2], self.c[3]])
        self.assertEqual(primeiro["metricas"]["1"], {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0})
        self.assertEqual(primeiro["metricas"]["3"], {"recall": 1.0, "mrr": 1 / 3, "ndcg": 0.5})
        self.assertEqual(primeiro["metricas"]["10"], primeiro["metricas"]["3"])
        self.assertEqual(resultado["medias"], primeiro["metricas"])
        self.assertEqual(resultado["quantidades"], {"consultas": 1, "conteudos": 5})
        self.assertTrue(resultado["compatibilidade"]["compativel"])

    def test_graded_ndcg_uses_exponential_gain_and_ideal_order(self):
        resultado = avaliar_recuperacao(self.consultas, self.conteudos, {self.q[0]: {self.c[1]: 3, self.c[0]: 1}}, ks=(1, 3))
        metricas = resultado["resultados"][0]["metricas"]
        self.assertEqual(metricas["1"]["recall"], 0.5)
        self.assertEqual(metricas["1"]["mrr"], 1.0)
        self.assertAlmostEqual(metricas["1"]["ndcg"], 1 / 7)
        self.assertEqual(metricas["3"]["recall"], 1.0)
        self.assertAlmostEqual(metricas["3"]["ndcg"], (1 + 7 / 2) / (7 + 1 / math.log2(3)))

    def test_macro_average_and_query_order_do_not_depend_on_json_order(self):
        referencias = {self.q[1]: [self.c[2]], self.q[0]: [self.c[1]]}
        resultado = avaliar_recuperacao(self.consultas, self.conteudos, referencias, ks=(1, 3))
        self.assertEqual([r["consulta_id"] for r in resultado["resultados"]], self.q[:2])
        self.assertEqual(resultado["medias"]["1"]["recall"], 0.5)
        self.assertEqual(resultado["medias"]["3"]["recall"], 1.0)
        self.assertAlmostEqual(resultado["medias"]["3"]["mrr"], 2 / 3)
        self.assertAlmostEqual(resultado["medias"]["3"]["ndcg"], 0.75)

    def test_fp16_cosseno_renormalizes_both_vectors(self):
        resultado = avaliar_recuperacao(self.consultas, self.conteudos, {self.q[0]: [self.c[1]]}, ks=(5,))
        casa = next(r for r in resultado["resultados"][0]["ranking"] if r["conteudo_id"] == self.c[1])
        self.assertAlmostEqual(casa["cosseno"], math.sqrt(0.5), places=14)
        artefato_id = self.conteudos["representacoes"][2]["artefato_id"]
        vetor = next(a["valores"] for a in self.conteudos["artefatos"] if a["id"] == artefato_id)
        self.assertGreater(abs(casa["cosseno"] - vetor[0]), 0.00001)

    def test_explicit_corpus_order_breaks_ties_without_duplicate_results(self):
        corpus = [self.c[4], self.c[0], self.c[1]]
        resultado = avaliar_recuperacao(self.consultas, self.conteudos, {self.q[0]: [self.c[0]]}, ks=(1, 3), conteudo_ids=corpus)
        self.assertEqual([r["conteudo_id"] for r in resultado["resultados"][0]["ranking"]], corpus)
        self.assertEqual(resultado["resultados"][0]["metricas"]["3"]["mrr"], 0.5)
        self.assertEqual(resultado["configuracao"]["corpus_ids"], corpus)

    def test_references_and_configuration_have_real_hashes_and_inputs_are_unchanged(self):
        referencias = {self.q[0]: {self.c[0]: 2}}
        fontes = deepcopy((self.consultas, self.conteudos, referencias))
        resultado = avaliar_recuperacao(self.consultas, self.conteudos, referencias)
        self.assertEqual((self.consultas, self.conteudos, referencias), fontes)
        self.assertEqual(len(resultado["referencias_sha256"]), 64)
        self.assertEqual(len(resultado["configuracao_avaliacao_sha256"]), 64)
        self.assertEqual(resultado["consultas"]["configuracao_sha256"], self.consultas["configuracao_sha256"])
        modificado = avaliar_recuperacao(self.consultas, self.conteudos, {self.q[0]: {self.c[0]: 3}})
        self.assertNotEqual(resultado["referencias_sha256"], modificado["referencias_sha256"])
        self.assertEqual(json.loads(json.dumps(resultado, ensure_ascii=False, allow_nan=False)), resultado)

    def test_incompatible_models_profiles_and_direction_are_rejected(self):
        outros = [self.gerar("e5_simetrico", "simetrico-controlado"),
                  self.gerar("e5_conteudo", "outro-modelo-controlado", opcoes={"modelo": "outro/modelo", "revisao": "a" * 40})]
        for outro in outros:
            with self.subTest(perfil=outro["perfil"]):
                with self.assertRaises(ErroAvaliacao):
                    avaliar_recuperacao(self.consultas, outro, {self.q[0]: [outro["representacoes"][0]["id"]]})
        with self.assertRaises(ErroAvaliacao):
            avaliar_recuperacao(self.conteudos, self.consultas, {self.c[0]: [self.q[0]]})

    def test_invalid_references_grades_selectors_and_ks_raise_explicit_error(self):
        invalidas = [None, [], {}, {"consulta-inexistente": [self.c[0]]}, {self.q[0]: []},
                     {self.q[0]: [self.c[0], self.c[0]]}, {self.q[0]: {"conteudo-inexistente": 1}},
                     {self.q[0]: {True: 1}}]
        invalidas.extend({self.q[0]: {self.c[0]: grau}} for grau in (0, -1, True, None, "1", float("nan"), float("inf")))
        for referencia in invalidas:
            with self.subTest(referencia=referencia), self.assertRaises(ErroAvaliacao):
                avaliar_recuperacao(self.consultas, self.conteudos, referencia)
        validas = {self.q[0]: [self.c[0]]}
        for ks in ((), (0,), (-1,), (True,), (1, 1), (1.5,), "1"):
            with self.subTest(ks=ks), self.assertRaises(ErroAvaliacao):
                avaliar_recuperacao(self.consultas, self.conteudos, validas, ks=ks)
        for corpus in ([], [self.c[0], self.c[0]], ["inexistente"], self.c[0]):
            with self.subTest(corpus=corpus), self.assertRaises(ErroAvaliacao):
                avaliar_recuperacao(self.consultas, self.conteudos, validas, conteudo_ids=corpus)
        with self.assertRaises(ErroAvaliacao):
            avaliar_recuperacao(self.consultas, self.conteudos, {self.q[0]: [self.c[1]]}, conteudo_ids=[self.c[0]])

    def test_large_relevance_grades_do_not_overflow_ndcg(self):
        resultado = avaliar_recuperacao(self.consultas, self.conteudos,
                                       {self.q[0]: {self.c[1]: 1e300, self.c[0]: 1}}, ks=(3,))
        self.assertAlmostEqual(resultado["resultados"][0]["metricas"]["3"]["ndcg"], 0.5)
        json.dumps(resultado, allow_nan=False)

    def test_corruption_nonfinite_vector_and_hash_inconsistency_are_rejected(self):
        for valor in (float("nan"), float("inf"), 0.123):
            registro = deepcopy(self.conteudos)
            registro["artefatos"][0]["valores"][0] = valor
            with self.subTest(valor=valor), self.assertRaises(ErroAvaliacao):
                avaliar_recuperacao(self.consultas, registro, {self.q[0]: [self.c[0]]})
            with self.assertRaises(ErroAvaliacao):
                caracterizar_execucao(registro)

    def test_resource_report_counts_unique_artifacts_and_actual_format_bytes(self):
        resultado = caracterizar_execucao(self.conteudos)
        esperado = sum(len(codificar_vetor(a["valores"], a["formato"])) for a in self.conteudos["artefatos"])
        self.assertEqual(resultado["vetores"]["bytes_total"], esperado)
        self.assertEqual(resultado["vetores"]["artefatos_unicos"], len(self.conteudos["artefatos"]))
        self.assertEqual(resultado["vetores"]["dimensoes"], [4])
        self.assertLess(len(self.conteudos["artefatos"]), len(self.conteudos["representacoes"]))
        self.assertEqual(resultado["vetores"]["formatos"]["float16"]["bytes"], esperado)
        self.assertTrue(all(a["bytes"] == 8 for a in resultado["vetores"]["artefatos"]))
        for formato, tamanho in (("float32", 16), ("float64", 32)):
            registro = self.gerar("e5_conteudo", f"recurso-{formato}", opcoes={"formato_armazenamento": formato})
            relatorio = caracterizar_execucao(registro)
            self.assertTrue(all(a["bytes"] == tamanho for a in relatorio["vetores"]["artefatos"]))

    def test_missing_measurements_remain_null_and_supplied_values_are_preserved(self):
        resultado = caracterizar_execucao(self.conteudos)
        self.assertEqual(resultado["medicoes_fornecidas"], [])
        self.assertTrue(all(v is None for v in resultado["medicoes"].values()))
        medido = caracterizar_execucao(self.conteudos, tempo_total_segundos=2.75,
                                      tempo_carregamento_segundos=0.5, pico_memoria_bytes=4096,
                                      memoria_gpu_bytes=0)
        self.assertEqual(medido["medicoes"], {"tempo_total_segundos": 2.75, "tempo_carregamento_segundos": 0.5,
                                            "pico_memoria_bytes": 4096, "memoria_gpu_bytes": 0})
        self.assertEqual(set(medido["medicoes_fornecidas"]), set(medido["medicoes"]))
        self.assertEqual(json.loads(json.dumps(medido, allow_nan=False)), medido)

    def test_invalid_measurements_are_explicit_errors(self):
        casos = [{"tempo_total_segundos": v} for v in (-1, True, "2", float("nan"), float("inf"))]
        casos += [{"tempo_carregamento_segundos": -1}]
        casos += [{"pico_memoria_bytes": v} for v in (-1, True, 3.5, float("inf"))]
        casos += [{"memoria_gpu_bytes": "0"}]
        for opcoes in casos:
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroAvaliacao):
                caracterizar_execucao(self.conteudos, **opcoes)

    def test_sem_conteudo_has_no_artificial_bytes_and_cannot_be_retrieved(self):
        contexto = construir_unidades_contexto(construir_regras(texto="", linhas=[]),
                                              execucao_id="contexto-vazio-controlado", registrado_em=INSTANTE)
        perfil = carregar_perfil("e5_conteudo", opcoes={"formato_armazenamento": "float16"})
        vazio = vetorizar_contexto(contexto, perfil_id="e5_conteudo", opcoes={"formato_armazenamento": "float16"},
                                  adaptador=AdaptadorRanking(perfil), execucao_id="vetores-vazios-controlados",
                                  registrado_em=INSTANTE)
        resultado = caracterizar_execucao(vazio)
        self.assertEqual(resultado["vetores"]["bytes_total"], 0)
        self.assertEqual(resultado["representacoes"], {"total": 1, "com_vetor": 0})
        documento_id = vazio["representacoes"][0]["id"]
        with self.assertRaisesRegex(ErroAvaliacao, "sem vetor"):
            avaliar_recuperacao(self.consultas, vazio, {self.q[0]: [documento_id]}, conteudo_ids=[documento_id])

    def test_cli_recovers_metrics_and_writes_characterization_without_overwriting(self):
        raiz = Path(__file__).resolve().parents[1]
        script = raiz / "avaliacao_vetorizacao.py"
        with tempfile.TemporaryDirectory() as diretorio:
            caminhos = {nome: Path(diretorio) / f"{nome}.json" for nome in ("consultas", "conteudos", "referencias", "resultado", "recursos")}
            for nome, valor in (("consultas", self.consultas), ("conteudos", self.conteudos),
                                ("referencias", {self.q[0]: [self.c[1]]})):
                caminhos[nome].write_text(json.dumps(valor, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            comando = [sys.executable, str(script), "recuperar", str(caminhos["consultas"]), str(caminhos["conteudos"]),
                       str(caminhos["referencias"]), "--ks", "1", "3", "--saida", str(caminhos["resultado"])]
            processo = subprocess.run(comando, capture_output=True, text=True, check=False)
            self.assertEqual(processo.returncode, 0, processo.stderr)
            relatorio = json.loads(caminhos["resultado"].read_text(encoding="utf-8"))
            self.assertEqual(relatorio["medias"]["3"]["mrr"], 1 / 3)
            antes = caminhos["resultado"].read_bytes()
            repetido = subprocess.run(comando, capture_output=True, text=True, check=False)
            self.assertEqual(repetido.returncode, 2)
            self.assertEqual(caminhos["resultado"].read_bytes(), antes)
            comando_recurso = [sys.executable, str(script), "caracterizar", str(caminhos["conteudos"]),
                               "--tempo-total-segundos", "2.5", "--pico-memoria-bytes", "1024", "--saida", str(caminhos["recursos"])]
            recurso = subprocess.run(comando_recurso, capture_output=True, text=True, check=False)
            self.assertEqual(recurso.returncode, 0, recurso.stderr)
            medido = json.loads(caminhos["recursos"].read_text(encoding="utf-8"))
            self.assertEqual(medido["medicoes"]["tempo_total_segundos"], 2.5)
            self.assertEqual(medido["medicoes"]["pico_memoria_bytes"], 1024)
            self.assertIsNone(medido["medicoes"]["memoria_gpu_bytes"])

    def test_import_is_stdlib_only_and_does_not_load_an_inference_backend(self):
        programa = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch','numpy','spacy','transformers','sentence_transformers','fastembed'}:
        raise RuntimeError('Importação de inferência não autorizada: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import avaliacao_vetorizacao
"""
        processo = subprocess.run([sys.executable, "-c", programa], cwd=Path(__file__).resolve().parents[1],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(processo.returncode, 0, processo.stderr)


if __name__ == "__main__":
    unittest.main()
