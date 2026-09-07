<p align="center">
  <img src="./Rebecca-AI-Banner.png" alt="Rebecca AI Banner" width="100%">
</p>

<p align="center">
  <b>Local AI desktop assistant for Windows.</b><br>
  Voice, desktop control, automation, integrations, gaming tools and optional business utilities.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?style=flat-square&logo=windows11&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Node.js-LTS-339933?style=flat-square&logo=nodedotjs&logoColor=white" alt="Node.js">
  <img src="https://img.shields.io/badge/n8n-Optional-EA4B71?style=flat-square&logo=n8n&logoColor=white" alt="n8n">
  <img src="https://img.shields.io/badge/Core-Local-22D3EE?style=flat-square" alt="Local Core">
</p>

---

# Rebecca AI

**Rebecca AI** is a desktop assistant for Windows built around a local Core, a desktop companion and a collection of optional integrations.

The project combines voice interaction, an animated desktop companion, local Windows commands, game-related tools, Telegram, n8n automations, sports tracking and optional utilities for a small business.

Rebecca is modular by design.

You can use the desktop interface and local commands without configuring every external service, while additional integrations can be enabled only when you need them.

> This repository is a **sanitized public version** of the original Rebecca environment.

---

## Features

Rebecca currently includes tools and modules for:

- Desktop companion interface
- Animated avatar
- System tray integration
- Microphone input
- Continuous conversation
- Voice interaction
- Local Windows commands
- Local Core API
- Game spectator mode
- Game-related vision tools
- Telegram integration
- n8n automation workflows
- Sports tracking
- Optional small-business utilities
- Optional WhatsApp bridge
- Optional Minecraft integration
- Optional The Binding of Isaac integration
- Local memory and preferences
- Support for multiple external AI providers

Not every module is required.

The system is designed so that optional integrations can remain disabled without preventing the basic local assistant from running.

---

<p align="center">
  <img src="./Architecture-banner.png" alt="Rebecca AI Architecture" width="100%">
</p>

Rebecca is divided into a local Core, a desktop Companion and several optional modules.

```text
                     ┌──────────────────────────┐
                     │    Rebecca Companion     │
                     │                          │
                     │  Avatar • Voice • UI     │
                     │  Mic • Tray • Spectator  │
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │      Rebecca Core        │
                     │                          │
                     │    Local Python API      │
                     │    127.0.0.1:8000        │
                     └────────────┬─────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
 ┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
 │ Windows Control │    │  n8n Workflows   │    │ Business Tools  │
 └────────┬────────┘    └─────────┬────────┘    └─────────────────┘
          │                       │
          │              ┌────────┼────────────┐
          │              │        │            │
          ▼              ▼        ▼            ▼
        Games         Telegram   AI APIs    Other Services
```

The local Core acts as the central service while the Companion provides the desktop-facing interface.

Optional integrations connect to the system only when configured.

---

## Project Structure

| Component | Description |
|---|---|
| `rebecca_companion` | Desktop interface, avatar, system tray, microphone, continuous conversation and spectator mode |
| `servidor_controles_becca.py` | Local Core, Windows commands and business-related functions |
| `n8n-workflows` | Sanitized and disabled public n8n workflow exports |
| `puente-whatsapp` | Optional WhatsApp bridge |
| `Isaac_IA_Python` | Optional bridge for The Binding of Isaac |
| `Minecraft_Rebecca_Bot` | Optional Minecraft integration |
| `BotCoop` | Optional game companion module |
| `Rebecca_Sprites` | Current visual resources |
| `tests` | Local tests that should not contact real services |

Before redistributing visual resources, read:

```text
ASSETS_NOTICE.md
```

---

## Local Core

Rebecca Core runs locally at:

```text
http://127.0.0.1:8000
```

The local dashboard is available at:

```text
http://127.0.0.1:8000/dashboard
```

The default launcher does **not** start ngrok or another tunnel.

The Core therefore remains on the local machine unless you explicitly configure an external connection yourself.

---

<p align="center">
  <img src="./Installation-banner.png" alt="Rebecca AI Installation" width="100%">
</p>

## Requirements

Recommended environment:

- Windows 10 or Windows 11
- Python 3.11 or Python 3.12
- Node.js LTS
- PowerShell
- n8n — optional

External integrations are also optional.

You only need to configure the services you actually intend to use.

---

## 1. Install

Open PowerShell in the Rebecca AI directory and run:

```powershell
.\install.ps1
```

---

## 2. Create the Environment File

