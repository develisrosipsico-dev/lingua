"""Exporte uma etapa 08 validada como notebook autocontido para a etapa 09.

A geração usa somente a biblioteca padrão e os validadores existentes. Não
carrega nem baixa modelos. Dados e fontes UTF-8 são incorporados com hashes;
a execução no Colab instala o adaptador em um ambiente Python separado.
"""

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

from perfis_vetorizacao import carregar_perfil
from unidades_contexto import validar_unidades_contexto


SCHEMA_VERSION = "1.0.0"
_MODULOS = (
    "preparacao.py", "segmentacao.py", "anotacao.py", "sintaxe_entidades.py",
    "catalogo_regras.py", "regras_detectores.py", "regras_linguisticas.py",
    "unidades_contexto.py", "perfis_vetorizacao.py", "modelo_embeddings.py",
    "vetorizacao.py",
)


class ErroNotebookColab(ValueError):
    """O notebook não pode ser gerado a partir desta origem/configuração."""


def _canonico(valor):
    def conferir(item, ancestrais):
        if item is None or type(item) in (str, int, bool):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) not in (dict, list) or id(item) in ancestrais:
            raise ErroNotebookColab("Os dados precisam ser JSON finito, sem ciclos.")
        ancestrais.add(id(item))
        if type(item) is dict:
            if any(type(chave) is not str for chave in item):
                raise ErroNotebookColab("As chaves JSON precisam ser textos.")
            filhos = item.values()
        else:
            filhos = item
        for filho in filhos:
            conferir(filho, ancestrais)
        ancestrais.remove(id(item))
    try:
        conferir(valor, set())
        return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as erro:
        raise ErroNotebookColab("Dados JSON inválidos ou não codificáveis em UTF-8.") from erro


def _sha(conteudo):
    return hashlib.sha256(conteudo).hexdigest()


def _preparacao(contexto):
    return contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]


def _conferir_documento(documento, contexto):
    if documento is None:
        return
    if type(documento) is not dict:
        raise ErroNotebookColab("documento precisa ser um objeto JSON.")
    preparacao = _preparacao(contexto)
    if (type(documento.get("id")) is not type(contexto["documento_id"])
            or documento.get("id") != contexto["documento_id"]
            or documento.get("content") != preparacao["original"]["texto"]):
        raise ErroNotebookColab("A ingestão do documento diverge da origem da etapa 08.")
    instante = documento.get("created_at")
    if type(instante) is not str or not instante.strip():
        raise ErroNotebookColab("A ingestão precisa informar created_at.")
    recebido = preparacao["metadados_origem"].get("recebido_em")
    if recebido is not None and instante != recebido:
        raise ErroNotebookColab("A data da ingestão diverge dos metadados preservados.")


# Estes scripts também fazem parte da cápsula, com seus próprios hashes.
_VERIFICADOR = '''"""Validação stdlib da cápsula e de toda a cadeia linguística."""
import base64
import hashlib
import json
from pathlib import Path
import sys

def canonico(valor):
    return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")

def sha(conteudo):
    return hashlib.sha256(conteudo).hexdigest()

def carregar(caminho, esperado):
    caminho = Path(caminho).resolve()
    bruto = caminho.read_bytes()
    if sha(bruto) != esperado:
        raise ValueError("A cápsula foi alterada: SHA-256 divergente.")
    dados = json.loads(bruto.decode("utf-8"))
    if sha(canonico(dados["contexto"])) != dados["contexto_sha256"]:
        raise ValueError("A origem da etapa 08 foi alterada.")
    if dados["documento"] is not None and sha(canonico(dados["documento"])) != dados["documento_sha256"]:
        raise ValueError("O registro de ingestão foi alterado.")
    for fonte in dados["fontes"]:
        nome = fonte["nome"]
        if Path(nome).name != nome or not nome.endswith(".py"):
            raise ValueError("Nome de fonte inválido.")
        conteudo = base64.b64decode(fonte["conteudo_base64"], validate=True)
        if sha(conteudo) != fonte["sha256"] or (caminho.parent / nome).read_bytes() != conteudo:
            raise ValueError("Fonte do aplicativo alterada: " + nome)
    from unidades_contexto import validar_unidades_contexto
    validacao = validar_unidades_contexto(dados["contexto"])
    if validacao.get("pronto_para_etapa_09") is not True:
        raise ValueError("A origem não está pronta para a etapa 09.")
    contexto = dados["contexto"]
    preparacao = contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
    documento = dados["documento"]
    if documento is not None:
        if (type(documento["id"]) is not type(contexto["documento_id"])
                or documento["id"] != contexto["documento_id"]
                or documento["content"] != preparacao["original"]["texto"]):
            raise ValueError("A ingestão diverge do original preservado.")
        recebido = preparacao["metadados_origem"].get("recebido_em")
        if recebido is not None and documento["created_at"] != recebido:
            raise ValueError("A data da ingestão diverge da origem.")
    return dados

if __name__ == "__main__":
    dados = carregar(sys.argv[1], sys.argv[2])
    print("Origem validada: documento", dados["contexto"]["documento_id"],
          "— contexto", dados["contexto"]["execucao_id"])
'''

