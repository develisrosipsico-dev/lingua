"""Extração, preparação revisável e fragmentação de um PDF textual para E5.

Não executa OCR nem corrige a ordem espacial do PDF. O texto extraído permanece
intacto; exclusões e recomposições pertencem a uma camada derivada, com trilha
de origem. As posições são índices de caracteres Python, não bytes do PDF.
"""

from __future__ import annotations

import hashlib
import copy
import json
import math
import re
import statistics
from pathlib import Path


class ErroPreparacaoLivro(ValueError):
    """Um arquivo ou um limite não permite uma preparação sem perda silenciosa."""


def _sha256(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _aviso(codigo, mensagem, **detalhes):
    return {"codigo": codigo, "mensagem": mensagem, **detalhes}


def _numero(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def _colunas_ou_ordem_ambigua(blocos):
    """Um alerta conservador; não constitui um detector de layout de leitura."""
    caixas = [b["bbox"] for b in blocos if b["texto"].strip()]
    for i, a in enumerate(caixas):
        for b in caixas[i + 1:]:
            altura_comum = min(a[3], b[3]) - max(a[1], b[1])
            separados = a[2] + 8 < b[0] or b[2] + 8 < a[0]
            if altura_comum > 4 and separados:
                return True
    return any(b[1] + 12 < a[1] for a, b in zip(caixas, caixas[1:]))


def _possivel_tabela(linhas):
    """Três faixas de altura com três células: alerta, nunca remoção."""
    faixas = []
    for linha in sorted(linhas, key=lambda l: l["bbox"][1]):
        caixa = linha["bbox"]
        faixa = next((f for f in faixas if abs(f["y"] - caixa[1]) <= 3), None)
        if faixa is None:
            faixa = {"y": caixa[1], "caixas": []}
            faixas.append(faixa)
        faixa["caixas"].append(caixa)
    return sum(len(f["caixas"]) >= 3 for f in faixas) >= 3


def extrair_pdf(caminho, metadados=None):
    """Extrai blocos na ordem declarada pelo PDF, com páginas e offsets.

    ``metadados`` permite registrar título, autoria e edição informados pelo
    usuário. Eles ficam separados dos metadados declarados no próprio PDF.
    ``pagina_rotulo`` vem do catálogo do PDF; não é inferida como página impressa.
    """
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError as erro:
            raise ErroPreparacaoLivro("Instale PyMuPDF para extrair o PDF.") from erro

    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroPreparacaoLivro("O arquivo PDF não foi encontrado.")
    if metadados is not None and not isinstance(metadados, dict):
        raise ErroPreparacaoLivro("Os metadados bibliográficos devem ser um objeto.")
    metadados = json.loads(json.dumps(metadados or {}, ensure_ascii=False, allow_nan=False))
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for parte in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(parte)
    avisos, paginas, sumario = [], [], []
    try:
        documento = fitz.open(str(caminho))
    except Exception as erro:
        raise ErroPreparacaoLivro("Não foi possível abrir o PDF: " + str(erro)) from erro
    with documento:
        if not documento.is_pdf:
            raise ErroPreparacaoLivro("O arquivo deve ser um PDF.")
        if documento.needs_pass:
            raise ErroPreparacaoLivro("O PDF está protegido por senha; use uma cópia acessível.")
        if not len(documento):
            raise ErroPreparacaoLivro("O PDF não contém páginas.")
        metadata_pdf = dict(documento.metadata or {})
        for item in documento.get_toc(simple=True):
            if len(item) >= 3 and isinstance(item[2], int) and 1 <= item[2] <= len(documento):
                sumario.append({"nivel": item[0], "titulo": item[1], "pagina_pdf": item[2]})
        if not sumario:
            avisos.append(_aviso("sem_sumario_pdf", "O PDF não contém marcadores de seção; nenhum título foi inferido."))
        for indice, pagina in enumerate(documento):
            numero = indice + 1
            blocos, partes, cursor = [], [], 0
            estrutura = pagina.get_text("dict", sort=False)
            estilos = {}
            for grupo in estrutura.get("blocks", []):
                if grupo.get("type") != 0:
                    continue
                spans = [s for linha in grupo.get("lines", []) for s in linha.get("spans", [])]
                tamanhos = [float(s["size"]) for s in spans if s.get("text", "").strip()]
                if tamanhos:
                    estilos[tuple(round(float(v), 2) for v in grupo["bbox"])] = {
                        "tamanho_maximo": max(tamanhos), "tamanho_mediano": statistics.median(tamanhos),
                        "linhas": len(grupo.get("lines", [])),
                        "tem_negrito": any(int(s.get("flags", 0)) & 16 for s in spans),
                    }
            for bloco in pagina.get_text("blocks", sort=False):
                if len(bloco) < 7 or bloco[6] != 0:
                    continue
                texto = bloco[4]
                if not isinstance(texto, str) or not texto:
                    continue
                if partes:
                    partes.append("\n\n")
                    cursor += 2
                bbox = [float(v) for v in bloco[:4]]
                if not all(_numero(v) for v in bbox):
                    raise ErroPreparacaoLivro(f"Coordenadas inválidas na página {numero}.")
                blocos.append({
                    "id": f"pagina-{numero}:bloco-{len(blocos)}", "ordem": len(blocos),
                    "texto": texto, "bbox": bbox, "offset_inicio": cursor,
                    "offset_fim": cursor + len(texto),
                    "estilo": estilos.get(tuple(round(v, 2) for v in bbox), {}),
                })
                partes.append(texto)
                cursor += len(texto)
            texto_pagina = "".join(partes)
            caracteres = sum(not c.isspace() for c in texto_pagina)
            if not caracteres:
                avisos.append(_aviso("pagina_sem_texto", "Página sem texto extraível; não foi aplicado OCR.", pagina_pdf=numero))
            elif caracteres < 40 or texto_pagina.count("\ufffd") > max(2, caracteres // 100):
                avisos.append(_aviso("extracao_pobre", "Pouco texto ou caracteres de substituição: conferir esta página.", pagina_pdf=numero))
            # O extrator pode reunir duas colunas no mesmo bloco. As caixas
            # de linhas permitem sinalizar isso sem reordenar o texto citado.
            linhas = [{"bbox": linha["bbox"], "texto": "linha"}
                      for grupo in estrutura.get("blocks", [])
                      if grupo.get("type") == 0 for linha in grupo.get("lines", [])]
            if _colunas_ou_ordem_ambigua(blocos) or _colunas_ou_ordem_ambigua(linhas):
                avisos.append(_aviso("ordem_leitura_ambigua", "Possíveis colunas ou ordem de blocos ambígua; a ordem do PDF foi preservada.", pagina_pdf=numero))
            if _possivel_tabela(linhas):
                avisos.append(_aviso("possivel_quadro_ou_tabela", "Células alinhadas podem formar quadro ou tabela; a relação espacial exige conferência no PDF. Nenhum conteúdo foi removido.", pagina_pdf=numero))
            paginas.append({
                "pagina_pdf": numero, "pagina_rotulo": pagina.get_label() or None,
                "largura": float(pagina.rect.width), "altura": float(pagina.rect.height),
                "texto": texto_pagina, "blocos": blocos,
            })
    if not any(p["texto"].strip() for p in paginas):
        avisos.append(_aviso("pdf_sem_texto", "Nenhuma página contém texto extraível. A preparação não pode prosseguir sem uma nova fonte textual."))
    resultado = {
        "schema": "lingua.gramatica.extracao.v1",
        "obra": {"arquivo": caminho.name, "sha256_pdf": digest.hexdigest(),
                 "total_paginas": len(paginas), "metadados": metadados, "metadados_pdf": metadata_pdf},
        "paginas": paginas, "sumario": sumario,
        "relatorio": {"avisos": avisos, "ordem_blocos": "declarada_no_pdf",
                      "normalizacoes": [], "ocr_executado": False,
                      "separador_blocos": "\n\n",
                      "paginas_com_texto": sum(bool(p["texto"].strip()) for p in paginas),
                      "caracteres_extraidos": sum(len(b["texto"]) for p in paginas for b in p["blocos"])},
    }
    _enriquecer_extracao(resultado)
    return resultado


def _secoes_por_pagina(extracao):
    """Usa somente marcadores PDF; páginas com vários títulos são sinalizadas.

    Um marcador não determina em qual bloco da página começa uma seção. Por
    isso o caminho só é atribuído quando todos os títulos que começam naquela
    página pertencem à mesma cadeia hierárquica. Em páginas com seções irmãs,
    retorna apenas o ancestral comum e registra essa limitação.
    """
    entradas = sorted(enumerate(extracao.get("sumario", [])), key=lambda x: (x[1]["pagina_pdf"], x[0]))
    por_pagina = {}
    for _, item in entradas:
        por_pagina.setdefault(item["pagina_pdf"], []).append(item)
    pilha, resultado, avisos = [], {}, []
    for pagina in extracao["paginas"]:
        numero = pagina["pagina_pdf"]
        caminhos = []
        for item in por_pagina.get(numero, []):
            nivel = max(1, int(item["nivel"]))
            pilha = [(n, t) for n, t in pilha if n < nivel]
            pilha.append((nivel, str(item["titulo"])))
            caminhos.append([t for _, t in pilha])
        if len(caminhos) > 1:
            comum = []
            for titulos in zip(*caminhos):
                if len(set(titulos)) != 1:
                    break
                comum.append(titulos[0])
            resultado[numero] = comum
            avisos.append(_aviso("secoes_na_mesma_pagina", "Vários marcadores começam na página; usa-se apenas o caminho comum, sem atribuir blocos a uma seção incerta.", pagina_pdf=numero))
        else:
            resultado[numero] = [t for _, t in pilha]
    return resultado, avisos


def _contar(tokenizador, texto):
    return len(tokenizador.encode(texto, add_special_tokens=True, truncation=False))


def _corte_seguro(texto, cabecalho, tokenizador, limite):
    """Busca um prefixo pelo orçamento real, preferindo fronteiras de texto."""
    baixo, alto, melhor = 1, len(texto), 0
    while baixo <= alto:
        meio = (baixo + alto) // 2
        if _contar(tokenizador, cabecalho + texto[:meio]) <= limite:
            melhor, baixo = meio, meio + 1
        else:
            alto = meio - 1
    if melhor == 0:
        raise ErroPreparacaoLivro("O orçamento de tokens não comporta nem um caractere do texto com o prefixo e os tokens especiais.")
    if melhor == len(texto):
        return melhor
    minimo = max(1, melhor // 2)
    candidato = None
    for padrao in (r"[.!?;:]\s+|\n+", r"\s+"):
        limites = [m.end() for m in re.finditer(padrao, texto[:melhor]) if m.end() >= minimo]
        if limites:
            candidato = limites[-1]
            break
    if candidato and _contar(tokenizador, cabecalho + texto[:candidato]) <= limite:
        return candidato
    return melhor


def _fragmentar_literal(extracao, tokenizador, alvo_tokens=380, max_tokens=512, prefixo="passage: "):
    """Cria recortes sem truncar texto e com origem para cada trecho citado.

    Os limites incluem prefixo, eventual caminho de seção e tokens especiais.
    ``tokenizador`` deve ser o mesmo AutoTokenizer usado pelo E5; nenhum modelo
    é carregado por este módulo. O título da seção pode ser omitido do embedding
    se não couber, mas continua integralmente nos metadados do fragmento.
    """
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (alvo_tokens, max_tokens)):
        raise ErroPreparacaoLivro("Os limites de tokens devem ser inteiros.")
    if not 1 <= alvo_tokens <= max_tokens <= 512:
        raise ErroPreparacaoLivro("Use 1 <= alvo_tokens <= max_tokens <= 512 para o E5.")
    if not isinstance(prefixo, str):
        raise ErroPreparacaoLivro("O prefixo deve ser textual.")
    if not isinstance(extracao, dict) or not isinstance(extracao.get("paginas"), list):
        raise ErroPreparacaoLivro("A extração não contém uma lista de páginas.")
    if _contar(tokenizador, prefixo) >= max_tokens:
        raise ErroPreparacaoLivro("O prefixo e os tokens especiais esgotam o limite.")
    secoes, avisos = _secoes_por_pagina(extracao)
    fragmentos, atual, origem, secao_atual = [], "", [], None
    cabecalho_atual = prefixo
    caracteres_origem, caracteres_ignorados, blocos_ignorados = 0, 0, []
    sha_pdf = extracao.get("obra", {}).get("sha256_pdf", "")
    if not isinstance(sha_pdf, str) or len(sha_pdf) != 64:
        raise ErroPreparacaoLivro("A extração deve preservar o SHA-256 do PDF.")

    def finalizar():
        nonlocal atual, origem
        if not origem:
            return
        texto_embedding = cabecalho_atual + atual
        tokens = _contar(tokenizador, texto_embedding)
        if not atual.strip() or tokens > max_tokens:
            raise ErroPreparacaoLivro("Fragmento vazio ou acima do orçamento de tokens.")
        identidade = json.dumps({"pdf": sha_pdf, "origem": origem, "texto": atual}, ensure_ascii=False, sort_keys=True)
        fragmentos.append({
            "id": "frag-" + _sha256(identidade), "linha_vetor": len(fragmentos),
            "texto": atual, "texto_embedding": texto_embedding, "tokens_embedding": tokens,
            "secao": list(secao_atual or []), "origem": origem,
            "sha256_texto": _sha256(atual), "sha256_texto_embedding": _sha256(texto_embedding),
            "anterior_id": None, "seguinte_id": None,
        })
        atual, origem = "", []

    for pagina in extracao["paginas"]:
        secao = secoes[pagina["pagina_pdf"]]
        cabecalho = prefixo + (" > ".join(secao) + "\n\n" if secao else "")
        if _contar(tokenizador, cabecalho) >= max_tokens - 8:
            cabecalho = prefixo
            avisos.append(_aviso("titulo_fora_embedding", "O caminho da seção não cabe com folga no orçamento e foi mantido apenas nos metadados.", pagina_pdf=pagina["pagina_pdf"]))
        if secao_atual != secao or cabecalho_atual != cabecalho:
            finalizar()
            secao_atual, cabecalho_atual = list(secao), cabecalho
        for bloco in pagina.get("blocos", []):
            texto = bloco["texto"]
            if not isinstance(texto, str):
                raise ErroPreparacaoLivro("Um bloco da extração não contém texto.")
            if not texto.strip():
                caracteres_ignorados += len(texto)
                blocos_ignorados.append(bloco["id"])
                continue
            inicio_pagina = bloco["offset_inicio"]
            fim_pagina = bloco["offset_fim"]
            if pagina["texto"][inicio_pagina:fim_pagina] != texto:
                raise ErroPreparacaoLivro("Os offsets de origem não coincidem com o texto da página.")
            restantes, inicio = texto, 0
            while restantes:
                separador = "\n\n" if atual else ""
                if atual and _contar(tokenizador, cabecalho_atual + atual + separador + restantes) > alvo_tokens:
                    finalizar()
                    separador = ""
                if _contar(tokenizador, cabecalho_atual + restantes) <= max_tokens:
                    tamanho = len(restantes)
                else:
                    # Uma seção muito longa não deve impedir a citação do texto.
                    if cabecalho_atual != prefixo and _contar(tokenizador, cabecalho_atual + restantes[:1]) > alvo_tokens:
                        finalizar()
                        cabecalho_atual = prefixo
                        avisos.append(_aviso("titulo_fora_embedding", "Título omitido do embedding para comportar o texto sem truncamento.", pagina_pdf=pagina["pagina_pdf"]))
                    limite_corte = max(alvo_tokens, _contar(tokenizador, cabecalho_atual + restantes[:1]))
                    if limite_corte > max_tokens:
                        raise ErroPreparacaoLivro("Um caractere do texto não cabe no orçamento de tokens.")
                    tamanho = _corte_seguro(restantes, cabecalho_atual, tokenizador, limite_corte)
                parte = restantes[:tamanho]
                candidato = atual + separador + parte
                if _contar(tokenizador, cabecalho_atual + candidato) > max_tokens:
                    finalizar()
                    candidato = parte
                atual = candidato
                origem.append({
                    "bloco_id": bloco["id"], "pagina_pdf": pagina["pagina_pdf"],
                    "pagina_rotulo": pagina.get("pagina_rotulo"), "bbox": list(bloco["bbox"]),
                    "offset_inicio": inicio_pagina + inicio, "offset_fim": inicio_pagina + inicio + tamanho,
                    "offset_bloco_inicio": inicio, "offset_bloco_fim": inicio + tamanho,
                })
                caracteres_origem += tamanho
                inicio += tamanho
                restantes = restantes[tamanho:]
                if restantes:
                    finalizar()
    finalizar()
    if not fragmentos:
        raise ErroPreparacaoLivro("O PDF não contém texto utilizável para vetorizar. Confira o relatório de extração.")
    for indice, fragmento in enumerate(fragmentos):
        fragmento["anterior_id"] = fragmentos[indice - 1]["id"] if indice else None
        fragmento["seguinte_id"] = fragmentos[indice + 1]["id"] if indice + 1 < len(fragmentos) else None
    return {"fragmentos": fragmentos, "relatorio": {
        "total_fragmentos": len(fragmentos), "alvo_tokens": alvo_tokens, "max_tokens": max_tokens,
        "max_tokens_observado": max(f["tokens_embedding"] for f in fragmentos),
        "prefixo": prefixo, "caracteres_origem": caracteres_origem,
        "caracteres_brancos_ignorados": caracteres_ignorados, "blocos_brancos_ignorados": blocos_ignorados,
        "normalizacoes": [], "truncamento": False, "avisos": avisos,
        "separador_blocos": "\n\n",
        "escopo_secoes": "caminho_comum_dos_marcadores_pdf_por_pagina",
        "revisao_humana_necessaria": True,
    }}

# A hifenização ortográfica não pode ser distinguida de uma quebra tipográfica
# apenas pela presença de '-'. A lista aprovada é deliberadamente explícita.
_HIFEN_QUEBRA = re.compile(r"(?P<esquerda>[^\W\d_]+)(?P<hifen>[-\u00ad])(?P<quebra>[ \t]*\r?\n[ \t\r\n]*)(?P<direita>[^\W\d_]+)", re.UNICODE)


def _chave_texto(texto):
    return re.sub(r"\s+", " ", texto).strip().casefold()


def _origem_bloco(pagina, bloco):
    return {
        "bloco_id": bloco["id"], "pagina_pdf": pagina["pagina_pdf"],
        "pagina_rotulo": pagina.get("pagina_rotulo"), "bbox": list(bloco["bbox"]),
        "offset_inicio": bloco["offset_inicio"], "offset_fim": bloco["offset_fim"],
        "offset_bloco_inicio": 0, "offset_bloco_fim": len(bloco["texto"]),
    }


def _recortar_mapa(mapa, inicio, fim, deslocamento=0):
    """Recorta intervalos preparados; normalizações remetem ao span raw inteiro."""
    resultado = []
    for parte in mapa:
        a, b = max(inicio, parte["inicio"]), min(fim, parte["fim"])
        if a >= b:
            continue
        fontes = copy.deepcopy(parte["origens"])
        if parte.get("literal", False):
            for fonte in fontes:
                corte_a, corte_b = a - parte["inicio"], parte["fim"] - b
                fonte["offset_inicio"] += corte_a
                fonte["offset_bloco_inicio"] += corte_a
                fonte["offset_fim"] -= corte_b
                fonte["offset_bloco_fim"] -= corte_b
        resultado.append({"inicio": a - inicio + deslocamento, "fim": b - inicio + deslocamento,
                          "origens": fontes, "literal": parte.get("literal", False),
                          **({"normalizacao_id": parte["normalizacao_id"]} if "normalizacao_id" in parte else {})})
    return resultado


def _fontes_mapa(mapa):
    fontes = []
    for parte in mapa:
        for fonte in parte["origens"]:
            if fontes and all(fontes[-1].get(k) == fonte.get(k) for k in ("bloco_id", "pagina_pdf")):
                anterior = fontes[-1]
                if fonte["offset_inicio"] <= anterior["offset_fim"]:
                    anterior["offset_fim"] = max(anterior["offset_fim"], fonte["offset_fim"])
                    anterior["offset_bloco_fim"] = max(anterior["offset_bloco_fim"], fonte["offset_bloco_fim"])
                    continue
            fontes.append(copy.deepcopy(fonte))
    return fontes


def _detectar_margens(extracao):
    grupos = {}
    for pagina in extracao["paginas"]:
        altura = pagina.get("altura", 0)
        if not _numero(altura) or altura <= 0:
            continue
        for bloco in pagina.get("blocos", []):
            texto = bloco["texto"].strip()
            caixa = bloco["bbox"]
            posicao = "cabecalho" if caixa[3] <= altura * .12 else "rodape" if caixa[1] >= altura * .88 else None
            if not posicao or not texto or len(texto) > 180 or texto.count("\n") > 2:
                continue
            assinatura = re.sub(r"\d+", "#", _chave_texto(texto))
            registro = grupos.setdefault((posicao, assinatura), {
                "grupo_id": "margem-" + _sha256(posicao + ":" + assinatura)[:16],
                "assinatura": assinatura, "posicao": posicao, "ids_blocos": [],
                "paginas": [], "ocorrencias": 0, "amostras": [],
            })
            registro["ids_blocos"].append(bloco["id"])
            registro["paginas"].append(pagina["pagina_pdf"])
            registro["ocorrencias"] += 1
            if len(registro["amostras"]) < 3:
                registro["amostras"].append({"texto": texto, "pagina_pdf": pagina["pagina_pdf"], "bbox": caixa})
    return [g for g in grupos.values() if len(set(g["paginas"])) >= 3]


def _candidatos_hifenizacao(unidades):
    candidatos = []
    for unidade in unidades:
        for achado in _HIFEN_QUEBRA.finditer(unidade["texto"]):
            fontes = _fontes_mapa(_recortar_mapa(unidade["mapa"], achado.start(), achado.end()))
            candidatos.append({
                "palavra_recomposta": achado["esquerda"] + achado["direita"], "antes": achado.group(),
                "paginas": list(dict.fromkeys(f["pagina_pdf"] for f in fontes)),
                "ids_blocos": list(dict.fromkeys(f["bloco_id"] for f in fontes)),
                "tipo": "hifen_condicional" if achado["hifen"] == "\u00ad" else "hifen_ambiguo",
            })
    return candidatos


def _enriquecer_extracao(extracao):
    """Sinaliza padrões, sem promover candidatos a exclusões ou seções."""
    relatorio = extracao.setdefault("relatorio", {"avisos": []})
    relatorio["candidatos_margem"] = _detectar_margens(extracao)
    # A listagem também considera palavras partidas entre blocos/páginas.
    mapa, texto = [], ""
    for pagina in extracao["paginas"]:
        for bloco in pagina.get("blocos", []):
            if texto:
                texto += "\n\n"
            inicio = len(texto)
            texto += bloco["texto"]
            mapa.append({"inicio": inicio, "fim": len(texto), "origens": [_origem_bloco(pagina, bloco)], "literal": True})
    relatorio["candidatos_hifenizacao"] = _candidatos_hifenizacao([{"texto": texto, "mapa": mapa}])
    return extracao


def preparar_extracao(extracao, paginas_incluir=None, ids_excluir=(), recompor_palavras=()):
    """Cria uma camada derivada; a extração literal nunca é sobrescrita.

    Margens são apenas candidatas: só ``ids_excluir`` as retira da busca. Hífen
    ASCII exige palavra resultante explicitamente aprovada; hífen condicional
    U+00AD seguido de quebra pode ser recomposto sem inventar grafia. Páginas
    omitidas, títulos sem conteúdo e cada alteração ficam no relatório.
    """
    if not isinstance(extracao, dict) or not isinstance(extracao.get("paginas"), list):
        raise ErroPreparacaoLivro("A extração não contém páginas.")
    resultado = copy.deepcopy(extracao)
    _enriquecer_extracao(resultado)
    numeros = {p["pagina_pdf"] for p in resultado["paginas"]}
    selecionadas = numeros if paginas_incluir is None else set(paginas_incluir)
    if not selecionadas or not selecionadas <= numeros or any(type(n) is not int for n in selecionadas):
        raise ErroPreparacaoLivro("Selecione páginas PDF existentes; a numeração começa em 1.")
    ids_conhecidos = {b["id"] for p in resultado["paginas"] for b in p.get("blocos", [])}
    excluidos = set(ids_excluir)
    if not excluidos <= ids_conhecidos:
        raise ErroPreparacaoLivro("Há IDs de exclusão que não pertencem à extração.")
    if isinstance(recompor_palavras, str) or any(not isinstance(p, str) or not p.isalpha() for p in recompor_palavras):
        raise ErroPreparacaoLivro("recompor_palavras deve ser uma lista de palavras sem hífen, revisadas pelo usuário.")
    aprovadas = {p.casefold() for p in recompor_palavras}
    secoes, avisos = _secoes_por_pagina(resultado)
    paginas_layout_ambiguo = {a.get("pagina_pdf") for a in resultado.get("relatorio", {}).get("avisos", [])
                             if a.get("codigo") in ("ordem_leitura_ambigua", "possivel_quadro_ou_tabela")}
    titulos_pdf = {_chave_texto(s["titulo"]) for s in resultado.get("sumario", [])}
    tamanhos = [b.get("estilo", {}).get("tamanho_mediano") for p in resultado["paginas"] for b in p.get("blocos", [])
                if b.get("estilo", {}).get("tamanho_mediano") and len(b["texto"].strip()) >= 120]
    corpo = statistics.median(tamanhos) if tamanhos else None
    unidades, exclusoes = [], []
    for pagina in resultado["paginas"]:
        for bloco in pagina.get("blocos", []):
            texto = bloco.get("texto")
            if not isinstance(texto, str) or pagina["texto"][bloco["offset_inicio"]:bloco["offset_fim"]] != texto:
                raise ErroPreparacaoLivro("Os offsets da extração não coincidem com o texto original.")
            fonte = _origem_bloco(pagina, bloco)
            motivo = "pagina_fora_intervalo" if pagina["pagina_pdf"] not in selecionadas else "exclusao_confirmada" if bloco["id"] in excluidos else "bloco_branco" if not texto.strip() else None
            if motivo:
                exclusoes.append({"motivo": motivo, "origem": fonte, "texto": texto})
                continue
            estilo = bloco.get("estilo", {})
            curto = len(texto.strip()) <= 180 and len(texto.strip().splitlines()) <= 3
            titulo = curto and (_chave_texto(texto) in titulos_pdf or (
                corpo is not None and pagina["pagina_pdf"] not in paginas_layout_ambiguo
                and estilo.get("tamanho_mediano", 0) >= corpo * 1.15
                and not re.search(r"[.!?;:,]\s*$", texto)))
            papel = "titulo_candidato" if titulo else "conteudo"
            secao = secoes[pagina["pagina_pdf"]]
            juntar = bool(unidades and unidades[-1]["secao"] == secao and (
                not unidades[-1]["tem_conteudo"]
                or re.search(r"[^\W\d_][-\u00ad]\s*$", unidades[-1]["texto"], re.UNICODE)
                or unidades[-1]["texto"].rstrip().endswith(":")
                or re.search(r"\b(?:exemplo|período|oração|frase)\b[^.!?]*,\s*$", unidades[-1]["texto"], re.IGNORECASE)))
            if juntar:
                unidade = unidades[-1]
                unidade["texto"] += "\n\n"
            else:
                unidade = {"id": "unidade-" + bloco["id"], "texto": "", "mapa": [], "secao": list(secao),
                           "pagina_pdf": pagina["pagina_pdf"], "tem_conteudo": False, "papeis_blocos": {}}
                unidades.append(unidade)
            inicio = len(unidade["texto"])
            unidade["texto"] += texto
            unidade["mapa"].append({"inicio": inicio, "fim": len(unidade["texto"]), "origens": [fonte], "literal": True})
            unidade["papeis_blocos"][bloco["id"]] = papel
            unidade["tem_conteudo"] |= not titulo
    candidatos = _candidatos_hifenizacao(unidades)
    normalizacoes = []
    for unidade in unidades:
        texto, mapa, cursor, novo, novo_mapa = unidade["texto"], unidade["mapa"], 0, "", []
        for achado in _HIFEN_QUEBRA.finditer(texto):
            palavra = achado["esquerda"] + achado["direita"]
            if palavra.casefold() not in aprovadas and achado["hifen"] != "\u00ad":
                continue
            novo_mapa += _recortar_mapa(mapa, cursor, achado.start(), len(novo))
            novo += texto[cursor:achado.start()]
            fontes = _fontes_mapa(_recortar_mapa(mapa, achado.start(), achado.end()))
            registro = {"id": "normalizacao-" + _sha256(json.dumps([fontes, achado.group(), palavra], sort_keys=True, ensure_ascii=False))[:20],
                        "tipo": "recomposicao_hifen", "antes": achado.group(), "depois": palavra,
                        "origem": fontes, "criterio": "hifen_condicional" if achado["hifen"] == "\u00ad" else "palavra_confirmada"}
            normalizacoes.append(registro)
            novo_mapa.append({"inicio": len(novo), "fim": len(novo) + len(palavra), "origens": fontes,
                              "literal": False, "normalizacao_id": registro["id"]})
            novo += palavra
            cursor = achado.end()
        novo_mapa += _recortar_mapa(mapa, cursor, len(texto), len(novo))
        novo += texto[cursor:]
        unidade["texto"], unidade["mapa"] = novo, novo_mapa
    sem_conteudo = [u for u in unidades if not u["tem_conteudo"]]
    unidades = [u for u in unidades if u["tem_conteudo"]]
    paginas_utilizaveis = {f["pagina_pdf"] for u in unidades for parte in u["mapa"] for f in parte["origens"]}
    for numero in sorted(selecionadas - paginas_utilizaveis):
        avisos.append(_aviso("pagina_sem_conteudo_apos_preparacao", "Página selecionada sem conteúdo explicativo após a preparação; conferir a fonte e as exclusões antes de vetorizar.", pagina_pdf=numero))
    if sem_conteudo:
        avisos.append(_aviso("titulos_sem_conteudo", "Títulos sem texto explicativo foram preservados na preparação, sem gerar vetores isolados.", total=len(sem_conteudo)))
    resultado["preparacao"] = {
        "versao": "1.0.0", "unidades": unidades, "exclusoes": exclusoes,
        "normalizacoes": normalizacoes, "titulos_sem_conteudo": sem_conteudo,
        "paginas_incluidas": sorted(selecionadas), "candidatos_hifenizacao": candidatos,
        "recompor_palavras_confirmadas": sorted(aprovadas), "avisos": avisos,
    }
    return resultado


def fragmentar_livro(extracao, tokenizador, alvo_tokens=380, max_tokens=512, prefixo="passage: "):
    """Fragmenta extração literal ou preparada, sem truncamento e com fontes raw.

    Na preparação, cada fragmento leva o mapa de intervalos e o texto bruto.
    O agrupamento de título/regra/exemplo é preferido, mas o limite real do E5
    prevalece. Divisões de uma unidade são sinalizadas e ligadas por vizinhos.
    """
    if not isinstance(extracao, dict) or "preparacao" not in extracao:
        return _fragmentar_literal(extracao, tokenizador, alvo_tokens, max_tokens, prefixo)
    preparacao = extracao["preparacao"]
    unidades = {u["id"]: u for u in preparacao["unidades"]}
    if not unidades:
        raise ErroPreparacaoLivro("Não há conteúdo explicativo para vetorizar depois da preparação. Confira títulos, exclusões e páginas sem texto.")
    derivada = {"obra": extracao["obra"], "sumario": extracao.get("sumario", []), "paginas": []}
    for pagina in extracao["paginas"]:
        blocos, partes, cursor = [], [], 0
        for unidade in preparacao["unidades"]:
            if unidade["pagina_pdf"] != pagina["pagina_pdf"]:
                continue
            if partes:
                partes.append("\n\n")
                cursor += 2
            texto = unidade["texto"]
            fontes = _fontes_mapa(unidade["mapa"])
            blocos.append({"id": unidade["id"], "texto": texto, "bbox": fontes[0]["bbox"],
                           "offset_inicio": cursor, "offset_fim": cursor + len(texto)})
            partes.append(texto)
            cursor += len(texto)
        derivada["paginas"].append({**pagina, "blocos": blocos, "texto": "".join(partes)})
    resultado = _fragmentar_literal(derivada, tokenizador, alvo_tokens, max_tokens, prefixo)
    paginas = {p["pagina_pdf"]: p for p in extracao["paginas"]}
    por_normalizacao = {n["id"]: n for n in preparacao["normalizacoes"]}
    usos_unidades, mantidos, titulos_isolados = {}, [], []
    for fragmento in resultado["fragmentos"]:
        mapa, cursor, papeis = [], 0, {}
        for origem in fragmento["origem"]:
            if mapa:
                cursor += 2
            unidade = unidades[origem["bloco_id"]]
            inicio, fim = origem["offset_bloco_inicio"], origem["offset_bloco_fim"]
            partes = _recortar_mapa(unidade["mapa"], inicio, fim, cursor)
            mapa.extend(partes)
            papeis.update(unidade["papeis_blocos"])
            cursor += fim - inicio
            usos_unidades.setdefault(unidade["id"], []).append(fragmento["id"])
        fontes = _fontes_mapa(mapa)
        if fontes and all(papeis[f["bloco_id"]] == "titulo_candidato" for f in fontes):
            titulos_isolados.append({"texto": fragmento["texto"], "origem": fontes, "motivo": "titulo_excedeu_orcamento_com_conteudo"})
            continue
        fragmento["origem"] = fontes
        fragmento["mapa_texto_preparado"] = mapa
        fragmento["texto_extracao_bruta"] = "\n\n".join(paginas[f["pagina_pdf"]]["texto"][f["offset_inicio"]:f["offset_fim"]] for f in fontes)
        fragmento["normalizacoes"] = [copy.deepcopy(por_normalizacao[n]) for n in dict.fromkeys(p["normalizacao_id"] for p in mapa if "normalizacao_id" in p)]
        # A identidade inclui as fontes reais, e não IDs da camada derivada.
        identidade = json.dumps({"pdf": extracao["obra"]["sha256_pdf"], "origem": fontes, "texto": fragmento["texto"]}, ensure_ascii=False, sort_keys=True)
        fragmento["id"] = "frag-" + _sha256(identidade)
        mantidos.append(fragmento)
    if not mantidos:
        raise ErroPreparacaoLivro("Não restou conteúdo explicativo dentro do orçamento E5; confira títulos e extração.")
    for indice, fragmento in enumerate(mantidos):
        fragmento["linha_vetor"] = indice
        fragmento["anterior_id"] = mantidos[indice - 1]["id"] if indice else None
        fragmento["seguinte_id"] = mantidos[indice + 1]["id"] if indice + 1 < len(mantidos) else None
    resultado["fragmentos"] = mantidos
    relatorio = resultado["relatorio"]
    cobertura = {}
    for fragmento in mantidos:
        for fonte in fragmento["origem"]:
            cobertura.setdefault(fonte["bloco_id"], []).append((fonte["offset_inicio"], fonte["offset_fim"]))
    caracteres_raw = 0
    for intervalos in cobertura.values():
        inicio, fim = sorted(intervalos)[0]
        for a, b in sorted(intervalos)[1:]:
            if a <= fim:
                fim = max(fim, b)
            else:
                caracteres_raw += fim - inicio
                inicio, fim = a, b
        caracteres_raw += fim - inicio
    relatorio["caracteres_preparados"] = relatorio["caracteres_origem"]
    relatorio["caracteres_origem"] = caracteres_raw
    relatorio.update({"total_fragmentos": len(mantidos), "normalizacoes": copy.deepcopy(preparacao["normalizacoes"]),
                      "preparacao_versao": preparacao["versao"], "exclusoes": copy.deepcopy(preparacao["exclusoes"]),
                      "titulos_sem_conteudo": copy.deepcopy(preparacao["titulos_sem_conteudo"]),
                      "titulos_isolados_por_limite": titulos_isolados,
                      "paginas_incluidas": preparacao["paginas_incluidas"],
                      "candidatos_hifenizacao": copy.deepcopy(preparacao["candidatos_hifenizacao"])})
    relatorio["avisos"].extend(copy.deepcopy(preparacao["avisos"]))
    divididas = [u for u, usos in usos_unidades.items() if len(usos) > 1]
    if divididas:
        relatorio["avisos"].append(_aviso("unidades_divididas_por_limite", "Unidades título/regra/exemplo excederam o orçamento E5; conferir junto com fragmentos vizinhos.", unidades=divididas))
    return resultado
