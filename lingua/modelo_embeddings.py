"""Adaptadores reais de embeddings, carregados sob demanda e offline por padrão.

O núcleo fornece a string exata (incluindo prefixo). Este módulo não acrescenta
prompts, não trunca e não aplica a normalização final do perfil. Ler registros
JSON não exige PyTorch, SentenceTransformers, FastEmbed ou conexão de rede.
"""
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import threading

from perfis_vetorizacao import carregar_perfil, validar_perfil


class ErroModeloEmbeddings(ValueError):
    """Backend/modelo/configuração indisponível ou saída inválida."""


_RECURSOS = {}
_RECURSOS_LOCK = threading.RLock()


def _configurar_cache(cache):
    # hf-xet consulta o ambiente do processo independentemente de cache_dir.
    # Definir antes de importar o backend evita escritas no HOME da máquina.
    # Uma escolha explícita do usuário no ambiente tem precedência.
    os.environ.setdefault("HF_XET_CACHE", str(Path(cache).resolve() / "xet"))


def _versao(nome):
    try:
        return importlib.metadata.version(nome)
    except importlib.metadata.PackageNotFoundError:
        return "nao_instalado"


def _sha_arquivo(caminho):
    sha = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for trecho in iter(lambda: arquivo.read(1024 * 1024), b""):
            sha.update(trecho)
    return sha.hexdigest()


