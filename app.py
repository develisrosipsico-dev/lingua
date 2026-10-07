"""Recebimento de textos e persistência local, independente do projeto anterior."""

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from flask import Flask, abort, redirect, render_template, request, url_for


BASE = Path(__file__).resolve().parent


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("ANALISE_DB", str(BASE / "instance" / "textos.sqlite3")),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if config:
        app.config.update(config)

    database = Path(app.config["DATABASE"]).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    app.config["DATABASE"] = str(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
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
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            cursor = connection.execute(
                "INSERT INTO submissions (content, created_at) VALUES (?, ?)",
                (content, created_at),
            )
            record_id = cursor.lastrowid
            connection.commit()
        return redirect(url_for("submission", record_id=record_id), code=303)

    @app.get("/envios/<int:record_id>")
    def submission(record_id):
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT id, content, created_at FROM submissions WHERE id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            abort(404)
        record = dict(row)
        record["created_at"] = datetime.fromisoformat(record["created_at"]).astimezone(
            ZoneInfo("America/Sao_Paulo")
        ).strftime("%d/%m/%Y às %H:%M:%S")
        return render_template("index.html", content=row["content"], error=None, record=record)

    @app.errorhandler(413)
    def too_large(_error):
        return render_template(
            "index.html", content="", record=None,
            error="O envio excedeu o limite de 2 MB. Envie um texto menor.",
        ), 413

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5001)
