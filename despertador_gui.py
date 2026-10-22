#!/usr/bin/env python3
"""Aplicativo desktop do despertador (Tkinter + biblioteca padrão).

Três abas: alarmes, relógio mundial e relógio/cronômetro. Toda a lógica de
negócio fica no pacote `despertador`; aqui só há montagem de widgets.

Uso:
    ./despertador_gui.py
    ./despertador_gui.py --debug   # registra eventos no stderr
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from despertador import core, inhibit
from despertador import worldclock as wc
from despertador.timers import Cronometro, Temporizador, formatar_tempo, parse_duracao

LOG = logging.getLogger("despertador.gui")

COR_FUNDO = "#f2efe7"
COR_CARTAO = "#ffffff"
COR_DESTAQUE = "#2f4858"
COR_TEXTO = "#22333b"
COR_SUAVE = "#68767d"
COR_ALERTA = "#a63a4a"
COR_BORDA = "#dcd6c8"

FONTE = "DejaVu Sans"
FONTE_MONO = "DejaVu Sans Mono"


class Aplicativo:
    """Janela principal do despertador."""

    def __init__(self, root: tk.Tk, aba_inicial: int = 0):
        self.root = root
        self.root.title("Despertador")
        self.root.geometry("1080x720")
        self.root.minsize(900, 600)
        self.root.configure(background=COR_FUNDO)

        self._desvio = 0.0
        self._sincronizando = False
        self._fila_sync: queue.Queue = queue.Queue()
        self._em_disparo: set[int] = set()
        self._modal: tk.Toplevel | None = None
        self._som_alarme: core.TocadorSom | None = None
        self._som_aviso: core.TocadorSom | None = None
        self._cidades = wc.carregar_cidades()
        self._cards: dict[str, dict[str, tk.Label]] = {}
        self._ultimo_segundo: int | None = None
        self._contador_alarme = 0
        self._contador_arvore = 0
        self._fechado = False

        self.inibidor = inhibit.InibidorSono(aviso=self._aviso_inibidor)

        self.cronometro = Cronometro()
        self.temporizador = Temporizador()

        self._configurar_estilo()
        self._construir_cabecalho()
        self._construir_abas(aba_inicial)
        self._atualizar_alarmes()
        self._construir_cards()
        self._tick()
        LOG.info("GUI iniciada")

    # ------------------------------------------------------------------
    # Montagem
    # ------------------------------------------------------------------

    def _configurar_estilo(self) -> None:
        estilo = ttk.Style(self.root)
        if "clam" in estilo.theme_names():
            estilo.theme_use("clam")
        estilo.configure(
            ".", background=COR_FUNDO, foreground=COR_TEXTO, font=(FONTE, 10)
        )
        estilo.configure("TFrame", background=COR_FUNDO)
        estilo.configure("TLabel", background=COR_FUNDO)
        estilo.configure(
            "TLabelframe", background=COR_FUNDO, bordercolor=COR_BORDA, relief="solid"
        )
        estilo.configure(
            "TLabelframe.Label",
            background=COR_FUNDO,
            foreground=COR_DESTAQUE,
            font=(FONTE, 11, "bold"),
        )
        estilo.configure("TNotebook", background=COR_FUNDO, borderwidth=0)
        estilo.configure(
            "TNotebook.Tab", padding=(18, 10), font=(FONTE, 10, "bold")
        )
        estilo.map(
            "TNotebook.Tab",
            background=[("selected", COR_CARTAO)],
            foreground=[("selected", COR_DESTAQUE)],
        )
        estilo.configure(
            "Treeview",
            background=COR_CARTAO,
            fieldbackground=COR_CARTAO,
            rowheight=28,
        )
        estilo.configure("Treeview.Heading", font=(FONTE, 10, "bold"))
        estilo.configure("TButton", padding=(10, 6))

    def _construir_cabecalho(self) -> None:
        cabecalho = tk.Frame(self.root, bg=COR_DESTAQUE)
        cabecalho.pack(fill="x")
        tk.Label(
            cabecalho,
            text="Despertador",
            bg=COR_DESTAQUE,
            fg="white",
            font=(FONTE, 17, "bold"),
        ).pack(side="left", padx=18, pady=12)
        self.lbl_cabecalho = tk.Label(
            cabecalho,
            text="",
            bg=COR_DESTAQUE,
            fg="#cfd8dc",
            font=(FONTE_MONO, 12),
        )
        self.lbl_cabecalho.pack(side="right", padx=18)
        self.lbl_sono = tk.Label(
            cabecalho,
            text="",
            bg=COR_DESTAQUE,
            fg="#cfd8dc",
            font=(FONTE, 10, "bold"),
        )
        self.lbl_sono.pack(side="right", padx=(0, 18))

    def _construir_abas(self, aba_inicial: int = 0) -> None:
        self.abas = ttk.Notebook(self.root)
        self.abas.pack(fill="both", expand=True, padx=12, pady=12)

        self.aba_alarmes = ttk.Frame(self.abas, padding=10)
        self.aba_mundial = ttk.Frame(self.abas, padding=10)
        self.aba_relogio = ttk.Frame(self.abas, padding=10)
        self.abas.add(self.aba_alarmes, text="Alarmes")
        self.abas.add(self.aba_mundial, text="Relógio mundial")
        self.abas.add(self.aba_relogio, text="Relógio e cronômetro")

        self._montar_alarmes()
        self._montar_mundial()
        self._montar_relogio()
        self.abas.select(aba_inicial)

    # ------------------------------------------------------------------
    # Aba de alarmes
    # ------------------------------------------------------------------

    def _montar_alarmes(self) -> None:
        barra = ttk.Frame(self.aba_alarmes)
        barra.pack(fill="x", pady=(0, 10))

        ttk.Button(barra, text="Adicionar", command=self._novo_alarme).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(barra, text="Editar", command=self._editar_selecionado).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(barra, text="Ativar/desativar", command=self._alternar_selecionado).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(barra, text="Remover", command=self._remover_selecionado).pack(
            side="left", padx=(0, 6)
        )

        self.lbl_resumo = ttk.Label(barra, text="", foreground=COR_SUAVE)
        self.lbl_resumo.pack(side="right")

        moldura = ttk.Frame(self.aba_alarmes)
        moldura.pack(fill="both", expand=True)

        colunas = ("horario", "dias", "label", "ativo", "proxima")
        self.arvore = ttk.Treeview(
            moldura, columns=colunas, show="headings", selectmode="browse"
        )
        titulos = {
            "horario": ("Horário", 110),
            "dias": ("Dias", 190),
            "label": ("Rótulo", 260),
            "ativo": ("Ativo", 80),
            "proxima": ("Próxima ocorrência", 220),
        }
        for chave, (texto, largura) in titulos.items():
            self.arvore.heading(chave, text=texto)
            self.arvore.column(chave, width=largura, anchor="w")

        barra_rolagem = ttk.Scrollbar(
            moldura, orient="vertical", command=self.arvore.yview
        )
        self.arvore.configure(yscrollcommand=barra_rolagem.set)
        self.arvore.pack(side="left", fill="both", expand=True)
        barra_rolagem.pack(side="right", fill="y")
        self.arvore.tag_configure("inativo", foreground="#8d969b")
        self.arvore.bind("<Double-1>", lambda _evento: self._editar_selecionado())

        rodape = ttk.Label(
            self.aba_alarmes,
            text=(
                "Os alarmes só tocam com esta janela aberta (ou com "
                "`./despertador.py run`)."
            ),
            foreground=COR_SUAVE,
        )
        rodape.pack(fill="x", pady=(10, 0))

    def _atualizar_alarmes(self) -> None:
        estado = core.carregar_alarmes()
        agora = datetime.now()
        selecionados = set(self.arvore.selection())
        for item in self.arvore.get_children():
            self.arvore.delete(item)

        for alarme in sorted(estado["alarms"], key=lambda a: a["time"]):
            quando = core.proxima_ocorrencia(alarme, agora)
            if quando is not None:
                proxima = core.formatar_proxima(quando, agora)
            elif not alarme.get("enabled", True):
                proxima = "desativado"
            elif alarme.get("once") and alarme.get("last_fired"):
                proxima = "já disparou"
            else:
                proxima = "-"
            etiquetas = () if alarme.get("enabled", True) else ("inativo",)
            self.arvore.insert(
                "",
                "end",
                iid=str(alarme["id"]),
                values=(
                    core.formatar_hora(alarme),
                    core.formatar_dias(alarme),
                    alarme.get("label") or "—",
                    "sim" if alarme.get("enabled", True) else "não",
                    proxima,
                ),
                tags=etiquetas,
            )

        for iid in selecionados:
            if self.arvore.exists(iid):
                self.arvore.selection_set(iid)

        proximo = core.proxima_geral(estado["alarms"], agora)
        if proximo is None:
            self.lbl_resumo.config(text="nenhum alarme ativo")
        else:
            alarme, quando = proximo
            restante = max(0.0, (quando - agora).total_seconds())
            self.lbl_resumo.config(
                text=(
                    f"próximo: {quando.strftime('%H:%M')} "
                    f"({core.formatar_dias(alarme)}) em {core.formatar_duracao(restante)}"
                )
            )

        self._sincronizar_inibidor(estado["alarms"], agora)

    def _aviso_inibidor(self, mensagem: str) -> None:
        LOG.warning("%s", mensagem)

    def _sincronizar_inibidor(self, alarmes: list[dict], agora: datetime) -> None:
        """Inicia/libera o inhibitor conforme o estado atual dos alarmes."""
        acao = inhibit.decidir_inibicao(alarmes, agora, self.inibidor.ativo)
        if acao == "iniciar":
            if self.inibidor.iniciar(alarmes, agora):
                LOG.info(
                    "inibidor adquirido: what=%s motivo=%s pid=%s",
                    self.inibidor.what,
                    self.inibidor.motivo_atual,
                    self.inibidor.pid,
                )
            else:
                LOG.warning("não foi possível adquirir o inhibitor de suspensão")
        elif acao == "parar":
            self.inibidor.parar()
            LOG.info("inibidor liberado: nenhum alarme ativo")

        if self.inibidor.ativo:
            self.lbl_sono.config(
                text=inhibit.texto_estado(alarmes, agora), fg="#b7e4c7"
            )
        elif inhibit.what_para_inibir(alarmes, agora):
            self.lbl_sono.config(
                text="⚠ inibidor indisponível — o PC pode dormir", fg="#ffd6a5"
            )
        else:
            self.lbl_sono.config(
                text=inhibit.texto_estado(alarmes, agora), fg="#cfd8dc"
            )

    def _id_selecionado(self) -> int | None:
        selecao = self.arvore.selection()
        if not selecao:
            return None
        try:
            return int(selecao[0])
        except ValueError:
            return None

    def _alarme_selecionado(self) -> dict | None:
        identificador = self._id_selecionado()
        if identificador is None:
            return None
        return core.encontrar_alarme(core.carregar_alarmes(), identificador)

    def _novo_alarme(self) -> None:
        self._abrir_editor(None)

    def _editar_selecionado(self) -> None:
        alarme = self._alarme_selecionado()
        if alarme is None:
            messagebox.showinfo(
                "Despertador", "Selecione um alarme para editar.", parent=self.root
            )
            return
        self._abrir_editor(alarme)

    def _alternar_selecionado(self) -> None:
        identificador = self._id_selecionado()
        if identificador is None:
            return
        try:
            core.alternar_alarme(identificador)
        except (KeyError, OSError) as erro:
            messagebox.showerror("Despertador", str(erro), parent=self.root)
        self._atualizar_alarmes()

    def _remover_selecionado(self) -> None:
        identificador = self._id_selecionado()
        if identificador is None:
            return
        if not messagebox.askyesno(
            "Remover alarme", "Remover o alarme selecionado?", parent=self.root
        ):
            return
        core.remover_alarme(identificador)
        self._atualizar_alarmes()

    def _abrir_editor(self, alarme: dict | None) -> None:
        janela = tk.Toplevel(self.root)
        janela.title("Editar alarme" if alarme else "Novo alarme")
        janela.configure(background=COR_FUNDO)
        janela.transient(self.root)
        janela.resizable(False, False)

        corpo = ttk.Frame(janela, padding=16)
        corpo.pack(fill="both", expand=True)

        hora_var = tk.StringVar(value=(alarme["time"][:5] if alarme else "07:00"))
        label_var = tk.StringVar(value=(alarme.get("label") or "") if alarme else "")
        once_var = tk.BooleanVar(value=bool(alarme.get("once")) if alarme else False)
        snooze_var = tk.StringVar(
            value=str(alarme.get("snooze_minutes", 5)) if alarme else "5"
        )
        som_var = tk.StringVar(value=(alarme.get("sound") or "") if alarme else "")
        dias_marcados = set(alarme.get("days") or []) if alarme else set()
        dias_vars = [
            tk.BooleanVar(value=indice in dias_marcados) for indice in range(7)
        ]

        ttk.Label(corpo, text="Horário (HH:MM ou HH:MM:SS)").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        entrada_hora = ttk.Entry(corpo, textvariable=hora_var, width=14)
        entrada_hora.grid(row=1, column=0, sticky="w", pady=(0, 12))

        ttk.Label(corpo, text="Rótulo").grid(
            row=0, column=1, sticky="w", pady=(0, 4), padx=(16, 0)
        )
        ttk.Entry(corpo, textvariable=label_var, width=32).grid(
            row=1, column=1, sticky="we", pady=(0, 12), padx=(16, 0)
        )

        quadro_dias = ttk.LabelFrame(corpo, text="Dias (nenhum = todos)", padding=8)
        quadro_dias.grid(row=2, column=0, columnspan=2, sticky="we", pady=(0, 12))
        for indice, nome in enumerate(["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]):
            ttk.Checkbutton(
                quadro_dias, text=nome, variable=dias_vars[indice]
            ).pack(side="left", padx=4)

        ttk.Label(corpo, text="Soneca (min)").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(
            corpo, from_=1, to=120, textvariable=snooze_var, width=8
        ).grid(row=4, column=0, sticky="w", pady=(4, 12))

        ttk.Checkbutton(
            corpo, text="Tocar uma vez e desativar", variable=once_var
        ).grid(row=4, column=1, sticky="w", padx=(16, 0), pady=(4, 12))

        ttk.Label(corpo, text="Som (opcional)").grid(row=5, column=0, sticky="w")
        linha_som = ttk.Frame(corpo)
        linha_som.grid(row=6, column=0, columnspan=2, sticky="we", pady=(4, 16))
        ttk.Entry(linha_som, textvariable=som_var).pack(
            side="left", fill="x", expand=True
        )

        def escolher_som() -> None:
            escolhido = filedialog.askopenfilename(
                parent=janela,
                title="Escolher arquivo de som",
                filetypes=[
                    ("Áudio", "*.oga *.ogg *.wav *.mp3 *.flac"),
                    ("Todos os arquivos", "*"),
                ],
            )
            if escolhido:
                som_var.set(escolhido)

        ttk.Button(linha_som, text="Escolher…", command=escolher_som).pack(
            side="left", padx=(8, 0)
        )

        botoes = ttk.Frame(corpo)
        botoes.grid(row=7, column=0, columnspan=2, sticky="e")

        def salvar() -> None:
            dias = [indice for indice, var in enumerate(dias_vars) if var.get()]
            try:
                minutos = max(1, int(snooze_var.get()))
            except ValueError:
                messagebox.showerror(
                    "Despertador", "Soneca deve ser um número inteiro.", parent=janela
                )
                return
            try:
                if alarme is None:
                    core.adicionar_alarme(
                        hora_var.get(),
                        label=label_var.get(),
                        days=dias or None,
                        once=once_var.get(),
                        sound=som_var.get().strip() or None,
                        snooze_minutes=minutos,
                    )
                    LOG.info("alarme criado pela interface")
                else:
                    estado = core.carregar_alarmes()
                    alvo = core.encontrar_alarme(estado, alarme["id"])
                    if alvo is None:
                        raise KeyError(f"alarme {alarme['id']} não existe mais")
                    core.aplicar_edicao(
                        alvo,
                        hora_var.get(),
                        label=label_var.get(),
                        days=dias or None,
                        once=once_var.get(),
                        sound=som_var.get().strip() or None,
                        snooze_minutes=minutos,
                    )
                    core.salvar_alarmes(estado)
                    LOG.info("alarme %s editado pela interface", alarme["id"])
            except ValueError as erro:
                messagebox.showerror("Horário inválido", str(erro), parent=janela)
                return
            except (KeyError, OSError) as erro:
                messagebox.showerror("Despertador", str(erro), parent=janela)
                return
            janela.destroy()
            self._atualizar_alarmes()

        ttk.Button(botoes, text="Cancelar", command=janela.destroy).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(botoes, text="Salvar", command=salvar).pack(side="right")

        entrada_hora.focus_set()
        janela.grab_set()
        janela.wait_window()

    # ------------------------------------------------------------------
    # Aba de relógio mundial
    # ------------------------------------------------------------------

    def _montar_mundial(self) -> None:
        barra = ttk.Frame(self.aba_mundial)
        barra.pack(fill="x", pady=(0, 10))

        ttk.Button(barra, text="Adicionar cidade", command=self._abrir_dialogo_cidade).pack(
            side="left"
        )
        self.btn_sync = ttk.Button(
            barra, text="Sincronizar agora", command=self._iniciar_sincronizacao
        )
        self.btn_sync.pack(side="right")

        self.lbl_sync = ttk.Label(
            barra,
            text="offline — usando relógio do sistema (sincronização opcional)",
            foreground=COR_SUAVE,
        )
        self.lbl_sync.pack(side="right", padx=(0, 12))

        self.lbl_sync_erro = ttk.Label(
            self.aba_mundial, text="", foreground=COR_ALERTA, wraplength=980
        )
        self.lbl_sync_erro.pack(fill="x", pady=(0, 6))

        moldura = ttk.Frame(self.aba_mundial)
        moldura.pack(fill="both", expand=True)

        self.canvas_cards = tk.Canvas(
            moldura, background=COR_FUNDO, highlightthickness=0, borderwidth=0
        )
        rolagem = ttk.Scrollbar(
            moldura, orient="vertical", command=self.canvas_cards.yview
        )
        self.canvas_cards.configure(yscrollcommand=rolagem.set)
        self.canvas_cards.pack(side="left", fill="both", expand=True)
        rolagem.pack(side="right", fill="y")

        self.frame_cards = ttk.Frame(self.canvas_cards)
        self._item_cards = self.canvas_cards.create_window(
            (0, 0), window=self.frame_cards, anchor="nw"
        )
        self.frame_cards.bind(
            "<Configure>",
            lambda _evento: self.canvas_cards.configure(
                scrollregion=self.canvas_cards.bbox("all")
            ),
        )
        self.canvas_cards.bind("<Configure>", self._redimensionar_cards)

        ttk.Label(
            self.aba_mundial,
            text=(
                "Fusos vêm do banco tz do sistema (zoneinfo), sem internet. "
                "A sincronização é opcional e só ajusta a exibição."
            ),
            foreground=COR_SUAVE,
        ).pack(fill="x", pady=(8, 0))

    def _redimensionar_cards(self, evento) -> None:
        self.canvas_cards.itemconfigure(self._item_cards, width=evento.width)

    def _construir_cards(self) -> None:
        for filho in self.frame_cards.winfo_children():
            filho.destroy()
        self._cards.clear()

        colunas = 3
        for indice in range(colunas):
            self.frame_cards.columnconfigure(indice, weight=1)

        if not self._cidades:
            ttk.Label(
                self.frame_cards,
                text="Nenhuma cidade adicionada. Use “Adicionar cidade”.",
                foreground=COR_SUAVE,
            ).grid(row=0, column=0, columnspan=colunas, padx=10, pady=20, sticky="w")
            return

        for posicao, zona in enumerate(self._cidades):
            linha, coluna = divmod(posicao, colunas)
            cartao = tk.Frame(
                self.frame_cards,
                bg=COR_CARTAO,
                highlightbackground=COR_BORDA,
                highlightthickness=1,
                padx=14,
                pady=12,
            )
            cartao.grid(row=linha, column=coluna, padx=8, pady=8, sticky="nsew")
            cartao.columnconfigure(0, weight=1)

            topo = tk.Frame(cartao, bg=COR_CARTAO)
            topo.grid(row=0, column=0, sticky="we")
            tk.Label(
                topo,
                text=wc.nome_cidade(zona),
                bg=COR_CARTAO,
                fg=COR_DESTAQUE,
                font=(FONTE, 12, "bold"),
                anchor="w",
            ).pack(side="left")
            tk.Button(
                topo,
                text="×",
                command=lambda z=zona: self._remover_cidade(z),
                bg=COR_CARTAO,
                fg=COR_SUAVE,
                activebackground=COR_CARTAO,
                relief="flat",
                borderwidth=0,
                font=(FONTE, 12, "bold"),
                cursor="hand2",
            ).pack(side="right")

            hora = tk.Label(
                cartao,
                text="--:--:--",
                bg=COR_CARTAO,
                fg=COR_TEXTO,
                font=(FONTE_MONO, 26, "bold"),
                anchor="w",
            )
            hora.grid(row=1, column=0, sticky="we", pady=(6, 2))

            dia = tk.Label(
                cartao,
                text="",
                bg=COR_CARTAO,
                fg=COR_SUAVE,
                font=(FONTE, 9),
                anchor="w",
            )
            dia.grid(row=2, column=0, sticky="we")

            offset = tk.Label(
                cartao,
                text="",
                bg=COR_CARTAO,
                fg=COR_DESTAQUE,
                font=(FONTE, 9, "bold"),
                anchor="w",
            )
            offset.grid(row=3, column=0, sticky="we", pady=(6, 0))

            self._cards[zona] = {"hora": hora, "dia": dia, "offset": offset}

        self._atualizar_cards(forcar=True)

    def _atualizar_cards(self, forcar: bool = False) -> None:
        agora = self._agora()
        if not forcar and agora.second == self._ultimo_segundo:
            return
        self._ultimo_segundo = agora.second

        hoje = agora.date()
        for zona, rotulos in self._cards.items():
            local = agora.astimezone(wc.obter_zona(zona))
            rotulos["hora"].config(text=local.strftime("%H:%M:%S"))
            rotulos["dia"].config(
                text=(
                    f"{wc.DIAS_SEMANA[local.weekday()]}, "
                    f"{local.strftime('%d/%m')} · {wc.rotulo_dia(local.date(), hoje)}"
                )
            )
            diferenca = wc.diferenca_horas(agora, zona)
            rotulos["offset"].config(
                text=(
                    f"{wc.formatar_offset(local.utcoffset())} · "
                    f"{wc.formatar_diferenca(diferenca)} em relação a você"
                )
            )

    def _remover_cidade(self, zona: str) -> None:
        if zona in self._cidades:
            self._cidades.remove(zona)
            wc.salvar_cidades(self._cidades)
            LOG.info("cidade removida: %s", zona)
            self._construir_cards()

    def _abrir_dialogo_cidade(self) -> None:
        janela = tk.Toplevel(self.root)
        janela.title("Adicionar cidade")
        janela.configure(background=COR_FUNDO)
        janela.transient(self.root)
        janela.geometry("480x520")

        corpo = ttk.Frame(janela, padding=14)
        corpo.pack(fill="both", expand=True)
        ttk.Label(corpo, text="Buscar fuso IANA").pack(anchor="w")
        termo = tk.StringVar()
        entrada = ttk.Entry(corpo, textvariable=termo)
        entrada.pack(fill="x", pady=(4, 8))

        moldura = ttk.Frame(corpo)
        moldura.pack(fill="both", expand=True)
        lista = tk.Listbox(
            moldura,
            font=(FONTE_MONO, 10),
            selectmode="browse",
            background=COR_CARTAO,
            activestyle="none",
        )
        rolagem = ttk.Scrollbar(moldura, orient="vertical", command=lista.yview)
        lista.configure(yscrollcommand=rolagem.set)
        lista.pack(side="left", fill="both", expand=True)
        rolagem.pack(side="right", fill="y")

        def preencher(*_args) -> None:
            lista.delete(0, "end")
            for zona in wc.listar_zonas(termo.get()):
                lista.insert("end", zona)

        def confirmar(*_args) -> None:
            selecao = lista.curselection()
            if not selecao:
                return
            zona = lista.get(selecao[0])
            if zona not in self._cidades:
                self._cidades.append(zona)
                wc.salvar_cidades(self._cidades)
                LOG.info("cidade adicionada: %s", zona)
                self._construir_cards()
            janela.destroy()

        termo.trace_add("write", preencher)
        preencher()
        lista.bind("<Double-Button-1>", confirmar)

        botoes = ttk.Frame(corpo)
        botoes.pack(fill="x", pady=(10, 0))
        ttk.Button(botoes, text="Cancelar", command=janela.destroy).pack(side="right")
        ttk.Button(botoes, text="Adicionar", command=confirmar).pack(
            side="right", padx=(0, 8)
        )

        entrada.focus_set()
        janela.grab_set()

    # ------------------------------------------------------------------
    # Aba de relógio/cronômetro
    # ------------------------------------------------------------------

    def _montar_relogio(self) -> None:
        topo = tk.Frame(
            self.aba_relogio,
            bg=COR_CARTAO,
            highlightbackground=COR_BORDA,
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        topo.pack(fill="x", pady=(0, 12))

        self.lbl_relogio = tk.Label(
            topo,
            text="--:--:--",
            bg=COR_CARTAO,
            fg=COR_DESTAQUE,
            font=(FONTE_MONO, 54, "bold"),
        )
        self.lbl_relogio.pack(anchor="w")
        self.lbl_data = tk.Label(
            topo,
            text="",
            bg=COR_CARTAO,
            fg=COR_SUAVE,
            font=(FONTE, 12),
        )
        self.lbl_data.pack(anchor="w")

        baixo = ttk.Frame(self.aba_relogio)
        baixo.pack(fill="both", expand=True)
        baixo.columnconfigure(0, weight=1)
        baixo.columnconfigure(1, weight=1)

        self._montar_cronometro(baixo)
        self._montar_temporizador(baixo)

    def _montar_cronometro(self, pai) -> None:
        quadro = ttk.LabelFrame(pai, text="Cronômetro", padding=14)
        quadro.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        quadro.columnconfigure(0, weight=1)

        self.lbl_cronometro = tk.Label(
            quadro,
            text="00:00",
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            font=(FONTE_MONO, 34, "bold"),
        )
        self.lbl_cronometro.grid(row=0, column=0, sticky="w", pady=(0, 10))

        botoes = ttk.Frame(quadro)
        botoes.grid(row=1, column=0, sticky="w")
        self.btn_cronometro = ttk.Button(
            botoes, text="Iniciar", command=self._alternar_cronometro
        )
        self.btn_cronometro.pack(side="left", padx=(0, 6))
        ttk.Button(botoes, text="Volta", command=self._registrar_volta).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(botoes, text="Zerar", command=self._zerar_cronometro).pack(
            side="left"
        )

        self.lista_voltas = tk.Listbox(
            quadro,
            height=6,
            font=(FONTE_MONO, 10),
            background=COR_CARTAO,
            activestyle="none",
        )
        self.lista_voltas.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        quadro.rowconfigure(2, weight=1)

    def _montar_temporizador(self, pai) -> None:
        quadro = ttk.LabelFrame(pai, text="Temporizador regressivo", padding=14)
        quadro.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        quadro.columnconfigure(1, weight=1)

        self.lbl_temporizador = tk.Label(
            quadro,
            text="00:00",
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            font=(FONTE_MONO, 34, "bold"),
        )
        self.lbl_temporizador.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(quadro, text="Duração (MM:SS ou HH:MM:SS)").grid(
            row=1, column=0, sticky="w"
        )
        self.var_duracao = tk.StringVar(value="05:00")
        ttk.Entry(quadro, textvariable=self.var_duracao, width=12).grid(
            row=1, column=1, sticky="w", padx=(10, 0)
        )

        botoes = ttk.Frame(quadro)
        botoes.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(botoes, text="Iniciar", command=self._iniciar_temporizador).pack(
            side="left", padx=(0, 6)
        )
        self.btn_temporizador = ttk.Button(
            botoes, text="Pausar", command=self._alternar_temporizador, state="disabled"
        )
        self.btn_temporizador.pack(side="left", padx=(0, 6))
        ttk.Button(botoes, text="Zerar", command=self._zerar_temporizador).pack(
            side="left"
        )

        ttk.Label(
            quadro,
            text="Ao zerar, o aviso sonoro usa o mesmo mecanismo dos alarmes.",
            foreground=COR_SUAVE,
            wraplength=380,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def _alternar_cronometro(self) -> None:
        self.cronometro.alternar()
        self.btn_cronometro.config(
            text="Pausar" if self.cronometro.rodando else "Iniciar"
        )

    def _registrar_volta(self) -> None:
        valor = self.cronometro.volta()
        numero = len(self.cronometro.voltas)
        self.lista_voltas.insert("end", f"Volta {numero:02d}   {formatar_tempo(valor)}")
        self.lista_voltas.yview_moveto(1.0)

    def _zerar_cronometro(self) -> None:
        self.cronometro.zerar()
        self.lista_voltas.delete(0, "end")
        self.lbl_cronometro.config(text="00:00")
        self.btn_cronometro.config(text="Iniciar")

    def _iniciar_temporizador(self) -> None:
        try:
            segundos = parse_duracao(self.var_duracao.get())
        except ValueError as erro:
            messagebox.showerror("Duração inválida", str(erro), parent=self.root)
            return
        self.temporizador.iniciar(segundos)
        self._parar_aviso()
        self.btn_temporizador.config(text="Pausar")

    def _alternar_temporizador(self) -> None:
        self.temporizador.alternar()

    def _zerar_temporizador(self) -> None:
        self.temporizador.zerar()
        self._parar_aviso()
        self.lbl_temporizador.config(text="00:00")
        self.btn_temporizador.config(text="Pausar", state="disabled")

    # ------------------------------------------------------------------
    # Sincronização (thread + fila; nunca bloqueia a UI)
    # ------------------------------------------------------------------

    def _iniciar_sincronizacao(self) -> None:
        if self._sincronizando:
            return
        self._sincronizando = True
        self.btn_sync.config(state="disabled")
        self.lbl_sync.config(text="sincronizando…")
        self.lbl_sync_erro.config(text="")
        zona = wc.zona_local_padrao()
        LOG.info("sincronização iniciada (zona %s)", zona)
        threading.Thread(target=self._worker_sincronizacao, args=(zona,), daemon=True).start()

    def _worker_sincronizacao(self, zona: str) -> None:
        try:
            resultado = wc.sincronizar(zona)
        except Exception as erro:  # rede instável não pode derrubar a UI
            LOG.warning("sincronização offline: %s", erro)
            self._fila_sync.put(("offline", str(erro)))
            return
        LOG.info(
            "sincronização ok via %s: %s", resultado.fonte, resultado.descricao()
        )
        self._fila_sync.put(("ok", resultado))

    def _processar_sincronizacao(self) -> None:
        try:
            tipo, dado = self._fila_sync.get_nowait()
        except queue.Empty:
            return

        self._sincronizando = False
        self.btn_sync.config(state="normal")
        if tipo == "ok":
            self._desvio = float(dado.desvio_segundos)
            self.lbl_sync.config(
                text=f"online via {dado.fonte} · {dado.descricao()}"
            )
            self.lbl_sync_erro.config(text="")
        else:
            self._desvio = 0.0
            self.lbl_sync.config(text="offline — usando relógio do sistema")
            self.lbl_sync_erro.config(text=f"detalhe: {dado}")

    # ------------------------------------------------------------------
    # Disparo de alarmes e som
    # ------------------------------------------------------------------

    def _checar_alarmes(self) -> None:
        if self._modal is not None:
            return
        agora = datetime.now()
        estado = core.carregar_alarmes()
        pendentes = []
        for alarme in estado["alarms"]:
            if alarme["id"] in self._em_disparo:
                continue
            ocorrencia = core.ocorrencia_pendente(alarme, agora, 2.0)
            if ocorrencia is not None:
                pendentes.append((ocorrencia, alarme))
        if not pendentes:
            return
        ocorrencia, alarme = min(pendentes, key=lambda item: item[0])
        self._disparar(alarme, ocorrencia, agora)

    def _disparar(self, alarme: dict, ocorrencia: datetime, agora: datetime) -> None:
        atraso = max(0.0, (agora - ocorrencia).total_seconds())
        perdido = atraso > 60
        rotulo = alarme.get("label") or "Hora de acordar"
        hora = ocorrencia.strftime("%H:%M")
        LOG.info(
            "alarme %s disparado às %s (atraso %.0fs)",
            alarme["id"],
            ocorrencia.strftime("%H:%M:%S"),
            atraso,
        )

        self._em_disparo.add(alarme["id"])
        janela = tk.Toplevel(self.root)
        janela.title(f"Alarme — {hora}")
        janela.configure(background=COR_ALERTA)
        janela.geometry("480x280")
        janela.resizable(False, False)
        janela.attributes("-topmost", True)
        janela.transient(self.root)

        tk.Label(
            janela,
            text="ALARME",
            bg=COR_ALERTA,
            fg="#ffe8d6",
            font=(FONTE, 12, "bold"),
        ).pack(pady=(22, 0))
        tk.Label(
            janela,
            text=hora,
            bg=COR_ALERTA,
            fg="white",
            font=(FONTE_MONO, 44, "bold"),
        ).pack()
        tk.Label(
            janela,
            text=rotulo,
            bg=COR_ALERTA,
            fg="white",
            font=(FONTE, 15, "bold"),
            wraplength=420,
        ).pack(pady=(2, 2))
        if perdido:
            tk.Label(
                janela,
                text=f"alarme perdido há {core.formatar_duracao(atraso)}",
                bg=COR_ALERTA,
                fg="#ffe8d6",
                font=(FONTE, 9),
            ).pack()

        som = core.TocadorSom(alarme.get("sound"), campainha=self.root.bell)
        self._som_alarme = som
        som.iniciar()
        if som.comando:
            LOG.info(
                "som iniciado: player=%s arquivo=%s", som.comando[0], som.arquivo
            )
        else:
            LOG.info("som iniciado: campainha (sem player ou arquivo)")

        core.notificar(f"Alarme {hora}", rotulo)
        LOG.info("modal aberta para alarme %s", alarme["id"])

        def fechar(acao: str) -> None:
            minutos = core._int_ou(alarme.get("snooze_minutes"), 5)
            core.registrar_disparo(alarme["id"], ocorrencia, acao, minutos)
            if acao == "soneca":
                LOG.info("soneca de %s min para alarme %s", minutos, alarme["id"])
            else:
                LOG.info("alarme %s parado", alarme["id"])
            self._som_alarme = None
            som.parar()
            LOG.info("som parado")
            self._em_disparo.discard(alarme["id"])
            self._modal = None
            janela.destroy()
            self._atualizar_alarmes()

        botoes = tk.Frame(janela, bg=COR_ALERTA)
        botoes.pack(pady=18)
        minutos_soneca = core._int_ou(alarme.get("snooze_minutes"), 5)
        tk.Button(
            botoes,
            text=f"Soneca {minutos_soneca} min",
            command=lambda: fechar("soneca"),
            bg="#f6c177",
            fg="#3d2b1f",
            activebackground="#e8b05f",
            font=(FONTE, 11, "bold"),
            padx=16,
            pady=8,
            relief="flat",
        ).pack(side="left", padx=8)
        tk.Button(
            botoes,
            text="Parar",
            command=lambda: fechar("parar"),
            bg="white",
            fg=COR_ALERTA,
            activebackground="#f1f1f1",
            font=(FONTE, 11, "bold"),
            padx=22,
            pady=8,
            relief="flat",
        ).pack(side="left", padx=8)

        janela.protocol("WM_DELETE_WINDOW", lambda: fechar("parar"))
        self._modal = janela

        def manter_som() -> None:
            if self._som_alarme is not None:
                self._som_alarme.atualizar()
            if self._modal is not None:
                self.root.after(800, manter_som)

        manter_som()

    def _tocar_aviso(self) -> None:
        self._parar_aviso()
        som = core.TocadorSom(campainha=self.root.bell)
        som.iniciar()
        self._som_aviso = som
        LOG.info("temporizador zerou; aviso sonoro iniciado")
        self.root.after(5000, self._parar_aviso)

    def _parar_aviso(self) -> None:
        if self._som_aviso is not None:
            self._som_aviso.parar()
            self._som_aviso = None
            LOG.info("aviso sonoro encerrado")

    # ------------------------------------------------------------------
    # Laço principal
    # ------------------------------------------------------------------

    def _agora(self) -> datetime:
        """Horário local com o desvio medido aplicado somente à exibição."""
        return datetime.now().astimezone() + timedelta(seconds=self._desvio)

    def _atualizar_relogio(self) -> None:
        agora = self._agora()
        self.lbl_relogio.config(text=agora.strftime("%H:%M:%S"))
        self.lbl_data.config(text=wc.formatar_data_extensa(agora))
        self.lbl_cabecalho.config(text=agora.strftime("%H:%M:%S"))
        if self.cronometro.rodando:
            self.lbl_cronometro.config(text=formatar_tempo(self.cronometro.decorrido()))
        if self.temporizador.iniciado:
            if self.temporizador.atingiu_zero():
                self._tocar_aviso()
            self.lbl_temporizador.config(
                text=formatar_tempo(self.temporizador.restante())
            )
        if self.temporizador.rodando:
            self.btn_temporizador.config(text="Pausar", state="normal")
        elif self.temporizador.restante() > 0:
            self.btn_temporizador.config(text="Retomar", state="normal")
        else:
            self.btn_temporizador.config(text="Pausar", state="disabled")

    def _tick(self) -> None:
        self._atualizar_relogio()
        self._atualizar_cards()
        self._processar_sincronizacao()

        self._contador_alarme += 1
        if self._contador_alarme >= 2:  # ~500 ms
            self._contador_alarme = 0
            self._checar_alarmes()

        self._contador_arvore += 1
        if self._contador_arvore >= 10:  # ~2,5 s
            self._contador_arvore = 0
            self._atualizar_alarmes()

        self.root.after(250, self._tick)

    def fechar(self) -> None:
        if self._fechado:
            return
        self._fechado = True
        for som in (self._som_alarme, self._som_aviso):
            if som is not None:
                som.parar()
        self.inibidor.parar()
        LOG.info("inibidor liberado ao fechar a janela")
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="despertador_gui",
        description="Despertador desktop (Tkinter, biblioteca padrão).",
    )
    parser.add_argument(
        "--version", action="version", version=f"despertador-gui {core.__version__}"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="registra eventos (alarmes, som, sincronização) no stderr",
    )
    parser.add_argument(
        "--tab",
        choices=["alarmes", "mundial", "relogio"],
        default="alarmes",
        help="aba aberta ao iniciar (padrão: alarmes)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        root = tk.Tk()
    except tk.TclError as erro:
        print(f"erro: não consegui abrir a interface gráfica: {erro}", file=sys.stderr)
        return 1

    app = Aplicativo(root, aba_inicial={"alarmes": 0, "mundial": 1, "relogio": 2}[args.tab])
    root.protocol("WM_DELETE_WINDOW", app.fechar)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.fechar()
    finally:
        app.fechar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
