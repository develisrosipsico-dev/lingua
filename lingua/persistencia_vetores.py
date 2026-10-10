"""SQLite aditivo para vetores: associações, manifestos e bytes separados.

Nenhuma leitura carrega um modelo. O JSON completo é reconstruído com a
origem 08 armazenada e os BLOBs, e validado antes de ser entregue.
"""

from copy import deepcopy
import hashlib
import json

from unidades_contexto import validar_unidades_contexto


class ErroPersistenciaVetores(ValueError):
    """Origem, manifesto, associação ou bytes armazenados inconsistentes."""


def _canonico(valor):
    try:
        return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError) as erro:
        raise ErroPersistenciaVetores("Dados não serializáveis em JSON estrito.") from erro


def _hash(valor):
    return hashlib.sha256(_canonico(valor).encode("utf-8")).hexdigest()


def _row(connection, sql, parametros=()):
    cursor = connection.execute(sql, parametros)
    result = cursor.fetchone()
    return None if result is None else dict(zip((c[0] for c in cursor.description), result))


def _rows(connection, sql, parametros=()):
    cursor = connection.execute(sql, parametros)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, item)) for item in cursor.fetchall()]


def _json(texto, nome):
    try:
        result = json.loads(texto)
        _canonico(result)
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError) as erro:
        raise ErroPersistenciaVetores(f"JSON armazenado inválido: {nome}.") from erro


def inicializar_vetores(connection):
    """Acrescente tabelas sem substituir registros das etapas anteriores."""
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ErroPersistenciaVetores("A conexão SQLite precisa ter foreign_keys ativado antes da transação.")
    connection.execute("""CREATE TABLE IF NOT EXISTS vector_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        execution_id TEXT NOT NULL UNIQUE,
        context_execution_id TEXT NOT NULL REFERENCES context_runs(execution_id),
        submission_id INTEGER NOT NULL REFERENCES submissions(id),
        context_sha256 TEXT NOT NULL,
        profile_id TEXT NOT NULL, options_json TEXT NOT NULL, profile_json TEXT NOT NULL,
        registered_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('enfileirada','executando','concluida','falhou','interrompida')),
        progress_json TEXT NOT NULL, error_json TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        adapter_json TEXT, claim_token TEXT
    )""")
    connection.execute("""CREATE INDEX IF NOT EXISTS vector_jobs_source
        ON vector_jobs(submission_id, context_execution_id, id)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS vector_job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        execution_id TEXT NOT NULL REFERENCES vector_jobs(execution_id),
        registered_at TEXT NOT NULL, state TEXT NOT NULL,
        operation TEXT NOT NULL, attempts INTEGER NOT NULL, error_json TEXT
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS vector_blobs (
        blob_id TEXT PRIMARY KEY, sha256_bytes TEXT NOT NULL,
        format TEXT NOT NULL, dimension INTEGER NOT NULL, data BLOB NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS vector_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        execution_id TEXT NOT NULL UNIQUE REFERENCES vector_jobs(execution_id),
        context_execution_id TEXT NOT NULL REFERENCES context_runs(execution_id),
        submission_id INTEGER NOT NULL REFERENCES submissions(id),
        context_sha256 TEXT NOT NULL, registered_at TEXT NOT NULL,
        manifest_json TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS vector_representations (
        representation_id TEXT PRIMARY KEY,
        execution_id TEXT NOT NULL REFERENCES vector_runs(execution_id),
        ordinal INTEGER NOT NULL, representation_json TEXT NOT NULL,
        UNIQUE(execution_id, ordinal)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS vector_cache (
        execution_id TEXT NOT NULL REFERENCES vector_jobs(execution_id),
        cache_key TEXT NOT NULL, blob_id TEXT NOT NULL REFERENCES vector_blobs(blob_id),
        artifact_json TEXT NOT NULL,
        PRIMARY KEY(execution_id, cache_key)
    )""")


def carregar_contexto_armazenado(connection, documento_id, contexto_execucao_id):
    """Leia a origem exata e confira IDs, metadados e prontidão recalculada."""
    row = _row(connection, "SELECT * FROM context_runs WHERE execution_id = ?",
               (contexto_execucao_id,))
    if row is None:
        raise ErroPersistenciaVetores("A execução de contexto selecionada não existe.")
    contexto = _json(row["record_json"], "contexto")
    try:
        relatorio = validar_unidades_contexto(contexto)
    except ValueError as erro:
        raise ErroPersistenciaVetores(f"Origem 08 inválida: {erro}") from erro
    if (relatorio.get("pronto_para_etapa_09") is not True
            or contexto["documento_id"] != documento_id
            or type(contexto["documento_id"]) is not type(documento_id)
            or row["submission_id"] != documento_id
            or type(row["submission_id"]) is not type(documento_id)
            or contexto["execucao_id"] != contexto_execucao_id
            or row["registered_at"] != contexto["registrado_em"]
            or row["rule_execution_id"] != contexto["regras_execucao_id"]):
        raise ErroPersistenciaVetores("A origem 08 não corresponde ao documento e à execução armazenada.")
    return contexto


