"""Contratos 09 com origem linguística manual e adaptador somente de testes."""

from copy import deepcopy
import hashlib
import json
import math
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch

from fixtures_contexto import construir_regras, INSTANTE, TEXTO
from fixtures_regras import token
from perfis_vetorizacao import carregar_perfil
from unidades_contexto import construir_unidades_contexto
from vetorizacao import (ErroVetorizacao, codificar_vetor, decodificar_vetor,
                         vetorizar_contexto, validar_vetorizacao, consultar_representacao,
                         derivar_vetor, verificar_compatibilidade)


class AdaptadorTeste:
    """Tokeniza pontos de código, nunca registrado na fábrica de produção."""

    def __init__(self, perfil, *, dimensao=3, limite=160):
        self.perfil = deepcopy(perfil)
        self.dimensao = dimensao
        self.limite = limite
        self.chamadas = []

    def descrever(self):
        return {"backend": self.perfil["backend"], "versao_backend": "teste-1.0",
                "modelo": deepcopy(self.perfil["modelo"]), "tokenizador": deepcopy(self.perfil["tokenizador"]),
                "dimensao": self.dimensao, "limite_tokens": self.limite,
                "pooling": self.perfil["pooling"], "dispositivo": self.perfil["dispositivo"],
                "precisao_inferencia": self.perfil["precisao_inferencia"],
                "bibliotecas": {"fixture": "deterministica-sem-inferencia-neural"}}

    def tokenizar(self, texto):
        return {"input_ids": [1] + [ord(c) + 10 for c in texto] + [2],
                "attention_mask": [1] * (len(texto) + 2),
                "special_tokens_mask": [1] + [0] * len(texto) + [1],
                "offsets": [[0, 0]] + [[i, i + 1] for i in range(len(texto))] + [[0, 0]]}

    def gerar(self, entradas):
        self.chamadas.append(entradas[:])
        return [[float((sum(map(ord, t)) + i * 17) % 101 + 1) for i in range(self.dimensao)] for t in entradas]


