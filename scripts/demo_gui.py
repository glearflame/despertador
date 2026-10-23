#!/usr/bin/env python3
"""Roteiro de demonstração usado para gravar docs/screenshots/demo.gif.

Não é mock: abre a GUI real, preenche o diálogo "Novo alarme" de verdade,
troca de aba, inicia o cronômetro e registra voltas. Roda sob Xvfb e o
ffmpeg grava a tela. Use um XDG_CONFIG_HOME temporário para não sujar os
alarmes do usuário.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import despertador_gui as gui  # noqa: E402


def _preencher_editor(root: tk.Tk) -> None:
    """Localiza o diálogo aberto, preenche horário/rótulo e salva."""
    for janela in root.winfo_children():
        if not isinstance(janela, tk.Toplevel):
            continue
        entradas = []
        botoes = []
        pilha = list(janela.winfo_children())
        while pilha:
            widget = pilha.pop()
            pilha.extend(widget.winfo_children())
            if isinstance(widget, ttk.Entry):
                entradas.append(widget)
            elif isinstance(widget, ttk.Button):
                botoes.append(widget)
        for entrada in entradas:
            largura = int(entrada.cget("width") or 0)
            if largura == 14:  # horário
                entrada.delete(0, "end")
                entrada.insert(0, "07:30")
            elif largura >= 30:  # rótulo
                entrada.delete(0, "end")
                entrada.insert(0, "Acordar")
        for botao in botoes:
            if botao.cget("text") == "Salvar":
                botao.invoke()
                return


def main() -> int:
    root = tk.Tk()
    app = gui.Aplicativo(root, aba_inicial=0)

    root.after(300, app._novo_alarme)
    root.after(1200, lambda: _preencher_editor(root))
    root.after(2300, lambda: app.abas.select(1))       # relógio mundial
    root.after(4300, lambda: app.abas.select(2))       # relógio/cronômetro
    root.after(4700, app._alternar_cronometro)
    root.after(5400, app._registrar_volta)
    root.after(6400, app._registrar_volta)
    root.after(8200, app.fechar)

    root.protocol("WM_DELETE_WINDOW", app.fechar)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