def conferir_contexto(connection, documento_id, contexto, *, sha256=None):
    armazenado = carregar_contexto_armazenado(connection, documento_id, contexto["execucao_id"])
    if (_canonico(armazenado) != _canonico(contexto)
            or (sha256 is not None and _hash(armazenado) != sha256)):
        raise ErroPersistenciaVetores("A origem 08 mudou ou não corresponde ao snapshot selecionado.")
    return armazenado


def _guardar_blob(connection, artefato):
    from vetorizacao import codificar_vetor, decodificar_vetor
    try:
        dados = codificar_vetor(artefato["valores"], artefato["formato"])
        restaurados = decodificar_vetor(dados, artefato["formato"])
        sha = hashlib.sha256(dados).hexdigest()
        if (type(artefato["dimensao"]) is not int
                or len(restaurados) != artefato["dimensao"]
                or restaurados != artefato["valores"]
                or sha != artefato["sha256_bytes"]):
            raise ErroPersistenciaVetores("Valores e integridade binária do artefato são inconsistentes.")
        identificador = _hash({"formato": artefato["formato"],
                              "dimensao": artefato["dimensao"], "sha256_bytes": sha})
        existente = _row(connection, "SELECT * FROM vector_blobs WHERE blob_id = ?", (identificador,))
        if existente is not None:
            if (existente["sha256_bytes"] != sha or existente["format"] != artefato["formato"]
                    or existente["dimension"] != artefato["dimensao"] or bytes(existente["data"]) != dados):
                raise ErroPersistenciaVetores("Um BLOB anterior está corrompido; ele não será substituído.")
        else:
            connection.execute("INSERT INTO vector_blobs VALUES (?, ?, ?, ?, ?)",
                               (identificador, sha, artefato["formato"], artefato["dimensao"], dados))
        return identificador
    except (KeyError, TypeError, ValueError, OverflowError) as erro:
        if isinstance(erro, ErroPersistenciaVetores):
            raise
        raise ErroPersistenciaVetores(f"Artefato binário inválido: {erro}") from erro


def _restaurar_artefato(connection, manifesto, blob_id):
    from vetorizacao import decodificar_vetor
    result = deepcopy(manifesto)
    row = _row(connection, "SELECT * FROM vector_blobs WHERE blob_id = ?", (blob_id,))
    if row is None:
        raise ErroPersistenciaVetores("O BLOB do artefato não existe.")
    data = bytes(row["data"])
    sha = hashlib.sha256(data).hexdigest()
    esperado_id = _hash({"formato": row["format"], "dimensao": row["dimension"], "sha256_bytes": sha})
    if (sha != row["sha256_bytes"] or sha != result.get("sha256_bytes")
            or blob_id != esperado_id or row["format"] != result.get("formato")
            or row["dimension"] != result.get("dimensao")):
        raise ErroPersistenciaVetores("Metadados ou hash do BLOB são inconsistentes.")
    try:
        result["valores"] = decodificar_vetor(data, row["format"])
    except ValueError as erro:
        raise ErroPersistenciaVetores(f"BLOB inválido: {erro}") from erro
    if len(result["valores"]) != row["dimension"]:
        raise ErroPersistenciaVetores("Comprimento do BLOB não corresponde à dimensão declarada.")
    return result


def salvar_cache(connection, execucao_id, chave, artefato):
    """Checkpoint imutável por trabalho; bytes compartilhados com resultados."""
    if type(chave) is not str or not chave or artefato.get("chave_cache") != chave:
        raise ErroPersistenciaVetores("Chave de cache inconsistente com o artefato.")
    anterior = _row(connection, "SELECT * FROM vector_cache WHERE execution_id = ? AND cache_key = ?",
                    (execucao_id, chave))
    if anterior is not None:
        restaurado = _restaurar_artefato(connection, _json(anterior["artifact_json"], "cache"), anterior["blob_id"])
        if _canonico(restaurado) != _canonico(artefato):
            raise ErroPersistenciaVetores("O checkpoint anterior não pode ser substituído por outro artefato.")
        return
    blob_id = _guardar_blob(connection, artefato)
    manifesto = deepcopy(artefato)
    del manifesto["valores"]
    connection.execute("INSERT INTO vector_cache VALUES (?, ?, ?, ?)",
                       (execucao_id, chave, blob_id, _canonico(manifesto)))


def obter_cache(connection, execucao_id, chave):
    row = _row(connection, "SELECT * FROM vector_cache WHERE execution_id = ? AND cache_key = ?",
               (execucao_id, chave))
    if row is None:
        return None
    artefato = _restaurar_artefato(connection, _json(row["artifact_json"], "cache"), row["blob_id"])
    if artefato.get("chave_cache") != chave:
        raise ErroPersistenciaVetores("O checkpoint não corresponde à chave solicitada.")
    return artefato


