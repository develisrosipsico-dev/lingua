"""Contratos que declaram os tipos recebidos, sem importar o produtor."""

from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

from fixtures_importacao import TEXTO, construir_exportacao
from agente_analista.contratos_importacao import hash_json
from agente_analista.importacao import (
    ErroImportacao, importar_vetorizacao, selecionar_representacoes,
)


TIPOS = ("documento", "paragrafo", "contextual", "periodo")


def _relatorio(registro):
    registro["validacao"] = {"pronto": True, "estado": "concluida", "cobertura": {
        "representacoes_total": len(registro["representacoes"]),
        "artefatos_total": len(registro["artefatos"]),
        "representacoes_sem_conteudo": 0, "cobertura_integral": True}}


def _renovar_configuracao(registro):
    """Troque identidades de configuração; preserve os valores já exportados."""
    cfg = hash_json({"perfil": registro["perfil"], "processamento": registro["processamento"],
                     "modulo_versao": "1.0.0"})
    registro["configuracao_sha256"] = cfg
    substituidos, novos = {}, {}
    for artefato in registro["artefatos"]:
        anterior = artefato["id"]
        entrada = artefato["entrada"]
        artefato["configuracao_sha256"] = cfg
        artefato["chave_cache"] = hash_json({"versao": "1.0.0", "configuracao_sha256": cfg,
                                           "entrada_sha256": entrada["sha256"],
                                           "tokens_sha256": entrada["sha256_tokens"]})
        artefato["id"] = "sha256:" + hash_json({k: v for k, v in artefato.items() if k != "id"})
        substituidos[anterior] = artefato["id"]
        novos[artefato["id"]] = artefato
    for rep in registro["representacoes"]:
        rep["artefato_id"] = substituidos[rep["artefato_id"]]
        for fragmento in rep["fragmentos"]:
            fragmento["artefato_id"] = substituidos[fragmento["artefato_id"]]
    registro["artefatos"] = list(novos.values())
    _relatorio(registro)


