"""Transporte HTTPS dos provedores de justificativas, independente do Lingua.

Preserva a validação de certificados, os limites e a conferência das respostas.
A chave é usada somente durante a chamada e não é persistida.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


VARIAVEL_CHAVE = "AGENTE_ANALISTA_API_KEY"
TIMEOUT_SEGUNDOS = 60
LIMITE_RESPOSTA_BYTES = 1024 * 1024
LIMITE_PEDIDO_BYTES = 256 * 1024
LIMITE_ERRO_HTTP_BYTES = 16 * 1024

_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}


class ErroAPINarrativa(ValueError):
    """Falha de geração com mensagem segura para apresentação ao usuário."""


class _SemRedirecionamento(HTTPRedirectHandler):
    """Impeça que Authorization seja enviado a qualquer destino redirecionado."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _contexto_https():
    """Some as CA do Certifi às nativas, mantendo a verificação TLS integral.

    Acrescenta as CA do Certifi aos certificados nativos do Python e respeita
    os caminhos SSL_CERT_FILE/SSL_CERT_DIR e os arquivos de CA configurados.
    """
    try:
        contexto = ssl.create_default_context()
        try:
            import certifi
        except ImportError:
            # As CA nativas continuam disponíveis quando Certifi não está instalado.
            pass
        else:
            contexto.load_verify_locations(cafile=certifi.where())
        personalizado = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE")
        if personalizado:
            contexto.load_verify_locations(cafile=personalizado)
        return contexto
    except (OSError, ValueError):
        raise ErroAPINarrativa("Não foi possível carregar os certificados HTTPS do Python. "
                               "Confira a instalação e os caminhos de certificados configurados.") from None


def _erro_conexao(erro):
    """Classifique somente tipos e sinais conhecidos; nunca exponha detalhes brutos."""
    motivo = erro.reason if isinstance(erro, URLError) else erro
    if isinstance(motivo, ssl.SSLCertVerificationError):
        mensagem = ("O Python não conseguiu verificar o certificado HTTPS do provedor. "
                    "Confira os certificados do Python e execute o diagnóstico de conexão.")
    elif isinstance(motivo, ssl.SSLError):
        mensagem = ("Não foi possível estabelecer a conexão HTTPS com o provedor. "
                    "Confira os certificados, a rede e o diagnóstico de conexão.")
    elif isinstance(motivo, (socket.timeout, TimeoutError)):
        mensagem = "O provedor demorou demais para responder. Solicite a geração novamente."
    elif isinstance(motivo, socket.gaierror):
        mensagem = "Não foi possível localizar o endereço do provedor. Verifique a conexão e o DNS da rede."
    elif isinstance(motivo, OSError) and "tunnel connection failed" in str(motivo).lower():
        mensagem = "O proxy da rede recusou a conexão HTTPS com o provedor. Confira o proxy, a VPN ou tente outra rede."
    else:
        mensagem = "Não foi possível conectar ao provedor. Verifique a conexão e execute o diagnóstico de conexão."
    return ErroAPINarrativa(mensagem)


def _provedor_valido(provedor: str) -> str:
    if not isinstance(provedor, str) or provedor not in _ENDPOINTS:
        raise ErroAPINarrativa("Escolha um provedor suportado, OpenRouter ou OpenAI.")
    return provedor


def _erro_http(status: int) -> ErroAPINarrativa:
    if status in (401, 403):
        mensagem = f"O provedor recusou a autenticação. Verifique {VARIAVEL_CHAVE} e as permissões da conta."
    elif status == 402:
        mensagem = "A conta do provedor não possui créditos suficientes para esta geração."
    elif status == 429:
        mensagem = "O provedor atingiu o limite de solicitações. Aguarde antes de solicitar novamente."
    elif status in (301, 302, 303, 307, 308):
        mensagem = "O provedor tentou redirecionar a solicitação. A geração foi interrompida para proteger a credencial."
    elif status in (400, 404, 422):
        mensagem = "O provedor não aceitou o pedido. Verifique o modelo selecionado e sua disponibilidade."
    else:
        mensagem = "O provedor não concluiu a geração. Tente novamente mais tarde."
    return ErroAPINarrativa(mensagem)