def _resolver_snapshot(identificacao, revisao, cache, somente_local, *, somente_tokenizador=False):
    _configurar_cache(cache)
    caminho = Path(identificacao)
    if caminho.is_dir():
        # A revisão local é SHA-256 do manifesto canônico dos arquivos.
        arquivos = {str(p.relative_to(caminho)): _sha_arquivo(p)
                    for p in sorted(caminho.rglob("*")) if p.is_file()}
        manifesto = hashlib.sha256(json.dumps(arquivos, sort_keys=True,
                    ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        if revisao != manifesto:
            raise ErroModeloEmbeddings("A revisão local não corresponde ao SHA-256 do manifesto dos arquivos.")
        return str(caminho.resolve()), {"manifesto_sha256": manifesto, "arquivos": arquivos}
    try:
        from huggingface_hub import snapshot_download
        snapshot = snapshot_download(
            repo_id=identificacao, revision=revisao, cache_dir=cache,
            local_files_only=somente_local,
            allow_patterns=["*.json", "*.txt", "*.model", "*.vocab", "*.merges"]
                           + ([] if somente_tokenizador else ["*.safetensors"]),
            ignore_patterns=["onnx/*", "openvino/*", "*.bin", "*.h5", "*.msgpack", "*.onnx"],
        )
    except ImportError as exc:
        raise ErroModeloEmbeddings("Instale as dependências de embeddings; huggingface-hub não está disponível.") from exc
    except Exception as exc:
        raise ErroModeloEmbeddings(
            "Modelo/tokenizador indisponível no cache. Faça o download explícito com "
            "python modelo_embeddings.py --baixar --perfil e5_simetrico "
            "e as mesmas opções de modelo/cache. " + str(exc)
        ) from exc
    # snapshot_download devolve a pasta do commit, mesmo na leitura offline.
    commit = Path(snapshot).name
    if len(revisao) == 40 and commit != revisao:
        raise ErroModeloEmbeddings("O snapshot resolvido não corresponde à revisão solicitada.")
    pasta = Path(snapshot)
    arquivos = {}
    extensoes = {".json", ".txt", ".model", ".vocab", ".merges"}
    if not somente_tokenizador:
        extensoes.add(".safetensors")
    for item in sorted(pasta.rglob("*")):
        relativo = item.relative_to(pasta)
        if item.is_file() and item.suffix in extensoes and not any(
            parte in {"onnx", "openvino"} for parte in relativo.parts
        ):
            arquivos[str(relativo)] = _sha_arquivo(item)
    return snapshot, {"commit": commit, "arquivos": arquivos}


def _limite_efetivo(tokenizer, config, limite_solicitado, limite_st=None):
    candidatos = []
    for valor in (getattr(tokenizer, "model_max_length", None),
                  getattr(config, "max_position_embeddings", None),
                  getattr(config, "n_positions", None), limite_st):
        # HF usa inteiros gigantes como sentinela para limite desconhecido.
        if type(valor) is int and 0 < valor < 10**12:
            candidatos.append(valor)
    if not candidatos:
        raise ErroModeloEmbeddings("O modelo não declara limite efetivo verificável; use um adaptador específico.")
    capacidade = min(candidatos)
    if limite_solicitado is not None and limite_solicitado > capacidade:
        raise ErroModeloEmbeddings(f"limite_tokens={limite_solicitado} excede capacidade {capacidade}.")
    return min(capacidade, limite_solicitado) if limite_solicitado is not None else capacidade


def _validar_tokenizacao(resultado, texto):
    obrigatorios = ("input_ids", "attention_mask", "special_tokens_mask", "offsets")
    if any(type(resultado.get(k)) is not list for k in obrigatorios):
        raise ErroModeloEmbeddings("Tokenizador não retornou os campos exigidos.")
    n = len(resultado["input_ids"])
    if not n or any(len(resultado[k]) != n for k in obrigatorios):
        raise ErroModeloEmbeddings("Tokenização vazia ou campos com comprimentos diferentes.")
    for valor in resultado["input_ids"]:
        if type(valor) is not int or valor < 0:
            raise ErroModeloEmbeddings("ID de token inválido.")
    for campo in ("attention_mask", "special_tokens_mask"):
        if any(type(v) is not int or v not in (0, 1) for v in resultado[campo]):
            raise ErroModeloEmbeddings(f"{campo} inválida.")
    for i, par in enumerate(resultado["offsets"]):
        if type(par) is not list or len(par) != 2 or any(type(v) is not int for v in par):
            raise ErroModeloEmbeddings("Offsets precisam de pares inteiros.")
        if not 0 <= par[0] <= par[1] <= len(texto):
            raise ErroModeloEmbeddings("Offset fora da entrada efetiva.")
        if resultado["special_tokens_mask"][i] and par != [0, 0]:
            raise ErroModeloEmbeddings("Tokens especiais devem ter offset [0,0].")
    if "token_type_ids" in resultado and (
        type(resultado["token_type_ids"]) is not list or len(resultado["token_type_ids"]) != n
        or any(type(v) is not int or v < 0 for v in resultado["token_type_ids"])
    ):
        raise ErroModeloEmbeddings("token_type_ids inválidos.")
    return resultado


def _tokenizar_hf(tokenizer, texto):
    if type(texto) is not str:
        raise ErroModeloEmbeddings("A entrada do tokenizer deve ser string.")
    if not getattr(tokenizer, "is_fast", False):
        raise ErroModeloEmbeddings("É necessário tokenizer fast com offsets exatos.")
    try:
        dados = tokenizer(texto, add_special_tokens=True, truncation=False, padding=False,
                          return_attention_mask=True, return_special_tokens_mask=True,
                          return_offsets_mapping=True)
    except Exception as exc:
        raise ErroModeloEmbeddings(f"Falha na tokenização sem truncamento: {exc}") from exc
    resultado = {k: list(dados[k]) for k in ("input_ids", "attention_mask", "special_tokens_mask")}
    resultado["offsets"] = [list(par) for par in dados["offset_mapping"]]
    if "token_type_ids" in dados:
        resultado["token_type_ids"] = list(dados["token_type_ids"])
    for i, especial in enumerate(resultado["special_tokens_mask"]):
        if especial:
            resultado["offsets"][i] = [0, 0]
    return _validar_tokenizacao(resultado, texto)


def _validar_vetores(vetores, quantidade, dimensao):
    if type(vetores) is not list or len(vetores) != quantidade:
        raise ErroModeloEmbeddings("O backend retornou quantidade incorreta de vetores.")
    for vetor in vetores:
        if type(vetor) is not list or len(vetor) != dimensao:
            raise ErroModeloEmbeddings("O backend retornou dimensão incorreta.")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in vetor):
            raise ErroModeloEmbeddings("O backend retornou valores não finitos.")
        if not any(v != 0 for v in vetor):
            raise ErroModeloEmbeddings("O backend retornou vetor zero.")
    return vetores


def _selecionar_dispositivo(torch, solicitado):
    if solicitado.startswith("cuda"):
        if not torch.cuda.is_available():
            raise ErroModeloEmbeddings("CUDA solicitada, mas indisponível; escolha cpu ou instale PyTorch CUDA.")
        indice = int(solicitado.split(":", 1)[1]) if ":" in solicitado else 0
        if indice >= torch.cuda.device_count():
            raise ErroModeloEmbeddings("Índice de GPU CUDA indisponível.")
        return f"cuda:{indice}"
    if solicitado == "mps":
        if not hasattr(torch.backends, "mps") or not torch.backends.mps.is_available():
            raise ErroModeloEmbeddings("MPS solicitada, mas indisponível; escolha cpu.")
    return solicitado


def _chave_recurso(perfil):
    nomes = ("backend", "modelo", "tokenizador", "dispositivo", "precisao_inferencia",
             "cache_modelos", "somente_local", "arquivo_onnx", "modelo_onnx_sha256", "arquivos_onnx_sha256")
    return json.dumps({k: perfil[k] for k in nomes if k in perfil}, sort_keys=True, ensure_ascii=False)


def _carregar_st(perfil):
    _configurar_cache(perfil["cache_modelos"])
    try:
        import torch
        from sentence_transformers import SentenceTransformer
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise ErroModeloEmbeddings(
            "Não foi possível importar as dependências de embeddings. "
            f"Detalhe: {exc}. Confira Torch, SentenceTransformers e Transformers "
            "no ambiente Python que executa o Língua."
        ) from exc
    dispositivo = _selecionar_dispositivo(torch, perfil["dispositivo"])
    caminho, manifesto = _resolver_snapshot(**{
        "identificacao": perfil["modelo"]["identificacao"], "revisao": perfil["modelo"]["revisao"],
        "cache": perfil["cache_modelos"], "somente_local": perfil["somente_local"]})
    tokenizer_path, tokenizer_manifesto = _resolver_snapshot(
        perfil["tokenizador"]["identificacao"], perfil["tokenizador"]["revisao"],
        perfil["cache_modelos"], perfil["somente_local"], somente_tokenizador=True)
    try:
        modelo = SentenceTransformer(caminho, device=dispositivo, local_files_only=True,
                                     trust_remote_code=False)
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True,
                                                 use_fast=True, trust_remote_code=False)
        if not getattr(tokenizer, "is_fast", False):
            raise ErroModeloEmbeddings("O tokenizer precisa oferecer offsets fast.")
        modules = list(modelo._modules.values())
        nomes = [type(m).__name__ for m in modules]
        if not modules or nomes[0] != "Transformer" or any(
            nome not in {"Transformer", "Pooling", "Dense", "Normalize"} for nome in nomes
        ) or nomes.count("Pooling") != 1:
            raise ErroModeloEmbeddings("Arquitetura SentenceTransformers requer adaptador específico; suportamos Transformer/Pooling/Dense/Normalize.")
        # Transformer.forward usa sua própria referência ao tokenizer somente
        # na tokenização; aqui as entradas já foram tokenizadas exatamente.
        primeiro = modules[0]
        primeiro.tokenizer = tokenizer
        dtype = getattr(torch, perfil["precisao_inferencia"])
        modelo.to(device=dispositivo, dtype=dtype)
        modelo.eval()
    except ErroModeloEmbeddings:
        raise
    except Exception as exc:
        raise ErroModeloEmbeddings(f"Falha ao carregar SentenceTransformers: {exc}") from exc
    return {"modelo": modelo, "tokenizer": tokenizer, "torch": torch, "modules": modules,
            "config": primeiro.auto_model.config, "limite_st": modelo.max_seq_length,
            "dispositivo": dispositivo, "manifesto": manifesto,
            "tokenizer_manifesto": tokenizer_manifesto, "lock": threading.RLock()}


