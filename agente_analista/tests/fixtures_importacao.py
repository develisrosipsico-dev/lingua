"""Arquivos e vetores fictícios para testar somente o consumidor.

Os três JSONs foram congelados de uma execução do produtor da etapa 09 usando
anotações manuais e um adaptador determinístico. Nenhum modelo neural foi
executado. Este módulo adapta metadados e valores para os casos de teste sem
importar ou executar o aplicativo produtor.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct

from agente_analista.contratos_importacao import hash_json


PASTA = Path(__file__).resolve().parent / "fixtures"
INSTANTE = "2026-10-08T09:30:00-03:00"
TEXTO = "  Árvore\tverde.  Casa azul! \t Céu.\r\n\r\nOutro.  João saiu.  "


def _ler(nome):
    return json.loads((PASTA / (nome + ".json")).read_text(encoding="utf-8"))


def carregar_perfil(perfil_id="e5_simetrico", *, opcoes=None):
    perfil = deepcopy(_ler("exportacao_atual")["perfil"])
    if perfil_id not in ("e5_simetrico", "e5_consulta"):
        raise ValueError("Perfil não previsto nas fixtures do agente.")
    perfil.update(id=perfil_id, finalidade="consulta" if perfil_id == "e5_consulta" else "simetrico")
    perfil.update(deepcopy(opcoes or {}))
    return perfil


def contexto_teste(*, normalizar=False):
    return _ler("exportacao_atual_crlf" if normalizar else "exportacao_atual")["contexto"]


def _float32(valores):
    return list(struct.unpack("<" + "f" * len(valores), struct.pack("<" + "f" * len(valores), *valores)))


def _normalizar(valores):
    norma = math.hypot(*valores)
    return _float32([v / norma for v in valores])


def _espaco(perfil, processamento):
    campos_perfil = ("modelo", "tokenizador", "backend", "precisao_inferencia", "pooling", "normalizacao",
                     "formato_armazenamento", "fragmentacao", "agregacao")
    campos_proc = ("backend", "versao_backend", "modelo", "tokenizador", "dimensao", "pooling",
                   "precisao_inferencia", "bibliotecas", "limite_tokens", "limite_modelo_tokens")
    descricao = {"versao": "1.0.0", "perfil": {k: perfil[k] for k in campos_perfil},
                 "adaptador": {k: processamento[k] for k in campos_proc},
                 "prefixo": perfil["prefixo"], "finalidade": perfil["finalidade"],
                 "perfil_versao": perfil["versao"], "legado": perfil["legado"]}
    return "sha256:" + hash_json(descricao)


def construir_exportacao(*, normalizar=False, dimensao=1024, limite=160,
                        perfil_id="e5_simetrico", opcoes=None):
    """Adapte uma exportação congelada, sem depender de módulos do produtor.

    Limites 160/512 cobrem a fixture integral; 23 usa a exportação já recortada.
    A opção CRLF + fragmentação ainda não é um caso utilizado nesta suíte.
    """
    if limite not in (23, 160, 512) or (normalizar and limite == 23):
        raise ValueError("Combinação não prevista nas fixtures do agente.")
    nome = "exportacao_atual_crlf" if normalizar else (
        "exportacao_atual_fragmentada" if limite == 23 else "exportacao_atual")
    registro = _ler(nome)
    perfil = carregar_perfil(perfil_id, opcoes=opcoes)
    registro["perfil"] = perfil
    proc = registro["processamento"]
    proc["dimensao"] = proc["adaptador"]["dimensao"] = dimensao
    proc["limite_tokens"] = proc["limite_modelo_tokens"] = proc["adaptador"]["limite_tokens"] = limite
    for campo in ("modelo", "tokenizador", "backend", "pooling", "precisao_inferencia"):
        proc[campo] = deepcopy(perfil[campo])
        proc["adaptador"][campo] = deepcopy(perfil[campo])
    cfg = hash_json({"perfil": perfil, "processamento": proc, "modulo_versao": "1.0.0"})
    registro["configuracao_sha256"] = cfg
    registro["espaco_vetorial_id"] = _espaco(perfil, proc)
    substituidos, por_id = {}, {}
    for artefato in registro["artefatos"]:
        antigo_id = artefato["id"]
        artefato["dimensao"] = dimensao
        artefato["configuracao_sha256"] = cfg
        if "entrada" in artefato:
            entrada = artefato["entrada"]
            artefato["chave_cache"] = hash_json({"versao": "1.0.0", "configuracao_sha256": cfg,
                                               "entrada_sha256": entrada["sha256"],
                                               "tokens_sha256": entrada["sha256_tokens"]})
            valores = [float((sum(map(ord, entrada["texto"])) + i * 17) % 101 + 1)
                       for i in range(dimensao)]
            artefato["valores"] = _normalizar(valores)
        else:
            derivacao = artefato["derivacao"]
            derivacao["componentes_ids"] = [substituidos[i] for i in derivacao["componentes_ids"]]
            artefato["chave_cache"] = hash_json({"configuracao_sha256": cfg, "derivacao": derivacao})
            total = math.fsum(derivacao["pesos"])
            media = [math.fsum(por_id[i]["valores"][j] * (peso / total)
                              for i, peso in zip(derivacao["componentes_ids"], derivacao["pesos"]))
                     for j in range(dimensao)]
            artefato["valores"] = _normalizar(media)
        dados = struct.pack("<" + "f" * dimensao, *artefato["valores"])
        artefato["sha256_bytes"] = hashlib.sha256(dados).hexdigest()
        artefato["id"] = "sha256:" + hash_json({k: v for k, v in artefato.items() if k != "id"})
        substituidos[antigo_id] = artefato["id"]
        por_id[artefato["id"]] = artefato
    for rep in registro["representacoes"]:
        rep["artefato_id"] = substituidos[rep["artefato_id"]]
        for fragmento in rep["fragmentos"]:
            fragmento["artefato_id"] = substituidos[fragmento["artefato_id"]]
    return registro