def _erro_pedido(erro, *, estruturado=False):
    """Classifique uma rejeição sem reproduzir dados ou mensagens do provedor."""
    partes = []
    try:
        dados = erro.read(LIMITE_ERRO_HTTP_BYTES + 1)
        if isinstance(dados, bytes) and len(dados) <= LIMITE_ERRO_HTTP_BYTES:
            resposta = json.loads(dados.decode("utf-8"))
            detalhe = resposta.get("error") if isinstance(resposta, dict) else None
            if isinstance(detalhe, dict):
                for campo in ("code", "type", "param", "message"):
                    if isinstance(detalhe.get(campo), str):
                        partes.append(detalhe[campo].casefold())
                metadados = detalhe.get("metadata")
                if isinstance(metadados, dict) and isinstance(metadados.get("raw"), str):
                    partes.append(metadados["raw"].casefold())
    except (OSError, ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        pass
    finally:
        erro.close()
    detalhe = " ".join(partes)
    if any(texto in detalhe for texto in (
            "context_length_exceeded", "maximum context length", "context window",
            "context length", "too many input tokens", "input is too long")):
        mensagem = (
            "O contexto enviado excede a janela do modelo escolhido. "
            "Selecione um modelo com capacidade para contextos maiores; as fontes não foram truncadas."
        )
    elif any(texto in detalhe for texto in (
            "unsupported_response_format", "invalid_json_schema", "no endpoints found that support",
            "does not support json", "doesn't support json", "unsupported json")) or (
            "response_format" in detalhe and re.search(r"not supported|unsupported|not available", detalhe)):
        mensagem = (
            "Não há uma rota disponível que aceite o formato de resposta deste modelo. "
            "Confira as permissões e as preferências de provedores da conta ou selecione outro modelo."
        )
    elif any(texto in detalhe for texto in (
            "model_not_found", "invalid model", "not a valid model", "not found for model",
            "no endpoints found for", "unknown model", "model does not exist")):
        mensagem = "O modelo não foi encontrado ou está indisponível. Confira seu identificador no provedor selecionado."
    elif re.search(r"unsupported (?:parameter|argument)|(?:parameter|argument).*(?:not supported|unsupported)", detalhe):
        mensagem = "A rota do modelo rejeitou um parâmetro do pedido. Selecione outra rota ou outro modelo disponível."
    elif estruturado:
        mensagem = (
            "O provedor não aceitou o pedido estruturado. Verifique a disponibilidade "
            "do modelo selecionado e seu suporte ao formato de resposta solicitado."
        )
    else:
        mensagem = "O provedor não aceitou o pedido. Verifique o modelo selecionado e sua disponibilidade."
    return ErroAPINarrativa(f"{mensagem} (HTTP {erro.code}).")


def _interpretar_resposta(dados: bytes) -> str:
    if len(dados) > LIMITE_RESPOSTA_BYTES:
        raise ErroAPINarrativa("A resposta do provedor excedeu o tamanho permitido.")
    try:
        resposta = json.loads(dados.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ErroAPINarrativa("O provedor retornou uma resposta inválida. Nenhum texto foi substituído.") from None
    if not isinstance(resposta, dict) or resposta.get("error"):
        raise ErroAPINarrativa("O provedor não concluiu a geração. Nenhum texto foi substituído.")
    escolhas = resposta.get("choices")
    if not isinstance(escolhas, list) or len(escolhas) != 1 or not isinstance(escolhas[0], dict):
        raise ErroAPINarrativa("O provedor retornou uma resposta inválida. Nenhum texto foi substituído.")
    escolha = escolhas[0]
    mensagem = escolha.get("message")
    if not isinstance(mensagem, dict):
        raise ErroAPINarrativa("O provedor retornou uma resposta inválida. Nenhum texto foi substituído.")
    if mensagem.get("refusal") or escolha.get("finish_reason") == "content_filter":
        raise ErroAPINarrativa("O provedor recusou a geração deste conteúdo. Nenhum texto foi substituído.")
    if escolha.get("finish_reason") == "length":
        raise ErroAPINarrativa("O provedor interrompeu a geração por limite de tamanho. Solicite uma nova versão.")
    if escolha.get("finish_reason") != "stop":
        raise ErroAPINarrativa("O provedor não concluiu o texto solicitado. Nenhum texto foi substituído.")
    texto = mensagem.get("content")
    if not isinstance(texto, str) or not texto.strip():
        raise ErroAPINarrativa("O provedor retornou um texto vazio ou inválido. Nenhum texto foi substituído.")
    return texto.strip()


def _serializar_pedido(*, provedor, modelo, mensagens, formato_resposta=None) -> bytes:
    """Construa o mesmo pedido usado no envio e no cálculo do limite de contexto."""
    pedido = {"model": modelo, "messages": mensagens, "stream": False}
    if provedor == "openai":
        pedido["n"] = 1
    if formato_resposta is not None:
        if not isinstance(formato_resposta, dict) or formato_resposta.get("type") not in ("json_schema", "json_object"):
            raise ErroAPINarrativa("O formato estruturado da resposta está inválido.")
        pedido["response_format"] = formato_resposta
        if provedor == "openrouter":
            # Encaminhe somente a provedores que respeitam o formato solicitado.
            pedido["provider"] = {"require_parameters": True}
    try:
        return json.dumps(pedido, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (UnicodeEncodeError, ValueError, TypeError, RecursionError):
        raise ErroAPINarrativa("O contexto da geração contém texto inválido.") from None


def _obter_chave_api(chave_api=None):
    """Valide a chave desta chamada; None consulta somente a variável do agente."""
    chave = os.environ.get(VARIAVEL_CHAVE, "") if chave_api is None else chave_api
    if not isinstance(chave, str):
        raise ErroAPINarrativa("Informe uma chave de API válida.")
    chave = chave.strip()
    if not chave:
        raise ErroAPINarrativa(f"Configure {VARIAVEL_CHAVE} ou informe a chave de API antes de gerar o texto.")
    if len(chave) > 4096 or not chave.isascii() or any(not 33 <= ord(caractere) <= 126 for caractere in chave):
        raise ErroAPINarrativa(f"A configuração de {VARIAVEL_CHAVE} ou da chave de API é inválida. Verifique a credencial.")
    return chave


def _enviar_mensagens(*, provedor, modelo, construtor, contexto, nome_contexto="narrativa", formato_resposta=None,
                     chave_api=None) -> str:
    """Compartilhe autenticação e transporte HTTPS, mantendo prompts independentes."""
    provedor = _provedor_valido(provedor)
    if not isinstance(modelo, str) or not modelo.strip() or len(modelo) > 200:
        raise ErroAPINarrativa("Informe um modelo válido para a geração.")
    chave = _obter_chave_api(chave_api)
    try:
        mensagens = construtor(**contexto)
    except TypeError:
        raise ErroAPINarrativa("O contexto da geração está incompleto ou é inválido.") from None
    corpo = _serializar_pedido(provedor=provedor, modelo=modelo.strip(), mensagens=mensagens,
                              formato_resposta=formato_resposta)
    if len(corpo) > LIMITE_PEDIDO_BYTES:
        raise ErroAPINarrativa(f"O contexto da {nome_contexto} excedeu o tamanho permitido para geração.")
    pedido = Request(_ENDPOINTS[provedor], data=corpo, method="POST", headers={
        "Authorization": f"Bearer {chave}", "Content-Type": "application/json", "Accept": "application/json",
    })
    # Preserve a validação TLS e as CA locais; recuse redirecionamentos antes de
    # criar qualquer novo pedido com Authorization.
    cliente = build_opener(_SemRedirecionamento(), HTTPSHandler(context=_contexto_https()))
    try:
        with cliente.open(pedido, timeout=TIMEOUT_SEGUNDOS) as resposta:
            dados = resposta.read(LIMITE_RESPOSTA_BYTES + 1)
    except HTTPError as erro:
        if erro.code in (400, 404, 422):
            raise _erro_pedido(erro, estruturado=formato_resposta is not None) from None
        erro.close()
        raise _erro_http(erro.code) from None
    except (URLError, OSError) as erro:
        raise _erro_conexao(erro) from None
    return _interpretar_resposta(dados)