_EXECUTOR = '''"""Execute uma preparação exportada, sem alterar as etapas anteriores."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4
import zipfile

from conferir_origem import carregar, canonico, sha

def executar(caminho, esperado, configuracao):
    dados = carregar(caminho, esperado)
    from perfis_vetorizacao import carregar_perfil
    from modelo_embeddings import baixar_modelo
    from vetorizacao import validar_vetorizacao, vetorizar_contexto
    import torch
    dispositivo = configuracao["dispositivo"]
    if dispositivo == "auto":
        dispositivo = "cuda:0" if torch.cuda.is_available() else "cpu"
    if dispositivo.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("GPU CUDA solicitada, mas indisponível. Escolha 'cpu' ou 'auto'.")
    opcoes = dict(dados["opcoes_solicitadas"])
    for chave in ("dispositivo", "cache_modelos", "somente_local"):
        opcoes.pop(chave, None)
    opcoes.update(configuracao["opcoes_modelo"])
    opcoes.update(dispositivo=dispositivo, lote=configuracao["lote"],
                  cache_modelos=str(Path(configuracao["cache_modelos"]).resolve()),
                  somente_local=False)
    perfil = carregar_perfil(dados["perfil_exportado"]["id"], opcoes=opcoes)
    if perfil["backend"] != "sentence_transformers":
        raise ValueError("Este notebook instala o backend sentence_transformers.")
    print("Baixando/verificando a revisão exata do modelo e tokenizador...", flush=True)
    download = baixar_modelo(perfil)
    opcoes["somente_local"] = True
    inicio = time.perf_counter()
    execucao_id = str(uuid4())
    print("Vetorizando em", dispositivo, "— execução", execucao_id, flush=True)
    resultado = vetorizar_contexto(
        dados["contexto"], perfil_id=perfil["id"], opcoes=opcoes,
        execucao_id=execucao_id,
        progresso=lambda evento: print(json.dumps(evento, ensure_ascii=False), flush=True),
    )
    validar_vetorizacao(resultado)
    duracao = time.perf_counter() - inicio
    diretorio = Path(caminho).resolve().parent / "resultados" / execucao_id
    diretorio.mkdir(parents=True, exist_ok=False)
    arquivos = {}
    def gravar(nome, conteudo):
        with (diretorio / nome).open("xb") as arquivo:
            arquivo.write(conteudo)
        arquivos[nome] = {"sha256": sha(conteudo), "bytes": len(conteudo)}
    gravar("vetorizacao.json", canonico(resultado))
    gravar("contexto_etapa08.json", canonico(dados["contexto"]))
    preparacao = dados["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
    gravar("original.txt", preparacao["original"]["texto"].encode("utf-8"))
    gravar("trabalho.txt", preparacao["trabalho"]["texto"].encode("utf-8"))
    manifesto = {
        "schema_version": "1.0.0", "notebook_id": dados["notebook_id"],
        "exportado_em": dados["registrado_em"],
        "registrado_em": datetime.now(timezone.utc).isoformat(),
        "execucao_id": execucao_id, "documento_id": dados["contexto"]["documento_id"],
        "contexto_execucao_id": dados["contexto"]["execucao_id"],
        "contexto_sha256": dados["contexto_sha256"], "capsula_sha256": esperado,
        "documento": dados["documento"], "documento_sha256": dados["documento_sha256"],
        "fontes": [{"nome": fonte["nome"], "sha256": fonte["sha256"]} for fonte in dados["fontes"]],
        "dependencias_solicitadas": dados["dependencias"],
        "perfil_exportado": dados["perfil_exportado"],
        "opcoes_solicitadas": dados["opcoes_solicitadas"],
        "configuracao_colab": configuracao, "perfil_executado": resultado["perfil"],
        "processamento": resultado["processamento"], "download_modelo": download,
        "tempo_vetorizacao_segundos": duracao, "arquivos": dict(arquivos),
        "validacao": resultado["validacao"],
    }
    gravar("manifesto_colab.json", canonico(manifesto))
    pacote = diretorio / ("pacote_lingua_" + execucao_id + ".zip")
    with pacote.open("xb") as saida:
        with zipfile.ZipFile(saida, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
            for nome in arquivos:
                arquivo.write(diretorio / nome, arcname=nome)
    resumo = {"execucao_id": execucao_id, "diretorio": str(diretorio),
              "pacote": str(pacote), "pacote_sha256": sha(pacote.read_bytes()),
              "vetorizacao_json": str(diretorio / "vetorizacao.json")}
    # Só este ponteiro muda; cada conjunto de resultados tem uma pasta UUID própria.
    (Path(caminho).resolve().parent / "ultima_execucao.json").write_bytes(canonico(resumo))
    print("Concluído:", pacote, flush=True)
    return resumo

if __name__ == "__main__":
    executar(sys.argv[1], sys.argv[2], json.loads(Path(sys.argv[3]).read_bytes()))
'''

