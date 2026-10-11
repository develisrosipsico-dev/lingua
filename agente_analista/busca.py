"""BM25 e vetores importados no acervo completo, sem nova inferência."""

from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
import unicodedata

import numpy as np

from .importacao import conferir_compatibilidade


class ErroBusca(ValueError):
    """Falha segura e acionável para a busca local."""


# A fórmula e os parâmetros reutilizam search.py do arquivo anterior. A nova
# tokenização dispensa Snowball e registra essa diferença no método exportado.
# Não removemos não/nem/sem: a avaliação deve ter acesso às negações do relato.
_VAZIAS = set("a ao aos as ate com da das de do dos e em entre essa esse esta este eu ela ele "
              "elas eles isso isto ja lhe lhes mais me meu meus minha minhas muito na nas no nos "
              "num numa o os ou para pela pelas pelo pelos por qual quando que quem se ser seu "
              "seus sobre sua suas tambem ter um uma umas uns".split())


def _termos(texto):
    texto = "".join(c for c in unicodedata.normalize("NFD", texto.casefold())
                    if unicodedata.category(c) != "Mn")
    return [t for t in re.findall(r"[^\W\d_]+", texto, re.UNICODE) if len(t) > 1 and t not in _VAZIAS]


class _BM25:
    K1, B = 1.2, 0.75

    def __init__(self, fragmentos):
        self.postings = defaultdict(list)
        self.tamanhos = np.zeros(len(fragmentos), dtype=np.float64)
        for i, f in enumerate(fragmentos):
            contagens = Counter(_termos(f["cabecalho"] + "\n" + f["texto"]))
            self.tamanhos[i] = sum(contagens.values())
            for termo, frequencia in contagens.items():
                self.postings[termo].append((i, frequencia))
        self.media = float(self.tamanhos.mean()) or 1.0

    def pontuar(self, texto):
        pontuacoes = np.zeros(len(self.tamanhos), dtype=np.float64)
        for termo in set(_termos(texto)):
            encontrados = self.postings.get(termo, [])
            if not encontrados:
                continue
            idf = math.log(1 + (len(self.tamanhos) - len(encontrados) + 0.5) / (len(encontrados) + 0.5))
            for indice, frequencia in encontrados:
                denominador = frequencia + self.K1 * (1 - self.B + self.B * self.tamanhos[indice] / self.media)
                pontuacoes[indice] += idf * frequencia * (self.K1 + 1) / denominador
        return pontuacoes