class AdaptadorSentenceTransformers:
    def __init__(self, perfil):
        self.perfil = validar_perfil(perfil)
        self._recurso = None
        self._descricao = None
        self._modules = None

    def _carregar(self):
        if self._recurso is None:
            chave = _chave_recurso(self.perfil)
            with _RECURSOS_LOCK:
                if chave not in _RECURSOS:
                    _RECURSOS[chave] = _carregar_st(self.perfil)
                self._recurso = _RECURSOS[chave]
        if self._descricao is None:
            from sentence_transformers.models import Pooling
            recurso = self._recurso
            modules = []
            poolings = []
            dimensao_anterior = None
            for module in recurso["modules"]:
                nome = type(module).__name__
                if nome == "Normalize":
                    continue  # normalização final pertence ao núcleo
                if nome == "Pooling":
                    if self.perfil["pooling"] != "modelo":
                        modo = "lasttoken" if self.perfil["pooling"] == "last_token" else self.perfil["pooling"]
                        module = Pooling(module.word_embedding_dimension, pooling_mode=modo)
                    poolings.append(module.get_config_dict())
                    dimensao_anterior = module.get_sentence_embedding_dimension()
                if nome == "Dense":
                    if module.in_features != dimensao_anterior:
                        raise ErroModeloEmbeddings("Pooling selecionado tem dimensão incompatível com a projeção Dense do modelo.")
                    dimensao_anterior = module.get_sentence_embedding_dimension()
                modules.append(module)
            dimensao = next((m.get_sentence_embedding_dimension() for m in reversed(modules)
                             if hasattr(m, "get_sentence_embedding_dimension")), None)
            if type(dimensao) is not int or dimensao <= 0:
                raise ErroModeloEmbeddings("Dimensão do modelo não pôde ser resolvida.")
            # max_seq_length de SentenceTransformers regula seu encode(), que
            # este adaptador não usa. Não reduzir janelas maiores ao padrão
            # de um wrapper: o encoder/tokenizer e o perfil definem o orçamento.
            limite = _limite_efetivo(recurso["tokenizer"], recurso["config"], None)
            selecionado = _limite_efetivo(recurso["tokenizer"], recurso["config"],
                                         self.perfil["limite_tokens"])
            self._modules = modules
            self._descricao = {
                "backend": "sentence_transformers", "versao_backend": _versao("sentence-transformers"),
                "modelo": deepcopy(self.perfil["modelo"]), "tokenizador": deepcopy(self.perfil["tokenizador"]),
                "dimensao": dimensao, "limite_tokens": limite, "limite_tokens_selecionado": selecionado,
                "max_seq_length_wrapper": recurso["limite_st"],
                "pooling": self.perfil["pooling"], "pooling_configuracao": poolings,
                "dispositivo": recurso["dispositivo"], "precisao_inferencia": self.perfil["precisao_inferencia"],
                "normalizacao_interna": "nenhuma", "prefixo_automatico": False,
                "truncamento_automatico": False, "bibliotecas": {n: _versao(n) for n in (
                    "torch", "transformers", "sentence-transformers", "tokenizers", "huggingface-hub")},
                "artefatos": {"modelo": recurso["manifesto"], "tokenizador": recurso["tokenizer_manifesto"]},
            }
        return self._recurso

    def descrever(self):
        self._carregar()
        return deepcopy(self._descricao)

    def tokenizar(self, texto):
        recurso = self._carregar()
        with recurso["lock"]:
            return _tokenizar_hf(recurso["tokenizer"], texto)

    def gerar(self, entradas):
        if type(entradas) is not list or any(type(t) is not str for t in entradas):
            raise ErroModeloEmbeddings("entradas deve ser uma lista de strings.")
        if not entradas:
            return []
        recurso = self._carregar()
        torch = recurso["torch"]
        resultados = []
        with recurso["lock"], torch.inference_mode():
            for inicio in range(0, len(entradas), self.perfil["lote"]):
                lote = entradas[inicio:inicio + self.perfil["lote"]]
                tokens = [_tokenizar_hf(recurso["tokenizer"], texto) for texto in lote]
                limite = min(self._descricao["limite_tokens"], self.perfil["limite_tokens"] or self._descricao["limite_tokens"])
                if any(len(t["input_ids"]) > limite for t in tokens):
                    raise ErroModeloEmbeddings("Entrada excede limite efetivo; fragmente no núcleo. Nenhum truncamento foi aplicado.")
                features = recurso["tokenizer"].pad(
                    [{k: v for k, v in t.items() if k in ("input_ids", "attention_mask", "token_type_ids")} for t in tokens],
                    padding=True, return_tensors="pt")
                features = {k: v.to(recurso["dispositivo"]) for k, v in features.items()}
                try:
                    for module in self._modules:
                        features = module(features)
                    saida = features["sentence_embedding"].detach().float().cpu().tolist()
                except Exception as exc:
                    raise ErroModeloEmbeddings(f"Falha de inferência SentenceTransformers: {exc}") from exc
                resultados.extend(_validar_vetores(saida, len(lote), self._descricao["dimensao"]))
        return resultados