_RESTAURAR = '''#@title Restaurar o texto e conferir as etapas anteriores
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

CAPSULA_BASE64 = {capsula!r}
CAPSULA_SHA256 = {sha!r}
FONTES_PERMITIDAS = {nomes!r}

capsula_bytes = base64.b64decode(CAPSULA_BASE64, validate=True)
if hashlib.sha256(capsula_bytes).hexdigest() != CAPSULA_SHA256:
    raise ValueError("A cápsula do notebook foi alterada.")
capsula = json.loads(capsula_bytes.decode("utf-8"))
if [fonte["nome"] for fonte in capsula["fontes"]] != FONTES_PERMITIDAS:
    raise ValueError("Lista de fontes inválida.")
fontes_verificadas = {{}}
for fonte in capsula["fontes"]:
    nome = fonte["nome"]
    conteudo = base64.b64decode(fonte["conteudo_base64"], validate=True)
    if hashlib.sha256(conteudo).hexdigest() != fonte["sha256"]:
        raise ValueError("Fonte alterada: " + nome)
    fontes_verificadas[nome] = conteudo

PASTA_LINGUA = Path.cwd() / ("lingua-colab-" + uuid4().hex)
PASTA_LINGUA.mkdir(exist_ok=False)
for nome, conteudo in fontes_verificadas.items():
    (PASTA_LINGUA / nome).write_bytes(conteudo)
CAMINHO_CAPSULA = PASTA_LINGUA / "capsula.json"
CAMINHO_CAPSULA.write_bytes(capsula_bytes)

def conferir_arquivos():
    if hashlib.sha256(CAMINHO_CAPSULA.read_bytes()).hexdigest() != CAPSULA_SHA256:
        raise ValueError("A cápsula restaurada foi alterada.")
    for nome, conteudo in fontes_verificadas.items():
        if (PASTA_LINGUA / nome).read_bytes() != conteudo:
            raise ValueError("Fonte restaurada alterada: " + nome)

conferir_arquivos()
subprocess.run([sys.executable, str(PASTA_LINGUA / "conferir_origem.py"),
                str(CAMINHO_CAPSULA), CAPSULA_SHA256], check=True)
print("Texto e etapas anteriores preservados em:", PASTA_LINGUA)
'''

_CONFIGURAR = '''# 'auto' usa CUDA quando disponível e CPU quando não há GPU compatível.
# Também é possível escolher 'cpu', 'cuda' ou 'cuda:0'.
DISPOSITIVO = "auto"
LOTE = capsula["perfil_exportado"]["lote"]
CACHE_MODELOS = PASTA_LINGUA / "modelos"

# Opções opcionais seguem o contrato de perfis_vetorizacao.py.
# Ex.: {"formato_armazenamento": "float16", "limite_tokens": 512}
# Outro modelo/tokenizador exige uma revisão imutável explícita.
OPCOES_MODELO = {}
'''

