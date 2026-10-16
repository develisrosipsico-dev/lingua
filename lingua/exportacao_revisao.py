"""Pacote de diagnóstico de um documento, sem inferência ou escrita no banco.

O pacote reúne o histórico inteiro, inclusive registros inválidos, e uma cópia
restrita do código presente no momento da exportação. A validação é estrutural;
não atesta a correção linguística dos resultados.
"""

from base64 import b64encode
from datetime import datetime, timezone
import hashlib
from importlib import import_module, metadata
from io import BytesIO
import json
import math
from pathlib import Path
import platform
import sqlite3
import subprocess
import zipfile


SCHEMA_VERSION = "lingua.pacote_revisao.v1"

# (etapa, nome, tabela, módulo, validador, ID SQL, ID JSON,
#  etapa anterior, ID SQL da origem, ID JSON da origem, snapshot da origem)
_ETAPAS = (
    ("03", "preparacao", "preparations", "preparacao", "validar_registro",
     "preparation_id", "preparacao_id", None, None, None, None),
    ("04", "segmentacao", "segmentations", "segmentacao", "validar_segmentacao",
     "segmentation_id", "segmentacao_id", "03", "preparation_id", "preparacao_id", "preparacao"),
    ("05", "anotacao", "annotations", "anotacao", "validar_anotacao",
     "annotation_id", "anotacao_id", "04", "segmentation_id", "segmentacao_id", "segmentacao"),
    ("06", "sintaxe_entidades", "analyses", "sintaxe_entidades", "validar_analise_sintaxe_entidades",
     "analysis_id", "analise_id", "05", "annotation_id", "anotacao_id", "anotacao"),
    ("07", "regras_linguisticas", "rule_runs", "regras_linguisticas", "validar_regras_linguisticas",
     "execution_id", "execucao_id", "06", "analysis_id", "analise_id", "analise"),
    ("08", "unidades_contexto", "context_runs", "unidades_contexto", "validar_unidades_contexto",
     "execution_id", "execucao_id", "07", "rule_execution_id", "regras_execucao_id", "regras"),
)

_BIBLIOTECAS = (
    "Flask", "tzdata", "spacy", "pt-core-news-sm", "pt-core-news-md", "numpy", "torch",
    "transformers", "sentence-transformers", "huggingface-hub", "fastembed", "onnx",
)


def _erro(erro):
    return {"tipo": type(erro).__name__, "mensagem": str(erro)}


def _inconsistencia(mensagem):
    return {"tipo": "InconsistenciaArmazenada", "mensagem": mensagem}