def _carregar_fastembed(perfil):
    if "arquivo_onnx" not in perfil or "modelo_onnx_sha256" not in perfil:
        raise ErroModeloEmbeddings("FastEmbed exige arquivo_onnx local e modelo_onnx_sha256; exports externos não são presumidos equivalentes.")
    # Preservar a pasta lexical: snapshots HF usam links simbólicos para blobs,
    # mas external_data é resolvido relativamente à pasta do grafo solicitado.
    arquivo = Path(perfil["arquivo_onnx"]).expanduser().absolute()
    if not arquivo.is_file() or _sha_arquivo(arquivo) != perfil["modelo_onnx_sha256"]:
        raise ErroModeloEmbeddings("Arquivo ONNX ausente ou SHA-256 incorreto.")
    try:
        import numpy as np
        import onnx
        import onnxruntime as ort
        from fastembed.text.onnx_text_model import OnnxTextModel
        from tokenizers import Tokenizer
        from transformers import AutoConfig, AutoTokenizer
    except ImportError as exc:
        raise ErroModeloEmbeddings("Instale FastEmbed/ONNX Runtime e Transformers para este adaptador opcional.") from exc
    if perfil["dispositivo"] == "mps":
        raise ErroModeloEmbeddings("Este adaptador FastEmbed não oferece MPS; use CPU ou CUDA.")
    if perfil["pooling"] == "modelo":
        raise ErroModeloEmbeddings("FastEmbed exige pooling explícito para saídas ONNX de tokens.")
    caminho, manifesto = _resolver_snapshot(perfil["modelo"]["identificacao"], perfil["modelo"]["revisao"],
                                            perfil["cache_modelos"], perfil["somente_local"], somente_tokenizador=True)
    tokenizer_path, token_manifesto = _resolver_snapshot(perfil["tokenizador"]["identificacao"],
                    perfil["tokenizador"]["revisao"], perfil["cache_modelos"], perfil["somente_local"],
                    somente_tokenizador=True)
    config = AutoConfig.from_pretrained(caminho, local_files_only=True, trust_remote_code=False)
    hf_tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True,
                                               use_fast=True, trust_remote_code=False)
    if not getattr(hf_tokenizer, "is_fast", False):
        raise ErroModeloEmbeddings("FastEmbed precisa de tokenizer fast com offsets.")
    tokenizer = Tokenizer.from_str(hf_tokenizer.backend_tokenizer.to_str())
    tokenizer.no_truncation()
    tokenizer.no_padding()
    providers = ["CPUExecutionProvider"]
    dispositivo = perfil["dispositivo"]
    if dispositivo.startswith("cuda"):
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise ErroModeloEmbeddings("ONNX Runtime CUDA indisponível; use CPU ou instale o backend GPU.")
        indice = int(dispositivo.split(":", 1)[1]) if ":" in dispositivo else 0
        providers = [("CUDAExecutionProvider", {"device_id": indice}), "CPUExecutionProvider"]
        dispositivo = f"cuda:{indice}"
    grafo = onnx.load_model(str(arquivo), load_external_data=False)
    arquivos_onnx = {arquivo.name: perfil["modelo_onnx_sha256"]}
    def tensores(graph):
        yield from graph.initializer
        for sparse in graph.sparse_initializer:
            yield sparse.values
            yield sparse.indices
        for node in graph.node:
            for atributo in node.attribute:
                if atributo.HasField("t"):
                    yield atributo.t
                yield from atributo.tensors
                if atributo.HasField("g"):
                    yield from tensores(atributo.g)
                for nested in atributo.graphs:
                    yield from tensores(nested)
    def nos(graph):
        for node in graph.node:
            yield node
            for atributo in node.attribute:
                if atributo.HasField("g"):
                    yield from nos(atributo.g)
                for nested in atributo.graphs:
                    yield from nos(nested)
    floats_incompativeis = {getattr(onnx.TensorProto, nome) for nome in (
        "FLOAT16", "DOUBLE", "BFLOAT16", "FLOAT8E4M3FN", "FLOAT8E4M3FNUZ",
        "FLOAT8E5M2", "FLOAT8E5M2FNUZ") if hasattr(onnx.TensorProto, nome)}
    if any(t.data_type in floats_incompativeis for t in tensores(grafo.graph)):
        raise ErroModeloEmbeddings("ONNX usa tensores de precisão diferente de float32; selecione outro adaptador/perfil.")
    for node in nos(grafo.graph):
        if node.op_type.startswith("QLinear") or node.op_type in {
            "DynamicQuantizeLinear", "QuantizeLinear", "DequantizeLinear", "MatMulInteger",
            "ConvInteger", "MatMulNBits", "AttentionQuantized", "QAttention"
        } or any(a.name == "to" and a.i in floats_incompativeis for a in node.attribute):
            raise ErroModeloEmbeddings("ONNX usa quantização ou conversão de precisão não suportada pelo perfil float32.")
    for tensor in tensores(grafo.graph):
        externos = {item.key: item.value for item in tensor.external_data}
        if "location" in externos:
            relativo = Path(externos["location"])
            if relativo.is_absolute() or ".." in relativo.parts:
                raise ErroModeloEmbeddings("ONNX referencia arquivo externo fora da pasta do grafo.")
            alvo = arquivo.parent / relativo
            if not alvo.is_file():
                raise ErroModeloEmbeddings(f"Peso externo ONNX ausente: {relativo}.")
            if str(relativo) not in arquivos_onnx:
                arquivos_onnx[str(relativo)] = _sha_arquivo(alvo)
    if "arquivos_onnx_sha256" in perfil and perfil["arquivos_onnx_sha256"] != arquivos_onnx:
        raise ErroModeloEmbeddings("Hashes dos arquivos ONNX não correspondem aos artefatos fornecidos.")
    session = ort.InferenceSession(str(arquivo), providers=providers)
    if dispositivo.startswith("cuda") and session.get_providers()[0] != "CUDAExecutionProvider":
        raise ErroModeloEmbeddings("A sessão ONNX não ativou CUDA; execução em CPU não foi substituída silenciosamente.")
    saidas = [s for s in session.get_outputs() if len(s.shape) == 3]
    if len(saidas) != 1 or type(saidas[0].shape[2]) is not int or saidas[0].type != "tensor(float)":
        raise ErroModeloEmbeddings("ONNX deve expor uma saída 3D de tokens com dimensão conhecida; saídas já normalizadas/pooladas exigem outro adaptador.")
    if any(i.name not in {"input_ids", "attention_mask", "token_type_ids"} or i.type != "tensor(int64)"
           for i in session.get_inputs()):
        raise ErroModeloEmbeddings("Entradas ONNX requerem um adaptador específico; esperadas sequências inteiras int64.")
    # O caminho FastEmbed bruto executa os IDs recebidos sem pós-processamento
    # de .embed(), que aplicaria normalização e poderia acrescentar prompts.
    modelo = OnnxTextModel()
    modelo.tokenizer = tokenizer
    modelo.model = session
    modelo.ONNX_OUTPUT_NAMES = [saidas[0].name]
    return {"modelo": modelo, "tokenizer": tokenizer, "hf_tokenizer": hf_tokenizer,
            "np": np, "config": config, "dispositivo": dispositivo,
            "dimensao": saidas[0].shape[2], "manifesto": manifesto,
            "tokenizer_manifesto": token_manifesto, "arquivo_onnx": str(arquivo),
            "sha256_onnx": perfil["modelo_onnx_sha256"], "arquivos_onnx_sha256": arquivos_onnx,
            "lock": threading.RLock()}


