"""Perfis versionados da etapa 09; configurações E5 não são globais.

Este módulo só usa a biblioteca padrão. Ler um perfil não importa um modelo,
consulta a rede ou cria diretórios. A configuração efetiva vem do adaptador.
"""
from copy import deepcopy
import json
from pathlib import Path
import re


class ErroPerfil(ValueError):
    """Solicitação de perfil ausente, incompatível ou não serializável."""


E5_REVISAO = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"
E5_MODELO = "intfloat/multilingual-e5-large"
_VERSAO = "1.0.0"
_FINALIDADES = {
    "e5_consulta": ("consulta", "query: "),
    "e5_conteudo": ("conteudo", "passage: "),
    "e5_simetrico": ("simetrico", "query: "),
    "e5_legado": ("historico", "passage: "),
}
_OPCOES = {
    "modelo", "revisao", "tokenizador", "tokenizador_revisao", "backend",
    "dispositivo", "lote", "precisao_inferencia", "formato_armazenamento",
    "limite_tokens", "pooling", "normalizacao", "prefixo", "fragmentacao",
    "agregacao", "texto_documento", "cache_modelos", "somente_local",
    "corte_caracteres", "cabecalho", "juntar_paragrafos", "arquivo_onnx", "modelo_onnx_sha256", "arquivos_onnx_sha256",
}
_ENUMS = {
    "backend": {"sentence_transformers", "fastembed"},
    "precisao_inferencia": {"float32", "float16", "bfloat16"},
    "formato_armazenamento": {"float16", "float32", "float64"},
    "pooling": {"mean", "cls", "max", "last_token", "modelo"},
    "normalizacao": {"l2", "nenhuma"},
    "fragmentacao": {"sem_sobreposicao", "erro"},
    "agregacao": {"media_ponderada_tokens", "media_simples"},
    "texto_documento": {"trabalho", "original"},
}


def _texto(valor, nome, *, vazio=False):
    if not isinstance(valor, str) or (not vazio and not valor.strip()):
        raise ErroPerfil(f"{nome} deve ser texto{' não vazio' if not vazio else ''}.")
    return valor


def _revisao(valor, nome):
    _texto(valor, nome)
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", valor) is None:
        raise ErroPerfil(f"{nome} deve ser commit imutável de 40 dígitos hexadecimais ou SHA-256 local.")
    return valor


def _inteiro(valor, nome):
    if type(valor) is not int or valor <= 0:
        raise ErroPerfil(f"{nome} deve ser inteiro positivo.")
    return valor


def listar_perfis():
    """Perfis iniciais, sem carregar modelos; devolve cópias independentes."""
    return [carregar_perfil(perfil_id) for perfil_id in _FINALIDADES]