class VetorizacaoTestes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexto = construir_unidades_contexto(construir_regras(), execucao_id="contexto-fixture-09",
                                                  registrado_em=INSTANTE)
        cls.normalizado = construir_unidades_contexto(construir_regras(normalizar=True),
                                                      execucao_id="contexto-normalizado-09", registrado_em=INSTANTE)

    def gerar(self, *, contexto=None, perfil_id="e5_simetrico", opcoes=None, limite=160, dimensao=3, **kwargs):
        perfil = carregar_perfil(perfil_id, opcoes=opcoes)
        adaptador = AdaptadorTeste(perfil, dimensao=dimensao, limite=limite)
        registro = vetorizar_contexto(contexto or self.contexto, perfil_id=perfil_id, opcoes=opcoes,
                                     adaptador=adaptador, execucao_id="vetores-fixture-09",
                                     registrado_em=INSTANTE, **kwargs)
        return registro, adaptador

    def test_duas_representacoes_por_unidade_e_documento_integral(self):
        antes = deepcopy(self.contexto)
        registro, _ = self.gerar()
        self.assertEqual(self.contexto, antes)
        self.assertEqual(registro["contexto"], antes)
        self.assertEqual(len(registro["representacoes"]), 11)
        self.assertEqual([r["tipo"] for r in registro["representacoes"]], ["periodo", "contextual"] * 5 + ["documento"])
        doc = registro["representacoes"][-1]
        por_id = {a["id"]: a for a in registro["artefatos"]}
        self.assertEqual(por_id[doc["artefato_id"]]["entrada"]["texto"], "query: " + TEXTO)
        self.assertTrue(validar_vetorizacao(registro)["pronto"])

    def test_analista_vetoriza_paragrafos_e_relato_original_inclusive_crlf(self):
        registro, adaptador = self.gerar(contexto=self.normalizado, perfil_id="e5_analista", limite=512)
        self.assertEqual([r["tipo"] for r in registro["representacoes"]], ["paragrafo", "paragrafo", "documento"])
        original = self.normalizado["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["original"]["texto"]
        esperado = [p.strip() for p in original.split("\r\n\r\n")] + [original]
        self.assertEqual([t for lote in adaptador.chamadas for t in lote], ["query: " + t for t in esperado])
        self.assertEqual(registro["processamento"]["limite_tokens"], 504)
        self.assertTrue(all(r["metodo"] == "direto" for r in registro["representacoes"]))
        self.assertTrue(validar_vetorizacao(registro)["pronto"])

    def test_analista_recortes_longos_independentes_sem_media(self):
        perfil = carregar_perfil("e5_analista")
        adaptador = AdaptadorTeste(perfil, limite=512)
        tokenizar = adaptador.tokenizar
        def muitos_tokens(texto):
            t = tokenizar(texto)
            return {k: [v[0]] + [item for item in v[1:-1] for _ in range(20)] + [v[-1]]
                    for k, v in t.items()}
        adaptador.tokenizar = muitos_tokens
        registro = vetorizar_contexto(self.contexto, perfil_id="e5_analista", adaptador=adaptador)
        self.assertTrue(all("derivacao" not in a for a in registro["artefatos"]))
        por_id = {a["id"]: a for a in registro["artefatos"]}
        for rep in registro["representacoes"]:
            self.assertEqual(rep["metodo"], "fragmentado" if len(rep["fragmentos"]) > 1 else "direto")
            if rep["metodo"] == "fragmentado":
                self.assertIsNone(rep["artefato_id"])
            recortes = [por_id[f["artefato_id"]]["entrada"]["texto"][7:] for f in rep["fragmentos"]]
            origem = rep["origem"]["original"]
            self.assertEqual("".join(recortes), TEXTO[origem["inicio"]:origem["fim"]])
            self.assertTrue(all(len(t) == 18 for t in recortes[:-1]))
            self.assertTrue(all(len(por_id[f["artefato_id"]]["entrada"]["input_ids"]) <= 504 for f in rep["fragmentos"]))
        self.assertTrue(validar_vetorizacao(registro)["pronto"])

    def test_dimensao_nao_global_e_formatos_declarados(self):
        for dimensao, formato in ((2, "float16"), (5, "float32"), (7, "float64")):
            with self.subTest(dimensao=dimensao, formato=formato):
                registro, _ = self.gerar(dimensao=dimensao, opcoes={"formato_armazenamento": formato})
                for artefato in registro["artefatos"]:
                    self.assertEqual(len(artefato["valores"]), dimensao)
                    dados = codificar_vetor(artefato["valores"], formato)
                    self.assertEqual(hashlib.sha256(dados).hexdigest(), artefato["sha256_bytes"])
                    self.assertEqual(len(dados), dimensao * {"float16": 2, "float32": 4, "float64": 8}[formato])

    def test_prefixos_e_modelos_sao_opcoes(self):
        opcoes = {"modelo": {"identificacao": "outro/modelo", "revisao": "a" * 40},
                  "prefixo": "", "pooling": "cls", "normalizacao": "nenhuma"}
        registro, _ = self.gerar(opcoes=opcoes, dimensao=6)
        doc = registro["representacoes"][-1]
        alvo = next(a for a in registro["artefatos"] if a["id"] == doc["artefato_id"])
        self.assertEqual(alvo["entrada"]["texto"], TEXTO)
        self.assertNotAlmostEqual(math.hypot(*alvo["valores"]), 1)

    def test_limite_grande_preserva_entrada_sem_fragmentar(self):
        registro, _ = self.gerar(limite=8192)
        self.assertTrue(all(r["metodo"] == "direto" and len(r["fragmentos"]) == 1 for r in registro["representacoes"]))

    def test_fragmentacao_cobre_textos_literais_e_agrega(self):
        registro, _ = self.gerar(limite=23)
        por_id = {a["id"]: a for a in registro["artefatos"]}
        self.assertTrue(any(r["metodo"] == "agregado" for r in registro["representacoes"]))
        for rep in registro["representacoes"]:
            recortes = [por_id[f["artefato_id"]]["entrada"]["texto"][7:] for f in rep["fragmentos"]]
            pos = rep["origem"]["trabalho"]
            self.assertEqual("".join(recortes), TEXTO[pos["inicio"]:pos["fim"]])
            self.assertTrue(all(len(por_id[f["artefato_id"]]["entrada"]["input_ids"]) <= 23 for f in rep["fragmentos"]))
        doc = registro["representacoes"][-1]
        componentes = [por_id[f["artefato_id"]]["valores"] for f in doc["fragmentos"]]
        pesos = [f["peso"] for f in doc["fragmentos"]]
        media = [sum(v[i] * p for v, p in zip(componentes, pesos)) / sum(pesos) for i in range(3)]
        norma = math.hypot(*media)
        esperado = decodificar_vetor(codificar_vetor([v / norma for v in media], "float32"), "float32")
        for x, y in zip(por_id[doc["artefato_id"]]["valores"], esperado):
            self.assertAlmostEqual(x, y)

    def test_media_simples_nao_usa_tokens_como_peso(self):
        registro, _ = self.gerar(limite=23, opcoes={"agregacao": "media_simples"})
        for rep in registro["representacoes"]:
            self.assertTrue(all(f["peso"] == 1 for f in rep["fragmentos"]))

    def test_politica_erro_recusa_antes_da_inferencia(self):
        perfil = carregar_perfil(opcoes={"fragmentacao": "erro"})
        adaptador = AdaptadorTeste(perfil, limite=23)
        with self.assertRaises(ErroVetorizacao):
            vetorizar_contexto(self.contexto, opcoes={"fragmentacao": "erro"}, adaptador=adaptador)
        self.assertEqual(adaptador.chamadas, [])

    def test_prefixo_e_especiais_consumem_orcamento(self):
        with self.assertRaises(ErroVetorizacao):
            self.gerar(limite=9)
        registro, _ = self.gerar(limite=10)
        self.assertEqual(registro["processamento"]["limite_tokens"], 10)

    def test_orcamento_solicitado_nao_excede_capacidade(self):
        registro, _ = self.gerar(limite=160, opcoes={"limite_tokens": 23})
        self.assertEqual(registro["processamento"]["limite_tokens"], 23)
        self.assertEqual(registro["processamento"]["limite_modelo_tokens"], 160)
        with self.assertRaises(ErroVetorizacao):
            self.gerar(limite=160, opcoes={"limite_tokens": 8192})

    def test_tokenizador_sem_tokens_para_espacos_preserva_separadores_finais(self):
        class IgnoraEspacos(AdaptadorTeste):
            def tokenizar(self, texto):
                posicoes = [(i, c) for i, c in enumerate(texto) if not c.isspace()]
                return {"input_ids": [1] + [ord(c) + 10 for _, c in posicoes] + [2],
                        "attention_mask": [1] * (len(posicoes) + 2),
                        "special_tokens_mask": [1] + [0] * len(posicoes) + [1],
                        "offsets": [[0, 0]] + [[i, i + 1] for i, _ in posicoes] + [[0, 0]]}
        perfil = carregar_perfil()
        adaptador = IgnoraEspacos(perfil, limite=20)
        registro = vetorizar_contexto(self.contexto, adaptador=adaptador, registrado_em=INSTANTE)
        por_id = {a["id"]: a for a in registro["artefatos"]}
        doc = registro["representacoes"][-1]
        self.assertEqual("".join(por_id[f["artefato_id"]]["entrada"]["texto"][7:] for f in doc["fragmentos"]), TEXTO)
        self.assertTrue(all(f["tokens_conteudo"] > 0 for f in doc["fragmentos"]))

    def test_fragmentacao_orcamento_pequeno_nao_retokeniza_sufixos_quadraticos(self):
        texto = "a" * 2000 + "."
        linhas = [token("a" * 2000, "a", "NOUN", 0, "ROOT"), token(".", ".", "PUNCT", 0, "punct")]
        fonte = construir_unidades_contexto(construir_regras(texto=texto, linhas=linhas), registrado_em=INSTANTE)
        class Contador(AdaptadorTeste):
            caracteres = 0
            def tokenizar(self, efetiva):
                self.caracteres += len(efetiva)
                return super().tokenizar(efetiva)
        perfil = carregar_perfil()
        adaptador = Contador(perfil, limite=16)
        registro = vetorizar_contexto(fonte, adaptador=adaptador, registrado_em=INSTANTE)
        self.assertTrue(validar_vetorizacao(registro)["pronto"])
        self.assertLess(adaptador.caracteres, 80 * len(texto))

    def test_crlf_mapeia_fragmentos_ao_original(self):
        registro, _ = self.gerar(contexto=self.normalizado, limite=23)
        preparacao = self.normalizado["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
        doc = registro["representacoes"][-1]
        self.assertEqual(doc["origem"]["trabalho"]["fim"], len(preparacao["trabalho"]["texto"]))
        self.assertEqual(doc["origem"]["original"]["fim"], len(TEXTO))
        self.assertTrue(any(f["original"]["fim"] != f["trabalho"]["fim"] for f in doc["fragmentos"]))

    def test_documento_original_e_opcao_explicita_com_mapa_inverso(self):
        registro, _ = self.gerar(contexto=self.normalizado, opcoes={"texto_documento": "original"}, limite=23)
        doc = registro["representacoes"][-1]
        por_id = {a["id"]: a for a in registro["artefatos"]}
        self.assertEqual("".join(por_id[f["artefato_id"]]["entrada"]["texto"][7:] for f in doc["fragmentos"]), TEXTO)
        self.assertEqual(doc["origem"]["campo"], "preparacao.original.texto")

    def test_legado_corte_header_omissoes_e_sem_falsa_cobertura(self):
        registro, _ = self.gerar(perfil_id="e5_legado", opcoes={"corte_caracteres": 20, "cabecalho": "Título"})
        doc = registro["representacoes"][-1]
        alvo = next(a for a in registro["artefatos"] if a["id"] == doc["artefato_id"])
        self.assertEqual(alvo["entrada"]["texto"], "passage: Título\n" + TEXTO[:13])
        self.assertFalse(doc["cobertura_integral"])
        self.assertEqual(doc["omissoes"][0]["original"], {"inicio": 13, "fim": len(TEXTO)})
        self.assertFalse(registro["validacao"]["cobertura"]["cobertura_integral"])

    def test_legado_truncamento_adicional_e_declarado(self):
        registro, _ = self.gerar(perfil_id="e5_legado", limite=25)
        doc = registro["representacoes"][-1]
        self.assertEqual(doc["fragmentos"][0]["trabalho"]["fim"], 13)
        self.assertEqual(doc["omissoes"][0]["trabalho"]["inicio"], 13)

    def test_legado_juncao_paragrafos_opcional_e_rastreavel(self):
        registro, _ = self.gerar(perfil_id="e5_legado", opcoes={"juntar_paragrafos": True})
        doc = registro["representacoes"][-1]
        por_id = {a["id"]: a for a in registro["artefatos"]}
        self.assertEqual(por_id[doc["artefato_id"]]["entrada"]["texto"],
                         "passage: \nÁrvore\tverde.  Casa azul! \t Céu. Outro.  João saiu.")
        recortes = doc["fragmentos"][0]["recortes_origem"]
        self.assertEqual([r["tipo"] for r in recortes], ["copia", "insercao", "copia"])
        self.assertEqual(recortes[1]["texto"], " ")
        self.assertEqual([o["original"] for o in doc["omissoes"]],
                         [{"inicio": 0, "fim": 2}, {"inicio": 34, "fim": 38}, {"inicio": 56, "fim": 58}])
        self.assertEqual(registro["contexto"], self.contexto)
        self.assertFalse(doc["cobertura_integral"])

    def test_legado_composicao_cortada_mapeia_original_crlf(self):
        registro, _ = self.gerar(contexto=self.normalizado, perfil_id="e5_legado",
                                opcoes={"juntar_paragrafos": True, "cabecalho": "T", "corte_caracteres": 45})
        doc = registro["representacoes"][-1]
        self.assertTrue(doc["omissoes"])
        self.assertTrue(validar_vetorizacao(registro)["pronto"])
        adulterado = deepcopy(registro)
        adulterado["representacoes"][-1]["fragmentos"][0]["recortes_origem"][0]["original"]["inicio"] = 1
        with self.assertRaises(ErroVetorizacao):
            validar_vetorizacao(adulterado)

    def test_json_roundtrip_preserva_bytes_e_fonte(self):
        registro, _ = self.gerar(opcoes={"formato_armazenamento": "float16"}, limite=23)
        relido = json.loads(json.dumps(registro, ensure_ascii=False, allow_nan=False))
        self.assertEqual(validar_vetorizacao(registro), validar_vetorizacao(relido))
        self.assertEqual(registro, relido)

    def test_unicode_emoji_combinante_pontuacao_tab_e_linhas_vazias(self):
        texto = "  Árvore 😀 e\u0301.\t\r\n\r\nNão!  "
        linhas = [token("Árvore", "árvore", "NOUN", 0, "ROOT"),
                  token("😀", "😀", "SYM", 0, "dep"), token("e\u0301.", "e\u0301.", "NOUN", 0, "dep"),
                  token("Não", "não", "ADV", 3, "ROOT"), token("!", "!", "PUNCT", 3, "punct")]
        fonte = construir_unidades_contexto(construir_regras(texto=texto, linhas=linhas, normalizar=True),
                                           registrado_em=INSTANTE)
        registro, _ = self.gerar(contexto=fonte, limite=14)
        doc = registro["representacoes"][-1]
        por_id = {a["id"]: a for a in registro["artefatos"]}
        texto_work = texto.replace("\r\n", "\n")
        self.assertEqual("".join(por_id[f["artefato_id"]]["entrada"]["texto"][7:] for f in doc["fragmentos"]), texto_work)
        self.assertIn("e\u0301", texto_work)
        self.assertEqual(doc["origem"]["original"]["fim"], len(texto))

    def test_vazio_e_espacos_sem_vetor_artificial(self):
        for texto in ("", " \t\r\n\r\n "):
            fonte = construir_unidades_contexto(construir_regras(texto=texto, linhas=[]), registrado_em=INSTANTE)
            registro, adaptador = self.gerar(contexto=fonte)
            self.assertEqual(registro["artefatos"], [])
            self.assertEqual(adaptador.chamadas, [])
            self.assertEqual(registro["validacao"]["estado"], "sem_conteudo")
            self.assertEqual(registro["representacoes"][0]["estado"], "sem_conteudo")

    def test_validador_nao_importa_bibliotecas_de_modelo(self):
        registro, _ = self.gerar()
        script = """import builtins,json,sys
real = builtins.__import__
def protegido(nome,*args,**kwargs):
    if nome.split('.')[0] in {'torch','transformers','sentence_transformers','fastembed','spacy'}:
        raise RuntimeError('inferência proibida na leitura: '+nome)
    return real(nome,*args,**kwargs)
builtins.__import__ = protegido
from vetorizacao import validar_vetorizacao
print(validar_vetorizacao(json.load(sys.stdin))['pronto'])
"""
        processo = subprocess.run([sys.executable, "-c", script], input=json.dumps(registro),
                                  text=True, capture_output=True, timeout=20)
        self.assertEqual(processo.returncode, 0, processo.stderr)
        self.assertEqual(processo.stdout.strip(), "True")

    def test_cache_reutiliza_computacao_sem_perder_associacoes(self):
        cache, progresso = {}, []
        primeiro, _ = self.gerar(salvar_cache=lambda k, a: cache.setdefault(k, a), progresso=progresso.append)
        segundo, adaptador = self.gerar(obter_cache=cache.get)
        self.assertEqual(primeiro, segundo)
        self.assertEqual(adaptador.chamadas, [])
        self.assertEqual(progresso[-1]["concluidos"], progresso[-1]["total"])
        self.assertEqual(len(segundo["representacoes"]), 11)
        self.assertLess(len(segundo["artefatos"]), 11)

    def test_cache_adulterado_nao_e_aceito(self):
        cache = {}
        self.gerar(salvar_cache=lambda k, a: cache.setdefault(k, a))
        next(iter(cache.values()))["valores"][0] += 0.01
        with self.assertRaises(ErroVetorizacao):
            self.gerar(obter_cache=cache.get)

    def test_fonte_invalidada_e_prontidao_falsa(self):
        for mutacao in (lambda x: x["unidades"][0]["foco"].update(texto="Inventado"),
                        lambda x: x["validacao"].update(pronto_para_etapa_09=False)):
            fonte = deepcopy(self.contexto)
            mutacao(fonte)
            with self.assertRaises(ErroVetorizacao):
                self.gerar(contexto=fonte)

    def test_modelo_erros_explicitos_dimensao_nan_zero_e_falta_vetores(self):
        perfil = carregar_perfil()
        for saida in ([[1.0]], [[1.0, float("nan"), 3.0]], [[0.0, 0.0, 0.0]], [], [3]):
            adaptador = AdaptadorTeste(perfil)
            with patch.object(adaptador, "gerar", return_value=saida):
                with self.assertRaises(ErroVetorizacao):
                    vetorizar_contexto(self.contexto, adaptador=adaptador)

    def test_adulteracoes_do_registro_detectadas(self):
        original, _ = self.gerar(limite=23)
        mutacoes = [
            lambda r: r.update(contexto_sha256="a" * 64),
            lambda r: r.update(documento_id="estrangeiro"),
            lambda r: r.update(configuracao_sha256="b" * 64),
            lambda r: r.update(espaco_vetorial_id="c" * 64),
            lambda r: r["representacoes"].pop(),
            lambda r: r["representacoes"][0].update(texto_sha256="d" * 64),
            lambda r: r["representacoes"][0]["fragmentos"][0]["trabalho"].update(inicio=3),
            lambda r: r["representacoes"][0].update(cobertura_integral=False),
            lambda r: r["artefatos"][0].update(sha256_bytes="e" * 64),
            lambda r: r["artefatos"][0].update(dimensao=True),
            lambda r: r["artefatos"][0]["entrada"]["offsets"].__setitem__(1, [0, 10000]),
            lambda r: r["artefatos"][0]["entrada"]["input_ids"].__setitem__(0, True),
            lambda r: r["validacao"].update(pronto=False),
        ]
        for mutacao in mutacoes:
            with self.subTest(mutacao=mutacao):
                registro = deepcopy(original)
                mutacao(registro)
                with self.assertRaises(ErroVetorizacao):
                    validar_vetorizacao(registro)

    def test_consulta_defensiva_e_erro_estrangeiro(self):
        registro, _ = self.gerar()
        consultado = consultar_representacao(registro, representacao_id=registro["representacoes"][0]["id"])
        self.assertEqual(consultado["representacao"], registro["representacoes"][0])
        consultado["representacao"]["origem"]["unidade_id"] = "outro"
        self.assertNotEqual(consultado["representacao"], registro["representacoes"][0])
        with self.assertRaises(ErroVetorizacao):
            consultar_representacao(registro, representacao_id="estrangeiro")

    def test_compatibilidade_e5_query_passage_apenas_recuperacao(self):
        consulta, _ = self.gerar(perfil_id="e5_consulta")
        conteudo, _ = self.gerar(perfil_id="e5_conteudo")
        self.assertNotEqual(consulta["espaco_vetorial_id"], conteudo["espaco_vetorial_id"])
        self.assertTrue(verificar_compatibilidade(consulta, conteudo, operacao="recuperacao")["compativel"])
        self.assertFalse(verificar_compatibilidade(consulta, conteudo)["compativel"])
        self.assertFalse(verificar_compatibilidade(conteudo, consulta, operacao="recuperacao")["compativel"])

    def test_backend_modelo_revisao_pooling_separam_espacos(self):
        base, _ = self.gerar()
        for opcoes in ({"backend": "fastembed"}, {"revisao": "f" * 40}, {"pooling": "cls"},
                       {"modelo": {"identificacao": "outro", "revisao": "a" * 40}}):
            outro, _ = self.gerar(opcoes=opcoes)
            self.assertFalse(verificar_compatibilidade(base, outro)["compativel"])
        self.assertTrue(verificar_compatibilidade(base, base)["compativel"])

    def test_pesos_efetivos_distintos_nao_compartilham_espaco(self):
        perfil = carregar_perfil()
        def gerar_com_pesos(sha):
            adaptador = AdaptadorTeste(perfil)
            descricao = adaptador.descrever()
            descricao["artefatos"] = {"onnx": {"sha256": sha, "caminho": "/local/model.onnx"}}
            with patch.object(adaptador, "descrever", return_value=descricao):
                return vetorizar_contexto(self.contexto, adaptador=adaptador, registrado_em=INSTANTE)
        a, b = gerar_com_pesos("a" * 64), gerar_com_pesos("b" * 64)
        self.assertFalse(verificar_compatibilidade(a, b)["compativel"])

    def test_derivacao_preserva_componentes_e_nova_execucao(self):
        registro, _ = self.gerar(limite=23)
        antes = deepcopy(registro)
        ids = [registro["representacoes"][0]["id"], registro["representacoes"][-1]["id"]]
        derivado = derivar_vetor(registro, ids, execucao_id="derivado-09", registrado_em=INSTANTE)
        self.assertEqual(registro, antes)
        self.assertEqual(derivado["contexto"], registro["contexto"])
        self.assertEqual(derivado["representacoes"][0]["metodo"], "derivado")
        self.assertEqual(derivado["derivacao_execucao"]["pesos"], [1, 1])
        self.assertTrue(validar_vetorizacao(derivado)["pronto"])

    def test_derivacao_ponderada_e_pesos_invalidos(self):
        registro, _ = self.gerar()
        ids = [r["id"] for r in registro["representacoes"][:2]]
        derivado = derivar_vetor(registro, ids, metodo="media_ponderada", pesos=[1, 3])
        self.assertTrue(validar_vetorizacao(derivado)["pronto"])
        for pesos in ([0, 1], [True, 1], [float("inf"), 1], [1]):
            with self.assertRaises(ErroVetorizacao):
                derivar_vetor(registro, ids, metodo="media_ponderada", pesos=pesos)

    def test_derivacao_adulteracoes_origem_hash_e_orfaos(self):
        registro, _ = self.gerar()
        ids = [registro["representacoes"][0]["id"]]
        derivado = derivar_vetor(registro, ids)
        mutacoes = [lambda r: r["derivacao_execucao"].update(origem_sha256="a" * 64),
                    lambda r: r["derivacao_execucao"]["representacoes_origem"][0]["origem"].update(unidade_id="estrangeira"),
                    lambda r: r["derivacao_execucao"]["representacoes_origem"][0].update(texto_sha256="b" * 64),
                    lambda r: r["derivacao_execucao"]["manifesto_origem"]["representacoes"][0].update(texto_sha256="c" * 64),
                    lambda r: r["artefatos"].append(deepcopy(registro["artefatos"][-1]))]
        for mutacao in mutacoes:
            adulterado = deepcopy(derivado)
            mutacao(adulterado)
            with self.assertRaises(ErroVetorizacao):
                validar_vetorizacao(adulterado)

    def test_derivar_novamente_preserva_fonte08_uma_vez(self):
        registro, _ = self.gerar()
        primeiro = derivar_vetor(registro, [registro["representacoes"][0]["id"]])
        segundo = derivar_vetor(primeiro, [primeiro["representacoes"][0]["id"]])
        self.assertTrue(validar_vetorizacao(segundo)["pronto"])
        self.assertNotIn("contexto", segundo["derivacao_execucao"]["manifesto_origem"])
        self.assertEqual(json.dumps(segundo).count('"etapa": "08_unidades_contexto"'), 1)

    def test_data_e_ids_pelo_chamador_e_geracao_real(self):
        registro, _ = self.gerar()
        self.assertEqual(registro["registrado_em"], INSTANTE)
        perfil = carregar_perfil()
        outro = vetorizar_contexto(self.contexto, adaptador=AdaptadorTeste(perfil))
        self.assertNotEqual(outro["execucao_id"], registro["execucao_id"])
        for argumentos in ({"execucao_id": ""}, {"registrado_em": "2026-10-08"}, {"execucao_id": self.contexto["execucao_id"]}):
            with self.assertRaises(ErroVetorizacao):
                vetorizar_contexto(self.contexto, adaptador=AdaptadorTeste(perfil), **argumentos)

    def test_serializador_little_endian_e_erros(self):
        self.assertEqual(codificar_vetor([1.0, -2.0], "float32"), struct.pack("<ff", 1, -2))
        for valores in ([], [True], [float("nan")], [float("inf")], [1e100]):
            with self.assertRaises(ErroVetorizacao):
                codificar_vetor(valores, "float16")
        for dados in (b"", b"x", struct.pack("<f", float("nan"))):
            with self.assertRaises(ErroVetorizacao):
                decodificar_vetor(dados, "float32")
        for formato in ({}, [], None, True):
            with self.assertRaises(ErroVetorizacao):
                codificar_vetor([1.0], formato)


if __name__ == "__main__":
    unittest.main()
