"""Seleções explícitas e exportações individuais, sem executar o produtor."""

from copy import deepcopy
import hashlib
import json
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch

from fixtures_importacao import TEXTO, construir_exportacao
from agente_analista.contratos_importacao import hash_json
from agente_analista.importacao import ErroImportacao, importar_vetorizacao
import agente_analista.importacao as importacao


def exportacao_contextos(**opcoes):
    return construir_exportacao(
        dimensao=3, perfil_id="e5_analista",
        opcoes={"estrategia_consultas": "paragrafos_contextos_documento"}, **opcoes)


def exportacao_individual(registro, representacao_id):
    """Reproduza o JSON público individual usando somente dados congelados.

    O endpoint do Língua exporta um objeto ``representacao`` e suas referências,
    junto com os metadados abaixo. Ele não exporta a chave ``etapa``.
    """
    representacao = next(r for r in registro["representacoes"] if r["id"] == representacao_id)
    referencias = {representacao["artefato_id"]} | {
        f["artefato_id"] for f in representacao["fragmentos"]}
    campos = ("execucao_id", "contexto_execucao_id", "configuracao_sha256",
              "espaco_vetorial_id", "schema_version", "documento_id", "registrado_em",
              "contexto", "contexto_sha256", "perfil", "processamento")
    return {
        **{campo: deepcopy(registro[campo]) for campo in campos},
        "representacao": deepcopy(representacao),
        "artefatos": deepcopy([a for a in registro["artefatos"] if a["id"] in referencias]),
    }


def _rehash_artefato(registro, artefato):
    """Renove IDs após uma adulteração para alcançar a conferência semântica."""
    anterior = artefato["id"]
    dados = struct.pack("<" + "f" * len(artefato["valores"]), *artefato["valores"])
    artefato["sha256_bytes"] = hashlib.sha256(dados).hexdigest()
    artefato["id"] = "sha256:" + hash_json({k: v for k, v in artefato.items() if k != "id"})
    for rep in registro["representacoes"]:
        if rep["artefato_id"] == anterior:
            rep["artefato_id"] = artefato["id"]
        for fragmento in rep["fragmentos"]:
            if fragmento["artefato_id"] == anterior:
                fragmento["artefato_id"] = artefato["id"]


