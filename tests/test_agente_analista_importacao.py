"""Exportações completas de referência, sem rede ou inferência neural."""

from copy import deepcopy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from fixtures_contexto import TEXTO, INSTANTE, construir_regras
from test_vetorizacao import AdaptadorTeste
from perfis_vetorizacao import carregar_perfil
from unidades_contexto import construir_unidades_contexto
from vetorizacao import vetorizar_contexto
from agente_analista.importacao import (ErroImportacao, importar_vetorizacao,
                                        conferir_compatibilidade)
import agente_analista.importacao as modulo_importacao
from contratos_vetorizacao import (codificar_vetor, configuracao_vetorizacao,
                                  hash_json, perfil_compatibilidade, sha256)
from preparacao import mapear_intervalos


def construir_exportacao(*, normalizar=False, dimensao=1024, limite=160,
                        perfil_id="e5_simetrico", opcoes=None):
    """Gere uma fixture da exportação atual, preservando duasparas/CRLF.

    O adaptador determinístico é exclusivo dos testes. Seu uso não demonstra
    que os embeddings de um modelo neural possuem qualidade semântica.
    """
    perfil = carregar_perfil(perfil_id, opcoes=opcoes)
    contexto = construir_unidades_contexto(construir_regras(normalizar=normalizar),
                                          execucao_id="contexto-importacao-fixture",
                                          registrado_em=INSTANTE)
    return vetorizar_contexto(contexto, perfil_id=perfil_id, opcoes=opcoes,
                             adaptador=AdaptadorTeste(perfil, dimensao=dimensao, limite=limite),
                             execucao_id="vetores-importacao-fixture", registrado_em=INSTANTE)


exportacao_teste = construir_exportacao


