![Astrocade](/assets/astrocade_readme.png)

![Python](https://img.shields.io/badge/Python-3-blue?logo=python&logoColor=white)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/ethanc/astrocade/workflow.yaml)
![Docker Pulls](https://img.shields.io/docker/pulls/ethanchrisp/astrocade?label=Docker%20Pulls)
![Docker Image Size (tag)](https://img.shields.io/docker/image-size/ethanchrisp/astrocade/latest?label=Docker%20Image%20Size)

Astrocade is a Discord Bot that enhances the Discord Activities experience.

## Features

- [Wordle](https://discord.com/discovery/applications/1211781489931452447) stat tracking, history, and leaderboards.

![Wordle Commands](/assets/astrocade_readme_wordle.png)

## Getting Started

### Quick Start: Docker (Recommended)

> [!IMPORTANT]
> [Discord API](https://discord.com/developers/) credentials for a Bot user are required.

Edit and run this `compose.yaml` example with `docker compose up -d`.

```yaml
services:
  astrocade:
    container_name: astrocade
    image: ethanchrisp/astrocade:latest
    environment:
      LOG_LEVEL: INFO
      LOG_DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/XXXXXXXX/XXXXXXXX
      LOG_DISCORD_WEBHOOK_LEVEL: WARNING
      DISCORD_BOT_TOKEN: XXXXXXXX
      DISCORD_SERVER_IDS: 0000000000
    volumes:
      - /path/to/database.db:/astrocade/astrocade.db
    restart: unless-stopped
```

### Standalone

> [!NOTE]
> Astrocade targets Python 3.14 and newer. Compatibility with earlier versions is not guaranteed.

Install Python and the required dependencies with [uv](https://github.com/astral-sh/uv):

```
uv sync
```

Rename `.env.example` to `.env` and configure your environment.

Run Astrocade with uv.

```
uv run astrocade.py -OO
```

### Configuration

All configuration is managed through environment variables on the system hosting the Bot instance.

| **Environment Variable**        | **Description**                                                                                                                         | **Default**           |
|---------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|-----------------------|
| `LOG_LEVEL`                     | [Loguru level](https://loguru.readthedocs.io/en/stable/api/logger.html#levels) of log events to print to the console.                   | `INFO`                |
| `LOG_DISCORD_WEBHOOK_URL`       | Discord Webhook URL to forward log events to.                                                                                           | N/A                   |
| `LOG_DISCORD_WEBHOOK_LEVEL`     | [Loguru level](https://loguru.readthedocs.io/en/stable/api/logger.html#levels) of log events to forward to Discord.                     | N/A                   |
| `DATABASE_PATH`                 | Path to where the SQLite Database is stored.                                                                                            | `./astrocade.db`      |
| `DISCORD_BOT_TOKEN` (Required)  | [Discord API](https://discord.com/developers/docs/quick-start/getting-started#fetching-your-credentials) credentials for your Bot user. | N/A                   |
| `DISCORD_SERVER_IDS` (Required) | Comma-separated list of Discord server IDs to sync commands to.                                                                         | N/A                   |
| `WORDLE_BOT_ID`                 | User ID of the [Wordle Discord Activity](https://discord.com/discovery/applications/1211781489931452447) Bot user.                      | `1211781489931452447` |
| `WORDLE_POINTS_ATTEMPTS_1`      | Number of points awarded for a Wordle puzzle completion in 1 attempt.                                                                   | `10`                  |
| `WORDLE_POINTS_ATTEMPTS_2`      | Number of points awarded for a Wordle puzzle completion in 2 attempts.                                                                  | `5`                   |
| `WORDLE_POINTS_ATTEMPTS_3`      | Number of points awarded for a Wordle puzzle completion in 3 attempts.                                                                  | `4`                   |
| `WORDLE_POINTS_ATTEMPTS_4`      | Number of points awarded for a Wordle puzzle completion in 4 attempts.                                                                  | `3`                   |
| `WORDLE_POINTS_ATTEMPTS_5`      | Number of points awarded for a Wordle puzzle completion in 5 attempts.                                                                  | `2`                   |
| `WORDLE_POINTS_ATTEMPTS_6`      | Number of points awarded for a Wordle puzzle completion in 6 attempts.                                                                  | `1`                   |
| `WORDLE_POINTS_FAIL`            | Number of points deducted for a failed Wordle puzzle completion.                                                                        | `-5`                  |

## Disclaimer

Astrocade is not affiliated with or endorsed by Activision, Discord, or The New York Times.

All trademarks and assets belong to their respective owners.