class AdaptadorFastEmbed:
    def __init__(self, perfil):
        self.perfil = validar_perfil(perfil)
        if self.perfil["pooling"] == "modelo":
            raise ErroModeloEmbeddings("FastEmbed exige pooling explícito para saídas ONNX de tokens.")
        self._recurso = None
        self._descricao = None

    def _carregar(self):
        if self._recurso is None:
            chave = _chave_recurso(self.perfil)
            with _RECURSOS_LOCK:
                if chave not in _RECURSOS:
                    _RECURSOS[chave] = _carregar_fastembed(self.perfil)
                self._recurso = _RECURSOS[chave]
        if self._descricao is None:
            recurso = self._recurso
            self._descricao = {
                "backend": "fastembed", "versao_backend": _versao("fastembed"),
                "modelo": deepcopy(self.perfil["modelo"]), "tokenizador": deepcopy(self.perfil["tokenizador"]),
                "dimensao": recurso["dimensao"], "limite_tokens": _limite_efetivo(
                    recurso["hf_tokenizer"], recurso["config"], None),
                "limite_tokens_selecionado": _limite_efetivo(recurso["hf_tokenizer"], recurso["config"], self.perfil["limite_tokens"]),
                "pooling": self.perfil["pooling"], "dispositivo": recurso["dispositivo"],
                "precisao_inferencia": "float32", "normalizacao_interna": "nenhuma",
                "prefixo_automatico": False, "truncamento_automatico": False,
                "bibliotecas": {n: _versao(n) for n in ("fastembed", "onnxruntime", "transformers", "tokenizers", "numpy")},
                "artefatos": {"modelo": recurso["manifesto"], "tokenizador": recurso["tokenizer_manifesto"],
                              "onnx": {"arquivo": recurso["arquivo_onnx"], "sha256": recurso["sha256_onnx"],
                                       "arquivos_sha256": recurso["arquivos_onnx_sha256"]}},
                "compatibilidade_sentence_transformers": "nao_verificada",
            }
        return self._recurso

    def descrever(self):
        self._carregar()
        return deepcopy(self._descricao)

    def tokenizar(self, texto):
        if type(texto) is not str:
            raise ErroModeloEmbeddings("A entrada do tokenizer deve ser string.")
        recurso = self._carregar()
        with recurso["lock"]:
            recurso["tokenizer"].no_padding()
            codificacao = recurso["tokenizer"].encode(texto)
            resultado = {"input_ids": list(codificacao.ids), "attention_mask": list(codificacao.attention_mask),
                         "special_tokens_mask": list(codificacao.special_tokens_mask),
                         "offsets": [list(par) for par in codificacao.offsets],
                         "token_type_ids": list(codificacao.type_ids)}
            for i, especial in enumerate(resultado["special_tokens_mask"]):
                if especial:
                    resultado["offsets"][i] = [0, 0]
            resultado = _validar_tokenizacao(resultado, texto)
            # Conferir a equivalência exata dos IDs com o tokenizer declarado.
            hf = _tokenizar_hf(recurso["hf_tokenizer"], texto)
            if any(resultado[k] != hf[k] for k in ("input_ids", "attention_mask", "special_tokens_mask", "offsets")):
                raise ErroModeloEmbeddings("Tokenizer FastEmbed diverge da revisão declarada.")
            return resultado

    def gerar(self, entradas):
        if type(entradas) is not list or any(type(t) is not str for t in entradas):
            raise ErroModeloEmbeddings("entradas deve ser lista de strings.")
        if not entradas:
            return []
        recurso = self._carregar()
        np = recurso["np"]
        resultado = []
        with recurso["lock"]:
            for inicio in range(0, len(entradas), self.perfil["lote"]):
                lote = entradas[inicio:inicio + self.perfil["lote"]]
                tokens = [self.tokenizar(texto) for texto in lote]
                limite = min(self._descricao["limite_tokens"], self.perfil["limite_tokens"] or self._descricao["limite_tokens"])
                if any(len(t["input_ids"]) > limite for t in tokens):
                    raise ErroModeloEmbeddings("Entrada excede limite efetivo; nenhum truncamento foi aplicado.")
                tokenizer = recurso["tokenizer"]
                hf = recurso["hf_tokenizer"]
                if hf.pad_token_id is None:
                    raise ErroModeloEmbeddings("O tokenizer não declara token de padding.")
                tokenizer.enable_padding(pad_id=hf.pad_token_id, pad_token=hf.pad_token,
                                         direction=getattr(hf, "padding_side", "right"))
                try:
                    bruto = recurso["modelo"].onnx_embed(lote)
                    estados = np.asarray(bruto.model_output, dtype=np.float32)
                    mask = np.asarray(bruto.attention_mask, dtype=np.int64)
                    if estados.ndim != 3 or estados.shape[:2] != mask.shape:
                        raise ErroModeloEmbeddings("Formato de saída ONNX inconsistente.")
                    pooling = self.perfil["pooling"]
                    if pooling == "mean":
                        vetores = (estados * mask[..., None]).sum(axis=1) / mask.sum(axis=1)[:, None]
                    elif pooling == "max":
                        vetores = np.where(mask[..., None] != 0, estados, -np.inf).max(axis=1)
                    elif pooling == "cls":
                        vetores = estados[:, 0]
                    else:  # last_token; padding à esquerda ou direita
                        indices = np.where(mask != 0, np.arange(mask.shape[1]), -1).max(axis=1)
                        vetores = estados[np.arange(len(lote)), indices]
                    resultado.extend(_validar_vetores(vetores.tolist(), len(lote), self._descricao["dimensao"]))
                except ErroModeloEmbeddings:
                    raise
                except Exception as exc:
                    raise ErroModeloEmbeddings(f"Falha de inferência FastEmbed: {exc}") from exc
                finally:
                    tokenizer.no_padding()
        return resultado