class Buscador:
    K_RRF, TOP_METODO, TOP_BLOCOS = 60, 50, 12
    # Diagnóstico: o resultado conserva as primeiras posições da fusão,
    # inclusive as que ficam fora do pool enviado à avaliação.
    TOP_RANKING_FUNDIDO = 50
    # Âncoras: cada consulta de trecho marca os blocos que lideram o próprio
    # ranking; os melhores extras entram no pool depois do topo fundido.
    ANCORAS_RANK_E5, ANCORAS_RANK_BM25, TOP_ANCORAS_EXTRAS = 2, 1, 12
    # Pesos relativos: cada granularidade presente participa igualmente.
    PESOS_TIPOS = {"periodo": 1.0, "contextual": 1.0, "documento": 1.0, "paragrafo": 1.0}
    TIPOS_CONSULTAS = frozenset(PESOS_TIPOS)

    def __init__(self, corpus):
        self.corpus = corpus
        self.lexical = _BM25(corpus.fragmentos)

    def _consultas_importadas(self, importado):
        """Confira o contrato já importado e use os vetores recebidos intactos."""
        try:
            conferir_compatibilidade(importado, self.corpus.manifesto)
        except ValueError as exc:
            raise ErroBusca(str(exc)) from exc
        if not isinstance(importado, dict) or not isinstance(importado.get("consultas"), list) or not importado["consultas"]:
            raise ErroBusca("Importe uma vetorização com consultas do relato.")
        consultas = []
        selecao = importado["fonte"].get("selecao_representacoes") is True
        ids = set()
        repetidas = {}
        for consulta in importado["consultas"]:
            if (not isinstance(consulta, dict) or not isinstance(consulta.get("id"), str)
                    or not consulta["id"] or consulta["id"] in ids
                    or not isinstance(consulta.get("tipo"), str) or consulta["tipo"] not in self.TIPOS_CONSULTAS
                    or not isinstance(consulta.get("texto"), str) or not consulta["texto"].strip()
                    or not isinstance(consulta.get("vetor"), list)
                    or any(type(valor) not in (int, float) for valor in consulta["vetor"])):
                raise ErroBusca("As consultas importadas têm identificadores, tipos, textos ou vetores inválidos.")
            ids.add(consulta["id"])
            try:
                vetor = np.asarray(consulta["vetor"], dtype=np.float32)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ErroBusca("O vetor importado é incompatível com o corpus.") from exc
            if (vetor.shape != (self.corpus.manifesto["dimensao"],) or not np.isfinite(vetor).all()
                    or not np.isclose(np.linalg.norm(vetor), 1, atol=2e-5, rtol=0)):
                raise ErroBusca("O vetor importado tem dimensão ou normalização incompatível com o corpus.")
            # A seleção preserva as referências no JSON importado, mas usa uma
            # vez o mesmo artefato/recorte. Vetores nunca são combinados. Arquivos
            # completos históricos mantêm a contribuição usada nesta versão.
            if selecao:
                chave = (consulta["tipo"], consulta.get("artefato_id"), consulta.get("texto_vetorizado", consulta["texto"]),
                         consulta.get("inicio"), consulta.get("fim"))
                try:
                    anterior = repetidas.get(chave)
                except TypeError as exc:
                    raise ErroBusca("As referências da consulta importada são inválidas.") from exc
                if anterior is not None:
                    if not np.array_equal(anterior, vetor):
                        raise ErroBusca("Consultas repetidas apontam para vetores divergentes.")
                    continue
                repetidas[chave] = vetor
            consultas.append((consulta, vetor))
        return consultas

    def buscar(self, importado, progresso=lambda etapa: None):
        progresso("Conferindo os vetores importados da análise linguística")
        consultas = self._consultas_importadas(importado)
        selecao = importado["fonte"].get("selecao_representacoes") is True
        contagens = Counter(consulta["tipo"] for consulta, _ in consultas)
        estrategia_anterior = (not selecao and importado["fonte"].get("estrategia_consultas") == "paragrafos_documento"
                              and set(contagens) <= {"paragrafo", "documento"})
        pesos = {tipo: 1.0 for tipo in contagens} if selecao else None
        if not selecao and not estrategia_anterior:
            soma_pesos = sum(self.PESOS_TIPOS[tipo] for tipo in contagens)
            pesos = {tipo: peso / soma_pesos for tipo, peso in self.PESOS_TIPOS.items() if tipo in contagens}
        candidatos = {}
        ancoras_por_bloco = {}
        ancoras_por_consulta = {}
        for numero, (consulta, vetor) in enumerate(consultas, 1):
            progresso(f"Buscando no acervo: consulta {numero} de {len(consultas)}")
            lexical = self.lexical.pontuar(consulta.get("texto_vetorizado", consulta["texto"]))
            semantica = self.corpus.vetores @ vetor
            fator = 1.0 if estrategia_anterior else pesos[consulta["tipo"]] / contagens[consulta["tipo"]]
            ancoravel = consulta["tipo"] != "documento"
            for metodo, pontuacoes in (("BM25", lexical), ("E5", semantica)):
                # Lexical zero não constitui um resultado. E5 sempre examina
                # todas as linhas; a interpretação poderá rejeitar todos.
                indices = np.flatnonzero(pontuacoes > 0) if metodo == "BM25" else np.arange(len(pontuacoes))
                ordenados = sorted(indices, key=lambda i: (-float(pontuacoes[i]), int(i)))[:self.TOP_METODO]
                limite_ancora = self.ANCORAS_RANK_E5 if metodo == "E5" else self.ANCORAS_RANK_BM25
                blocos_ranking = {}
                for rank, indice in enumerate(ordenados, 1):
                    f = self.corpus.fragmentos[int(indice)]
                    bloco_id = f["bloco_id"]
                    novo = bloco_id not in blocos_ranking
                    if novo:
                        blocos_ranking[bloco_id] = len(blocos_ranking) + 1
                    rank_bloco = blocos_ranking[bloco_id]
                    if bloco_id not in candidatos:
                        candidatos[bloco_id] = deepcopy(self.corpus.blocos[bloco_id])
                        candidatos[bloco_id].update(pontuacoes=[], rrf=0.0, fragmentos_recuperados_ids=[])
                    candidato = candidatos[bloco_id]
                    contribuicao_bruta = 1.0 / (self.K_RRF + rank_bloco) if novo else 0.0
                    contribuicao = fator * contribuicao_bruta
                    candidato["rrf"] += contribuicao
                    candidato["pontuacoes"].append({"consulta_id": consulta["id"], "metodo": metodo,
                        "consulta_tipo": consulta["tipo"], "origem": deepcopy(consulta.get("origem")),
                        "periodo_id": consulta.get("periodo_id"), "unidade_id": consulta.get("unidade_id"),
                        "artefato_id": consulta.get("artefato_id"),
                        "fragmento_id": f["id"], "rank": rank, "rank_bloco": rank_bloco,
                        "score": float(pontuacoes[indice]), "contribuicao_rrf_bruta": contribuicao_bruta,
                        "peso_tipo": 1.0 if estrategia_anterior else pesos[consulta["tipo"]],
                        "consultas_do_tipo": contagens[consulta["tipo"]], "contribuicao_rrf": contribuicao})
                    if f["id"] not in candidato["fragmentos_recuperados_ids"]:
                        candidato["fragmentos_recuperados_ids"].append(f["id"])
                    if ancoravel and novo and rank_bloco <= limite_ancora:
                        registro = {"metodo": metodo, "rank": rank_bloco, "score": float(pontuacoes[indice])}
                        ancoras_por_bloco.setdefault(bloco_id, []).append(
                            {"consulta_id": consulta["id"], "tipo": consulta["tipo"], **registro})
                        ancoras_por_consulta.setdefault(consulta["id"], []).append(
                            {"bloco_id": bloco_id, **registro})
        progresso("Reunindo fragmentos e ampliando o contexto das fontes")
        ordenados = sorted(candidatos.values(), key=lambda c: (-c["rrf"], c["bloco_id"]))
        ranking_fundido = [{"posicao": posicao, "bloco_id": candidato["bloco_id"], "rrf": candidato["rrf"]}
                           for posicao, candidato in enumerate(ordenados[:self.TOP_RANKING_FUNDIDO], 1)]
        base = ordenados[:self.TOP_BLOCOS]
        ids_base = {c["bloco_id"] for c in base}
        extras = []
        for bloco_id, registros in ancoras_por_bloco.items():
            if bloco_id in ids_base:
                continue
            ranks_e5 = [registro["rank"] for registro in registros if registro["metodo"] == "E5"]
            if ranks_e5:
                extras.append((min(ranks_e5), -len(registros), -candidatos[bloco_id]["rrf"], bloco_id))
        extras.sort()
        selecionados = base + [candidatos[bloco_id] for _, _, _, bloco_id in extras[:self.TOP_ANCORAS_EXTRAS]]
        ids_finais = {c["bloco_id"] for c in selecionados}
        for candidato in selecionados:
            registros = ancoras_por_bloco.get(candidato["bloco_id"])
            if registros:
                candidato["ancoras"] = [dict(registro) for registro in registros]
        ancoras = []
        for consulta, _ in consultas:
            if consulta["tipo"] == "documento":
                continue
            blocos = [dict(registro) for registro in ancoras_por_consulta.get(consulta["id"], [])
                      if registro["bloco_id"] in ids_finais]
            if blocos:
                ancoras.append({"consulta_id": consulta["id"], "tipo": consulta["tipo"],
                    "inicio": consulta.get("inicio"), "fim": consulta.get("fim"),
                    "periodo_id": consulta.get("periodo_id"), "unidade_id": consulta.get("unidade_id"),
                    "paragrafo_id": consulta.get("paragrafo_id"), "blocos": blocos})
        return {"consultas": [deepcopy({k: v for k, v in c.items() if k != "vetor"}) for c, _ in consultas],
                "fonte_vetorizacao": deepcopy(importado["fonte"]), "candidatos": selecionados,
                "ranking_fundido": ranking_fundido,
                "ancoras": ancoras,
                "metodo_fusao": {"nome": "Reciprocal Rank Fusion (RRF)", "k": self.K_RRF,
                    **({"politica": "tipos_presentes_v1"} if selecao else {}),
                    "top_por_metodo_consulta": self.TOP_METODO, "max_blocos": self.TOP_BLOCOS,
                    "formula": ("soma de 1/(consultas_do_tipo*(60+rank_bloco)) para os tipos presentes"
                                if selecao else "soma de 1/(60+rank_bloco), com uma contribuição por bloco, método e consulta"
                                if estrategia_anterior else "soma de peso_tipo/(consultas_do_tipo*(60+rank_bloco)), com uma contribuição por bloco, método e consulta"),
                    "pesos_por_tipo": pesos, "consultas_por_tipo": dict(contagens),
                    "normalizacao": ("média das contribuições por consulta distinta; peso 1 por tipo presente, sem gerar ou combinar vetores"
                                     if selecao else "estratégia anterior: cada consulta de parágrafo, relato ou recorte contribui integralmente"
                                     if estrategia_anterior else "média das contribuições por consulta em cada tipo; os tipos presentes têm pesos iguais"),
                    "agrupamento": "blocos distintos por ordem de primeira aparição no top 50 de fragmentos",
                    "ancoras": {"criterio": "cada trecho (período, janela ou parágrafo) marca como âncoras os blocos que lideram o próprio ranking: E5 até 2 blocos distintos, BM25 apenas o 1º",
                        "extras_no_pool": len(selecionados) - len(base), "max_extras": self.TOP_ANCORAS_EXTRAS,
                        "ordenacao_extras": "melhor rank E5, quantidade de âncoras, RRF e identificador",
                        "aviso": "âncora indica proximidade local entre o trecho e o bloco; não representa probabilidade de pertinência"},
                    "bm25": {"k1": self.lexical.K1, "b": self.lexical.B,
                        "tokenizacao": "palavras sem acentos, minúsculas, sem palavras funcionais; sem radicalização"},
                    "semantica": "produto escalar dos vetores importados normalizados L2 com todas as linhas do corpus (similaridade cosseno)",
                    "vetorizacao": ("vetores dos tipos presentes no arquivo; cada recorte é consultado separadamente, sem inferência no agente"
                                    if selecao else "vetores do texto original de P1, P2 e relato inteiro; recortes E5 independentes produzidos no Língua, sem inferência no agente"
                                    if estrategia_anterior else "vetores dos tipos selecionados (períodos, parágrafos, janelas contextuais ou documento) recebidos da análise linguística; nenhuma inferência ou divisão adicional"),
                    "aviso": "Pontuações ordenam candidatos; não representam probabilidades de interpretação correta."}}