Copy the provided example configuration:

```powershell
Copy-Item .env.example .env
```

Then open:

```text
.env
```

and configure only the integrations you want to use.

The local interface and local commands do not require every API to be configured.

---

## 3. Start Rebecca

Run:

```powershell
.\start_rebecca.ps1
```

Rebecca Core will start at:

```text
http://127.0.0.1:8000
```

The dashboard will be available at:

```text
http://127.0.0.1:8000/dashboard
```

---

## n8n Integration

Rebecca can use **n8n** as an automation layer.

Public workflow exports are located in:

```text
n8n-workflows/
```

To configure them:

1. Install n8n separately.
2. Import the JSON files from `n8n-workflows`.
3. Create your own credentials.
4. Assign those credentials manually to the required nodes.
5. Find nodes marked `CONFIGURE_ME`.
6. Review every trigger.
7. Activate workflows only after checking their configuration.

Depending on the workflow, Rebecca may use providers such as:

- Telegram
- Gemini
- Groq
- PostgreSQL
- Tavily
- Other configured services

The public workflow exports do **not** retain:

- Credential IDs
- Real webhook IDs
- Tags from the original installation
- Pinned production data
- Private account identifiers

---

## Local Data

On first launch, Rebecca Core creates a new empty local database.

Companion memory and preferences also start empty.

These runtime files are covered by `.gitignore`.

Cloning this repository does **not** clone the original Rebecca instance, its private memory or its owner data.

It creates a new local installation.

---

## Privacy & Public Release

This repository is intentionally sanitized before publication.

The public version does **not** contain:

- API keys
- Account IDs
- Personal databases
- Personal memory
- Sales information
- WhatsApp sessions
- Private audio
- Screenshots
- Payment receipts
- Production logs
- Private filesystem paths
- Private domains
- Production credentials

Anyone using Rebecca must provide their own credentials for external integrations.

### Never commit

```text
.env
databases
logs
audio
screenshots
WhatsApp sessions
credentials
private paths
private user data
```

Even if Git allows you to add these files manually, they should remain outside the public repository.

---

## Testing

Rebecca includes local tests designed to avoid contacting real external services.

### Python tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

### Boca live processor

```powershell
node tests\test_boca_live_processor.js
```

External calls are replaced with simulations during testing.

Do not use production credentials when running the test suite.

---

## Public Release Check

Before publishing a modified version of Rebecca, run:

```powershell
python tests\test_public_release.py
```

Also verify that you have not accidentally added:

```text
.env
databases
logs
screenshots
audio
WhatsApp sessions
private credentials
private paths
```

Always review:

```text
ASSETS_NOTICE.md
```

before redistributing Rebecca's visual resources.

---

## Optional Modules

Rebecca contains several modules that are not required for the base desktop assistant.

### WhatsApp

```text
puente-whatsapp/
```

Provides the optional WhatsApp bridge.

Local session data is intentionally excluded from the public repository.

### The Binding of Isaac

```text
Isaac_IA_Python/
```

Optional integration for **The Binding of Isaac**.

### Minecraft

```text
Minecraft_Rebecca_Bot/
```

Optional Minecraft integration.

The public repository does not include private configuration or installed dependencies for this module.

### BotCoop

```text
BotCoop/
```

Additional optional game companion functionality.

---

## Design Principles

Rebecca is structured around a few simple ideas.

### Local First

The Core runs on the local machine by default.

### Modular

Optional services can be enabled independently.

### Private by Default

Personal databases, memory, credentials and session files are not part of the public repository.

### Extensible

External tools and automation services can be connected through dedicated modules and n8n workflows.

---

## Public Repository

This repository represents the **public version** of Rebecca AI.

Because the original development environment contains private data and local integrations, the public release is intentionally separated from that environment.

A fresh clone starts without:

- Personal memory
- Local databases
- Credentials
- Account sessions
- Production logs
- Original user data

This is intentional.

---

## Contributing

Rebecca AI is currently a personal project, but suggestions, bug reports and technical feedback are welcome.

If you modify the project for your own environment, remember to keep credentials and runtime data outside version control.

---

## License

A software license has **not yet been selected** for Rebecca AI.

Until a license is added, this repository should not be assumed to grant permission to copy, modify or redistribute the source code.

Visual assets may have separate restrictions.

Review:

```text
ASSETS_NOTICE.md
```

before redistributing them.

---

<p align="center">
  <b>REBECCA AI</b>
</p>

<p align="center">
  <i>Private • Local • Modular</i>
</p>