_INSTALAR = '''import os
import re
import shutil

if type(DISPOSITIVO) is not str or re.fullmatch(r"auto|cpu|cuda(?::[0-9]+)?", DISPOSITIVO) is None:
    raise ValueError("Escolha DISPOSITIVO='auto', 'cpu' ou 'cuda[:índice]'.")
if type(LOTE) is not int or LOTE < 1:
    raise ValueError("LOTE precisa ser um inteiro positivo.")
if type(OPCOES_MODELO) is not dict:
    raise ValueError("OPCOES_MODELO precisa ser um objeto JSON.")
json.dumps(OPCOES_MODELO, allow_nan=False)
conferir_arquivos()

AMBIENTE = PASTA_LINGUA / "ambiente"
PYTHON_VETORIZACAO = AMBIENTE / "bin" / "python"
pip_pronto = False
if PYTHON_VETORIZACAO.exists():
    consulta_pip = subprocess.run([str(PYTHON_VETORIZACAO), "-m", "pip", "--version"],
                                  capture_output=True, text=True)
    pip_pronto = consulta_pip.returncode == 0
if not pip_pronto:
    tentativa = subprocess.run([sys.executable, "-m", "venv", str(AMBIENTE)])
    if tentativa.returncode:
        # Algumas imagens Colab não incluem ensurepip para venv.
        bootstrap = PASTA_LINGUA / "bootstrap-virtualenv"
        subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(bootstrap),
                        "virtualenv==20.35.4"], check=True)
        ambiente_bootstrap = dict(os.environ, PYTHONPATH=str(bootstrap))
        subprocess.run([sys.executable, "-m", "virtualenv", "--clear", str(AMBIENTE)],
                        env=ambiente_bootstrap, check=True)

gpu_sistema = False
if shutil.which("nvidia-smi"):
    consulta_gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                  capture_output=True, text=True)
    gpu_sistema = consulta_gpu.returncode == 0 and bool(consulta_gpu.stdout.strip())
instalar_cpu = DISPOSITIVO == "cpu" or (DISPOSITIVO == "auto" and not gpu_sistema)
torch_solicitado = capsula["dependencias"]["torch"]
if instalar_cpu:
    # Uma escolha posterior de CPU substitui também uma wheel CUDA já instalada.
    nome_torch, versao_torch = torch_solicitado.split("==", 1)
    torch_solicitado = nome_torch + "==" + versao_torch.split("+", 1)[0] + "+cpu"
comando_torch = [str(PYTHON_VETORIZACAO), "-m", "pip", "install", torch_solicitado]
if instalar_cpu:
    comando_torch += ["--index-url", "https://download.pytorch.org/whl/cpu"]
else:
    consulta_torch = subprocess.run(
        [str(PYTHON_VETORIZACAO), "-c", "import importlib.metadata as m; "
         "print(m.version('torch') if m.packages_distributions().get('torch') else '')"],
        capture_output=True, text=True,
    )
    if consulta_torch.returncode == 0 and "+cpu" in consulta_torch.stdout.strip():
        # == sem sufixo local também aceita +cpu; trocar para CUDA exige reinstalar.
        comando_torch.append("--force-reinstall")
subprocess.run(comando_torch, check=True)
restantes = [valor for nome, valor in capsula["dependencias"].items() if nome != "torch"]
subprocess.run([str(PYTHON_VETORIZACAO), "-m", "pip", "install", *restantes], check=True)
print("Ambiente de vetorização pronto:", PYTHON_VETORIZACAO)
'''

_VETORIZAR = '''conferir_arquivos()
CONFIGURACAO_COLAB = {
    "dispositivo": DISPOSITIVO, "lote": LOTE,
    "cache_modelos": str(Path(CACHE_MODELOS).resolve()), "opcoes_modelo": OPCOES_MODELO,
}
CAMINHO_CONFIGURACAO = PASTA_LINGUA / "configuracao_colab.json"
CAMINHO_CONFIGURACAO.write_bytes(json.dumps(CONFIGURACAO_COLAB, ensure_ascii=False,
                                            allow_nan=False).encode("utf-8"))
subprocess.run([str(PYTHON_VETORIZACAO), str(PASTA_LINGUA / "executar_etapa09.py"),
                str(CAMINHO_CAPSULA), CAPSULA_SHA256, str(CAMINHO_CONFIGURACAO)], check=True)
ULTIMA_EXECUCAO = json.loads((PASTA_LINGUA / "ultima_execucao.json").read_bytes())
print("JSON completo:", ULTIMA_EXECUCAO["vetorizacao_json"])
'''