def carregar_perfil(perfil_id="e5_simetrico", *, opcoes=None):
    if not isinstance(perfil_id, str) or perfil_id not in _FINALIDADES:
        raise ErroPerfil("Perfil desconhecido.")
    if opcoes is None:
        opcoes = {}
    if type(opcoes) is not dict:
        raise ErroPerfil("opcoes deve ser um objeto JSON.")
    try:
        # Rejeitar objetos arbitrários, NaN e chaves não textuais.
        def conferir(valor):
            if type(valor) is dict:
                if any(type(k) is not str for k in valor):
                    raise ErroPerfil("Chaves de opções devem ser textos.")
                for v in valor.values():
                    conferir(v)
            elif type(valor) is list:
                for v in valor:
                    conferir(v)
            elif type(valor) not in (str, int, float, bool, type(None)):
                raise ErroPerfil("As opções precisam conter apenas tipos JSON.")
        conferir(opcoes)
        json.dumps(opcoes, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ErroPerfil(f"Opções JSON inválidas: {exc}") from exc
    desconhecidas = set(opcoes) - _OPCOES
    if desconhecidas:
        raise ErroPerfil("Opções desconhecidas: " + ", ".join(sorted(desconhecidas)))
    opcoes = deepcopy(opcoes)
    finalidade, prefixo = _FINALIDADES[perfil_id]
    modelo = opcoes.pop("modelo", E5_MODELO)
    revisao = opcoes.pop("revisao", None)
    if type(modelo) is dict:
        if set(modelo) != {"identificacao", "revisao"}:
            raise ErroPerfil("modelo deve conter identificacao e revisao.")
        if revisao is not None and revisao != modelo["revisao"]:
            raise ErroPerfil("Revisões conflitantes para o modelo.")
        revisao = modelo["revisao"]
        modelo = modelo["identificacao"]
    _texto(modelo, "modelo")
    if revisao is None:
        if modelo != E5_MODELO:
            raise ErroPerfil("Outro modelo exige revisão imutável explícita.")
        revisao = E5_REVISAO
    _revisao(revisao, "revisao")
    tokenizer = opcoes.pop("tokenizador", modelo)
    tokenizer_revision = opcoes.pop("tokenizador_revisao", None)
    if type(tokenizer) is dict:
        if set(tokenizer) != {"identificacao", "revisao"}:
            raise ErroPerfil("tokenizador deve conter identificacao e revisao.")
        if tokenizer_revision is not None and tokenizer_revision != tokenizer["revisao"]:
            raise ErroPerfil("Revisões conflitantes para o tokenizador.")
        tokenizer_revision = tokenizer["revisao"]
        tokenizer = tokenizer["identificacao"]
    _texto(tokenizer, "tokenizador")
    if tokenizer_revision is None:
        if tokenizer != modelo:
            raise ErroPerfil("Outro tokenizador exige revisão imutável explícita.")
        tokenizer_revision = revisao
    _revisao(tokenizer_revision, "tokenizador_revisao")
    base = Path(__file__).resolve().parent
    base_dados = base.parent if (base.parent / ".git").exists() else base
    perfil = {
        "id": perfil_id, "versao": _VERSAO, "finalidade": finalidade,
        "modelo": {"identificacao": modelo, "revisao": revisao},
        "tokenizador": {"identificacao": tokenizer, "revisao": tokenizer_revision},
        "backend": "sentence_transformers", "dispositivo": "cpu", "lote": 16,
        "precisao_inferencia": "float32", "formato_armazenamento": "float32",
        "limite_tokens": None, "pooling": "mean", "normalizacao": "l2",
        "prefixo": prefixo, "fragmentacao": "sem_sobreposicao",
        "agregacao": "media_ponderada_tokens", "texto_documento": "trabalho",
        "cache_modelos": str((base_dados / "instance" / "modelos").resolve()),
        "somente_local": True, "legado": None,
    }
    if perfil_id == "e5_legado":
        perfil["formato_armazenamento"] = "float16"
        perfil["legado"] = {"ativo": True, "corte_caracteres": 2000,
                            "cabecalho": "", "compatibilidade": "nao_verificada",
                            "juntar_paragrafos": False,
                            "composicao": "cabecalho_mais_lf_mais_texto",
                            "cobertura_integral_exigida": False}
    corte = opcoes.pop("corte_caracteres", None)
    cabecalho = opcoes.pop("cabecalho", None)
    juntar = opcoes.pop("juntar_paragrafos", None)
    if perfil["legado"] is None and (corte is not None or cabecalho is not None or juntar is not None):
        raise ErroPerfil("Corte e cabeçalho são opções exclusivas do perfil histórico.")
    if perfil["legado"] is not None:
        if corte is not None:
            perfil["legado"]["corte_caracteres"] = _inteiro(corte, "corte_caracteres")
        if cabecalho is not None:
            perfil["legado"]["cabecalho"] = _texto(cabecalho, "cabecalho", vazio=True)
        if juntar is not None:
            if type(juntar) is not bool:
                raise ErroPerfil("juntar_paragrafos deve ser booleano.")
            perfil["legado"]["juntar_paragrafos"] = juntar
            if juntar:
                perfil["legado"]["composicao"] = "cabecalho_mais_lf_mais_paragrafos"
    perfil.update(opcoes)
    for nome, permitidos in _ENUMS.items():
        valor = perfil[nome]
        if type(valor) is not str or valor not in permitidos:
            raise ErroPerfil(f"{nome} não suportado: {valor!r}.")
    _inteiro(perfil["lote"], "lote")
    if perfil["limite_tokens"] is not None:
        _inteiro(perfil["limite_tokens"], "limite_tokens")
    _texto(perfil["prefixo"], "prefixo", vazio=True)
    dispositivo = _texto(perfil["dispositivo"], "dispositivo")
    if re.fullmatch(r"cpu|mps|cuda(?::[0-9]+)?", dispositivo) is None:
        raise ErroPerfil("dispositivo deve ser cpu, mps ou cuda[:índice].")
    if type(perfil["somente_local"]) is not bool:
        raise ErroPerfil("somente_local deve ser booleano.")
    cache = _texto(perfil["cache_modelos"], "cache_modelos")
    perfil["cache_modelos"] = str(Path(cache).expanduser().resolve())
    if perfil["backend"] == "fastembed" and perfil["precisao_inferencia"] != "float32":
        raise ErroPerfil("FastEmbed/ONNX inicial exige float32; quantização requer outro adaptador/perfil.")
    for campo in ("arquivo_onnx", "modelo_onnx_sha256"):
        if campo in perfil:
            _texto(perfil[campo], campo)
    if "modelo_onnx_sha256" in perfil and re.fullmatch(r"[0-9a-f]{64}", perfil["modelo_onnx_sha256"]) is None:
        raise ErroPerfil("modelo_onnx_sha256 deve ser SHA-256 hexadecimal.")
    if "arquivos_onnx_sha256" in perfil:
        arquivos = perfil["arquivos_onnx_sha256"]
        if type(arquivos) is not dict or not arquivos or any(
            type(k) is not str or not k or Path(k).is_absolute() or ".." in Path(k).parts
            or type(v) is not str or re.fullmatch(r"[0-9a-f]{64}", v) is None
            for k, v in arquivos.items()
        ):
            raise ErroPerfil("arquivos_onnx_sha256 deve mapear nomes relativos seguros para SHA-256.")
    return perfil


def validar_perfil(perfil):
    """Confere um snapshot completo sem importar backend nem acessar a rede."""
    if type(perfil) is not dict:
        raise ErroPerfil("Perfil deve ser objeto JSON.")
    esperado = carregar_perfil(perfil.get("id"))
    obrigatorios = set(esperado)
    if not obrigatorios.issubset(perfil) or set(perfil) - obrigatorios - {"arquivo_onnx", "modelo_onnx_sha256", "arquivos_onnx_sha256"}:
        raise ErroPerfil("Campos do perfil incompletos ou desconhecidos.")
    if perfil.get("versao") != _VERSAO:
        raise ErroPerfil("Versão de perfil desconhecida.")
    opcoes = {k: deepcopy(v) for k, v in perfil.items() if k in _OPCOES}
    if perfil.get("legado") is not None:
        legado = perfil["legado"]
        if type(legado) is not dict or set(legado) != set(esperado["legado"] or {}):
            raise ErroPerfil("Configuração histórica inválida.")
        opcoes["corte_caracteres"] = legado["corte_caracteres"]
        opcoes["cabecalho"] = legado["cabecalho"]
        opcoes["juntar_paragrafos"] = legado["juntar_paragrafos"]
    reconstruido = carregar_perfil(perfil["id"], opcoes=opcoes)
    if json.dumps(perfil, sort_keys=True, allow_nan=False) != json.dumps(reconstruido, sort_keys=True, allow_nan=False):
        raise ErroPerfil("Snapshot de perfil inconsistente.")
    return deepcopy(reconstruido)
