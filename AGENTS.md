# AGENTS.md

Guidance for AI coding agents working in this repository. Keep it accurate: when you change a convention or workflow described here, update this file in the same commit.

## What this is

Astrocade is a single-process Discord bot (Python 3.15, [hikari](https://github.com/hikari-py/hikari) + [hikari-arc](https://github.com/hypergonial/hikari-arc)) that tracks stats for the Wordle Discord Activity. It watches channels for messages posted by the Wordle Activity bot, parses them into per-player results, stores them in SQLite, and serves `/wordle …` slash commands (stats, history, leaderboards, seasons, puzzles). It ships as a Docker image to GHCR; one instance serves the guilds listed in `DISCORD_SERVER_IDS`.

There is no web server, no test suite, and no migration tool.

## Commands

| Task | Command |
|---|---|
| Install deps (incl. dev tools) | `uv sync` |
| Run the bot | `uv run python -OO astrocade.py` |
| Lint + autofix (CI-enforced) | `uv run ruff check .` |
| Format (CI-enforced via `--check`) | `uv run ruff format .` |
| Type check | `uv run ty check .` |
| Build the image | `docker build -t astrocade .` |

- Running locally needs a `.env` in the repo root (copy `.env.example`; variables are documented in the README table). `core/consts.py` loads it at import time. `DISCORD_BOT_TOKEN` is the only variable without a default; importing `core.consts` without it raises. The bot requires the privileged **Message Content** intent on the Discord application.
- CI runs `uv run ruff check .` and `uv run ruff format --check .`; both must pass. `ruff check` has `fix = true`, so it rewrites import order and some docstring issues in place; re-read files after running it.
- `uv run ty check .` passes with zero diagnostics and must stay that way. It is not run in CI (ty is pre-1.0 and a new release can flag previously clean code), so run it locally before committing. When ty can't resolve a sqlmodel `select()` overload, wrap the columns in `col()`.
- Line endings are LF everywhere, enforced by `.gitattributes` (`* text=auto eol=lf`) and ruff's `line-ending = "lf"`.

## Layout

```
astrocade.py          Entrypoint: logging, event loop, DB setup, bot + client wiring, extension loading
core/
  consts.py           ALL environment variables (via environs), enums, regexes, asset paths, TZ
  database.py         Engine lifecycle, Player get-or-create/count; re-exports sqlmodel/sqlalchemy names
  models.py           SQLModel tables: players, wordle_puzzles, wordle_results, wordle_streaks
  hooks.py            Lifecycle/command hooks (Hooks) and the 1-minute presence task (Tasks)
  seasons.py          Season dataclass and date helpers; seasons are pure calendar math
  templates.py        Discord Components V2 message builders and TemplateType accent colors
  ocr.py              Engine-free glyph matching that reads "Wordle No. N" from results images
extensions/
  admin.py            /export (Administrator only)
  wordle.py           /wordle slash group, "Import Wordle Data" message command, message listener, WordleOps
docker/
  entrypoint.py       Drops root to PUID/PGID, chowns /astrocade, execs the command
  healthcheck.py      Verifies the SQLite file contains the required tables
assets/               Icons/logos; only the three named in the Dockerfile ship in the image
```

Gitignored runtime files in the repo root: `.env` (real secrets; never print or commit) and `astrocade.db` (a live local database; don't delete, rewrite, or commit it).

## How it fits together

- **Startup** (`astrocade.py`): configure loguru (console, plus a Discord webhook sink when `LOG_DISCORD_WEBHOOK_URL` is set), install uvloop on non-Windows, `Database.setup()` creates the SQLite file and tables via `SQLModel.metadata.create_all`, then an arc `GatewayClient` wraps a hikari `GatewayBot`. The `AsyncEngine` and `GatewayBot` are registered as arc type dependencies and `client.load_extensions_from("extensions")` imports every module in `extensions/`, calling its `@arc.loader`.
- **Extensions** are arc plugins. Each module declares `plugin = GatewayPluginBase("name")`, an `@arc.loader` that calls `client.add_plugin(plugin)`, and commands via `@plugin.include` / `@group.include`. Commands are guild-only (`invocation_contexts=[GUILD]`) and registered to `DISCORD_SERVER_IDS`.
- **Database access** lives in static-method namespaces (`Database`, `WordleOps`), never on models. The pattern:

  ```python
  engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

  async with AsyncSession(engine) as session:
      statement: SelectOfScalar[Foo] = select(Foo).where(col(Foo.bar) == baz)

      logger.debug("Find Foo ... in database")
      logger.trace(f"{statement=}\n{engine=}\n{session=}")

      results: ScalarResult[Foo] = await session.exec(statement)
  ```

  Use `col()` for column expressions, `selectinload` for relationships, and sqlalchemy's `Select` (not sqlmodel's `select`) when selecting more than four columns — see `WordleOps._stats_statement`.
- **Data ingestion** (`WordleOps.import_data`) accepts only messages authored by the Wordle Activity bot (`WORDLE_BOT_ID`). Two formats:
  - *Streak* messages: plain text `Your group is on a N day streak` followed by `N/6: @user …` lines, with a results image attached. The text never names the puzzle; `_resolve_streak_puzzle` picks it from, in order, the image header (`core/ocr.py`), adjacent `wordle_streaks` rows in the same channel (the group counter advances exactly one per day), then the message timestamp with `WORDLE_CUTOFF_HOUR`. Change this logic carefully; it exists because of real misattribution bugs (see git history). Players without a mention appear as `@Display Name` (may contain spaces and markdown escapes), using the name they had when they played. `_resolve_streak_names` matches them first against the Activity's own `Name was playing` messages within the preceding `WORDLE_NAME_LOOKBACK` (the interaction user is the launcher), then against current server members, accepting only an unambiguous match. Nicknames change often, so don't resolve against current members alone.
  - *Share* messages: a Components V2 container whose first text display matches `Wordle N X/6`; the player is `interaction_metadata.user`.

  Ingestion runs live (`GuildMessageCreateEvent` listener), via `/wordle import` (channel history or an exported `.db` attachment), and via the "Import Wordle Data" message command. Live streak imports reply with the points leaderboard for the puzzle's season.
- **Puzzle metadata** comes from `https://www.nytimes.com/svc/wordle/v2/{YYYY-MM-DD}.json` (`days_since_launch`, `print_date`, `solution`). Puzzle ID = days since `WORDLE_EPOCH` (2021-06-19). Definitions come from Merriam-Webster when `DICTIONARY_API_KEY` is set.
- **Seasons** (`core/seasons.py`) are derived from `WORDLE_SEASONS_PER_YEAR`; nothing is stored per season. `Season | None` is the convention everywhere: `None` means All-Time. Season command options default to the current season; the value `"all"` selects All-Time.
- **Presence** (`Tasks.presence`) refreshes the bot status every minute from DB counts. It imports `WordleOps` inside the function to avoid a circular import; keep it that way.

## Domain encodings

- `WordleResult.attempts` is 1–6, or **7 for a fail ("X")**. Every stat query excludes 7 from averages and counts it as a fail.
- `WordleResult.id` is `f"{player_id}_{puzzle_id}"`. `WordleStreak.id` is the Discord message snowflake; `(channel_id, puzzle_id)` is unique per streak row.
- Points per attempt count come from `WordlePoints` (env-configurable). `points` includes fail deductions; `points_gross` does not.
- `LEADERBOARDS` maps category → (title, `WordleStats` field, unit). The points board tiebreaks on gross points, then average attempts. The Seasonal Wins board is always all-time and ignores the season option.

## Conventions

**Style** — Python 3.15, ruff-format style (88 columns, double quotes, `skip-magic-trailing-comma`). Imports sorted by ruff `I` with `split-on-trailing-comma = false`. Every module, class, function, and method has a docstring whose first line is one sentence ending in a period (ruff `D`, pep257; CI fails otherwise). Model fields and `NamedTuple` members get attribute docstrings (a string literal on the following line).

**Typing** — Annotate nearly every local (`engine: AsyncEngine = ...`), module constants as `Final[...]`, methods with `self: Self`. Use `X | None`, never `Optional`. Prefer `NamedTuple` or a frozen `dataclass` for plain records.

**Structure** — Group related functions as `@staticmethod`s on a namespace class (`WordleOps`, `Database`, `Hooks`, `Templates`, `OCR`) rather than free functions or instances. Private helpers take a leading underscore. Constants, enums, regexes, and environment reads live in `core/consts.py`, not next to their use.

**Logging** — loguru only, f-strings. Levels as used here: `trace` dumps objects (`f"{statement=}"`), `debug` narrates control flow and skipped inputs, `info` for lifecycle, `success` for created/added records, `warning` for suspicious data that was still handled, `error` for failures; attach exceptions with `logger.opt(exception=e)`. Leave a blank line after a log block before `return` / `continue` / `break`.

**Discord output** — Always Components V2: `Templates.generic(...)` / `Templates.generic_thumb(..., WORDLE_ICON)` or the hikari `*ComponentBuilder`s, sent with `ctx.respond(component=...)`. Text uses Discord markdown: `##` headings, `-#` subtext for captions, `||spoilers||` for solutions, `**bold**` numbers formatted with `:,`. `TemplateType` sets the accent color: `SUCCESS` for data, `INFO` for empty results, `ERROR` for user mistakes.

**Commands** — Decorator stack, top to bottom: `@group.include` (or `@plugin.include`), `@arc.with_hook(arc.has_permissions(Permissions.ADMINISTRATOR))` when admin-only, `@arc.with_hook(Hooks.command_use)`, then `@arc.slash_subcommand("name", "Description.")`. Options are `Option[T, XParams("Description.", ...)]` with defaults in the signature; reuse `SeasonOption` for a season argument. Handlers are named `command_<group>_<name>` (`command_<name>` when ungrouped). The `/wordle` group has `autodefer=AutodeferMode.ON` because every command hits the DB. Blocking work (PIL, `sqlite3`) goes through `asyncio.to_thread`; HTTP goes through `niquests.aget`.

**Errors** — Raise a specific exception (e.g. `InvalidSeasonError`) and translate it to a user-facing message in the plugin's `@plugin.set_error_handler`, falling back to `Hooks.command_error` for everything else. Don't wrap command bodies in broad `try/except` just to respond.

## Adding things

- **Environment variable**: add the name to the `Environment` enum and read it with a default in `core/consts.py`; document it in the README configuration table, `.env.example`, and (if deployment-relevant) the README `compose.yaml` example. Code is the source of truth for names.
- **Table or column**: schema is created by `create_all` at startup and there are no migrations. New tables appear on existing databases; new columns on existing tables do **not**. When adding a table, also add it to `REQUIRED_TABLES` in `docker/healthcheck.py` and, if it holds Wordle data, to the `queries` in `WordleOps._read_database` so exported databases round-trip through `/wordle import`.
- **Extension**: new module in `extensions/`; `extensions/admin.py` is the minimal template. Register a `@plugin.set_error_handler` that delegates to `Hooks.command_error`.
- **Runtime asset**: add the file to the `COPY ... ./assets/` line in the Dockerfile; only listed assets ship.
- **Dependency**: `uv add <pkg>` (or `uv add --dev <pkg>`); commit `pyproject.toml` and `uv.lock` together. Docker installs with `--frozen`.

## Git & CI

- Work on `main`; there are no feature branches. Don't create one unless asked.
- Commit messages: `Type(Scope): Imperative summary`, e.g. `Feat(Wordle): Add seasonal wins leaderboard`, `Fix(Logging): Suppress gateway server closed errors from Discord sink`, `Chore(Project): Upgrade dependencies`. Types in use: `Feat`, `Fix`, `Chore`. Scopes in use: `Wordle`, `Admin`, `Core`, `Project`, `CI`, `Docker`, `Logging`, `README`, `Docs`, `Deps`.
- Every push to `main` runs `.github/workflows/workflow.yaml`: `uv sync --dev`, `ruff check .`, `ruff format --check .`, then builds the Alpine image and pushes `ghcr.io/ethanc/astrocade:latest` and `:<sha>`. **A commit on `main` is a release.** The version lives in `pyproject.toml`.
- Before committing: `uv run ruff check .`, `uv run ruff format --check .`, and `uv run ty check .` all pass, and the bot starts (`uv run python -OO astrocade.py`) if you touched startup, config, or models. There are no automated tests; exercise changed commands in a test guild.