def _serializavel(valor):
    """Preserve inclusive uma coluna SQLite que contenha bytes em vez de JSON."""
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return {"codificacao": "base64", "dados": b64encode(bytes(valor)).decode("ascii")}
    if isinstance(valor, float) and not math.isfinite(valor):
        return {"tipo_sqlite": "real_nao_finito", "representacao": repr(valor)}
    if isinstance(valor, dict):
        return {str(chave): _serializavel(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serializavel(item) for item in valor]
    return valor


def _json_bytes(valor):
    return (json.dumps(_serializavel(valor), ensure_ascii=False, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _constante_invalida(valor):
    raise ValueError("Constante não permitida em JSON estrito: " + valor)


def _ler_json(bruto):
    try:
        valor = json.loads(bruto, parse_constant=_constante_invalida)
        # Também detecta estruturas demasiado profundas e Unicode não exportável.
        json.dumps(valor, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return valor, None
    except Exception as erro:
        return None, _erro(erro)


def _validacao(erros=None, relatorio=None):
    erros = list(erros or ())
    return {"estado": "invalido" if erros else "valido",
            "relatorio": relatorio, "erros": erros}


def _adicionar_erro(item, erro):
    item["validacao"]["erros"].append(erro)
    item["validacao"]["estado"] = "invalido"


def _registro(row, coluna="record_json"):
    bruto = row[coluna]
    valor, erro = _ler_json(bruto)
    erros = [erro] if erro else []
    erros.extend(_inconsistencia("Valor SQLite não finito no campo " + chave + ".")
                 for chave, item in row.items()
                 if isinstance(item, float) and not math.isfinite(item))
    return {
        "banco": {chave: item for chave, item in row.items() if chave != coluna},
        "registro_bruto": bruto,
        "registro": valor,
        "validacao": _validacao(erros),
    }


def _campos_json(row):
    campos = {}
    erros = []
    banco = {}
    for chave, bruto in row.items():
        if chave.endswith("_json"):
            valor, erro = (None, None) if bruto is None else _ler_json(bruto)
            campos[chave] = {"bruto": bruto, "valor": valor, "erro": erro}
            if erro:
                erros.append({**erro, "campo": chave})
        else:
            banco[chave] = bruto
            if isinstance(bruto, float) and not math.isfinite(bruto):
                erros.append(_inconsistencia("Valor SQLite não finito no campo " + chave + "."))
    return {"banco": banco, "campos_json": campos, "validacao": _validacao(erros)}


def _consultar(connection, sql, parametros, erros):
    try:
        cursor = connection.execute(sql, parametros)
        nomes = [coluna[0] for coluna in cursor.description]
        return [dict(zip(nomes, row)) for row in cursor.fetchall()]
    except Exception as erro:
        erros.append(_erro(erro))
        return []


def _mesmo(valor, esperado):
    return type(valor) is type(esperado) and valor == esperado


def _validar_etapa(item, especificacao, documento, indices):
    (etapa, _, _, modulo, validador, id_sql, id_json,
     anterior, origem_sql, origem_json, snapshot) = especificacao
    registro = item["registro"]
    banco = item["banco"]
    if registro is None and item["validacao"]["erros"]:
        return
    try:
        item["validacao"]["relatorio"] = getattr(import_module(modulo), validador)(registro)
    except Exception as erro:
        _adicionar_erro(item, _erro(erro))
    if type(registro) is not dict:
        _adicionar_erro(item, _inconsistencia("O registro precisa ser um objeto JSON."))
        return
    comparacoes = [("documento_id", documento["id"]), (id_json, banco[id_sql])]
    if "registered_at" in banco:
        comparacoes.append(("registrado_em", banco["registered_at"]))
    if anterior:
        comparacoes.append((origem_json, banco[origem_sql]))
        pai = indices.get(anterior, {}).get(banco[origem_sql])
        if pai is None:
            _adicionar_erro(item, _inconsistencia("Origem exata não encontrada no histórico deste documento."))
        elif registro.get(snapshot) != pai["registro"]:
            _adicionar_erro(item, _inconsistencia("O snapshot da origem diverge do registro persistido exato."))
        elif pai["validacao"]["estado"] != "valido":
            _adicionar_erro(item, _inconsistencia("A origem exata possui erros de validação."))
    for campo, esperado in comparacoes:
        if not _mesmo(registro.get(campo), esperado):
            _adicionar_erro(item, _inconsistencia("Campo divergente da associação persistida: " + campo))
    if etapa == "03":
        original = registro.get("original")
        metadados = registro.get("metadados_origem")
        if not isinstance(original, dict) or original.get("texto") != documento["content"]:
            _adicionar_erro(item, _inconsistencia("O texto original diverge do documento armazenado."))
        if (not isinstance(metadados, dict)
                or not _mesmo(metadados.get("documento_id"), documento["id"])
                or metadados.get("recebido_em") != documento["created_at"]):
            _adicionar_erro(item, _inconsistencia("Os metadados de origem divergem do documento armazenado."))


def _estado(registros, erros):
    if erros or any(item["validacao"]["estado"] != "valido" for item in registros):
        return "com_erros"
    return "disponivel" if registros else "ausente"


def _historicos(connection, documento):
    etapas = {}
    indices = {}
    for especificacao in _ETAPAS:
        etapa, nome, tabela = especificacao[:3]
        erros = []
        rows = _consultar(connection, "SELECT * FROM " + tabela + " WHERE submission_id = ? ORDER BY id",
                          (documento["id"],), erros)
        registros = [_registro(row) for row in rows]
        for item in registros:
            _validar_etapa(item, especificacao, documento, indices)
        indices[etapa] = {item["banco"][especificacao[5]]: item for item in registros}
        etapas[etapa] = {"nome": nome, "tabela": tabela, "estado": _estado(registros, erros),
                         "registros": registros, "erros": erros}
    return etapas, indices


def _vetores(connection, documento, indices, arquivos):
    from persistencia_vetores import obter_cache, obter_vetorizacao
    from trabalhos_vetorizacao import obter_trabalho

    documento_id = documento["id"]
    erros = []
    # A projeção explícita não lê claim_token para o pacote, nem futuras colunas
    # privadas adicionadas à fila.
    jobs = _consultar(connection, """SELECT id, execution_id, context_execution_id, submission_id,
        context_sha256, profile_id, options_json, profile_json, registered_at, updated_at,
        state, progress_json, error_json, attempts, adapter_json FROM vector_jobs
        WHERE submission_id = ? ORDER BY id""", (documento_id,), erros)
    trabalhos = [_campos_json(row) for row in jobs]
    jobs_por_id = {row["execution_id"]: row for row in jobs}
    for item, row in zip(trabalhos, jobs):
        try:
            publico = obter_trabalho(connection, row["execution_id"], documento_id=documento_id)
            if publico is None:
                raise ValueError("O trabalho não pertence ao documento selecionado.")
            contexto = indices.get("08", {}).get(row["context_execution_id"])
            if contexto is None or contexto["validacao"]["estado"] != "valido":
                raise ValueError("A origem exata do trabalho não é um contexto válido deste documento.")
        except Exception as erro:
            _adicionar_erro(item, _erro(erro))

    events = _consultar(connection, """SELECT e.id, e.execution_id, e.registered_at, e.state,
        e.operation, e.attempts, e.error_json FROM vector_job_events e
        JOIN vector_jobs j ON j.execution_id = e.execution_id
        WHERE j.submission_id = ? ORDER BY e.id""", (documento_id,), erros)
    eventos = [_campos_json(row) for row in events]
    for item in eventos:
        banco = item["banco"]
        if banco["state"] not in {"enfileirada", "executando", "concluida", "falhou", "interrompida"}:
            _adicionar_erro(item, _inconsistencia("Estado histórico não reconhecido."))
        if type(banco["attempts"]) is not int or banco["attempts"] < 0:
            _adicionar_erro(item, _inconsistencia("Tentativas históricas precisam ser um inteiro não negativo."))
        if not isinstance(banco["operation"], str) or not banco["operation"].strip():
            _adicionar_erro(item, _inconsistencia("Operação histórica ausente ou inválida."))
        try:
            instante = datetime.fromisoformat(banco["registered_at"].replace("Z", "+00:00"))
            if instante.tzinfo is None or instante.utcoffset() is None:
                raise ValueError("A data histórica precisa conter fuso.")
        except Exception as erro:
            _adicionar_erro(item, _erro(erro))
    runs = _consultar(connection, "SELECT * FROM vector_runs WHERE submission_id = ? ORDER BY id",
                      (documento_id,), erros)
    execucoes = [_registro(row, "manifest_json") for row in runs]
    referencias_blobs = {}
    for item, row in zip(execucoes, runs):
        item["resultado_completo"] = None
        try:
            completo = obter_vetorizacao(connection, documento_id, execucao_id=row["execution_id"])
            if completo is None:
                raise ValueError("Resultado vetorial não encontrado para este documento.")
            item["resultado_completo"] = completo
            item["validacao"]["relatorio"] = completo.get("validacao")
            job = jobs_por_id.get(row["execution_id"])
            if job is None:
                raise ValueError("Resultado vetorial sem trabalho deste documento.")
            for campo in ("execution_id", "context_execution_id", "submission_id", "context_sha256", "registered_at"):
                if not _mesmo(row[campo], job[campo]):
                    raise ValueError("Resultado e trabalho divergem no campo " + campo + ".")
            perfil, falha = _ler_json(job["profile_json"])
            if falha or completo.get("perfil") != perfil:
                raise ValueError("O perfil do resultado diverge do trabalho persistido.")
            contexto = indices.get("08", {}).get(row["context_execution_id"])
            if contexto is None or contexto["validacao"]["estado"] != "valido":
                raise ValueError("O contexto exato do resultado possui erros no histórico.")
        except Exception as erro:
            _adicionar_erro(item, _erro(erro))
        manifesto = item["registro"]
        if isinstance(manifesto, dict) and _mesmo(manifesto.get("documento_id"), documento_id):
            artefatos = manifesto.get("artefatos")
            if isinstance(artefatos, list):
                for artefato in artefatos:
                    if isinstance(artefato, dict) and isinstance(artefato.get("_blob_id"), str):
                        referencias_blobs.setdefault(artefato["_blob_id"], []).append(
                            {"origem": "vector_runs", "execucao_id": row["execution_id"]})

    representations = _consultar(connection, """SELECT r.representation_id, r.execution_id,
        r.ordinal, r.representation_json FROM vector_representations r
        JOIN vector_runs v ON v.execution_id = r.execution_id
        WHERE v.submission_id = ? ORDER BY v.id, r.ordinal""", (documento_id,), erros)
    representacoes = [_registro(row, "representation_json") for row in representations]
    execucoes_por_id = {item["banco"]["execution_id"]: item for item in execucoes}
    for item in representacoes:
        registro = item["registro"]
        if (not isinstance(registro, dict)
                or not _mesmo(registro.get("id"), item["banco"]["representation_id"])
                or not _mesmo(registro.get("ordem"), item["banco"]["ordinal"])):
            _adicionar_erro(item, _inconsistencia("Representação divergente da associação persistida."))
        execucao = execucoes_por_id.get(item["banco"]["execution_id"])
        manifesto = execucao["registro"] if execucao else None
        esperadas = manifesto.get("representacoes") if isinstance(manifesto, dict) else None
        if (not isinstance(esperadas, list)
                or not any(registro == esperada for esperada in esperadas)):
            _adicionar_erro(item, _inconsistencia("Representação divergente do manifesto da execução exata."))

    caches = _consultar(connection, """SELECT c.execution_id, c.cache_key, c.blob_id, c.artifact_json
        FROM vector_cache c JOIN vector_jobs j ON j.execution_id = c.execution_id
        WHERE j.submission_id = ? ORDER BY j.id, c.cache_key""", (documento_id,), erros)
    cache = [_registro(row, "artifact_json") for row in caches]
    for item, row in zip(cache, caches):
        item["artefato_restaurado"] = None
        try:
            item["artefato_restaurado"] = obter_cache(connection, row["execution_id"], row["cache_key"])
        except Exception as erro:
            _adicionar_erro(item, _erro(erro))
        referencias_blobs.setdefault(row["blob_id"], []).append(
            {"origem": "vector_cache", "execucao_id": row["execution_id"], "chave_cache": row["cache_key"]})

    blobs = []
    for blob_id, referencias in referencias_blobs.items():
        rows = _consultar(connection, "SELECT blob_id, sha256_bytes, format, dimension, data FROM vector_blobs WHERE blob_id = ?",
                          (blob_id,), erros)
        if not rows:
            erros.append(_inconsistencia("BLOB referenciado pelo documento não encontrado: " + str(blob_id)))
            continue
        row = rows[0]
        item = {"banco": {chave: valor for chave, valor in row.items() if chave != "data"},
                "referencias": referencias, "caminho": None, "validacao": _validacao()}
        try:
            if not isinstance(row["data"], (bytes, bytearray, memoryview)):
                item["dados_brutos"] = row["data"]
                raise ValueError("A coluna de dados vetoriais não contém bytes.")
            dados = bytes(row["data"])
            sha = hashlib.sha256(dados).hexdigest()
            item["caminho"] = "vetores/blobs/" + sha + ".bin"
            item["tamanho_bytes"] = len(dados)
            item["sha256_real"] = sha
            arquivos[item["caminho"]] = dados
            if row["sha256_bytes"] != sha:
                raise ValueError("O hash real do BLOB diverge do hash persistido.")
            if type(row["dimension"]) is not int or row["dimension"] <= 0:
                raise ValueError("A dimensão do BLOB precisa ser um inteiro positivo.")
        except Exception as erro:
            _adicionar_erro(item, _erro(erro))
        blobs.append(item)

    todos = trabalhos + eventos + execucoes + representacoes + cache + blobs
    return {"nome": "vetorizacao", "estado": _estado(todos, erros),
            "trabalhos": trabalhos, "eventos": eventos, "execucoes": execucoes,
            "representacoes": representacoes, "cache": cache, "blobs": blobs, "erros": erros}


def _ambiente():
    bibliotecas = {}
    for nome in _BIBLIOTECAS:
        try:
            bibliotecas[nome] = {"versao": metadata.version(nome), "estado": "instalada"}
        except metadata.PackageNotFoundError:
            bibliotecas[nome] = {"versao": None, "estado": "nao_instalada"}
        except Exception as erro:
            bibliotecas[nome] = {"versao": None, "estado": "erro", "erro": _erro(erro)}
    return {"python": platform.python_version(), "implementacao_python": platform.python_implementation(),
            "sqlite": sqlite3.sqlite_version, "bibliotecas": bibliotecas,
            "escopo": "Versões de uma lista fixa de bibliotecas, sem importar modelos, enumerar variáveis de ambiente ou ler credenciais."}


def _codigo(codigo_raiz, arquivos):
    erros = []
    ignorados = []
    raiz_fornecida = Path(codigo_raiz)
    resultado = {"escopo": "Fontes locais do Língua no momento da exportação; não comprovam a versão usada em registros antigos.",
                 "arquivos": [], "ignorados": ignorados, "git": {}, "erros": erros}
    try:
        if raiz_fornecida.is_symlink():
            raise ValueError("A raiz do código não pode ser um link simbólico.")
        raiz = raiz_fornecida.resolve(strict=True)
        if not raiz.is_dir():
            raise ValueError("A raiz do código precisa ser um diretório.")
        candidatos = list(raiz.glob("*.py")) + list(raiz.glob("requirements*.txt"))
        for pasta, extensoes in (("templates", (".html",)), ("static", (".js", ".css"))):
            diretorio = raiz / pasta
            if diretorio.is_symlink():
                ignorados.append({"caminho": pasta, "motivo": "link_simbolico"})
                continue
            if diretorio.is_dir():
                candidatos.extend(item for item in diretorio.iterdir() if item.suffix in extensoes)
        for caminho in sorted(set(candidatos)):
            relativo = caminho.relative_to(raiz).as_posix()
            if any(parte.startswith(".") for parte in caminho.relative_to(raiz).parts):
                continue
            try:
                if caminho.is_symlink() or not caminho.is_file():
                    ignorados.append({"caminho": relativo, "motivo": "link_simbolico_ou_nao_regular"})
                    continue
                caminho.resolve(strict=True).relative_to(raiz)
                dados = caminho.read_bytes()
                destino = "codigo/" + relativo
                arquivos[destino] = dados
                resultado["arquivos"].append({"caminho": destino, "tamanho_bytes": len(dados),
                                               "sha256": hashlib.sha256(dados).hexdigest()})
            except Exception as erro:
                erros.append({"caminho": relativo, **_erro(erro)})
        caminhos = [item["caminho"][len("codigo/"):] for item in resultado["arquivos"]]
        comandos = {
            "commit": ["rev-parse", "--verify", "HEAD"],
            "branch": ["symbolic-ref", "--quiet", "--short", "HEAD"],
        }
        if caminhos:
            comandos["status"] = ["status", "--porcelain=v1", "--untracked-files=all", "--", *caminhos]
        for chave, argumentos in comandos.items():
            try:
                processo = subprocess.run(["git", "--no-optional-locks", "-C", str(raiz), *argumentos],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          timeout=5, check=False, encoding="utf-8", errors="replace")
                resultado["git"][chave] = processo.stdout.rstrip("\n") if processo.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                resultado["git"][chave] = None
    except Exception as erro:
        erros.append(_erro(erro))
    return resultado


def _leia_me(processamento, codigo):
    relato = processamento["processamento_solicitado"]
    linhas = [
        "# Pacote de revisão do Língua", "",
        "Documento: " + str(processamento["documento"]["id"]),
        "Exportado em: " + processamento["gerado_em"], "",
        "texto_original.txt preserva literalmente o conteúdo armazenado, codificado em UTF-8.",
        "processamento_completo.json contém todos os históricos deste documento, inclusive versões anteriores, erros e trabalhos em andamento.",
        "processamento_solicitado registra as etapas executadas, reutilizadas e os erros informados pela solicitação que produziu o pacote.", "",
    ]
    if isinstance(relato, dict):
        if relato.get("completo") is False:
            linhas.extend(["**O processamento solicitado ficou INCOMPLETO. Este ZIP é um diagnóstico parcial com as pendências e falhas preservadas.**", ""])
        elif relato.get("completo") is True:
            linhas.extend(["A solicitação informou conclusão de todas as etapas. A integridade dos históricos continua indicada separadamente abaixo.", ""])
        if relato.get("escopo"):
            linhas.append("Escopo solicitado: " + str(relato["escopo"]) + ".")
        for item in relato.get("etapas", ()):
            if isinstance(item, dict):
                linhas.append("- Etapa " + str(item.get("etapa", "?")) + ": " + str(item.get("estado", "não informado")) + ".")
        falhas = relato.get("erros", ())
        if isinstance(falhas, list) and falhas:
            linhas.extend(["", "Falhas da solicitação:", ""])
            for falha in falhas:
                if isinstance(falha, dict):
                    linhas.append("- Etapa " + str(falha.get("etapa", "?")) + " — "
                                  + str(falha.get("tipo", "Erro")) + ": " + str(falha.get("mensagem", "")))
        if relato.get("estado") in {"falhou", "incompleto"}:
            linhas.extend(["", "**Estado informado pela solicitação: " + str(relato["estado"]) + ".**"])
            if relato.get("erro"):
                linhas.append("Detalhe: " + str(relato["erro"]))
        linhas.append("")
    linhas.extend([
        "| Etapa | Conteúdo | Estado do snapshot | Registros |",
        "| --- | --- | --- | --- |",
    ])
    for etapa, dados in processamento["etapas"].items():
        quantidade = len(dados.get("registros", dados.get("trabalhos", [])))
        linhas.append("| " + etapa + " | " + dados["nome"] + " | " + dados["estado"] + " | " + str(quantidade) + " |")
    linhas.extend([
        "", "Estados: disponivel indica registros presentes; ausente indica que nenhum registro foi encontrado; com_erros indica falhas de leitura ou validação.",
        "Um registro estruturalmente válido ainda pode declarar processamento incompleto, pendências ou prontidão falsa. Confira seus relatórios e estados originais.",
        "As verificações estruturais conferem formato, associações e integridade. Não atestam correção linguística, semântica, sintática, morfológica ou qualidade dos embeddings.", "",
        "revisao_gramatical.json reúne diagnósticos reproduzíveis das cadeias de regras salvas: conflitos, hierarquia e palavras fora de agrupamentos. Não altera as previsões nem bloqueia a vetorização. A ausência de avisos não certifica acerto gramatical.", "",
        "O exportador lê um snapshot SQLite e não executa modelos nem grava no banco. A rota que o chamou pode ter processado etapas antes deste snapshot.",
        "Os resultados vetoriais publicados são restaurados e validados por obter_vetorizacao, com todos os valores disponíveis. Falhas ficam registradas e o JSON bruto é mantido.",
        "O cache contém checkpoints, inclusive parciais. Sua restauração confere bytes e chave, mas não substitui a validação integral de um resultado publicado.",
        "BLOBs incluídos são somente os referidos por resultados ou cache vinculados a este documento. Referências inválidas e bytes corrompidos são preservados para diagnóstico, com os erros indicados.",
        "Trabalhos enfileirados, executando, interrompidos ou falhos não equivalem a um resultado concluído. Estados podem mudar depois da exportação.", "",
        "codigo/ contém apenas fontes Python diretas, requirements*.txt, templates/*.html e static/*.js ou *.css deste Língua; links simbólicos são excluídos.",
        "O código copiado é o código no momento da exportação. Commit, branch, status local e hashes são metadados; não garantem qual código produziu registros antigos.",
        "Arquivos não versionados permitidos também podem aparecer no snapshot. Bancos, arquivos .env, modelos, outros documentos e projetos vizinhos não são copiados.",
        "ambiente.json informa somente versões de bibliotecas de uma lista fixa e do runtime. Variáveis de ambiente, credenciais, remotos Git e tokens de claim não são exportados.",
        "manifesto.json fornece tamanho e SHA-256 de cada arquivo do ZIP, exceto o próprio manifesto, para evitar uma referência circular.",
        "A ausência de uma biblioteca ou de uma etapa é registrada; este pacote não instala dependências nem comprova reprodutibilidade de inferências.",
        "O pacote contém o texto integral e resultados derivados deste documento; compartilhe-o conforme a privacidade do próprio documento.",
        "", "Diagnósticos estruturais encontrados:", "",
    ])
    quantidade_erros = 0
    for etapa, dados in processamento["etapas"].items():
        for erro in dados["erros"]:
            quantidade_erros += 1
            linhas.append("- Etapa " + etapa + " — " + erro["tipo"] + ": " + erro["mensagem"])
        for grupo in ("registros", "trabalhos", "eventos", "execucoes", "representacoes", "cache", "blobs"):
            for item in dados.get(grupo, ()):
                banco = item["banco"]
                identificador = banco.get("id", banco.get("execution_id", banco.get("representation_id", banco.get("blob_id", "?"))))
                for erro in item["validacao"]["erros"]:
                    quantidade_erros += 1
                    linhas.append("- Etapa " + etapa + ", " + grupo + " " + str(identificador)
                                  + " — " + erro["tipo"] + ": " + erro["mensagem"])
    if not quantidade_erros:
        linhas.append("Nenhum erro estrutural foi detectado nos registros disponíveis. Consulte as prontidões e os estados de execução originais para identificar pendências.")
    linhas.extend([
        "", "Erros gerais de leitura: " + str(len(processamento["erros"])),
        "Erros ao copiar código: " + str(len(codigo["erros"])), "",
    ])
    return "\n".join(linhas).encode("utf-8")


def _revisao_gramatical(etapas):
    """Diagnostique as cadeias salvas sem inferência ou revisão da fonte."""
    from diagnosticos_gramaticais import diagnosticar_gramatica

    revisoes, erros = [], []
    for item in etapas["07"]["registros"]:
        if item["validacao"]["estado"] != "valido":
            continue
        regra = item["registro"]
        try:
            analise = regra["analise"]
            anotacao = analise["anotacao"]
            relatorio = diagnosticar_gramatica(anotacao["segmentacao"], anotacao, analise, regra)
            revisoes.append({"execucao_id": regra["execucao_id"], "relatorio": relatorio})
        except Exception as erro:
            erros.append({"execucao_id": regra.get("execucao_id"), **_erro(erro)})
    return {"schema_version": "1.0.0", "escopo": "cadeias_de_regras_salvas",
            "limite": "Diagnósticos não certificam correção gramatical nem alteram a prontidão técnica.",
            "execucoes": revisoes, "erros": erros}


def gerar_pacote_revisao(connection, documento, *, codigo_raiz=None, processamento=None):
    """Retorne BytesIO ZIP posicionado em zero, sem alterar registros ou arquivos.

    ``documento`` é uma sqlite3.Row ou dict com id/content/created_at. A conexão
    pode ser somente leitura. Se já existir uma transação, ela é preservada;
    caso contrário, uma transação exclusivamente de leitura delimita o snapshot.
    Registros corrompidos permanecem exportáveis e recebem erros por registro.
    ``processamento`` contém o relato da solicitação, sem executar novas etapas.
    """
    documento = {campo: documento[campo] for campo in ("id", "content", "created_at")}
    if not isinstance(documento["content"], str):
        raise TypeError("O conteúdo do documento precisa ser texto.")
    arquivos = {"texto_original.txt": documento["content"].encode("utf-8")}
    gerado_em = datetime.now(timezone.utc).isoformat()
    erros = []
    transacao_propria = not connection.in_transaction
    if transacao_propria:
        connection.execute("BEGIN")
    try:
        etapas, indices = _historicos(connection, documento)
        try:
            etapas["09"] = _vetores(connection, documento, indices, arquivos)
        except Exception as erro:
            # As etapas anteriores e o original continuam disponíveis mesmo se
            # alguma dependência da leitura vetorial não puder ser importada.
            etapas["09"] = {"nome": "vetorizacao", "estado": "com_erros", "trabalhos": [],
                             "eventos": [], "execucoes": [], "representacoes": [],
                             "cache": [], "blobs": [], "erros": [_erro(erro)]}
        for etapa, dados in etapas.items():
            erros.extend({"etapa": etapa, **erro} for erro in dados["erros"])
    finally:
        if transacao_propria:
            connection.rollback()
    relato = {} if processamento is None else processamento
    # Relato é uma estrutura controlada pela rota; não leia configuração do
    # processo e nunca transporte um claim mesmo se o chamador o incluir.
    def sem_claim(valor):
        if isinstance(valor, dict):
            return {chave: sem_claim(item) for chave, item in valor.items()
                    if str(chave).lower() not in {"claim_token", "token", "password", "senha", "secret", "credentials", "credenciais", "environ"}}
        if isinstance(valor, (list, tuple)):
            return [sem_claim(item) for item in valor]
        return valor
    completo = {"schema_version": SCHEMA_VERSION, "gerado_em": gerado_em,
                "documento": documento, "processamento_solicitado": sem_claim(relato),
                "etapas": etapas, "erros": erros}
    completo["revisao_gramatical"] = _revisao_gramatical(etapas)
    arquivos["revisao_gramatical.json"] = _json_bytes(completo["revisao_gramatical"])
    codigo = _codigo(Path(__file__).resolve().parent if codigo_raiz is None else codigo_raiz, arquivos)
    arquivos["processamento_completo.json"] = _json_bytes(completo)
    arquivos["ambiente.json"] = _json_bytes(_ambiente())
    arquivos["LEIA-ME.md"] = _leia_me(completo, codigo)
    manifesto = {"schema_version": SCHEMA_VERSION, "documento_id": documento["id"],
                 "gerado_em": gerado_em, "algoritmo_integridade": "SHA-256",
                 "escopo_hashes": "Todos os arquivos do ZIP, exceto manifesto.json (sem auto-hash circular).",
                 "arquivos": [{"caminho": nome, "tamanho_bytes": len(dados),
                                "sha256": hashlib.sha256(dados).hexdigest()}
                               for nome, dados in sorted(arquivos.items())], "codigo": codigo}
    arquivos["manifesto.json"] = _json_bytes(manifesto)
    resultado = BytesIO()
    with zipfile.ZipFile(resultado, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as pacote:
        for nome, dados in sorted(arquivos.items()):
            pacote.writestr(nome, dados)
    resultado.seek(0)
    return resultado
