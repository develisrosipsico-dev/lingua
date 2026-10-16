"""Vetorização retomável e pacote portátil de uma gramática, sem carregar modelos.

O chamador prepara o PDF, os fragmentos e o SentenceTransformer. Este módulo
recebe esse objeto explicitamente; não baixa pesos nem depende do aplicativo.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

import numpy as np


MODELO = "intfloat/multilingual-e5-large"
REVISAO = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"
DIMENSAO = 1024
SCHEMA = "gramatica.acervo.v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ARQUIVOS = (
    "obra.json", "perfil_embeddings.json", "fragmentos.jsonl", "vetores.npy",
    "relatorio_extracao.json", "LEIA-ME.md",
)
_ANEXOS_OPCIONAIS = (
    "avaliacao_recuperacao.json", "consultas_teste.json", "vetores_consultas.npy",
    "paginas.jsonl", "blocos.jsonl",
    "config_notebook.json", "extracao.json", "preparacao.json",
    "auditoria_fragmentos.json", "historico_execucao.jsonl",
)


class ErroAcervo(ValueError):
    """Entrada incompatível, checkpoint corrompido ou acervo incompleto."""


def _json_bytes(valor):
    return (json.dumps(valor, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _ler_json(caminho):
    def invalido(valor):
        raise ErroAcervo(f"Constante JSON inválida: {valor}")
    return json.loads(Path(caminho).read_text(encoding="utf-8"), parse_constant=invalido)


def _sha_bytes(valor):
    return hashlib.sha256(valor).hexdigest()


def _sha_arquivo(caminho):
    h = hashlib.sha256()
    with Path(caminho).open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _atomico(caminho, dados):
    caminho = Path(caminho)
    temporario = caminho.with_name(caminho.name + ".tmp")
    with temporario.open("wb") as arquivo:
        arquivo.write(dados)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    temporario.replace(caminho)


def _limite_tokens(perfil):
    if not isinstance(perfil, dict):
        raise ErroAcervo("O perfil de embeddings deve ser um objeto.")
    limite = perfil.get("limite_tokens", 512)
    if type(limite) is not int or not 16 <= limite <= 512:
        raise ErroAcervo("limite_tokens deve ser um inteiro entre 16 e 512.")
    return limite


def _perfil(perfil, fragmentos_sha256):
    perfil = dict(perfil)
    limite = _limite_tokens(perfil)
    padroes = {
        "modelo": MODELO, "revisao": REVISAO,
        "tokenizador": {"identificacao": MODELO, "revisao": REVISAO},
        "pooling": "mean", "precisao_inferencia": "float32",
        "prefixo": "passage: ", "dimensao": DIMENSAO,
        "normalizacao": "l2", "formato_armazenamento": "float32",
    }
    for chave, valor in padroes.items():
        if chave in perfil and perfil[chave] != valor:
            raise ErroAcervo(f"Perfil E5 incompatível: {chave}.")
        perfil[chave] = valor
    perfil["limite_tokens"] = limite
    if not _SHA.fullmatch(str(perfil.get("pdf_sha256", ""))):
        raise ErroAcervo("Informe pdf_sha256: SHA256 do PDF de origem.")
    if ("fragmentos_sha256" in perfil
            and perfil["fragmentos_sha256"] != fragmentos_sha256):
        raise ErroAcervo("O perfil pertence a outros fragmentos.")
    perfil["fragmentos_sha256"] = fragmentos_sha256
    hash_informado = perfil.pop("perfil_sha256", None)
    perfil["perfil_sha256"] = _sha_bytes(_json_bytes(perfil))
    if hash_informado is not None and hash_informado != perfil["perfil_sha256"]:
        raise ErroAcervo("O hash do perfil não confere.")
    return perfil


def _conferir_fragmentos(fragmentos, limite_tokens=512):
    if not isinstance(fragmentos, list) or not fragmentos:
        raise ErroAcervo("Não há fragmentos para vetorizar.")
    ids = set()
    linhas = []
    for numero, fragmento in enumerate(fragmentos):
        if not isinstance(fragmento, dict):
            raise ErroAcervo(f"Fragmento {numero}: objeto inválido.")
        identificador = fragmento.get("id")
        if not isinstance(identificador, str) or not identificador or identificador in ids:
            raise ErroAcervo(f"Fragmento {numero}: id ausente ou repetido.")
        ids.add(identificador)
        if type(fragmento.get("linha_vetor")) is not int or fragmento["linha_vetor"] != numero:
            raise ErroAcervo(f"Fragmento {numero}: linha_vetor deve seguir a ordem 0..N-1.")
        texto = fragmento.get("texto")
        entrada = fragmento.get("texto_embedding")
        if not isinstance(texto, str) or not texto.strip():
            raise ErroAcervo(f"Fragmento {numero}: texto vazio.")
        if not isinstance(entrada, str) or not entrada.startswith("passage: "):
            raise ErroAcervo(f"Fragmento {numero}: texto_embedding sem prefixo passage.")
        tokens = fragmento.get("tokens_embedding")
        if type(tokens) is not int or not 1 <= tokens <= limite_tokens:
            raise ErroAcervo(f"Fragmento {numero}: contagem de tokens inválida.")
        for chave, conteudo in (("sha256_texto", texto), ("sha256_texto_embedding", entrada)):
            if fragmento.get(chave) != _sha_bytes(conteudo.encode("utf-8")):
                raise ErroAcervo(f"Fragmento {numero}: {chave} não confere.")
        linhas.append(_json_bytes(fragmento))
    return b"".join(linhas)


def _conferir_matriz(matriz, linhas):
    if matriz.shape != (linhas, DIMENSAO) or matriz.dtype != np.dtype("float32"):
        raise ErroAcervo(f"Matriz deve ser float32 com dimensão ({linhas}, {DIMENSAO}).")
    for inicio in range(0, linhas, 1024):
        bloco = matriz[inicio:inicio + 1024]
        if not np.isfinite(bloco).all():
            raise ErroAcervo("Matriz contém NaN ou infinito.")
        normas = np.linalg.norm(bloco.astype(np.float64), axis=1)
        if not np.allclose(normas, 1.0, atol=2e-5, rtol=2e-5):
            raise ErroAcervo("Matriz contém vetores sem normalização L2 válida.")


def _validar_modelo(modelo, fragmentos, limite_perfil=512):
    dimensao = getattr(modelo, "get_sentence_embedding_dimension", None)
    if callable(dimensao) and dimensao() != DIMENSAO:
        raise ErroAcervo("O modelo carregado não gera 1024 dimensões.")
    dtype = getattr(modelo, "dtype", None)
    if dtype is not None and str(dtype) not in ("torch.float32", "float32"):
        raise ErroAcervo("Carregue os pesos em float32 para corresponder ao perfil.")
    tokenizador = getattr(modelo, "tokenizer", None)
    if tokenizador is None or not callable(getattr(tokenizador, "encode", None)):
        raise ErroAcervo("O modelo deve expor seu tokenizer para conferir ausência de truncamento.")
    limite = getattr(modelo, "max_seq_length", None)
    if type(limite) is not int or limite <= 0:
        raise ErroAcervo("O modelo deve informar max_seq_length.")
    for fragmento in fragmentos:
        tokens = len(tokenizador.encode(fragmento["texto_embedding"], add_special_tokens=True,
                                        truncation=False))
        if tokens != fragmento["tokens_embedding"] or tokens > min(limite, limite_perfil, 512):
            raise ErroAcervo(f"Fragmento {fragmento['id']}: tokens incompatíveis ou truncamento possível.")


def _oom(erro):
    # PyTorch é opcional aqui. A exceção pode ser torch.cuda.OutOfMemoryError,
    # mas também RuntimeError nas versões suportadas pelo Colab.
    return (isinstance(erro, RuntimeError)
            and (type(erro).__name__ == "OutOfMemoryError"
                 or "cuda out of memory" in str(erro).lower()
                 or "cuda error: out of memory" in str(erro).lower()))


def _limpar_cuda(modelo):
    # Somente o objeto de modelo já carregado pode optar por essa limpeza.
    limpar = getattr(modelo, "limpar_cache_cuda", None)
    if callable(limpar):
        limpar()
    # SentenceTransformer não fornece esse método. Importação tardia somente
    # após OOM, e somente se o chamador realmente carregou torch.
    import sys
    torch = sys.modules.get("torch")
    if torch is not None and getattr(torch, "cuda", None) is not None:
        torch.cuda.empty_cache()


def _ler_lotes(pasta, perfil, total):
    lotes = []
    proximo = 0
    for metadados_path in sorted(pasta.glob("lote-*.json")):
        dados = _ler_json(metadados_path)
        inicio, fim = dados.get("inicio"), dados.get("fim")
        if (dados.get("perfil_sha256") != perfil["perfil_sha256"]
                or type(inicio) is not int or type(fim) is not int
                or inicio != proximo or not inicio < fim <= total):
            raise ErroAcervo("Checkpoint pertence a outra execução ou contém lacunas.")
        esperado = f"lote-{inicio:09d}-{fim:09d}.npy"
        if dados.get("arquivo") != esperado or metadados_path.stem != esperado[:-4]:
            raise ErroAcervo("Nome de checkpoint inválido.")
        arquivo = pasta / esperado
        if not arquivo.is_file() or _sha_arquivo(arquivo) != dados.get("sha256"):
            raise ErroAcervo("SHA256 do checkpoint não confere; não será reutilizado.")
        matriz = np.load(arquivo, mmap_mode="r", allow_pickle=False)
        _conferir_matriz(matriz, fim - inicio)
        lotes.append((inicio, fim, arquivo))
        proximo = fim
    return lotes, proximo


def vetorizar_fragmentos(fragmentos, modelo, pasta, perfil, lote_inicial=64, progresso=None):
    """Gera vetores reais pelo modelo recebido, com retomada estritamente compatível.

    ``progresso``, quando presente, recebe um dicionário com ``concluidos``,
    ``total``, ``lote`` e ``retomado``. Erros interrompem a execução; os lotes
    válidos permanecem disponíveis. Nenhum vetor sintético substitui uma falha.
    """
    if type(lote_inicial) is not int or lote_inicial < 1:
        raise ErroAcervo("lote_inicial deve ser um inteiro positivo.")
    bytes_fragmentos = _conferir_fragmentos(fragmentos, _limite_tokens(perfil))
    perfil = _perfil(perfil, _sha_bytes(bytes_fragmentos))
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    checkpoints = pasta / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    perfil_path = pasta / "perfil_embeddings.json"
    if perfil_path.exists() and _perfil(_ler_json(perfil_path), _sha_bytes(bytes_fragmentos)) != perfil:
        raise ErroAcervo("Esta pasta contém outro perfil ou outros fragmentos. Use outra pasta.")
    fragmentos_path = pasta / "fragmentos.jsonl"
    if fragmentos_path.exists() and fragmentos_path.read_bytes() != bytes_fragmentos:
        raise ErroAcervo("Esta pasta contém outros fragmentos. Use outra pasta.")
    checkpoint_perfil = checkpoints / "perfil.json"
    if checkpoint_perfil.exists() and _ler_json(checkpoint_perfil) != perfil:
        raise ErroAcervo("O perfil dos checkpoints não confere.")
    _validar_modelo(modelo, fragmentos, perfil["limite_tokens"])
    _atomico(perfil_path, _json_bytes(perfil))
    _atomico(fragmentos_path, bytes_fragmentos)
    _atomico(checkpoint_perfil, _json_bytes(perfil))
    lotes, concluido = _ler_lotes(checkpoints, perfil, len(fragmentos))
    estado_path = pasta / "estado_vetorizacao.json"
    _atomico(estado_path, _json_bytes({"estado": "incompleto", "perfil_sha256": perfil["perfil_sha256"]}))
    tamanho = lote_inicial
    if progresso:
        progresso({"concluidos": concluido, "total": len(fragmentos), "lote": tamanho,
                   "retomado": concluido > 0})
    while concluido < len(fragmentos):
        fim = min(concluido + tamanho, len(fragmentos))
        entradas = [f["texto_embedding"] for f in fragmentos[concluido:fim]]
        try:
            vetores = modelo.encode(entradas, batch_size=tamanho, prompt="",
                                    normalize_embeddings=True, convert_to_numpy=True,
                                    show_progress_bar=False)
        except RuntimeError as erro:
            if not _oom(erro) or tamanho == 1:
                raise
            _limpar_cuda(modelo)
            tamanho = max(1, tamanho // 2)
            continue
        vetores = np.asarray(vetores)
        # Armazenar fp32 não promete precisão de inferência fp32: o notebook
        # também deve carregar os pesos e executar o modelo em float32.
        if not np.issubdtype(vetores.dtype, np.floating):
            raise ErroAcervo("O modelo retornou valores que não são ponto flutuante.")
        vetores = np.asarray(vetores, dtype=np.float32)
        _conferir_matriz(vetores, fim - concluido)
        nome = f"lote-{concluido:09d}-{fim:09d}.npy"
        caminho = checkpoints / nome
        temporario = caminho.with_suffix(".npy.tmp")
        with temporario.open("wb") as arquivo:
            np.save(arquivo, vetores, allow_pickle=False)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        temporario.replace(caminho)
        _atomico(caminho.with_suffix(".json"), _json_bytes({
            "inicio": concluido, "fim": fim, "arquivo": nome,
            "sha256": _sha_arquivo(caminho), "perfil_sha256": perfil["perfil_sha256"],
        }))
        lotes.append((concluido, fim, caminho))
        concluido = fim
        if progresso:
            progresso({"concluidos": concluido, "total": len(fragmentos), "lote": tamanho,
                       "retomado": False})
    destino = pasta / "vetores.npy"
    temporario = pasta / "vetores.npy.tmp"
    matriz = np.lib.format.open_memmap(temporario, mode="w+", dtype=np.float32,
                                       shape=(len(fragmentos), DIMENSAO))
    for inicio, fim, caminho in lotes:
        matriz[inicio:fim] = np.load(caminho, mmap_mode="r", allow_pickle=False)
    matriz.flush()
    _conferir_matriz(matriz, len(fragmentos))
    del matriz
    temporario.replace(destino)
    _atomico(estado_path, _json_bytes({
        "estado": "completo", "perfil_sha256": perfil["perfil_sha256"],
        "fragmentos": len(fragmentos), "vetores_sha256": _sha_arquivo(destino),
    }))
    return destino


def validar_acervo(pasta):
    """Confere os arquivos locais, referências e a matriz; nunca faz inferência."""
    pasta = Path(pasta)
    erros = []
    resultado = {"schema": SCHEMA, "valido": False, "estado": "incompleto", "erros": erros}
    try:
        for nome in _ARQUIVOS[:-1]:
            if not (pasta / nome).is_file() or (pasta / nome).is_symlink():
                raise ErroAcervo(f"Arquivo obrigatório ausente ou inválido: {nome}.")
        fragmentos = [json.loads(linha) for linha in (pasta / "fragmentos.jsonl").read_text(
            encoding="utf-8").splitlines() if linha.strip()]
        perfil_bruto = _ler_json(pasta / "perfil_embeddings.json")
        canonico = _conferir_fragmentos(fragmentos, _limite_tokens(perfil_bruto))
        if canonico != (pasta / "fragmentos.jsonl").read_bytes():
            raise ErroAcervo("fragmentos.jsonl não coincide com a serialização canônica.")
        perfil = _perfil(perfil_bruto, _sha_bytes(canonico))
        obra = _ler_json(pasta / "obra.json")
        if not isinstance(obra, dict) or not str(obra.get("titulo", "")).strip():
            raise ErroAcervo("obra.json precisa identificar o título da obra.")
        if obra.get("pdf_sha256") != perfil["pdf_sha256"]:
            raise ErroAcervo("SHA256 do PDF diverge entre obra e perfil.")
        if not isinstance(_ler_json(pasta / "relatorio_extracao.json"), dict):
            raise ErroAcervo("Relatório de extração inválido.")
        matriz = np.load(pasta / "vetores.npy", mmap_mode="r", allow_pickle=False)
        _conferir_matriz(matriz, len(fragmentos))
        estado = _ler_json(pasta / "estado_vetorizacao.json")
        if (estado.get("estado") != "completo" or estado.get("perfil_sha256") != perfil["perfil_sha256"]
                or estado.get("fragmentos") != len(fragmentos)
                or estado.get("vetores_sha256") != _sha_arquivo(pasta / "vetores.npy")):
            raise ErroAcervo("Execução não concluída ou integridade final inválida.")
        resultado.update(valido=True, estado="completo", fragmentos=len(fragmentos),
                         dimensao=DIMENSAO, dtype="float32", perfil_sha256=perfil["perfil_sha256"])
    except (OSError, ValueError, TypeError, KeyError) as erro:
        erros.append(f"{type(erro).__name__}: {erro}")
    return resultado


def exportar_acervo(pasta, destino=None):
    """Exporta apenas acervo completo; checkpoints e pesos ficam fora do ZIP."""
    pasta = Path(pasta)
    relatorio = validar_acervo(pasta)
    if not relatorio["valido"]:
        raise ErroAcervo("O acervo está incompleto: " + "; ".join(relatorio["erros"]))
    leia_me = (
        "# Acervo gramatical vetorizado\n\n"
        "Os textos e suas referências de origem acompanham vetores E5 reais. "
        "Este pacote não aplica regras gramaticais automaticamente e não é o formato "
        "de vetorização de relatos do Agente Analista. A importação no Língua será "
        "uma etapa separada.\n\n"
        "`fragmentos.jsonl`: linha_vetor identifica a linha correspondente em "
        "`vetores.npy`. `texto` preserva a citação; `texto_embedding` registra "
        "exatamente a entrada enviada ao modelo, incluindo prefixo e seção. "
        "Os vetores são float32, 1024 dimensões, com normalização L2.\n\n"
        "`obra.json` identifica o PDF por SHA256; páginas PDF e rótulos impressos "
        "são distintos. Confira amostras, notas e leitura de colunas no relatório "
        "de extração. Integridade numérica não demonstra fidelidade editorial "
        "nem correção de uma futura análise linguística.\n\n"
        "`manifesto.json` registra tamanho e SHA256 de cada arquivo, exceto o próprio "
        "manifesto. Pesos do modelo, PDF e checkpoints não são incluídos.\n"
    )
    _atomico(pasta / "LEIA-ME.md", leia_me.encode("utf-8"))
    arquivos = list(_ARQUIVOS) + ["estado_vetorizacao.json"]
    for nome in _ANEXOS_OPCIONAIS:
        if (pasta / nome).is_file():
            arquivos.append(nome)
    codigo = pasta / "codigo"
    if codigo.is_dir() and not codigo.is_symlink():
        arquivos.extend("codigo/" + f.name for f in sorted(codigo.glob("*.py"))
                        if f.is_file() and not f.is_symlink())
    for nome in arquivos:
        if (pasta / nome).is_symlink():
            raise ErroAcervo(f"Não exportar link simbólico: {nome}.")
    manifesto = {
        "schema": SCHEMA, "estado": "completo", "validacao": relatorio,
        "arquivos": {nome: {"sha256": _sha_arquivo(pasta / nome),
                            "bytes": (pasta / nome).stat().st_size} for nome in arquivos},
    }
    _atomico(pasta / "manifesto.json", _json_bytes(manifesto))
    destino = Path(destino) if destino else pasta.with_name(pasta.name + ".zip")
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.resolve() in {(pasta / nome).resolve() for nome in arquivos + ["manifesto.json"]}:
        raise ErroAcervo("O ZIP de destino não pode substituir arquivos do acervo.")
    if destino.resolve().is_relative_to((pasta / "checkpoints").resolve()):
        raise ErroAcervo("O destino não pode ficar na pasta de checkpoints.")
    temporario = destino.with_name(destino.name + ".tmp")
    try:
        with zipfile.ZipFile(temporario, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as pacote:
            for nome in arquivos + ["manifesto.json"]:
                pacote.write(pasta / nome, arcname=nome)
        temporario.replace(destino)
    finally:
        if temporario.exists():
            temporario.unlink()
    return destino