def exportacao_tipos(tipos, *, normalizar=False, perfil_id="e5_simetrico"):
    """Combine representações congeladas em um pacote com tipos declarados.

    O modelo/tokenizador e os valores dos vetores continuam os das fixtures.
    As referências dos parágrafos seguem a segmentação armazenada no contexto,
    em vez de usarem os rótulos P1/P2 do antigo perfil do analista.
    """
    unidades = construir_exportacao(dimensao=3, normalizar=normalizar)
    paragrafos = construir_exportacao(dimensao=3, normalizar=normalizar, perfil_id="e5_analista")
    registro = deepcopy(paragrafos if perfil_id == "e5_analista" else unidades)
    registro["perfil"]["tipos_representacao"] = list(tipos)
    segmentacao = registro["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]
    por_tipo = {
        "documento": [registro["representacoes"][-1]],
        "paragrafo": deepcopy(paragrafos["representacoes"][:2]),
        "contextual": [r for r in unidades["representacoes"] if r["tipo"] == "contextual"],
        "periodo": [r for r in unidades["representacoes"] if r["tipo"] == "periodo"],
    }
    for rep, paragrafo in zip(por_tipo["paragrafo"], segmentacao["paragrafos"]):
        rep["origem"] = {"campo": "segmentacao.paragrafos.texto", "paragrafo_id": paragrafo["id"],
                         "trabalho": deepcopy(paragrafo["trabalho"]),
                         "original": deepcopy(paragrafo["original"])}
    registro["representacoes"] = deepcopy([r for tipo in tipos for r in por_tipo[tipo]])
    for ordem, rep in enumerate(registro["representacoes"]):
        rep["id"] = f"{registro['execucao_id']}:representacao:{ordem}"
        rep["ordem"] = ordem
        for fragmento in rep["fragmentos"]:
            fragmento["peso"] = (fragmento["tokens_conteudo"]
                                  if registro["perfil"]["agregacao"] == "media_ponderada_tokens" else 1)
    usados = {r["artefato_id"] for r in registro["representacoes"]}
    usados.update(f["artefato_id"] for r in registro["representacoes"] for f in r["fragmentos"])
    disponiveis = {a["id"]: a for a in unidades["artefatos"] + paragrafos["artefatos"]}
    registro["artefatos"] = deepcopy([a for id_, a in disponiveis.items() if id_ in usados])
    # tipos_representacao não integra o espaço matemático do E5; a identidade
    # do espaço permanece a do perfil original. A configuração muda acima.
    _renovar_configuracao(registro)
    return registro


def _individual(registro, representacao):
    campos = ("execucao_id", "contexto_execucao_id", "configuracao_sha256", "espaco_vetorial_id",
              "schema_version", "documento_id", "registrado_em", "contexto", "contexto_sha256",
              "perfil", "processamento")
    usados = {representacao["artefato_id"]} | {f["artefato_id"] for f in representacao["fragmentos"]}
    return {**{k: deepcopy(registro[k]) for k in campos},
            "representacao": deepcopy(representacao),
            "artefatos": deepcopy([a for a in registro["artefatos"] if a["id"] in usados])}


class TiposRecebidosTests(unittest.TestCase):
    def assert_preservados(self, registro, importado, representacoes):
        preparacao = registro["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
        artefatos = {a["id"]: a for a in registro["artefatos"]}
        self.assertEqual(importado["relato"]["texto"], TEXTO)
        self.assertEqual(importado["fonte"]["tipos_representacao"], registro["perfil"]["tipos_representacao"])
        self.assertEqual(len(importado["consultas"]), len(representacoes))
        for consulta, rep in zip(importado["consultas"], representacoes):
            self.assertEqual(consulta["tipo"], rep["tipo"])
            original = rep["origem"]["original"]
            self.assertEqual(consulta["texto"], TEXTO[original["inicio"]:original["fim"]])
            campo = "original" if rep["origem"]["campo"] == "preparacao.original.texto" else "trabalho"
            posicao = rep["origem"][campo]
            self.assertEqual(consulta["texto_vetorizado"],
                             preparacao[campo]["texto"][posicao["inicio"]:posicao["fim"]])
            self.assertEqual(consulta["vetor"], artefatos[rep["artefato_id"]]["valores"])
            self.assertEqual(consulta["origem"], rep["origem"])
            if rep["tipo"] == "paragrafo":
                self.assertEqual(consulta["paragrafo_id"], rep["origem"]["paragrafo_id"])

    def test_importa_somente_tipos_declarados_com_ordem_e_vetores_preservados(self):
        selecoes = [(tipo,) for tipo in TIPOS] + [
            ("documento", "contextual"), ("paragrafo", "periodo"), TIPOS]
        for normalizar in (False, True):
            for perfil_id in ("e5_simetrico", "e5_analista"):
                for tipos in selecoes:
                    with self.subTest(tipos=tipos, normalizar=normalizar, perfil=perfil_id):
                        registro = exportacao_tipos(tipos, normalizar=normalizar, perfil_id=perfil_id)
                        antes = deepcopy(registro)
                        with patch.dict(sys.modules, {"torch": None, "transformers": None,
                                                       "sentence_transformers": None}):
                            importado = importar_vetorizacao(registro)
                        self.assert_preservados(registro, importado, registro["representacoes"])
                        self.assertEqual(registro, antes)
                        self.assertEqual(importado["registro"], registro)

    def test_selecao_e_individual_tipados_preservam_crlf_e_total_de_origem(self):
        for perfil_id in ("e5_simetrico", "e5_analista"):
            registro = exportacao_tipos(TIPOS, normalizar=True, perfil_id=perfil_id)
            escolhidas = [registro["representacoes"][0], registro["representacoes"][2],
                          registro["representacoes"][-1]]
            selecionado = selecionar_representacoes(registro, [r["id"] for r in escolhidas])
            self.assertEqual(selecionado["selecao_representacoes"]["representacoes_origem_total"], 13)
            self.assert_preservados(registro, importar_vetorizacao(selecionado), escolhidas)
            for rep in escolhidas:
                with self.subTest(perfil=perfil_id, tipo=rep["tipo"]):
                    individual = _individual(registro, rep)
                    self.assert_preservados(registro, importar_vetorizacao(individual), [rep])

    def test_falta_de_item_declarado_continua_sendo_incompletude(self):
        for tipo in TIPOS:
            with self.subTest(tipo=tipo):
                registro = exportacao_tipos(TIPOS)
                indice = next(i for i, rep in enumerate(registro["representacoes"]) if rep["tipo"] == tipo)
                registro["representacoes"].pop(indice)
                _relatorio(registro)
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(registro)

    def test_tipos_duplicados_desordenados_desconhecidos_e_vazios_rejeitados(self):
        for tipos in ([], ["contextual", "documento"], ["documento", "documento"],
                      ["sentenca"], [True], "documento"):
            with self.subTest(tipos=tipos):
                registro = exportacao_tipos(TIPOS)
                registro["perfil"]["tipos_representacao"] = tipos
                _renovar_configuracao(registro)
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(registro)

    def test_ordem_de_representacoes_e_hashes_divergentes_rejeitados(self):
        mudancas = (
            lambda r: r["representacoes"].reverse(),
            lambda r: r.__setitem__("contexto_sha256", "0" * 64),
            lambda r: r.__setitem__("configuracao_sha256", "0" * 64),
            lambda r: r["representacoes"][0].__setitem__("texto_sha256", "0" * 64),
            lambda r: r["artefatos"][0].__setitem__("sha256_bytes", "0" * 64),
        )
        for alterar in mudancas:
            registro = exportacao_tipos(TIPOS, normalizar=True)
            alterar(registro)
            with self.subTest(alterar=alterar), self.assertRaises(ErroImportacao):
                importar_vetorizacao(registro)


if __name__ == "__main__":
    unittest.main()