def salvar_vetorizacao(connection, registro):
    """Valide tudo antes de publicar; inserção inteira ocorre em savepoint."""
    from vetorizacao import validar_vetorizacao
    try:
        validar_vetorizacao(registro)
    except ValueError as erro:
        raise ErroPersistenciaVetores(f"Vetorização inválida: {erro}") from erro
    # Uma transação de escrita impede que a origem mude entre a última
    # conferência e a publicação. Nenhum modelo é executado nesta função.
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    conferir_contexto(connection, registro["documento_id"], registro["contexto"],
                      sha256=registro["contexto_sha256"])
    job = _row(connection, "SELECT * FROM vector_jobs WHERE execution_id = ?", (registro["execucao_id"],))
    if (job is None or job["context_execution_id"] != registro["contexto_execucao_id"]
            or job["submission_id"] != registro["documento_id"]
            or job["context_sha256"] != registro["contexto_sha256"]
            or _canonico(_json(job["profile_json"], "perfil")) != _canonico(registro["perfil"])
            or job["registered_at"] != registro["registrado_em"]):
        raise ErroPersistenciaVetores("A vetorização não corresponde ao trabalho e perfil selecionados.")
    # RELEASE não encerra a transação externa; run e estado do job podem
    # ser publicados pelo chamador na mesma transação.
    connection.execute("SAVEPOINT salvar_vetores")
    try:
        manifesto = deepcopy(registro)
        del manifesto["contexto"]
        for artefato in manifesto["artefatos"]:
            artefato["_blob_id"] = _guardar_blob(connection, artefato)
            del artefato["valores"]
        connection.execute("""INSERT INTO vector_runs
            (execution_id, context_execution_id, submission_id, context_sha256, registered_at, manifest_json)
            VALUES (?, ?, ?, ?, ?, ?)""", (registro["execucao_id"], registro["contexto_execucao_id"],
            registro["documento_id"], registro["contexto_sha256"], registro["registrado_em"], _canonico(manifesto)))
        for representacao in registro["representacoes"]:
            connection.execute("INSERT INTO vector_representations VALUES (?, ?, ?, ?)",
                               (representacao["id"], registro["execucao_id"], representacao["ordem"], _canonico(representacao)))
        connection.execute("RELEASE SAVEPOINT salvar_vetores")
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT salvar_vetores")
        connection.execute("RELEASE SAVEPOINT salvar_vetores")
        raise


def obter_vetorizacao(connection, documento_id, *, execucao_id):
    from vetorizacao import validar_vetorizacao
    row = _row(connection, "SELECT * FROM vector_runs WHERE execution_id = ? AND submission_id = ?",
               (execucao_id, documento_id))
    if row is None:
        return None
    manifesto = _json(row["manifest_json"], "manifesto")
    if (manifesto.get("execucao_id") != execucao_id
            or manifesto.get("documento_id") != documento_id
            or type(manifesto.get("documento_id")) is not type(documento_id)
            or manifesto.get("contexto_execucao_id") != row["context_execution_id"]
            or manifesto.get("contexto_sha256") != row["context_sha256"]
            or manifesto.get("registrado_em") != row["registered_at"]
            or "contexto" in manifesto):
        raise ErroPersistenciaVetores("Metadados do manifesto não correspondem à execução armazenada.")
    contexto = carregar_contexto_armazenado(connection, documento_id, row["context_execution_id"])
    if _hash(contexto) != row["context_sha256"]:
        raise ErroPersistenciaVetores("O snapshot da origem 08 diverge do hash armazenado.")
    manifesto["contexto"] = contexto
    try:
        manifesto["artefatos"] = [_restaurar_artefato(connection,
            {k: v for k, v in a.items() if k != "_blob_id"}, a["_blob_id"])
            for a in manifesto["artefatos"]]
        associacoes = _rows(connection, """SELECT * FROM vector_representations
            WHERE execution_id = ? ORDER BY ordinal""", (execucao_id,))
        if (len(associacoes) != len(manifesto["representacoes"])
                or any(a["representation_id"] != r["id"] or a["ordinal"] != r["ordem"]
                       or _canonico(_json(a["representation_json"], "representação")) != _canonico(r)
                       for a, r in zip(associacoes, manifesto["representacoes"]))):
            raise ErroPersistenciaVetores("As associações de origem não correspondem ao manifesto.")
        validar_vetorizacao(manifesto)
    except (KeyError, TypeError, ValueError) as erro:
        if isinstance(erro, ErroPersistenciaVetores):
            raise
        raise ErroPersistenciaVetores(f"Vetorização armazenada inconsistente: {erro}") from erro
    return manifesto
