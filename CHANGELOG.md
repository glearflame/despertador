# Changelog

Todas as mudanças relevantes do Despertador. Formato baseado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e versionamento
semântico.

## [1.4.0] — 2026-10-23

### Adicionado

- **Serviço systemd de usuário** (`packaging/despertador.service`,
  `packaging/despertador-run` e `packaging/install-service.sh`): mantém o laço
  de alarmes rodando com o app fechado, adquire o inhibitor do logind e toca
  mesmo sem TTY. Instalação/remoção sem `sudo`, idempotente, e log via
  `journalctl --user -u despertador -f`.
- Opção `--continuo` em `run`/`inhibit`: depois de tocar no modo não
  interativo, o laço segue para os próximos alarmes (usada pelo serviço).
- **Tema escuro** na GUI, com alternância no cabeçalho e preferência persistida
  em `$XDG_CONFIG_HOME/despertador/gui.json`; opção `--tema claro|escuro`.
- GIF de demonstração real em `docs/screenshots/demo.gif` e capturas dos dois
  temas.
- Templates de issue (`bug.yml` e `feature.yml`) com versão, distro, forma de
  instalação e logs.
- Testes para as preferências da GUI (153 testes no total).

### Corrigido

- **tzdata no AppImage:** o build copia `/usr/share/zoneinfo` para dentro do
  bundle e o `zoneinfo` passa a usar esse diretório quando congelado. O relógio
  mundial resolve fusos IANA mesmo em distros sem tzdata do sistema e com
  `PYTHONTZPATH`/`TZDIR` apontando para caminho inexistente.

## [1.3.0] — 2026-10-22

### Corrigido

- **`inhibit` não tocava o alarme.** O comando mantinha o PC acordado, mas ao
  chegar o horário apenas imprimia uma mensagem e encerrava — quem dependia
  dele para acordar não era avisado. Agora `inhibit` executa o mesmo laço de
  `run`: toca o som, aceita soneca/parar e mantém o inhibitor durante o toque e
  a soneca, liberando só quando não resta alarme pendente.
- O laço continua em fatias de no máximo 30 s, relendo o JSON e reagindo a
  mudanças de horário do sistema.

### Adicionado

- `run` adquire automaticamente o inhibitor do logind enquanto houver alarme
  ativo (é o modo principal de uso).
- `inhibit` aceita `--ring-seconds`, `--grace-hours` e `--snooze`, como `run`.
- Testes do laço com relógio, som e inhibitor injetados (`tests/test_cli_laco.py`).
- Integração contínua (GitHub Actions) com Python 3.10 e 3.12 sob Xvfb.
- Licença MIT e este changelog.

## [1.2.0] — 2026-10-21

### Adicionado

- Empacotamento em **AppImage** (`build_appimage.sh` e
  `scripts/verify_appimage.sh`), com verificação headless sob Xvfb.
- **Inhibitor do logind**: o PC não suspende enquanto houver alarme ativo,
  na GUI e na CLI (`inhibit` / `inhibit --status`).
- Soneca persistida no JSON e respeitada por todos os modos.

## [1.1.0] — 2026-10-21

### Adicionado

- Aba **Relógio mundial** com fusos IANA via `zoneinfo`, offline por padrão e
  sincronização opcional por API.
- Aba **Relógio e cronômetro**: relógio grande, cronômetro com voltas e
  temporizador regressivo com aviso sonoro.

## [1.0.0] — 2026-10-21

### Adicionado

- Núcleo em Python puro (sem dependências): parsing de horários/dias,
  ocorrências, soneca e persistência atômica em JSON.
- CLI compatível: `add`, `list`, `rm`, `enable`, `disable`, `next`, `run`.
- Interface Tkinter com lista de alarmes, editor e janela modal ao tocar.
- Alarme perdido (janela de tolerância) e cadeia de som
  `paplay` → `aplay` → `ffplay` → `canberra-gtk-play` → campainha.