_BAIXAR = '''# O ZIP contém o JSON completo da etapa 09, as etapas anteriores,
# os textos exatos e o manifesto com hashes/configuração da execução.
pacote = Path(ULTIMA_EXECUCAO["pacote"])
if hashlib.sha256(pacote.read_bytes()).hexdigest() != ULTIMA_EXECUCAO["pacote_sha256"]:
    raise ValueError("O pacote de resultados foi alterado.")
try:
    from google.colab import files
except ImportError:
    print("Pacote disponível localmente:", pacote)
else:
    files.download(str(pacote))
print("Para outra execução, repita a célula de vetorização: os resultados anteriores são preservados.")
'''


def _celula(tipo, identificador, texto):
    celula = {"cell_type": tipo, "id": identificador, "metadata": {},
              "source": texto.splitlines(keepends=True)}
    if tipo == "code":
        celula.update(execution_count=None, outputs=[])
    return celula


def gerar_notebook_colab(contexto, *, perfil_id="e5_simetrico", opcoes=None,
                         documento=None, notebook_id=None, registrado_em=None):
    """Gere um .ipynb sem inferência local, preservando a cadeia selecionada.

    Data e ID podem ser fornecidos pelo chamador. A execução no notebook terá
    outro UUID, criado a cada execução. Configurações de dispositivo/cache
    locais ficam no manifesto; no Colab são resolvidas em seu próprio ambiente.
    """
    try:
        _canonico(contexto)
        validacao = validar_unidades_contexto(contexto)
        if validacao.get("pronto_para_etapa_09") is not True:
            raise ErroNotebookColab("A etapa 08 precisa estar validada e pronta para a etapa 09.")
        perfil = carregar_perfil(perfil_id, opcoes=opcoes)
        if perfil["backend"] != "sentence_transformers":
            raise ErroNotebookColab("Este notebook suporta sentence_transformers; o backend ONNX exige seus arquivos próprios.")
        _canonico(documento)
        _conferir_documento(documento, contexto)
        if notebook_id is None:
            notebook_id = str(uuid4())
        if type(notebook_id) is not str or not notebook_id.strip():
            raise ErroNotebookColab("notebook_id precisa ser uma string não vazia.")
        if registrado_em is None:
            registrado_em = datetime.now(timezone.utc).isoformat()
        elif isinstance(registrado_em, datetime):
            registrado_em = registrado_em.isoformat()
        if type(registrado_em) is not str:
            raise ErroNotebookColab("registrado_em precisa ser uma data ISO 8601 com fuso.")
        data = datetime.fromisoformat(registrado_em.replace("Z", "+00:00"))
        if data.tzinfo is None or data.utcoffset() is None:
            raise ErroNotebookColab("registrado_em precisa conter fuso.")
        raiz = Path(__file__).resolve().parent
        dependencias = {}
        for linha in (raiz / "requirements-vetorizacao.txt").read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#"):
                nome, versao = linha.split("==", 1)
                if not nome or not versao or nome in dependencias:
                    raise ErroNotebookColab("Dependências de vetorização precisam ter versões exatas e únicas.")
                dependencias[nome] = linha
        if "torch" not in dependencias:
            raise ErroNotebookColab("A lista de dependências precisa incluir torch.")
        conteudos = {nome: (raiz / nome).read_bytes() for nome in _MODULOS}
        conteudos.update({"conferir_origem.py": _VERIFICADOR.encode("utf-8"),
                         "executar_etapa09.py": _EXECUTOR.encode("utf-8")})
        fontes = [{"nome": nome, "sha256": _sha(conteudo),
                   "conteudo_base64": base64.b64encode(conteudo).decode("ascii")}
                  for nome, conteudo in conteudos.items()]
        capsula = {
            "schema_version": SCHEMA_VERSION, "notebook_id": notebook_id,
            "registrado_em": registrado_em, "documento": deepcopy(documento),
            "documento_sha256": None if documento is None else _sha(_canonico(documento)),
            "contexto": deepcopy(contexto), "contexto_sha256": _sha(_canonico(contexto)),
            "perfil_exportado": perfil, "opcoes_solicitadas": deepcopy(opcoes or {}),
            "dependencias": dependencias, "fontes": fontes,
        }
        bruto = _canonico(capsula)
        soma = _sha(bruto)
        restaurar = _RESTAURAR.format(capsula=base64.b64encode(bruto).decode("ascii"),
                                     sha=soma, nomes=list(conteudos))
        introducao = """# Língua — vetorização no Colab\n\nEste notebook contém o texto original e os registros completos até a etapa 08 selecionada. As etapas anteriores já foram executadas: seus resultados são preservados e validados, sem refazer a análise.\n\n1. No Colab, use **Arquivo → Fazer upload de notebook** e selecione este arquivo.\n2. Para usar GPU, escolha **Ambiente de execução → Alterar tipo de ambiente de execução → GPU**. CPU também funciona; a configuração `auto` escolhe uma GPU CUDA disponível ou CPU.\n3. Revise a configuração e execute as células em ordem (**Ambiente de execução → Executar tudo**). O modelo/tokenizador serão baixados nas revisões fixadas do perfil, sem depender de instalação no seu computador.\n4. Ao terminar, baixe o ZIP: ele contém `vetorizacao.json`, a cadeia anterior completa, os textos exatos e o manifesto da execução.\n\nAo enviar e executar este notebook, o texto e seus registros serão processados no Google Colab. O download inicial do E5 Large é de aproximadamente 2,2 GB; o tempo e a memória dependem do ambiente e do lote escolhido. Em caso de falta de memória, reduza `LOTE`. A instalação ocorre em um ambiente Python isolado, sem substituir as bibliotecas do kernel. As coordenadas continuam em pontos de código Unicode, base zero e fim exclusivo.\n"""
        celulas = [
            _celula("markdown", "instrucoes", introducao),
            _celula("code", "restaurar-origem", restaurar),
            _celula("markdown", "configuracao-ajuda", "## Configuração\n\nAs opções do perfil escolhido acompanham o notebook. Dispositivo e cache do computador original não são exigências do Colab. Novos modelos podem usar este adaptador com revisão imutável explícita; espaços vetoriais diferentes continuam separados.\n"),
            _celula("code", "configurar", _CONFIGURAR),
            _celula("markdown", "instalacao-ajuda", "## Instalar dependências\n\nEsta célula pode demorar alguns minutos. Não instala o modelo no seu Mac.\n"),
            _celula("code", "instalar", _INSTALAR),
            _celula("markdown", "vetorizacao-ajuda", "## Gerar vetores\n\nO código confere novamente a origem e as fontes, baixa o modelo e valida o registro final. Cada execução recebe um UUID e uma pasta própria; repetir esta célula preserva os resultados anteriores. Os perfis comuns exigem cobertura integral; o perfil histórico mantém seus cortes explicitamente registrados.\n"),
            _celula("code", "vetorizar", _VETORIZAR),
            _celula("code", "baixar-resultados", _BAIXAR),
        ]
        # O formulário do Colab oculta a longa cápsula; configuração segue editável.
        celulas[1]["metadata"]["cellView"] = "form"
        notebook = {
            "nbformat": 4, "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python"},
                "colab": {"name": "lingua-vetorizacao.ipynb", "provenance": []},
                "lingua": {"schema_version": SCHEMA_VERSION, "notebook_id": notebook_id,
                            "registrado_em": registrado_em, "documento_id": contexto["documento_id"],
                            "contexto_execucao_id": contexto["execucao_id"],
                            "contexto_sha256": capsula["contexto_sha256"],
                            "capsula_sha256": soma, "perfil_id": perfil_id,
                            "fontes": [{"nome": fonte["nome"], "sha256": fonte["sha256"]} for fonte in fontes]},
            }, "cells": celulas,
        }
        _canonico(notebook)
        return notebook
    except ErroNotebookColab:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError) as erro:
        raise ErroNotebookColab(f"Não foi possível gerar o notebook: {erro}") from erro