def criar_adaptador(perfil):
    """Fábrica de produção: nunca registra adaptadores simulados."""
    perfil = validar_perfil(perfil)
    if perfil["backend"] == "sentence_transformers":
        return AdaptadorSentenceTransformers(perfil)
    if perfil["backend"] == "fastembed":
        return AdaptadorFastEmbed(perfil)
    raise ErroModeloEmbeddings("Backend não registrado.")


def baixar_modelo(perfil):
    """Download explicitamente solicitado; não executa inferência nem baixa ONNX."""
    perfil = validar_perfil(perfil)
    resultados = {}
    for tipo in ("modelo", "tokenizador"):
        campo = perfil[tipo]
        caminho, manifesto = _resolver_snapshot(campo["identificacao"], campo["revisao"],
                                                perfil["cache_modelos"], False, somente_tokenizador=tipo == "tokenizador")
        resultados[tipo] = {"caminho": caminho, "revisao": campo["revisao"], "artefatos": manifesto}
    return resultados


def _main():
    import argparse
    parser = argparse.ArgumentParser(description="Modelos reais da etapa 09; download somente com --baixar.")
    acao = parser.add_mutually_exclusive_group(required=True)
    acao.add_argument("--baixar", action="store_true")
    acao.add_argument("--descrever", action="store_true")
    parser.add_argument("--perfil", default="e5_simetrico")
    parser.add_argument("--opcoes", default="{}", help="Opções do perfil em JSON.")
    parser.add_argument("--cache-modelos")
    args = parser.parse_args()
    try:
        opcoes = json.loads(args.opcoes)
        if args.cache_modelos:
            if type(opcoes) is not dict:
                raise ErroModeloEmbeddings("opcoes deve ser objeto JSON.")
            opcoes["cache_modelos"] = args.cache_modelos
        perfil = carregar_perfil(args.perfil, opcoes=opcoes)
        resultado = baixar_modelo(perfil) if args.baixar else criar_adaptador(perfil).descrever()
        print(json.dumps(resultado, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Erro: {exc}\n")


if __name__ == "__main__":
    _main()