def construir_exportacao_portatil(*, dimensao=3, normalizar=False, limite=512,
                                 agregar=True, schema_version="1.1.0",
                                 natureza="inferencia_real"):
    """Fixture do contrato b64. Valores determinísticos, sem modelo neural.

    A declaração de inferência real permite verificar o contrato de importação;
    hashes e metadados, por si só, não autenticam uma inferência externa.
    """
    contexto = construir_unidades_contexto(construir_regras(normalizar=normalizar),
                                          execucao_id="contexto-portatil-fixture",
                                          registrado_em=INSTANTE)
    prep = contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
    perfil = carregar_perfil()
    modelo = {"identificacao": perfil["modelo"]["identificacao"], "revisao": perfil["modelo"]["revisao"],
              "tokenizador": perfil["tokenizador"], "dimensao": dimensao, "limite_tokens": 512,
              "natureza": natureza, "ambiente": {"backend": "fixture-sem-inferencia",
              "versao_backend": "teste-1", "dispositivo": "cpu", "precisao_calculo": "float32"}}
    config = configuracao_vetorizacao({"perfil": "similaridade", "max_tokens": limite, "agregar": agregar})
    compatibilidade = perfil_compatibilidade(modelo, config)
    descricao_geracao = {"compatibilidade": compatibilidade, "ambiente": modelo["ambiente"],
                        "tamanho_lote": config["tamanho_lote"], "modulo_version": schema_version}
    geracao = {"descricao": descricao_geracao, "sha256": hash_json(descricao_geracao)}
    execucao = "vetores-portatil-fixture"
    alvos = modulo_importacao._portatil_alvos(contexto, prep, execucao,
                                             incluir_paragrafos=schema_version != "1.0.0")
    artefatos = {}
    representacoes = []
    capacidade = limite - len(config["prefixo"]) - 2
    for alvo in alvos:
        blocos = []
        for numero, inicio in enumerate(range(0, len(alvo["texto"]), capacidade)):
            fim = min(inicio + capacidade, len(alvo["texto"]))
            texto = alvo["texto"][inicio:fim]
            efetivo = config["prefixo"] + texto
            tokens = {"input_ids": [1] + [ord(c) + 10 for c in efetivo] + [2],
                      "offset_mapping": [[0, 0]] + [[i, i + 1] for i in range(len(efetivo))] + [[0, 0]],
                      "special_tokens_mask": [1] + [0] * len(efetivo) + [1]}
            entrada = modulo_importacao._portatil_entrada(texto, config["prefixo"], tokens)
            artefato_id = (modulo_importacao._portatil_artefato_id(entrada, geracao)
                           if entrada["tokens_conteudo"] else None)
            if artefato_id:
                if artefato_id not in artefatos:
                    valores = [float(i + 1) for i in range(dimensao)]
                    norma = sum(v * v for v in valores) ** 0.5
                    artefatos[artefato_id] = {"id": artefato_id, "entrada_modelo": entrada,
                                            "armazenamento": codificar_vetor([v / norma for v in valores], dimensao),
                                            "origem_calculo": "gerado"}
            ti, tf = alvo["trabalho"]["inicio"] + inicio, alvo["trabalho"]["inicio"] + fim
            blocos.append({"id": f"{alvo['id']}:bloco:{numero}", "ordem": numero,
                           "relativo": {"inicio": inicio, "fim": fim},
                           "trabalho": {"inicio": ti, "fim": tf},
                           "original": mapear_intervalos(prep, [(ti, tf)])[0],
                           "texto": texto, "sha256_texto": sha256(texto), "entrada_modelo": entrada,
                           "peso_tokens": entrada["tokens_conteudo"], "artefato_id": artefato_id})
        representacoes.append(modulo_importacao._portatil_concluir_representacao(
            alvo, blocos, artefatos, contexto, prep, compatibilidade, config, dimensao))
    lista_artefatos = list(artefatos.values())
    return {"schema_version": schema_version, "etapa": "09_vetorizacao", "execucao_id": execucao,
            "contexto_execucao_id": contexto["execucao_id"], "documento_id": contexto["documento_id"],
            "registrado_em": INSTANTE, "contexto": contexto, "contexto_sha256": hash_json(contexto),
            "coordenadas": deepcopy(contexto["coordenadas"]), "modelo": modelo, "configuracao": config,
            "compatibilidade": compatibilidade, "geracao": geracao,
            "processamento": {"modulo": {"nome": "vetorizacao", "versao": schema_version},
                              "limites_recursos": modulo_importacao._portatil_limites()},
            "representacoes": representacoes, "artefatos": lista_artefatos,
            "validacao": modulo_importacao._portatil_relatorio(modelo, representacoes, lista_artefatos)}


def manifesto_teste(dimensao=3):
    perfil = carregar_perfil()
    return {"modelo": perfil["modelo"]["identificacao"], "revisao": perfil["modelo"]["revisao"],
            "dimensao": dimensao, "normalizacao": "L2", "tipo_vetor": "float32",
            "prefixo_consultas": "query: ", "prefixo_passagens": "passage: "}


class ImportacaoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registro = construir_exportacao(dimensao=3)

    def test_periodo_janela_documento_preservados_sem_gerar_novamente(self):
        antes = deepcopy(self.registro)
        with patch.object(AdaptadorTeste, "gerar", side_effect=AssertionError("Não inferir")):
            importado = importar_vetorizacao(self.registro)
        self.assertEqual(self.registro, antes)
        self.assertEqual(importado["relato"]["texto"], TEXTO)
        self.assertEqual(len(importado["consultas"]), 11)
        self.assertEqual([c["tipo"] for c in importado["consultas"]],
                         ["periodo", "contextual"] * 5 + ["documento"])
        por_id = {a["id"]: a for a in self.registro["artefatos"]}
        for consulta in importado["consultas"]:
            self.assertEqual(consulta["texto"], TEXTO[consulta["inicio"]:consulta["fim"]])
            self.assertEqual(consulta["vetor"], por_id[consulta["artefato_id"]]["valores"])
        self.assertIsNone(conferir_compatibilidade(importado, manifesto_teste()))

    def test_importacao_nao_carrega_bibliotecas_de_inferencia(self):
        script = ("import json,sys; "
                  "from agente_analista.importacao import importar_vetorizacao; "
                  "importar_vetorizacao(json.load(sys.stdin)); "
                  "print(json.dumps([n for n in ('torch','transformers','spacy','sentence_transformers') "
                  "if n in sys.modules]))")
        resultado = subprocess.run([sys.executable, "-c", script],
                                   input=json.dumps(self.registro), text=True,
                                   capture_output=True, check=True)
        self.assertEqual(json.loads(resultado.stdout), [])

    def test_normalizacao_crlf_preserva_original_e_mapeamentos(self):
        registro = construir_exportacao(dimensao=3, normalizar=True)
        importado = importar_vetorizacao(registro)
        self.assertEqual(importado["relato"]["texto"], TEXTO)
        doc = importado["consultas"][-1]
        self.assertEqual(doc["texto"], TEXTO)
        self.assertEqual(doc["texto_vetorizado"], TEXTO.replace("\r\n", "\n"))
        self.assertEqual(doc["fim"], len(TEXTO))

    def test_usa_vetor_agregado_exportado_sem_desfazer_fragmentacao(self):
        registro = construir_exportacao(dimensao=3, limite=23)
        importado = importar_vetorizacao(registro)
        doc = importado["consultas"][-1]
        agregado = next(a for a in registro["artefatos"] if a["id"] == doc["artefato_id"])
        self.assertIn("derivacao", agregado)
        self.assertEqual(doc["vetor"], agregado["valores"])

    def test_perfil_consulta_e_simetrico_query_sao_aceitos(self):
        for perfil_id in ("e5_consulta", "e5_simetrico"):
            with self.subTest(perfil_id=perfil_id):
                importado = importar_vetorizacao(construir_exportacao(dimensao=3, perfil_id=perfil_id))
                self.assertIsNone(conferir_compatibilidade(importado, manifesto_teste()))

    def test_copia_importada_nao_compartilha_mutacoes(self):
        importado = importar_vetorizacao(self.registro)
        importado["consultas"][0]["vetor"][0] = 0
        importado["registro"]["contexto"]["unidades"][0]["foco"]["texto"] = "alterado"
        self.assertEqual(self.registro, construir_exportacao(dimensao=3))

    def test_hash_vetor_texto_ids_cobertura_e_estado_adulterados_rejeitados(self):
        mutacoes = [lambda r: r["artefatos"][0]["valores"].__setitem__(0, 0.0),
                    lambda r: r["representacoes"][0].__setitem__("texto_sha256", "0" * 64),
                    lambda r: r["representacoes"][0].__setitem__("id", "duplicado"),
                    lambda r: r["representacoes"].pop(),
                    lambda r: r["representacoes"][0].__setitem__("estado", "sem_conteudo")]
        for mutacao in mutacoes:
            with self.subTest(mutacao=mutacao):
                registro = deepcopy(self.registro)
                mutacao(registro)
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(registro)

    def test_nao_aceita_passage_ou_formato_sem_precision_float32(self):
        for opcoes in ({"prefixo": "passage: "}, {"formato_armazenamento": "float16"},
                       {"pooling": "cls"}, {"normalizacao": "nenhuma"}):
            with self.subTest(opcoes=opcoes):
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(construir_exportacao(dimensao=3, opcoes=opcoes))

    def test_incompatibilidade_indica_campo_sem_expor_relato(self):
        importado = importar_vetorizacao(self.registro)
        for campo, valor in (("modelo", "outro/modelo"), ("revisao", "a" * 40),
                             ("dimensao", 1024), ("prefixo_consultas", "passage: ")):
            with self.subTest(campo=campo):
                manifesto = {**manifesto_teste(), campo: valor}
                with self.assertRaises(ErroImportacao) as erro:
                    conferir_compatibilidade(importado, manifesto)
                self.assertNotIn(TEXTO, str(erro.exception))

    def test_json_nao_finito_ciclico_e_tipo_arbitrario_rejeitados(self):
        ciclo = deepcopy(self.registro)
        ciclo["ciclo"] = ciclo
        for registro in (None, [], {**self.registro, "arbitrario": object()},
                         {**self.registro, "valor": float("nan")}, ciclo):
            with self.subTest(tipo=type(registro)):
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(registro)

    def test_metadados_com_tipos_incorretos_retornam_erro_seguro(self):
        for mutacao in (lambda r: r.__setitem__("validacao", []),
                        lambda r: r.__setitem__("registrado_em", 10),
                        lambda r: r["contexto"].__setitem__("validacao", [])):
            registro = deepcopy(self.registro)
            mutacao(registro)
            with self.assertRaises(ErroImportacao) as erro:
                importar_vetorizacao(registro)
            self.assertNotIn(TEXTO, str(erro.exception))

    def test_limites_de_representacoes_aplicados_antes_de_validar_origem(self):
        registro = deepcopy(self.registro)
        registro["representacoes"] = [registro["representacoes"][0]] * 1025
        with self.assertRaisesRegex(ErroImportacao, "1.024"):
            importar_vetorizacao(registro)

    def test_formato_portatil_base64_1_1_preserva_vetores_e_origem(self):
        registro = construir_exportacao_portatil(normalizar=True)
        importado = importar_vetorizacao(registro)
        self.assertEqual(len(registro["representacoes"]), 13)
        self.assertEqual(len(importado["consultas"]), 11)
        self.assertEqual(importado["relato"]["texto"], TEXTO)
        self.assertEqual(importado["consultas"][-1]["texto_vetorizado"], TEXTO.replace("\r\n", "\n"))
        self.assertEqual(importado["registro"], registro)
        self.assertIsNone(conferir_compatibilidade(importado, manifesto_teste()))

    def test_formato_portatil_1_0_anterior_sem_paragrafos_tambem_importa(self):
        registro = construir_exportacao_portatil(schema_version="1.0.0")
        self.assertEqual(len(importar_vetorizacao(registro)["consultas"]), 11)

    def test_formato_portatil_agregado_e_blocos_prontos_sem_reinferir(self):
        for agregar in (True, False):
            with self.subTest(agregar=agregar):
                registro = construir_exportacao_portatil(limite=23, agregar=agregar)
                importado = importar_vetorizacao(registro)
                doc = [c for c in importado["consultas"] if c["tipo"] == "documento"]
                self.assertTrue(doc)
                # Bloco só de espaços permanece na exportação e não recebe
                # vetor artificial quando o produtor escolheu não agregar.
                self.assertEqual("".join(c["texto_vetorizado"] for c in doc).rstrip(), TEXTO.rstrip())
                self.assertTrue(all(len(c["vetor"]) == 3 for c in doc))

    def test_formato_portatil_hash_bytes_base64_e_mapa_adulterados_rejeitados(self):
        mutacoes = [lambda r: r["artefatos"][0]["armazenamento"].__setitem__("sha256_bytes", "0" * 64),
                    lambda r: r["artefatos"][0]["armazenamento"].__setitem__("base64", "invalido"),
                    lambda r: r["representacoes"][0]["blocos"][0]["original"].__setitem__("inicio", 0),
                    lambda r: r["representacoes"].pop(),
                    lambda r: r["representacoes"][0]["blocos"][0]["entrada_modelo"]["input_ids"].__setitem__(0, 10)]
        for mutacao in mutacoes:
            registro = construir_exportacao_portatil()
            mutacao(registro)
            with self.assertRaises(ErroImportacao):
                importar_vetorizacao(registro)

    def test_formato_portatil_nao_aceita_registro_de_simulacao(self):
        with self.assertRaisesRegex(ErroImportacao, "inferência real"):
            importar_vetorizacao(construir_exportacao_portatil(natureza="simulado_teste"))


if __name__ == "__main__":
    unittest.main()
