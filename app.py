"""Recebimento de textos e persistência local, independente do projeto anterior."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from preparacao import ErroPreparacao, preparar_texto, validar_registro
from segmentacao import ErroSegmentacao, contexto_periodo, segmentar_preparacao, validar_segmentacao
from anotacao import (
    ErroAlinhamento, ErroAnotacao, ErroLimite, ErroModelo, anotar_segmentacao, validar_anotacao,
)


BASE = Path(__file__).resolve().parent


def connect_database(database):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def form_crlf_option():
    values = request.form.getlist("normalizar_crlf")
    if not values:
        return False
    if values == ["on"]:
        return True
    raise ErroPreparacao("A opção CRLF deve ser enviada por uma única caixa de seleção.")


def save_preparation(connection, document, **options):
    """Registra uma nova preparação sem atualizar o documento ou versões anteriores."""
    prepared = preparar_texto(
        document["content"], document["id"],
        metadados={"documento_id": document["id"], "recebido_em": document["created_at"]},
        **options,
    )
    connection.execute(
        "INSERT INTO preparations (preparation_id, submission_id, record_json) VALUES (?, ?, ?)",
        (prepared["preparacao_id"], document["id"], json.dumps(prepared, ensure_ascii=False, allow_nan=False)),
    )
    return prepared


def latest_preparation(connection, document, preparation_id=None):
    sql = "SELECT preparation_id, record_json FROM preparations WHERE submission_id = ?"
    parameters = [document["id"]]
    if preparation_id is not None:
        sql += " AND preparation_id = ?"
        parameters.append(preparation_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        prepared = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroPreparacao("O JSON da preparação armazenada é inválido.") from error
    validar_registro(prepared)
    if (
        prepared["documento_id"] != document["id"]
        or prepared["preparacao_id"] != row["preparation_id"]
        or prepared["original"]["texto"] != document["content"]
        or prepared["metadados_origem"].get("documento_id") != document["id"]
        or prepared["metadados_origem"].get("recebido_em") != document["created_at"]
    ):
        raise ErroPreparacao("A preparação não corresponde ao documento original armazenado.")
    return prepared


def save_segmentation(connection, document, prepared, **options):
    """Acrescenta uma execução vinculada à preparação efetivamente processada."""
    segmented = segmentar_preparacao(prepared, **options)
    connection.execute(
        """INSERT INTO segmentations
           (segmentation_id, preparation_id, submission_id, registered_at, record_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            segmented["segmentacao_id"], prepared["preparacao_id"], document["id"],
            segmented["registrado_em"], json.dumps(segmented, ensure_ascii=False, allow_nan=False),
        ),
    )
    return segmented


