"""Adaptador determinístico exclusivo de testes; nenhuma inferência neural."""

from copy import deepcopy

class AdaptadorControlado:
    """Contagem por caracteres e dimensão variável: exclusivo deste teste."""

    def __init__(self, perfil, *, falhar=False):
        self.perfil = deepcopy(perfil)
        self.falhar = falhar

    def descrever(self):
        return {
            "backend": self.perfil["backend"], "versao_backend": "teste-controlado-1",
            "modelo": self.perfil["modelo"], "tokenizador": self.perfil["tokenizador"],
            "dimensao": 4, "limite_tokens": 160,
            "pooling": self.perfil["pooling"], "dispositivo": self.perfil["dispositivo"],
            "precisao_inferencia": self.perfil["precisao_inferencia"],
            "bibliotecas": {"fixture": "teste-contrato-sem-inferencia-neural"},
        }

    def tokenizar(self, texto):
        return {
            "input_ids": [1] + [ord(char) + 10 for char in texto] + [2],
            "attention_mask": [1] * (len(texto) + 2),
            "special_tokens_mask": [1] + [0] * len(texto) + [1],
            "offsets": [[0, 0]] + [[i, i + 1] for i in range(len(texto))] + [[0, 0]],
        }

    def gerar(self, entradas):
        if self.falhar:
            raise ValueError("Falha controlada do adaptador de teste.")
        return [[1.0, float(len(texto) + 1), float(sum(map(ord, texto)) % 79 + 1), 3.0] for texto in entradas]