class SelecaoVetorialTests(unittest.TestCase):
    def selecionar(self, registro, ids):
        return importacao.selecionar_representacoes(registro, ids)

    def assert_consultas_preservadas(self, registro, importado, representacoes):
        self.assertEqual(importado["relato"]["texto"], TEXTO)
        por_id = {a["id"]: a for a in registro["artefatos"]}
        esperados = [f for r in representacoes for f in r["fragmentos"]]
        self.assertEqual(len(importado["consultas"]), len(esperados))
        for consulta, fragmento in zip(importado["consultas"], esperados):
            posicao = fragmento["original"]
            self.assertEqual((consulta["inicio"], consulta["fim"]),
                             (posicao["inicio"], posicao["fim"]))
            self.assertEqual(consulta["texto"], TEXTO[posicao["inicio"]:posicao["fim"]])
            self.assertEqual(consulta["texto_vetorizado"], consulta["texto"])
            self.assertEqual(consulta["vetor"], por_id[fragmento["artefato_id"]]["valores"])

    def test_execucoes_integrais_anteriores_e_novas_permanecem_aceitas(self):
        for registro in (construir_exportacao(dimensao=3),
                         construir_exportacao(dimensao=3, perfil_id="e5_analista"),
                         exportacao_contextos()):
            with self.subTest(perfil=registro["perfil"]):
                antes = deepcopy(registro)
                importado = importar_vetorizacao(registro)
                self.assertTrue(importado["consultas"])
                self.assertEqual(importado["registro"], registro)
                self.assertEqual(registro, antes)

    def test_selecao_de_paragrafo_documento_contextos_ou_mista_preserva_tudo(self):
        for fragmentar in (False, True):
            registro = exportacao_contextos(fragmentar=fragmentar)
            reps = registro["representacoes"]
            grupos = ([reps[0]], [reps[-1]],
                      [r for r in reps if r["tipo"] == "contextual"],
                      [reps[0], reps[3], reps[-1]])
            for escolhidas in grupos:
                with self.subTest(fragmentar=fragmentar, tipos=[r["tipo"] for r in escolhidas]):
                    antes = deepcopy(registro)
                    # A escolha pode chegar em ordem arbitrária; o arquivo preserva
                    # a ordem e os IDs da execução que originou cada representação.
                    ids = [r["id"] for r in reversed(escolhidas)]
                    selecionado = self.selecionar(registro, ids)
                    self.assertEqual(selecionado["representacoes"], escolhidas)
                    self.assertEqual(selecionado["selecao_representacoes"], {
                        "versao": "1.0.0", "representacao_ids": [r["id"] for r in escolhidas],
                        "representacoes_origem_total": len(reps)})
                    with patch.dict(sys.modules, {"torch": None, "transformers": None,
                                                   "sentence_transformers": None}):
                        importado = importar_vetorizacao(selecionado)
                    self.assert_consultas_preservadas(registro, importado, escolhidas)
                    self.assertIs(importado["fonte"]["selecao_representacoes"], True)
                    self.assertEqual(importado["registro"], selecionado)
                    self.assertEqual(registro, antes)
                    referencias = {f["artefato_id"] for r in escolhidas for f in r["fragmentos"]}
                    self.assertEqual({a["id"] for a in selecionado["artefatos"]}, referencias)

    def test_crlf_normalizado_preserva_recortes_do_texto_original(self):
        registro = exportacao_contextos(normalizar=True)
        escolhidas = [registro["representacoes"][2], registro["representacoes"][-1]]
        selecionado = self.selecionar(registro, [r["id"] for r in escolhidas])
        importado = importar_vetorizacao(selecionado)
        self.assert_consultas_preservadas(registro, importado, escolhidas)
        self.assertIn("\r\n\r\n", importado["consultas"][-1]["texto_vetorizado"])

    def test_periodos_contextos_e_documento_agregado_podem_ser_selecionados(self):
        registro = construir_exportacao(dimensao=3, limite=23)
        por_id = {a["id"]: a for a in registro["artefatos"]}
        reps = registro["representacoes"]
        for escolhidas in ([reps[0]], [r for r in reps if r["tipo"] == "contextual"],
                           [reps[-1]], [reps[0], reps[1], reps[-1]]):
            with self.subTest(tipos=[r["tipo"] for r in escolhidas]):
                selecionado = self.selecionar(registro, [r["id"] for r in escolhidas])
                importado = importar_vetorizacao(selecionado)
                self.assertEqual(len(importado["consultas"]), len(escolhidas))
                referencias = set()
                for consulta, rep in zip(importado["consultas"], escolhidas):
                    self.assertEqual(consulta["tipo"], rep["tipo"])
                    self.assertEqual(consulta["vetor"], por_id[rep["artefato_id"]]["valores"])
                    referencias.add(rep["artefato_id"])
                    referencias.update(f["artefato_id"] for f in rep["fragmentos"])
                self.assertEqual({a["id"] for a in selecionado["artefatos"]}, referencias)

    def test_exportacao_individual_rica_de_cada_tipo_e_recortes_importa(self):
        for perfil, fragmentar in (("e5_simetrico", False), ("e5_analista", False),
                                  ("e5_analista", True)):
            registro = (construir_exportacao(dimensao=3) if perfil == "e5_simetrico"
                        else exportacao_contextos(fragmentar=fragmentar))
            for representacao in registro["representacoes"]:
                with self.subTest(tipo=representacao["tipo"], fragmentar=fragmentar):
                    individual = exportacao_individual(registro, representacao["id"])
                    antes = deepcopy(individual)
                    self.assertNotIn("etapa", individual)
                    importado = importar_vetorizacao(individual)
                    self.assertEqual(individual, antes)
                    self.assertEqual(importado["registro"], individual)
                    self.assertIs(importado["fonte"]["selecao_representacoes"], True)
                    self.assertTrue(all(c["tipo"] == representacao["tipo"]
                                        for c in importado["consultas"]))
                    if perfil == "e5_analista":
                        self.assert_consultas_preservadas(registro, importado, [representacao])
                    else:
                        self.assertEqual(len(importado["consultas"]), 1)
                        self.assertEqual(importado["consultas"][0]["vetor"], next(
                            a["valores"] for a in registro["artefatos"]
                            if a["id"] == representacao["artefato_id"]))

    def test_selecao_nao_compartilha_objetos_com_origem(self):
        registro = exportacao_contextos()
        antes = deepcopy(registro)
        selecionado = self.selecionar(registro, [registro["representacoes"][0]["id"]])
        selecionado["contexto"]["unidades"][0]["foco"]["texto"] = "alterado"
        selecionado["artefatos"][0]["valores"][0] = 0
        selecionado["representacoes"][0]["origem"]["original"]["inicio"] = 0
        self.assertEqual(registro, antes)

    def test_nao_importa_bibliotecas_neurais_nem_produtor(self):
        registro = exportacao_contextos()
        individual = exportacao_individual(registro, registro["representacoes"][0]["id"])
        script = (
            "import json,sys; from agente_analista.importacao import importar_vetorizacao; "
            "importar_vetorizacao(json.load(sys.stdin)); "
            "print(json.dumps([n for n in sys.modules if n in "
            "('torch','transformers','spacy','sentence_transformers','vetorizacao') "
            "or n.startswith('lingua.')]))")
        resultado = subprocess.run([sys.executable, "-c", script], input=json.dumps(individual),
                                   text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(resultado.stdout), [])

    def test_selecionar_exige_ids_existentes_distintos_e_selecao_nao_vazia(self):
        registro = exportacao_contextos()
        id_valido = registro["representacoes"][0]["id"]
        for ids in ([], [id_valido, id_valido], ["inexistente"], [id_valido, None], id_valido):
            with self.subTest(ids=ids), self.assertRaises(ErroImportacao):
                self.selecionar(registro, ids)

    def test_selecao_de_origem_incompleta_nao_legaliza_arquivo_danificado(self):
        registro = exportacao_contextos()
        registro["representacoes"].pop()
        with self.assertRaises(ErroImportacao):
            self.selecionar(registro, [registro["representacoes"][0]["id"]])

    def test_integral_sem_declaracao_continua_exigindo_cobertura_total(self):
        registro = exportacao_contextos()
        registro["representacoes"].pop(2)
        with self.assertRaises(ErroImportacao):
            importar_vetorizacao(registro)

    def test_declaracao_de_selecao_invalida_ou_divergente_rejeitada(self):
        registro = exportacao_contextos()
        base = self.selecionar(registro, [r["id"] for r in registro["representacoes"][:3]])
        mudancas = (
            lambda r: r["selecao_representacoes"].__setitem__("versao", "9.0.0"),
            lambda r: r["selecao_representacoes"]["representacao_ids"].append(
                r["selecao_representacoes"]["representacao_ids"][0]),
            lambda r: r["selecao_representacoes"]["representacao_ids"].append("inexistente"),
            lambda r: r["representacoes"].pop(),
            lambda r: r["selecao_representacoes"].__setitem__("representacoes_origem_total", 0),
            lambda r: r["selecao_representacoes"].__setitem__("representacoes_origem_total", True),
            lambda r: r["selecao_representacoes"].__setitem__(
                "representacoes_origem_total", len(registro["representacoes"]) + 1),
            lambda r: r["selecao_representacoes"]["representacao_ids"].reverse(),
            lambda r: r.__setitem__("selecao_representacoes", True),
        )
        for alterar in mudancas:
            adulterado = deepcopy(base)
            alterar(adulterado)
            with self.subTest(alterar=alterar), self.assertRaises(ErroImportacao):
                importar_vetorizacao(adulterado)

    def test_vetores_hashes_tokens_e_offsets_adulterados_na_selecao_rejeitados(self):
        registro = exportacao_contextos(fragmentar=True)
        base = self.selecionar(registro, [registro["representacoes"][0]["id"]])
        mudancas = (
            lambda r: r["artefatos"][0]["valores"].__setitem__(0, 0.0),
            lambda r: r["artefatos"][0].__setitem__("sha256_bytes", "0" * 64),
            lambda r: r["artefatos"][0]["entrada"].__setitem__("sha256_tokens", "0" * 64),
            lambda r: r["representacoes"][0].__setitem__("texto_sha256", "0" * 64),
            lambda r: r["representacoes"][0]["fragmentos"][0]["original"].__setitem__("inicio", 0),
            lambda r: r["representacoes"][0]["fragmentos"].pop(),
            lambda r: r["representacoes"][0].__setitem__("cobertura_integral", False),
            lambda r: r["artefatos"].pop(),
        )
        for alterar in mudancas:
            adulterado = deepcopy(base)
            alterar(adulterado)
            with self.subTest(alterar=alterar), self.assertRaises(ErroImportacao):
                importar_vetorizacao(adulterado)

    def test_norma_l2_invalida_rejeitada_mesmo_com_hashes_e_ids_refeitos(self):
        registro = exportacao_contextos()
        selecionado = self.selecionar(registro, [registro["representacoes"][0]["id"]])
        artefato = selecionado["artefatos"][0]
        artefato["valores"] = [2.0, 0.0, 0.0]
        _rehash_artefato(selecionado, artefato)
        with self.assertRaises(ErroImportacao):
            importar_vetorizacao(selecionado)

    def test_contexto_nao_pode_inventar_foco_ou_citacao_de_origem(self):
        registro = exportacao_contextos()
        base = self.selecionar(registro, [registro["representacoes"][2]["id"]])
        for campo, valor in (("periodo_foco_id", "foco-inventado"),
                             ("unidade_id", "unidade-inventada"),
                             ("janela_logica_id", "janela-inventada"),
                             ("campo", "citacao.inventada")):
            adulterado = deepcopy(base)
            adulterado["representacoes"][0]["origem"][campo] = valor
            with self.subTest(campo=campo), self.assertRaises(ErroImportacao):
                importar_vetorizacao(adulterado)

    def test_individual_sem_metadados_e_array_solto_dao_erro_acionavel(self):
        registro = exportacao_contextos()
        individual = exportacao_individual(registro, registro["representacoes"][0]["id"])
        for campo in ("contexto", "perfil", "processamento", "contexto_sha256"):
            incompleto = deepcopy(individual)
            del incompleto[campo]
            with self.subTest(campo=campo), self.assertRaisesRegex(
                    ErroImportacao, "(?i)origem|metadados|complet|contexto|perfil|processamento"):
                importar_vetorizacao(incompleto)
        with self.assertRaisesRegex(ErroImportacao, "(?i)objeto|JSON|metadados|arquivo"):
            importar_vetorizacao([0.1, 0.2, 0.3])


if __name__ == "__main__":
    unittest.main()
