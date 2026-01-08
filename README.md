![Astrocade](/assets/astrocade_readme.png)

![Python](https://img.shields.io/badge/Python-3-blue?logo=python&logoColor=white)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/ethanc/astrocade/workflow.yaml)
![Docker Pulls](https://img.shields.io/docker/pulls/ethanchrisp/astrocade?label=Docker%20Pulls)
![Docker Image Size (tag)](https://img.shields.io/docker/image-size/ethanchrisp/astrocade/latest?label=Docker%20Image%20Size)

Astrocade is a Discord Bot designed to expand and enhance Discord’s built-in Activities and Games.

## Features

- [Wordle](https://discord.com/discovery/applications/1211781489931452447) stat tracking, history, and leaderboards 

## Getting Started

### Docker (Recommended)

> [!IMPORTANT]
> [Discord API](https://discord.com/developers/) credentials for a Bot user are required.

Edit the following `compose.yaml` example as needed, then run `docker compose up -d`.

```yaml
services:
  astrocade:
    container_name: astrocade
    image: ethanchrisp/astrocade:latest
    environment:
      LOG_LEVEL: INFO
      LOG_DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/XXXXXXXX/XXXXXXXX
      LOG_DISCORD_WEBHOOK_LEVEL: WARNING
      DISCORD_GUILD_IDS: 0000000000
      DISCORD_BOT_TOKEN: XXXXXXXX
      DISCORD_WORDLE_BOT_ID: 0000000000
    volumes:
      - /path/to/database.db:/astrocade/astrocade.db
    restart: unless-stopped
```

### Standalone

> [!IMPORTANT]
> Astrocade targets Python 3.14 and newer. Compatibility with earlier versions is not guaranteed.

Install Python and the required dependencies with [uv](https://github.com/astral-sh/uv):

```
uv sync
```

Rename `.env.example` to `.env` and fill in the required variables.

Start Astrocade using uv.

```
uv run astrocade.py -OO
```

## Disclaimer

Astrocade is not affiliated with or endorsed by Activision, Discord, or The New York Times.

All trademarks and assets belong to their respective owners.
