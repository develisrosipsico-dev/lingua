"""Fila local da etapa 09, com claims curtos e checkpoints imutáveis.

O modelo nunca roda dentro de uma transação SQLite. Uma execução só é
publicada depois de revalidar a origem e o registro completo.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import sqlite3
import threading
from uuid import uuid4

from perfis_vetorizacao import carregar_perfil
from persistencia_vetores import (
    ErroPersistenciaVetores, _canonico, _hash, _json, _row, _rows,
    carregar_contexto_armazenado, conferir_contexto, obter_cache,
    obter_vetorizacao, salvar_cache, salvar_vetorizacao,
)
from unidades_contexto import validar_unidades_contexto


class ErroTrabalhoVetorizacao(ValueError):
    """Configuração, estado ou claim incompatível com o trabalho."""


_EXECUTORES = {}
_EXECUTORES_LOCK = threading.Lock()


def _agora():
    return datetime.now(timezone.utc).isoformat()


def _id(valor, campo):
    if type(valor) is not str or not valor.strip():
        raise ErroTrabalhoVetorizacao(f"{campo} deve ser uma string não vazia.")
    return valor


def _data(valor):
    if valor is None:
        return _agora()
    if isinstance(valor, datetime):
        valor = valor.isoformat()
    if type(valor) is not str:
        raise ErroTrabalhoVetorizacao("registrado_em deve ser uma data ISO 8601 com fuso.")
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        if data.tzinfo is None or data.utcoffset() is None:
            raise ValueError
    except ValueError as erro:
        raise ErroTrabalhoVetorizacao("registrado_em deve ser uma data ISO 8601 com fuso.") from erro
    return valor


def _conectar(database_path):
    connection = sqlite3.connect(str(database_path), timeout=20)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _publico(row):
    return {
        "execucao_id": row["execution_id"], "documento_id": row["submission_id"],
        "contexto_execucao_id": row["context_execution_id"], "contexto_sha256": row["context_sha256"],
        "perfil_id": row["profile_id"], "opcoes": _json(row["options_json"], "opções"),
        "perfil": _json(row["profile_json"], "perfil"),
        "registrado_em": row["registered_at"], "atualizado_em": row["updated_at"],
        "estado": row["state"], "progresso": _json(row["progress_json"], "progresso"),
        "erro": None if row["error_json"] is None else _json(row["error_json"], "erro"),
        "tentativas": row["attempts"],
        "adaptador": None if row["adapter_json"] is None else _json(row["adapter_json"], "adaptador"),
    }


def _evento(connection, execucao_id, operacao):
    row = _row(connection, "SELECT state, attempts, error_json FROM vector_jobs WHERE execution_id = ?", (execucao_id,))
    connection.execute("""INSERT INTO vector_job_events
        (execution_id, registered_at, state, operation, attempts, error_json) VALUES (?, ?, ?, ?, ?, ?)""",
        (execucao_id, _agora(), row["state"], operacao, row["attempts"], row["error_json"]))


def _historico(connection, execucao_id):
    return [{"registrado_em": row["registered_at"], "estado": row["state"],
             "operacao": row["operation"], "tentativa": row["attempts"],
             "erro": None if row["error_json"] is None else _json(row["error_json"], "erro histórico")}
            for row in _rows(connection, "SELECT * FROM vector_job_events WHERE execution_id = ? ORDER BY id", (execucao_id,))]


def enfileirar_vetorizacao(connection, documento_id, contexto, *, perfil_id="e5_simetrico",
                         opcoes=None, execucao_id=None, registrado_em=None):
    """Valide a origem e o perfil, sem importar nem executar um modelo."""
    if type(contexto) is not dict:
        raise ErroTrabalhoVetorizacao("A origem 08 deve ser um objeto JSON.")
    relatorio = validar_unidades_contexto(contexto)
    if relatorio.get("pronto_para_etapa_09") is not True:
        raise ErroTrabalhoVetorizacao("A origem 08 não está pronta para a etapa 09.")
    conferir_contexto(connection, documento_id, contexto)
    perfil = carregar_perfil(perfil_id, opcoes=opcoes)
    identificador = _id(str(uuid4()) if execucao_id is None else execucao_id, "execucao_id")
    if identificador == contexto["execucao_id"]:
        raise ErroTrabalhoVetorizacao("A vetorização precisa de um ID próprio, diferente da origem 08.")
    data = _data(registrado_em)
    connection.execute("""INSERT INTO vector_jobs
        (execution_id, context_execution_id, submission_id, context_sha256,
         profile_id, options_json, profile_json, registered_at, updated_at, state, progress_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'enfileirada', ?)""",
        (identificador, contexto["execucao_id"], documento_id, _hash(contexto), perfil_id,
         _canonico({} if opcoes is None else opcoes), _canonico(perfil), data, _agora(),
         _canonico({"concluidos": 0, "total": None, "etapa": "enfileirada"})))
    _evento(connection, identificador, "enfileirar")
    return obter_trabalho(connection, identificador, documento_id=documento_id)


def obter_trabalho(connection, execucao_id, *, documento_id=None):
    _id(execucao_id, "execucao_id")
    row = _row(connection, "SELECT * FROM vector_jobs WHERE execution_id = ?", (execucao_id,))
    if row is None or (documento_id is not None and
                      (row["submission_id"] != documento_id or type(row["submission_id"]) is not type(documento_id))):
        return None
    result = _publico(row)
    result["historico"] = _historico(connection, execucao_id)
    if (result["estado"] not in {"enfileirada", "executando", "concluida", "falhou", "interrompida"}
            or type(result["tentativas"]) is not int or result["tentativas"] < 0
            or _canonico(carregar_perfil(result["perfil_id"], opcoes=result["opcoes"])) != _canonico(result["perfil"])):
        raise ErroTrabalhoVetorizacao("Metadados ou perfil do trabalho são inconsistentes.")
    progresso = result["progresso"]
    if (type(progresso) is not dict or type(progresso.get("concluidos")) is not int
            or progresso["concluidos"] < 0 or type(progresso.get("etapa")) is not str
            or not progresso["etapa"] or (progresso.get("total") is not None and
                (type(progresso["total"]) is not int or progresso["total"] < progresso["concluidos"]))
            or ((result["estado"] == "executando") != (type(row["claim_token"]) is str and bool(row["claim_token"])))):
        raise ErroTrabalhoVetorizacao("Estado, claim ou progresso do trabalho são inconsistentes.")
    _data(result["registrado_em"])
    _data(result["atualizado_em"])
    contexto = carregar_contexto_armazenado(connection, result["documento_id"], result["contexto_execucao_id"])
    if _hash(contexto) != result["contexto_sha256"]:
        raise ErroPersistenciaVetores("O snapshot da origem 08 do trabalho foi alterado.")
    run = _row(connection, "SELECT execution_id FROM vector_runs WHERE execution_id = ?", (execucao_id,))
    if (result["estado"] == "concluida") != (run is not None):
        raise ErroPersistenciaVetores("Estado do trabalho e publicação do resultado são inconsistentes.")
    return result


def listar_trabalhos(connection, documento_id, contexto_execucao_id=None):
    sql = "SELECT execution_id FROM vector_jobs WHERE submission_id = ?"
    parametros = [documento_id]
    if contexto_execucao_id is not None:
        sql += " AND context_execution_id = ?"
        parametros.append(contexto_execucao_id)
    rows = _rows(connection, sql + " ORDER BY id DESC", parametros)
    return [obter_trabalho(connection, row["execution_id"], documento_id=documento_id) for row in rows]


def retomar_trabalho(connection, execucao_id, *, documento_id=None):
    """Reenfileire uma falha/interrupção, conservando configuração e caches."""
    job = obter_trabalho(connection, execucao_id, documento_id=documento_id)
    if job is None:
        raise ErroTrabalhoVetorizacao("O trabalho selecionado não existe.")
    if job["estado"] not in {"falhou", "interrompida"}:
        raise ErroTrabalhoVetorizacao("Somente trabalhos que falharam ou foram interrompidos podem ser retomados.")
    cursor = connection.execute("""UPDATE vector_jobs SET state = 'enfileirada', updated_at = ?,
        claim_token = NULL, progress_json = ? WHERE execution_id = ? AND state IN ('falhou','interrompida')""",
        (_agora(), _canonico({**job["progresso"], "etapa": "retomada_enfileirada"}), execucao_id))
    if cursor.rowcount != 1:
        raise ErroTrabalhoVetorizacao("O estado do trabalho mudou durante a retomada.")
    _evento(connection, execucao_id, "retomar")
    return obter_trabalho(connection, execucao_id, documento_id=documento_id)


def interromper_trabalho(connection, execucao_id, *, motivo="Interrupção explicitamente solicitada."):
    """Invalide um claim, inclusive após parar um processo que não respondeu.

    Não há recuperação automática por tempo: isso poderia duplicar inferência
    de um trabalhador ainda ativo. Após a interrupção explícita, a retomada
    revalida origem e descrição do adaptador.
    """
    job = obter_trabalho(connection, execucao_id)
    if job is None or job["estado"] != "executando":
        raise ErroTrabalhoVetorizacao("Somente um trabalho em execução pode ser interrompido.")
    cursor = connection.execute("""UPDATE vector_jobs SET state = 'interrompida', claim_token = NULL,
        updated_at = ?, error_json = ? WHERE execution_id = ? AND state = 'executando'""",
        (_agora(), _canonico({"tipo": "Interrupcao", "mensagem": str(motivo)[:2000]}), execucao_id))
    if cursor.rowcount != 1:
        raise ErroTrabalhoVetorizacao("O estado do trabalho mudou durante a interrupção.")
    _evento(connection, execucao_id, "interromper")
    return obter_trabalho(connection, execucao_id)


def _confirmar_claim(connection, execucao_id, token):
    row = _row(connection, "SELECT state, claim_token FROM vector_jobs WHERE execution_id = ?", (execucao_id,))
    if row is None or row["state"] != "executando" or row["claim_token"] != token:
        raise ErroTrabalhoVetorizacao("O claim do trabalho foi interrompido ou transferido; resultado não publicado.")


def processar_trabalho(database_path, execucao_id, *, fabrica_adaptador=None, carregar_contexto=None):
    """Execute um trabalho com conexão própria; falhas ficam no histórico."""
    from modelo_embeddings import criar_adaptador
    from vetorizacao import vetorizar_contexto

    connection = _conectar(database_path)
    try:
        _id(execucao_id, "execucao_id")
        row = _row(connection, "SELECT * FROM vector_jobs WHERE execution_id = ?", (execucao_id,))
        if row is None:
            raise ErroTrabalhoVetorizacao("O trabalho selecionado não existe.")
        job = _publico(row)
        if job["estado"] == "concluida":
            if obter_vetorizacao(connection, job["documento_id"], execucao_id=execucao_id) is None:
                raise ErroPersistenciaVetores("O trabalho concluído não tem resultado publicado.")
            return job
        if job["estado"] == "executando":
            return job
        if job["estado"] != "enfileirada":
            raise ErroTrabalhoVetorizacao("O trabalho precisa ser retomado explicitamente antes de executar.")
        token = str(uuid4())
        with connection:
            cursor = connection.execute("""UPDATE vector_jobs SET state = 'executando', attempts = attempts + 1,
                claim_token = ?, updated_at = ?, error_json = NULL WHERE execution_id = ? AND state = 'enfileirada'""",
                (token, _agora(), execucao_id))
            if cursor.rowcount == 1:
                _evento(connection, execucao_id, "iniciar")
        if cursor.rowcount != 1:
            return obter_trabalho(connection, execucao_id)

        def ler_fonte():
            contexto = (carregar_contexto or carregar_contexto_armazenado)(
                connection, job["documento_id"], job["contexto_execucao_id"])
            conferir_contexto(connection, job["documento_id"], contexto, sha256=job["contexto_sha256"])
            return contexto

        try:
            # A validação completa acontece depois do claim: uma origem que
            # deixou de ser válida deve gerar um estado de falha persistido.
            obter_trabalho(connection, execucao_id)
            contexto = ler_fonte()
            adaptador = (fabrica_adaptador or criar_adaptador)(deepcopy(job["perfil"]))
            descricao = adaptador.descrever()
            _canonico(descricao)
            with connection:
                _confirmar_claim(connection, execucao_id, token)
                row = _row(connection, "SELECT adapter_json FROM vector_jobs WHERE execution_id = ?", (execucao_id,))
                if row["adapter_json"] is not None and row["adapter_json"] != _canonico(descricao):
                    raise ErroTrabalhoVetorizacao("A configuração efetiva do adaptador mudou; crie uma nova execução.")
                connection.execute("UPDATE vector_jobs SET adapter_json = ?, updated_at = ? WHERE execution_id = ?",
                                   (_canonico(descricao), _agora(), execucao_id))

            def progresso(valor):
                if type(valor) is not dict:
                    raise ErroTrabalhoVetorizacao("O progresso deve ser um objeto JSON.")
                with connection:
                    _confirmar_claim(connection, execucao_id, token)
                    connection.execute("UPDATE vector_jobs SET progress_json = ?, updated_at = ? WHERE execution_id = ?",
                                       (_canonico(valor), _agora(), execucao_id))

            def cache_ler(chave):
                _confirmar_claim(connection, execucao_id, token)
                return obter_cache(connection, execucao_id, chave)

            def cache_salvar(chave, artefato):
                with connection:
                    _confirmar_claim(connection, execucao_id, token)
                    salvar_cache(connection, execucao_id, chave, artefato)

            registro = vetorizar_contexto(contexto, perfil_id=job["perfil_id"], opcoes=job["opcoes"],
                execucao_id=execucao_id, registrado_em=job["registrado_em"], adaptador=adaptador,
                progresso=progresso, obter_cache=cache_ler, salvar_cache=cache_salvar)
            # Ler outra vez detecta alterações enquanto o modelo estava trabalhando.
            final = ler_fonte()
            if _canonico(final) != _canonico(contexto):
                raise ErroPersistenciaVetores("A origem 08 mudou durante a geração.")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                _confirmar_claim(connection, execucao_id, token)
                if _canonico(ler_fonte()) != _canonico(contexto):
                    raise ErroPersistenciaVetores("A origem 08 mudou antes da publicação.")
                salvar_vetorizacao(connection, registro)
                connection.execute("""UPDATE vector_jobs SET state = 'concluida', claim_token = NULL,
                    error_json = NULL, updated_at = ?, progress_json = ? WHERE execution_id = ?""",
                    (_agora(), _canonico({"concluidos": len(registro["representacoes"]),
                                         "total": len(registro["representacoes"]), "etapa": "concluida"}), execucao_id))
                _evento(connection, execucao_id, "concluir")
        except BaseException as erro:
            estado = "interrompida" if isinstance(erro, (KeyboardInterrupt, SystemExit)) else "falhou"
            with connection:
                cursor = connection.execute("""UPDATE vector_jobs SET state = ?, claim_token = NULL,
                    updated_at = ?, error_json = ? WHERE execution_id = ? AND claim_token = ? AND state = 'executando'""",
                    (estado, _agora(), _canonico({"tipo": type(erro).__name__, "mensagem": str(erro)[:2000]}),
                     execucao_id, token))
                if cursor.rowcount == 1:
                    _evento(connection, execucao_id, "interromper" if estado == "interrompida" else "falhar")
            if isinstance(erro, (KeyboardInterrupt, SystemExit)):
                raise
        final = _publico(_row(connection, "SELECT * FROM vector_jobs WHERE execution_id = ?", (execucao_id,)))
        final["historico"] = _historico(connection, execucao_id)
        return final
    finally:
        connection.close()


def iniciar_trabalho(database_path, execucao_id, *, fabrica_adaptador=None, carregar_contexto=None, max_workers=1):
    """Envie ao executor compartilhado desta base; nenhuma inferência no HTTP."""
    if type(max_workers) is not int or max_workers < 1:
        raise ErroTrabalhoVetorizacao("max_workers deve ser um inteiro positivo.")
    chave = str(database_path)
    with _EXECUTORES_LOCK:
        existente = _EXECUTORES.get(chave)
        if existente is not None and existente[0] != max_workers:
            raise ErroTrabalhoVetorizacao("A capacidade deste executor já foi fixada; reinicie para alterá-la.")
        if existente is None:
            existente = (max_workers, ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lingua-vetores"))
            _EXECUTORES[chave] = existente
        executor = existente[1]
    return executor.submit(processar_trabalho, database_path, execucao_id,
                           fabrica_adaptador=fabrica_adaptador, carregar_contexto=carregar_contexto)
