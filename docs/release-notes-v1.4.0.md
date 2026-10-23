# v1.4.0 — toca mesmo com o app fechado

Esta versão resolve o problema do alarme que não tocou porque o aplicativo
estava fechado.

## Serviço systemd de usuário

Instale uma vez, sem `sudo`:

```bash
cd despertador
./packaging/install-service.sh
```

O serviço roda o laço de alarmes em segundo plano, adquire o inhibitor do
logind e toca mesmo sem terminal. Ao chegar a hora, toca por 60 s e continua
para os próximos alarmes. Log:

```bash
journalctl --user -u despertador -f
systemctl --user status despertador
```

Para remover:

```bash
./packaging/install-service.sh --remove
```

## tzdata embutido no AppImage

O AppImage agora leva o banco de fusos completo. O relógio mundial resolve
`Asia/Tokyo`, `America/Sao_Paulo` e qualquer fuso IANA mesmo em distros mínimas
sem `tzdata`, e mesmo que `TZDIR`/`PYTHONTZPATH` apontem para um caminho
inexistente.

## Tema escuro

Botão de alternância no cabeçalho, com a escolha salva em
`~/.config/despertador/gui.json`. Também disponível via
`--tema claro|escuro`.

## Como usar

- **AppImage:** baixe `Despertador-x86_64.AppImage`, dê `chmod +x` e execute.
  Se faltar FUSE: `APPIMAGE_EXTRACT_AND_RUN=1 ./Despertador-x86_64.AppImage`.
- **Repositório:** `./despertador_gui.py` (GUI) ou `./despertador.py run` (CLI).
- **Com o app fechado:** `./packaging/install-service.sh` (serviço systemd).

## Verificação desta release

- `make check`: ruff + 153 testes.
- `systemd-analyze verify packaging/despertador.service`: OK.
- AppImage executado com `env -i PYTHONTZPATH=/caminho/inexistente`:
  `Asia/Tokyo` e `America/Sao_Paulo` resolvidos pelo tzdata embutido.
- Demo e capturas em `docs/screenshots/`.

**Changelog completo:** [CHANGELOG.md](https://github.com/glearflame/despertador/blob/main/CHANGELOG.md)
