"""Contratos do notebook: origem literal, ambiente portátil e saídas rastreáveis."""

import ast
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from colab_notebook import ErroNotebookColab, gerar_notebook_colab
from fixtures_contexto import construir_regras, INSTANTE, TEXTO
from fixtures_regras import token
from fixtures_vetorizacao import AdaptadorControlado
from unidades_contexto import construir_unidades_contexto
from vetorizacao import validar_vetorizacao


def canonico(valor):
    return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(conteudo):
    return hashlib.sha256(conteudo).hexdigest()


def celula(notebook, identificador):
    return "".join(next(c for c in notebook["cells"] if c["id"] == identificador)["source"])


def capsula(notebook):
    arvore = ast.parse(celula(notebook, "restaurar-origem"))
    atribuicoes = {no.targets[0].id: ast.literal_eval(no.value)
                   for no in arvore.body if isinstance(no, ast.Assign)
                   and isinstance(no.targets[0], ast.Name)
                   and no.targets[0].id in ("CAPSULA_BASE64", "CAPSULA_SHA256")}
    bruto = base64.b64decode(atribuicoes["CAPSULA_BASE64"], validate=True)
    return json.loads(bruto), bruto, atribuicoes


class NotebookColabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexto = construir_unidades_contexto(
            construir_regras(normalizar=True), execucao_id="contexto-colab-fixture",
            registrado_em=INSTANTE,
        )

    def gerar(self, contexto=None, **opcoes):
        return gerar_notebook_colab(
            self.contexto if contexto is None else contexto,
            notebook_id="notebook-colab-fixture", registrado_em=INSTANTE, **opcoes,
        )

    def restaurar(self, notebook=None):
        temporario = tempfile.TemporaryDirectory()
        self.addCleanup(temporario.cleanup)
        anterior = Path.cwd()
        try:
            os.chdir(temporario.name)
            globais = {}
            exec(compile(celula(self.gerar() if notebook is None else notebook,
                               "restaurar-origem"), "restaurar-origem", "exec"), globais)
        finally:
            os.chdir(anterior)
        return globais

    def test_json_roundtrip_nbformat_celulas_sem_outputs(self):
        notebook = json.loads(json.dumps(self.gerar(), ensure_ascii=False, allow_nan=False))
        self.assertEqual((notebook["nbformat"], notebook["nbformat_minor"]), (4, 5))
        ids = [c["id"] for c in notebook["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        for item in notebook["cells"]:
            if item["cell_type"] == "code":
                self.assertIsNone(item["execution_count"])
                self.assertEqual(item["outputs"], [])
                compile("".join(item["source"]), item["id"], "exec")
        dados, bruto, atribuicoes = capsula(notebook)
        self.assertEqual(sha(bruto), notebook["metadata"]["lingua"]["capsula_sha256"])
        self.assertEqual(sha(bruto), atribuicoes["CAPSULA_SHA256"])
        self.assertEqual(dados["contexto"], self.contexto)

    def test_reproduzivel_data_identificador_fornecidos(self):
        self.assertEqual(self.gerar(), self.gerar())
        recebido = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        notebook = gerar_notebook_colab(self.contexto, notebook_id="reproduzivel", registrado_em=recebido)
        self.assertEqual(notebook["metadata"]["lingua"]["registrado_em"], recebido.isoformat())
        self.assertNotEqual(gerar_notebook_colab(self.contexto)["metadata"]["lingua"]["notebook_id"],
                            gerar_notebook_colab(self.contexto)["metadata"]["lingua"]["notebook_id"])

    def test_preserva_original_trabalho_metadados_e_toda_cadeia(self):
        contexto = deepcopy(self.contexto)
        preparacao = contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
        preparacao["metadados_origem"].update(
            recebido_em=INSTANTE, nota='"""\n__import__("os").system("touch PWNED")\n</script>\n👩🏽‍💻 cafe\u0301',
        )
        documento = {"id": contexto["documento_id"], "content": TEXTO, "created_at": INSTANTE}
        antes = deepcopy(contexto)
        dados, _, _ = capsula(self.gerar(contexto, documento=documento))
        self.assertEqual(dados["contexto"], contexto)
        self.assertEqual(dados["documento"], documento)
        self.assertEqual(dados["documento_sha256"], sha(canonico(documento)))
        exportada = dados["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
        self.assertEqual(exportada["original"]["texto"].encode("utf-8"), TEXTO.encode("utf-8"))
        self.assertEqual(exportada["trabalho"]["texto"], TEXTO.replace("\r\n", "\n"))
        self.assertEqual(contexto, antes)
        for item in self.gerar(contexto)["cells"]:
            self.assertNotIn('__import__("os").system("touch PWNED")', "".join(item["source"]))

    def test_emoji_unicode_combinante_e_linhas_vazias(self):
        texto = "  Oi 👩🏽‍💻 cafe\u0301.\r\n\r\n"
        linhas = [token("Oi", "oi", "NOUN", 0, "ROOT"),
                  token("👩", "👩", "SYM", 0, "dep"), token("🏽\u200d", "🏽\u200d", "SYM", 0, "dep"),
                  token("💻", "💻", "SYM", 0, "dep"), token("cafe\u0301.", "cafe\u0301.", "NOUN", 0, "dep")]
        contexto = construir_unidades_contexto(construir_regras(texto=texto, linhas=linhas))
        dados, _, _ = capsula(self.gerar(contexto))
        preparacao = dados["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
        self.assertEqual(preparacao["original"]["texto"], texto)
        self.assertEqual(preparacao["trabalho"]["texto"], texto)
        self.assertEqual(preparacao["original"]["sha256"], sha(texto.encode("utf-8")))

    def test_fontes_reais_e_dependencias_pinned_sem_carregar_modelo(self):
        with patch("modelo_embeddings.baixar_modelo") as baixar, patch("modelo_embeddings.criar_adaptador") as criar:
            notebook = self.gerar()
        baixar.assert_not_called()
        criar.assert_not_called()
        dados, _, _ = capsula(notebook)
        raiz = Path(__file__).resolve().parents[1]
        for fonte in dados["fontes"]:
            conteudo = base64.b64decode(fonte["conteudo_base64"], validate=True)
            self.assertEqual(sha(conteudo), fonte["sha256"])
            if (raiz / fonte["nome"]).exists():
                self.assertEqual(conteudo, (raiz / fonte["nome"]).read_bytes())
            compile(conteudo.decode("utf-8"), fonte["nome"], "exec")
        self.assertEqual(set(dados["dependencias"]),
                         {"torch", "transformers", "sentence-transformers", "huggingface-hub"})
        self.assertTrue(all("==" in item for item in dados["dependencias"].values()))

    def test_configuracao_local_nao_vincula_colab_ao_mac(self):
        notebook = self.gerar(perfil_id="e5_conteudo", opcoes={"dispositivo": "mps", "lote": 7,
                                                                            "cache_modelos": "/tmp/modelos-mac"})
        dados, _, _ = capsula(notebook)
        self.assertEqual(dados["perfil_exportado"]["dispositivo"], "mps")
        self.assertEqual(dados["perfil_exportado"]["prefixo"], "passage: ")
        self.assertEqual(dados["opcoes_solicitadas"]["cache_modelos"], "/tmp/modelos-mac")
        self.assertIn('DISPOSITIVO = "auto"', celula(notebook, "configurar"))
        self.assertIn("https://download.pytorch.org/whl/cpu", celula(notebook, "instalar"))
        self.assertIn('"--target"', celula(notebook, "instalar"))

    def test_instalador_cpu_fallback_isolado_e_troca_de_wheel_cuda(self):
        notebook = self.gerar()
        for gpu in (False, True):
            with self.subTest(gpu=gpu):
                globais = self.restaurar(notebook)
                exec(compile(celula(notebook, "configurar"), "configurar", "exec"), globais)
                if not gpu:
                    # Um venv interrompido pode ter o executável, mas ainda não ter pip.
                    python_parcial = globais["PASTA_LINGUA"] / "ambiente" / "bin" / "python"
                    python_parcial.parent.mkdir(parents=True)
                    python_parcial.touch()
                def processo_controlado(comando, **kwargs):
                    if "--version" in comando:
                        return subprocess.CompletedProcess(comando, 1, stdout="", stderr="sem pip")
                    if "venv" in comando:
                        return subprocess.CompletedProcess(comando, 0 if gpu else 1)
                    if comando[0] == "nvidia-smi":
                        return subprocess.CompletedProcess(comando, 0, stdout="GPU controlada\n")
                    if "-c" in comando:
                        return subprocess.CompletedProcess(comando, 0, stdout="2.10.0+cpu\n")
                    return subprocess.CompletedProcess(comando, 0)
                with patch("subprocess.run", side_effect=processo_controlado) as chamadas, \
                        patch("shutil.which", return_value="/usr/bin/nvidia-smi" if gpu else None):
                    exec(compile(celula(notebook, "instalar"), "instalar", "exec"), globais)
                registros = chamadas.call_args_list
                torch = next(c.args[0] for c in registros if any(str(a).startswith("torch==") for a in c.args[0]))
                self.assertEqual(torch[0], str(globais["PYTHON_VETORIZACAO"]))
                if gpu:
                    self.assertNotIn("--index-url", torch)
                    self.assertIn("torch==2.10.0", torch)
                    self.assertIn("--force-reinstall", torch)
                else:
                    self.assertIn("torch==2.10.0+cpu", torch)
                    self.assertIn("https://download.pytorch.org/whl/cpu", torch)
                    bootstrap = next(c.args[0] for c in registros if "--target" in c.args[0])
                    self.assertEqual(bootstrap[0], sys.executable)
                    self.assertEqual(Path(bootstrap[bootstrap.index("--target") + 1]).parent, globais["PASTA_LINGUA"])
                    virtualenv = next(c for c in registros if "virtualenv" in c.args[0])
                    self.assertIn("--clear", virtualenv.args[0])
                    self.assertEqual(Path(virtualenv.kwargs["env"]["PYTHONPATH"]).parent, globais["PASTA_LINGUA"])
                dependencias = next(c.args[0] for c in registros if "transformers==4.57.3" in c.args[0])
                self.assertEqual(dependencias[0], str(globais["PYTHON_VETORIZACAO"]))

    def test_erro_explicito_origem_adulterada(self):
        contexto = deepcopy(self.contexto)
        contexto["unidades"][0]["foco"]["texto"] = "Outro conteúdo."
        with self.assertRaises(ErroNotebookColab):
            self.gerar(contexto)

    def test_erros_documento_perfil_data_e_identificador(self):
        documento = {"id": self.contexto["documento_id"], "content": TEXTO, "created_at": INSTANTE}
        for alteracao in ({"id": "outro-documento"}, {"content": TEXTO.strip()}, {"created_at": ""}):
            with self.subTest(alteracao=alteracao), self.assertRaises(ErroNotebookColab):
                self.gerar(documento={**documento, **alteracao})
        for extra in ({"opcoes": {"backend": "fastembed"}}, {"opcoes": {"lote": True}},
                      {"opcoes": {"desconhecida": 1}}, {"perfil_id": "inexistente"}):
            with self.subTest(extra=extra), self.assertRaises(ErroNotebookColab):
                self.gerar(**extra)
        for extra in ({"notebook_id": ""}, {"notebook_id": False}, {"registrado_em": "2026-10-08"},
                      {"registrado_em": "data"}, {"registrado_em": 5}):
            with self.subTest(extra=extra), self.assertRaises(ErroNotebookColab):
                gerar_notebook_colab(self.contexto, **extra)

    def test_data_ingestao_conferida_se_existente(self):
        contexto = deepcopy(self.contexto)
        contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["metadados_origem"]["recebido_em"] = INSTANTE
        documento = {"id": contexto["documento_id"], "content": TEXTO, "created_at": "2026-10-09T00:00:00Z"}
        with self.assertRaisesRegex(ErroNotebookColab, "data da ingestão"):
            self.gerar(contexto, documento=documento)

    def test_restauracao_executa_validador_com_fontes_autocontidas(self):
        globais = self.restaurar()
        pasta = globais["PASTA_LINGUA"]
        self.assertEqual(json.loads(globais["CAMINHO_CAPSULA"].read_bytes())["contexto"], self.contexto)
        self.assertTrue((pasta / "conferir_origem.py").exists())
        self.assertTrue((pasta / "vetorizacao.py").exists())
        self.assertFalse((pasta / "instance").exists())  # Sem modelos ou inferência.
        globais["conferir_arquivos"]()

    def test_restauracao_recusa_hash_adulterado_antes_de_escrever(self):
        notebook = self.gerar()
        _, bruto, atribuicoes = capsula(notebook)
        adulterado = bruto.replace(b'"notebook-colab-fixture"', b'"notebook-adulterado"')
        self.assertNotEqual(adulterado, bruto)
        codigo = celula(notebook, "restaurar-origem").replace(
            repr(atribuicoes["CAPSULA_BASE64"]), repr(base64.b64encode(adulterado).decode("ascii")))
        with tempfile.TemporaryDirectory() as diretorio:
            anterior = Path.cwd()
            try:
                os.chdir(diretorio)
                with self.assertRaisesRegex(ValueError, "alterada"):
                    exec(compile(codigo, "restaurar-origem", "exec"), {})
                self.assertEqual(list(Path(diretorio).iterdir()), [])
            finally:
                os.chdir(anterior)

    def test_fontes_adulteradas_recusadas_antes_modelo(self):
        globais = self.restaurar()
        fonte = globais["PASTA_LINGUA"] / "vetorizacao.py"
        fonte.write_bytes(fonte.read_bytes() + b"\n# fonte alterada\n")
        with self.assertRaisesRegex(ValueError, "Fonte restaurada alterada"):
            globais["conferir_arquivos"]()

    def test_allowlist_rejeita_path_traversal_mesmo_com_hash_recalculado(self):
        notebook = self.gerar()
        dados, _, atribuicoes = capsula(notebook)
        dados["fontes"][0]["nome"] = "../arquivo.py"
        bruto = canonico(dados)
        codigo = celula(notebook, "restaurar-origem").replace(
            repr(atribuicoes["CAPSULA_BASE64"]), repr(base64.b64encode(bruto).decode("ascii"))).replace(
            repr(atribuicoes["CAPSULA_SHA256"]), repr(sha(bruto)))
        with self.assertRaisesRegex(ValueError, "Lista de fontes inválida"):
            exec(compile(codigo, "restaurar-origem", "exec"), {})

    def test_execucao_controlada_exporta_json_zip_textos_e_preserva_anterior(self):
        # Adaptador controlado apenas neste teste; o notebook entregue sempre usa o modelo real.
        globais = self.restaurar()
        pasta = globais["PASTA_LINGUA"]
        anterior = list(sys.path)
        modulo_anterior = sys.modules.pop("conferir_origem", None)
        self.addCleanup(lambda: sys.modules.pop("conferir_origem", None))
        try:
            sys.path.insert(0, str(pasta))
            runtime = {"__name__": "runtime_controlado_de_teste"}
            exec(compile((pasta / "executar_etapa09.py").read_bytes(), "executar_etapa09.py", "exec"), runtime)
            configuracao = {"dispositivo": "auto", "lote": 2, "cache_modelos": str(pasta / "cache"), "opcoes_modelo": {}}
            torch_teste = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
            with patch.dict(sys.modules, {"torch": torch_teste}), \
                    patch("modelo_embeddings.baixar_modelo", return_value={"modo": "fixture_sem_download"}), \
                    patch("modelo_embeddings.criar_adaptador", side_effect=AdaptadorControlado):
                primeiro = runtime["executar"](globais["CAMINHO_CAPSULA"], globais["CAPSULA_SHA256"], configuracao)
                bytes_primeiro = Path(primeiro["pacote"]).read_bytes()
                segundo = runtime["executar"](globais["CAMINHO_CAPSULA"], globais["CAPSULA_SHA256"], configuracao)
            self.assertNotEqual(primeiro["execucao_id"], segundo["execucao_id"])
            self.assertEqual(Path(primeiro["pacote"]).read_bytes(), bytes_primeiro)
            with zipfile.ZipFile(primeiro["pacote"]) as pacote:
                self.assertEqual(set(pacote.namelist()), {"vetorizacao.json", "contexto_etapa08.json",
                                                        "manifesto_colab.json", "original.txt", "trabalho.txt"})
                resultado = json.loads(pacote.read("vetorizacao.json"))
                manifesto = json.loads(pacote.read("manifesto_colab.json"))
                self.assertEqual(resultado["contexto"], self.contexto)
                self.assertTrue(validar_vetorizacao(resultado)["pronto"])
                self.assertEqual(resultado["perfil"]["dispositivo"], "cpu")
                self.assertEqual(pacote.read("original.txt"), TEXTO.encode("utf-8"))
                self.assertEqual(pacote.read("trabalho.txt"), TEXTO.replace("\r\n", "\n").encode("utf-8"))
                self.assertEqual(manifesto["notebook_id"], "notebook-colab-fixture")
                for nome, metadados in manifesto["arquivos"].items():
                    self.assertEqual(sha(pacote.read(nome)), metadados["sha256"])
            self.assertEqual(sha(bytes_primeiro), primeiro["pacote_sha256"])
        finally:
            sys.path[:] = anterior
            sys.modules.pop("conferir_origem", None)
            if modulo_anterior is not None:
                sys.modules["conferir_origem"] = modulo_anterior

    def test_gpu_auto_cpu_forcada_e_erro_cuda_indisponivel(self):
        # Disponibilidade controlada testa a escolha; não simula uma prova de inferência em GPU.
        globais = self.restaurar()
        pasta = globais["PASTA_LINGUA"]
        anterior = list(sys.path)
        modulo_anterior = sys.modules.pop("conferir_origem", None)
        try:
            sys.path.insert(0, str(pasta))
            runtime = {"__name__": "runtime_controlado_de_teste"}
            exec(compile((pasta / "executar_etapa09.py").read_bytes(), "executar_etapa09.py", "exec"), runtime)
            configuracao = {"dispositivo": "auto", "lote": 3,
                            "cache_modelos": str(pasta / "cache"), "opcoes_modelo": {}}
            gpu_teste = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
            with patch.dict(sys.modules, {"torch": gpu_teste}), \
                    patch("modelo_embeddings.baixar_modelo", return_value={"modo": "fixture_sem_download"}), \
                    patch("modelo_embeddings.criar_adaptador", side_effect=AdaptadorControlado):
                gpu = runtime["executar"](globais["CAMINHO_CAPSULA"], globais["CAPSULA_SHA256"], configuracao)
                cpu = runtime["executar"](globais["CAMINHO_CAPSULA"], globais["CAPSULA_SHA256"],
                                          {**configuracao, "dispositivo": "cpu"})
            self.assertEqual(json.loads(Path(gpu["vetorizacao_json"]).read_bytes())["perfil"]["dispositivo"], "cuda:0")
            self.assertEqual(json.loads(Path(cpu["vetorizacao_json"]).read_bytes())["perfil"]["dispositivo"], "cpu")
            sem_gpu = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
            with patch.dict(sys.modules, {"torch": sem_gpu}), patch("modelo_embeddings.baixar_modelo") as baixar:
                with self.assertRaisesRegex(ValueError, "GPU CUDA solicitada, mas indisponível"):
                    runtime["executar"](globais["CAMINHO_CAPSULA"], globais["CAPSULA_SHA256"],
                                        {**configuracao, "dispositivo": "cuda:0"})
                baixar.assert_not_called()
        finally:
            sys.path[:] = anterior
            sys.modules.pop("conferir_origem", None)
            if modulo_anterior is not None:
                sys.modules["conferir_origem"] = modulo_anterior


if __name__ == "__main__":
    unittest.main()
