"""Relatos e relações conferidas em um SQLite separado do projeto Lingua."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .entrada import validar_relato


class ErroPersistencia(ValueError):
    """Falha com mensagem própria, sem conteúdo de credenciais."""


_CAMPOS_RESULTADO = {
    "consultas", "candidatos", "metodo_fusao", "rejeitadas", "avaliacao",
    "mensagem", "justificativas", "posicoes", "aviso", "fonte_vetorizacao",
}
_CAMPOS_RELACAO = {
    "id", "paragrafo", "relato", "freud", "observacao", "justificativa", "ligacao", "limites",
    "situacao", "bloco_id", "fragmentos_ids", "alternativas", "conceito",
    "referencia", "pontuacoes", "rrf", "contexto", "conferencias",
}


def _agora():
    return datetime.now(timezone.utc).isoformat()


def _sem_credenciais(valor):
    if isinstance(valor, dict):
        return {chave: _sem_credenciais(item) for chave, item in valor.items()
                if chave.lower() not in {"chave_api", "api_key", "authorization"}}
    if isinstance(valor, list):
        return [_sem_credenciais(item) for item in valor]
    return valor


def _json(valor):
    return json.dumps(_sem_credenciais(valor), ensure_ascii=False, allow_nan=False)


def _json_vetorizacao(registro):
    """Serialize o contrato intacto; seus hashes não permitem remover campos."""
    def conferir(valor):
        if type(valor) is dict:
            if any(type(chave) is not str for chave in valor):
                raise ValueError
            for item in valor.values():
                conferir(item)
        elif type(valor) is list:
            for item in valor:
                conferir(item)
        elif type(valor) not in (str, int, float, bool, type(None)):
            raise ValueError

    try:
        if type(registro) is not dict:
            raise ValueError
        conferir(registro)
        bruto = json.dumps(registro, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
        bruto.encode("utf-8")
        return bruto
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise ErroPersistencia("A vetorização precisa conter um objeto JSON válido e finito.") from None


def _texto_vetorizado(registro):
    try:
        texto = registro["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["original"]["texto"]
        if type(texto) is not str:
            raise ValueError
        return texto
    except (KeyError, TypeError, ValueError):
        raise ErroPersistencia("A vetorização não possui o texto original do relato.") from None


def _relacao_conferida(item, texto):
    if not isinstance(item, dict):
        raise ErroPersistencia("A relação não tem o formato conferido esperado.")
    conferencias = item.get("conferencias", {})
    if not isinstance(conferencias, dict) or any(
        conferencias.get(campo) is not True
        for campo in ("relato_literal", "freud_literal", "fragmentos_validos")
    ):
        raise ErroPersistencia("Somente relações com citações e fontes conferidas podem ser salvas.")
    for campo in ("relato", "freud"):
        trecho = item.get(campo)
        if (not isinstance(trecho, dict) or type(trecho.get("inicio")) is not int
                or type(trecho.get("fim")) is not int
                or not 0 <= trecho["inicio"] < trecho["fim"]
                or not isinstance(trecho.get("texto"), str) or not trecho["texto"].strip()):
            raise ErroPersistencia("As posições ou citações da relação são inválidas.")
    trecho = item["relato"]
    if texto[trecho["inicio"]:trecho["fim"]] != trecho["texto"]:
        raise ErroPersistencia("A citação da relação difere do relato original.")
    if (not isinstance(item.get("justificativa"), str) or not item["justificativa"].strip()
            or not isinstance(item.get("bloco_id"), str) or not item["bloco_id"]
            or item.get("situacao") not in ("pertinente", "parcial", "descartada")):
        raise ErroPersistencia("A justificativa, a fonte ou a situação da relação está inválida.")
    return {campo: item[campo] for campo in _CAMPOS_RELACAO if campo in item}


class BancoAnalista:
    def __init__(self, caminho):
        self.caminho = Path(caminho).expanduser().resolve()
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.conectar()) as conexao, conexao:
            conexao.executescript("""
                CREATE TABLE IF NOT EXISTS vetorizacoes (
                    id TEXT PRIMARY KEY,
                    registro_json TEXT NOT NULL,
                    criado_em TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relatos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    palavras INTEGER NOT NULL CHECK (palavras > 0 AND palavras <= 400),
                    criado_em TEXT NOT NULL,
                    atualizado_em TEXT NOT NULL,
                    estado TEXT NOT NULL CHECK (estado IN
                        ('registrado', 'executando', 'concluido', 'erro', 'interrompido')),
                    busca_id TEXT UNIQUE,
                    provedor TEXT,
                    modelo TEXT,
                    erro TEXT,
                    resultado_json TEXT,
                    vetorizacao_id TEXT REFERENCES vetorizacoes(id)
                );
                CREATE TABLE IF NOT EXISTS relacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relato_id INTEGER NOT NULL REFERENCES relatos(id) ON DELETE CASCADE,
                    ordem INTEGER NOT NULL CHECK (ordem > 0),
                    trecho_relato TEXT NOT NULL,
                    relato_inicio INTEGER NOT NULL,
                    relato_fim INTEGER NOT NULL,
                    trecho_freud TEXT NOT NULL,
                    freud_inicio INTEGER NOT NULL,
                    freud_fim INTEGER NOT NULL,
                    justificativa TEXT NOT NULL,
                    bloco_id TEXT NOT NULL,
                    situacao TEXT NOT NULL CHECK (situacao IN ('pertinente', 'parcial', 'descartada')),
                    dados_json TEXT NOT NULL,
                    UNIQUE (relato_id, ordem)
                );
            """)
            # Bancos anteriores mantêm relatos, relações, IDs e resultados.
            colunas = {coluna["name"] for coluna in conexao.execute("PRAGMA table_info(relatos)")}
            if "vetorizacao_id" not in colunas:
                conexao.execute("ALTER TABLE relatos ADD COLUMN vetorizacao_id TEXT REFERENCES vetorizacoes(id)")

    def conectar(self):
        conexao = sqlite3.connect(self.caminho, timeout=10)
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA foreign_keys = ON")
        return conexao

    def interromper_buscas(self):
        with closing(self.conectar()) as conexao, conexao:
            conexao.execute("""UPDATE relatos SET estado = 'interrompido',
                erro = 'O servidor foi reiniciado antes de concluir esta busca.', atualizado_em = ?
                WHERE estado = 'executando'""", (_agora(),))

    def registrar_vetorizacao(self, registro):
        bruto = _json_vetorizacao(registro)
        _texto_vetorizado(registro)
        identificador = hashlib.sha256(bruto.encode("utf-8")).hexdigest()
        with closing(self.conectar()) as conexao, conexao:
            conexao.execute("""INSERT OR IGNORE INTO vetorizacoes
                (id, registro_json, criado_em) VALUES (?, ?, ?)""", (identificador, bruto, _agora()))
        return identificador

    def obter_vetorizacao(self, vetorizacao_id):
        with closing(self.conectar()) as conexao:
            registro = conexao.execute("SELECT registro_json FROM vetorizacoes WHERE id = ?",
                                      (vetorizacao_id,)).fetchone()
            return json.loads(registro["registro_json"]) if registro is not None else None

    def _conferir_vetorizacao(self, conexao, vetorizacao_id, texto):
        if vetorizacao_id is None:
            return
        registro = conexao.execute("SELECT registro_json FROM vetorizacoes WHERE id = ?",
                                  (vetorizacao_id,)).fetchone()
        if registro is None:
            raise LookupError("Vetorização não encontrada. Importe o arquivo antes de buscar.")
        if _texto_vetorizado(json.loads(registro["registro_json"])) != texto:
            raise ErroPersistencia("O texto enviado difere do texto original da vetorização importada.")

    def registrar(self, relato, vetorizacao_id=None):
        agora = _agora()
        with closing(self.conectar()) as conexao, conexao:
            self._conferir_vetorizacao(conexao, vetorizacao_id, relato["texto"])
            registro = conexao.execute("""INSERT INTO relatos
                (texto, palavras, criado_em, atualizado_em, estado, vetorizacao_id)
                VALUES (?, ?, ?, ?, 'registrado', ?)""",
                (relato["texto"], relato["palavras"], agora, agora, vetorizacao_id))
            return registro.lastrowid

    def iniciar_busca(self, relato, busca_id, provedor, modelo, relato_id=None, vetorizacao_id=None):
        agora = _agora()
        with closing(self.conectar()) as conexao, conexao:
            self._conferir_vetorizacao(conexao, vetorizacao_id, relato["texto"])
            if relato_id is None:
                registro = conexao.execute("""INSERT INTO relatos
                    (texto, palavras, criado_em, atualizado_em, estado, busca_id, provedor, modelo, vetorizacao_id)
                    VALUES (?, ?, ?, ?, 'executando', ?, ?, ?, ?)""",
                    (relato["texto"], relato["palavras"], agora, agora, busca_id, provedor, modelo, vetorizacao_id))
                return registro.lastrowid
            registro = conexao.execute("SELECT texto, estado, vetorizacao_id FROM relatos WHERE id = ?", (relato_id,)).fetchone()
            if registro is None:
                raise LookupError("Relato não encontrado.")
            if registro["texto"] != relato["texto"]:
                raise ErroPersistencia("O texto enviado difere do relato salvo. Salve um novo registro.")
            if registro["estado"] != "registrado":
                raise FileExistsError("Este relato já possui uma busca. Inicie um novo registro para preservar o histórico.")
            if registro["vetorizacao_id"] is not None:
                if vetorizacao_id is not None and registro["vetorizacao_id"] != vetorizacao_id:
                    raise ErroPersistencia("Este relato está vinculado a outra vetorização. Salve um novo registro.")
                vetorizacao_id = registro["vetorizacao_id"]
            registro = conexao.execute("""UPDATE relatos SET estado = 'executando',
                busca_id = ?, provedor = ?, modelo = ?, atualizado_em = ?, vetorizacao_id = ?
                WHERE id = ? AND estado = 'registrado'""",
                (busca_id, provedor, modelo, agora, vetorizacao_id, relato_id))
            if registro.rowcount != 1:
                raise FileExistsError("Este relato já possui uma busca.")
            return relato_id

    def concluir(self, busca_id, resultado):
        with closing(self.conectar()) as conexao, conexao:
            registro = conexao.execute("SELECT id, texto, estado FROM relatos WHERE busca_id = ?", (busca_id,)).fetchone()
            if registro is None or registro["estado"] != "executando":
                raise ErroPersistencia("A busca não está disponível para salvar o resultado.")
            if not isinstance(resultado, dict):
                raise ErroPersistencia("O resultado da busca tem formato inválido.")
            seguro = {campo: resultado[campo] for campo in _CAMPOS_RESULTADO if campo in resultado}
            seguro["relato"] = validar_relato(registro["texto"])
            ordem = 0
            for grupo in ("ligacoes", "descartadas"):
                itens = resultado.get(grupo, [])
                if not isinstance(itens, list):
                    raise ErroPersistencia("O resultado não contém uma lista válida de relações.")
                seguro[grupo] = []
                for item in itens:
                    item = _relacao_conferida(item, registro["texto"])
                    seguro[grupo].append(item)
                    ordem += 1
                    conexao.execute("""INSERT INTO relacoes
                        (relato_id, ordem, trecho_relato, relato_inicio, relato_fim,
                         trecho_freud, freud_inicio, freud_fim, justificativa, bloco_id, situacao, dados_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (registro["id"], ordem, item["relato"]["texto"], item["relato"]["inicio"],
                         item["relato"]["fim"], item["freud"]["texto"], item["freud"]["inicio"],
                         item["freud"]["fim"], item["justificativa"], item["bloco_id"], item["situacao"], _json(item)))
            conexao.execute("""UPDATE relatos SET estado = 'concluido',
                resultado_json = ?, atualizado_em = ?, erro = NULL WHERE id = ?""",
                (_json(seguro), _agora(), registro["id"]))
        return json.loads(_json(seguro))

    def falhar(self, busca_id, mensagem):
        with closing(self.conectar()) as conexao, conexao:
            conexao.execute("""UPDATE relatos SET estado = 'erro', erro = ?, atualizado_em = ?
                WHERE busca_id = ? AND estado = 'executando'""", (mensagem, _agora(), busca_id))

    def listar(self, limite=20, antes_id=None):
        with closing(self.conectar()) as conexao:
            registros = conexao.execute("""SELECT r.id, r.texto, r.palavras, r.criado_em,
                r.estado, r.provedor, r.modelo, r.busca_id, r.vetorizacao_id,
                (SELECT COUNT(*) FROM relacoes WHERE relato_id = r.id) AS total_relacoes
                FROM relatos r WHERE (? IS NULL OR r.id < ?) ORDER BY r.id DESC LIMIT ?""",
                (antes_id, antes_id, limite + 1)).fetchall()
            pagina = [dict(registro) for registro in registros[:limite]]
            return {"relatos": pagina, "proximo_antes_id": pagina[-1]["id"] if len(registros) > limite else None}

    def obter(self, relato_id):
        with closing(self.conectar()) as conexao:
            registro = conexao.execute("SELECT * FROM relatos WHERE id = ?", (relato_id,)).fetchone()
            if registro is None:
                return None
            dados = dict(registro)
            bruto = dados.pop("resultado_json")
            dados["resultado"] = json.loads(bruto) if bruto is not None else None
            dados["relacoes"] = []
            for relacao in conexao.execute("SELECT * FROM relacoes WHERE relato_id = ? ORDER BY ordem", (relato_id,)):
                relacao = dict(relacao)
                relacao["dados"] = json.loads(relacao.pop("dados_json"))
                dados["relacoes"].append(relacao)
            return dados

    def obter_busca(self, busca_id):
        with closing(self.conectar()) as conexao:
            registro = conexao.execute("SELECT id FROM relatos WHERE busca_id = ?", (busca_id,)).fetchone()
        if registro is None:
            return None
        relato = self.obter(registro["id"])
        estado = "erro" if relato["estado"] == "interrompido" else relato["estado"]
        tarefa = {"id": busca_id, "relato_id": relato["id"], "vetorizacao_id": relato["vetorizacao_id"], "estado": estado,
                  "etapa": "Busca concluída" if estado == "concluido" else "Busca interrompida"}
        if estado == "concluido":
            tarefa["resultado"] = relato["resultado"]
        elif relato["erro"]:
            tarefa["erro"] = relato["erro"]
        return tarefa