def latest_segmentation(connection, document, preparation_id=None, segmentation_id=None):
    sql = "SELECT * FROM segmentations WHERE submission_id = ?"
    parameters = [document["id"]]
    if preparation_id is not None:
        sql += " AND preparation_id = ?"
        parameters.append(preparation_id)
    if segmentation_id is not None:
        sql += " AND segmentation_id = ?"
        parameters.append(segmentation_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        segmented = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroSegmentacao("O JSON da segmentação armazenada é inválido.") from error
    validar_segmentacao(segmented)
    prepared = latest_preparation(connection, document, row["preparation_id"])
    if (
        prepared is None
        or segmented["documento_id"] != document["id"]
        or segmented["segmentacao_id"] != row["segmentation_id"]
        or segmented["preparacao_id"] != row["preparation_id"]
        or segmented["registrado_em"] != row["registered_at"]
        or segmented["preparacao"] != prepared
    ):
        raise ErroSegmentacao("A segmentação não corresponde à preparação original armazenada.")
    return segmented


def save_annotation(connection, document, segmented, **options):
    """Acrescenta anotações da segmentação escolhida, sem substituir o histórico."""
    annotated = anotar_segmentacao(segmented, **options)
    connection.execute(
        """INSERT INTO annotations
           (annotation_id, segmentation_id, submission_id, registered_at, record_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            annotated["anotacao_id"], segmented["segmentacao_id"], document["id"],
            annotated["registrado_em"], json.dumps(annotated, ensure_ascii=False, allow_nan=False),
        ),
    )
    return annotated


def latest_annotation(connection, document, segmentation_id=None, annotation_id=None):
    sql = "SELECT * FROM annotations WHERE submission_id = ?"
    parameters = [document["id"]]
    if segmentation_id is not None:
        sql += " AND segmentation_id = ?"
        parameters.append(segmentation_id)
    if annotation_id is not None:
        sql += " AND annotation_id = ?"
        parameters.append(annotation_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        annotated = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroAnotacao("O JSON da anotação armazenada é inválido.") from error
    validar_anotacao(annotated)
    segmented = latest_segmentation(connection, document, segmentation_id=row["segmentation_id"])
    if (
        segmented is None
        or annotated["documento_id"] != document["id"]
        or annotated["anotacao_id"] != row["annotation_id"]
        or annotated["segmentacao_id"] != row["segmentation_id"]
        or annotated["registrado_em"] != row["registered_at"]
        or annotated["segmentacao"] != segmented
    ):
        raise ErroAnotacao("A anotação não corresponde à segmentação original armazenada.")
    return annotated


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("ANALISE_DB", str(BASE / "instance" / "textos.sqlite3")),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if config:
        app.config.update(config)

    @app.template_filter("horario_brasilia")
    def display_time(value):
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.utcoffset() is None:
                return "Data indisponível"
            return instant.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y às %H:%M:%S")
        except (AttributeError, TypeError, ValueError):
            return "Data indisponível"

    database = Path(app.config["DATABASE"]).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    app.config["DATABASE"] = str(database)
    with closing(connect_database(database)) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS preparations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preparation_id TEXT NOT NULL UNIQUE,
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS segmentations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segmentation_id TEXT NOT NULL UNIQUE,
                preparation_id TEXT NOT NULL REFERENCES preparations(preparation_id),
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                registered_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS segmentations_preparation ON segmentations (submission_id, preparation_id, id)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotation_id TEXT NOT NULL UNIQUE,
                segmentation_id TEXT NOT NULL REFERENCES segmentations(segmentation_id),
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                registered_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS annotations_segmentation ON annotations (submission_id, segmentation_id, id)"
        )
        connection.commit()

    @app.get("/")
    def index():
        return render_template("index.html", content="", error=None, record=None)

    @app.post("/envios")
    def submit_text():
        content = request.form.get("content", "")
        if not content.strip():
            return render_template(
                "index.html", content=content,
                error="Digite um texto antes de registrar.", record=None,
            ), 400

        created_at = datetime.now(timezone.utc).isoformat()
        try:
            normalizar_crlf = form_crlf_option()
            with closing(connect_database(app.config["DATABASE"])) as connection:
                cursor = connection.execute(
                    "INSERT INTO submissions (content, created_at) VALUES (?, ?)",
                    (content, created_at),
                )
                record_id = cursor.lastrowid
                save_preparation(
                    connection, {"id": record_id, "content": content, "created_at": created_at},
                    normalizar_crlf=normalizar_crlf,
                    registrado_em=created_at,
                )
                connection.commit()
        except ErroPreparacao as error:
            return render_template("index.html", content=content, error=str(error), record=None), 400
        return redirect(url_for("submission", record_id=record_id), code=303)

    @app.get("/envios/<int:record_id>")
    def submission(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            row = connection.execute(
                "SELECT id, content, created_at FROM submissions WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                abort(404)
            try:
                preparation_id = request.args.get("preparacao_id")
                segmentation_id = request.args.get("segmentacao_id")
                annotation_id = request.args.get("anotacao_id")
                if annotation_id is not None:
                    annotated = latest_annotation(connection, row, annotation_id=annotation_id)
                    if annotated is None:
                        abort(404)
                    if (
                        segmentation_id is not None and segmentation_id != annotated["segmentacao_id"]
                        or preparation_id is not None and preparation_id != annotated["preparacao_id"]
                    ):
                        return {"erro": "A anotação pertence a outra segmentação ou preparação."}, 400
                    segmentation_id = annotated["segmentacao_id"]
                    preparation_id = annotated["preparacao_id"]
                if segmentation_id is not None:
                    segmented = latest_segmentation(connection, row, segmentation_id=segmentation_id)
                    if segmented is None:
                        abort(404)
                    if preparation_id is not None and preparation_id != segmented["preparacao_id"]:
                        return {"erro": "A segmentação pertence a outra preparação."}, 400
                    preparation_id = segmented["preparacao_id"]
                prepared = latest_preparation(connection, row, preparation_id)
                if preparation_id is not None and prepared is None:
                    abort(404)
                if segmentation_id is None:
                    segmented = latest_segmentation(connection, row, prepared["preparacao_id"]) if prepared else None
                if annotation_id is None:
                    annotated = latest_annotation(connection, row, segmented["segmentacao_id"]) if segmented else None
                annotation_history = connection.execute(
                    """SELECT id, annotation_id AS anotacao_id, registered_at AS registrado_em
                       FROM annotations WHERE submission_id = ? AND segmentation_id = ? ORDER BY id DESC""",
                    (record_id, segmented["segmentacao_id"]),
                ).fetchall() if segmented else []
                history = connection.execute(
                    """SELECT segmentation_id AS segmentacao_id, registered_at AS registrado_em
                       FROM segmentations WHERE submission_id = ? AND preparation_id = ? ORDER BY id DESC""",
                    (record_id, prepared["preparacao_id"]),
                ).fetchall() if prepared else []
                preparation_history = connection.execute(
                    "SELECT id, preparation_id AS preparacao_id FROM preparations WHERE submission_id = ? ORDER BY id DESC",
                    (record_id,),
                ).fetchall()
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao) as error:
                return {"erro": str(error)}, 409
        record = dict(row)
        record["created_at"] = datetime.fromisoformat(record["created_at"]).astimezone(
            ZoneInfo("America/Sao_Paulo")
        ).strftime("%d/%m/%Y às %H:%M:%S")
        return render_template(
            "index.html", content=row["content"], error=None, record=record, prepared=prepared,
            segmented=segmented, segmentation_history=[dict(item) for item in history],
            preparation_history=[dict(item) for item in preparation_history],
            annotated=annotated, annotation_history=[dict(item) for item in annotation_history],
        )

    @app.post("/envios/<int:record_id>/preparacoes")
    def prepare_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"normalizar_crlf", "preparacao_id", "registrado_em"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com normalizar_crlf, preparacao_id ou registrado_em."}, 400
            options = payload
        else:
            try:
                options = {"normalizar_crlf": form_crlf_option()}
            except ErroPreparacao as error:
                return {"erro": str(error)}, 400
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                prepared = save_preparation(connection, document, **options)
                connection.commit()
        except ErroPreparacao as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de preparação já existe. Crie uma nova preparação."}, 409
        if request.is_json:
            return Response(json.dumps(prepared, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(url_for("submission", record_id=record_id), code=303)

    @app.get("/envios/<int:record_id>/preparacao.json")
    def preparation_json(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                prepared = latest_preparation(connection, document, request.args.get("preparacao_id"))
            except ErroPreparacao as error:
                return {"erro": str(error)}, 409
        if prepared is None:
            return {"erro": "Nenhuma preparação encontrada. Gere uma preparação para este documento."}, 404
        return Response(
            json.dumps(prepared, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="preparacao-{record_id}.json"'},
        )

    @app.post("/envios/<int:record_id>/segmentacoes")
    def segment_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"preparacao_id", "segmentacao_id", "registrado_em"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com preparacao_id, segmentacao_id ou registrado_em."}, 400
            options = dict(payload)
        else:
            if set(request.form) != {"preparacao_id"} or len(request.form.getlist("preparacao_id")) != 1:
                return {"erro": "Selecione uma única preparação para segmentar."}, 400
            options = {"preparacao_id": request.form["preparacao_id"]}
        preparation_id = options.pop("preparacao_id", None)
        if "preparacao_id" in (payload if request.is_json else request.form) and (
            not isinstance(preparation_id, str) or not preparation_id.strip()
        ):
            return {"erro": "preparacao_id deve ser um identificador textual não vazio."}, 400
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                prepared = latest_preparation(connection, document, preparation_id)
                if prepared is None:
                    return {"erro": "Preparação não encontrada. Prepare este documento antes de segmentar."}, 404
                segmented = save_segmentation(connection, document, prepared, **options)
                connection.commit()
        except ErroPreparacao as error:
            return {"erro": str(error)}, 409
        except ErroSegmentacao as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de segmentação já existe. Gere uma nova execução."}, 409
        if request.is_json:
            return Response(json.dumps(segmented, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(
            url_for("submission", record_id=record_id, preparacao_id=prepared["preparacao_id"], segmentacao_id=segmented["segmentacao_id"]),
            code=303,
        )

    @app.get("/envios/<int:record_id>/segmentacao.json")
    def segmentation_json(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                segmented = latest_segmentation(connection, document, segmentation_id=request.args.get("segmentacao_id"))
            except (ErroPreparacao, ErroSegmentacao) as error:
                return {"erro": str(error)}, 409
        if segmented is None:
            return {"erro": "Nenhuma segmentação encontrada. Tokenize e segmente uma preparação deste documento."}, 404
        return Response(
            json.dumps(segmented, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="segmentacao-{record_id}.json"'},
        )

    @app.post("/envios/<int:record_id>/anotacoes")
    def annotate_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"segmentacao_id", "anotacao_id", "registrado_em"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com segmentacao_id, anotacao_id ou registrado_em."}, 400
            options = dict(payload)
        else:
            if set(request.form) != {"segmentacao_id"} or len(request.form.getlist("segmentacao_id")) != 1:
                return {"erro": "Selecione uma única segmentação para anotar."}, 400
            options = {"segmentacao_id": request.form["segmentacao_id"]}
        for field in ("segmentacao_id", "anotacao_id", "registrado_em"):
            if field in options and (not isinstance(options[field], str) or not options[field].strip()):
                return {"erro": f"{field} deve ser uma string não vazia."}, 400
        segmentation_id = options.pop("segmentacao_id", None)
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                segmented = latest_segmentation(connection, document, segmentation_id=segmentation_id)
                if segmented is None:
                    return {"erro": "Segmentação não encontrada. Segmente uma preparação antes de anotar."}, 404
                annotated = save_annotation(connection, document, segmented, **options)
                connection.commit()
        except (ErroPreparacao, ErroSegmentacao) as error:
            return {"erro": str(error)}, 409
        except ErroModelo as error:
            return {"erro": str(error)}, 503
        except ErroAlinhamento as error:
            return {"erro": str(error)}, 422
        except ErroLimite as error:
            return {"erro": str(error)}, 413
        except ErroAnotacao as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de anotação já existe. Gere uma nova execução."}, 409
        if request.is_json:
            return Response(json.dumps(annotated, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(
            url_for(
                "submission", record_id=record_id, preparacao_id=annotated["preparacao_id"],
                segmentacao_id=annotated["segmentacao_id"], anotacao_id=annotated["anotacao_id"],
            ),
            code=303,
        )

    @app.get("/envios/<int:record_id>/anotacao.json")
    def annotation_json(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                annotated = latest_annotation(connection, document, annotation_id=request.args.get("anotacao_id"))
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao) as error:
                return {"erro": str(error)}, 409
        if annotated is None:
            return {"erro": "Nenhuma anotação encontrada. Anote uma segmentação deste documento."}, 404
        return Response(
            json.dumps(annotated, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="anotacao-{record_id}.json"'},
        )

    @app.get("/envios/<int:record_id>/segmentacoes/<segmentacao_id>/periodos/<periodo_id>/contexto.json")
    @app.get("/envios/<int:record_id>/contexto-periodo.json")
    def period_context_json(record_id, segmentacao_id=None, periodo_id=None):
        if segmentacao_id is None:
            values = [request.args.getlist(name) for name in ("segmentacao_id", "periodo_id")]
            if any(len(items) != 1 or not items[0].strip() for items in values):
                return {"erro": "Forneça segmentacao_id e periodo_id uma única vez."}, 400
            segmentacao_id, periodo_id = (items[0] for items in values)
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                segmented = latest_segmentation(connection, document, segmentation_id=segmentacao_id)
                if segmented is None:
                    abort(404)
                if not any(period["id"] == periodo_id for period in segmented["periodos"]):
                    return {"erro": "Período não encontrado nesta segmentação."}, 404
                context = contexto_periodo(segmented, periodo_id)
            except (ErroPreparacao, ErroSegmentacao) as error:
                return {"erro": str(error)}, 409
        return Response(json.dumps(context, ensure_ascii=False, allow_nan=False), mimetype="application/json")

    @app.errorhandler(413)
    def too_large(_error):
        return render_template(
            "index.html", content="", record=None,
            error="O envio excedeu o limite de 2 MB. Envie um texto menor.",
        ), 413

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5001)
