# auto-ticket

[![CI](https://github.com/yuzen9622/auto-ticket/actions/workflows/ci.yml/badge.svg)](https://github.com/yuzen9622/auto-ticket/actions/workflows/ci.yml)
[![npm version](https://img.shields.io/npm/v/%40yuzen9622/auto-ticket)](https://www.npmjs.com/package/@yuzen9622/auto-ticket)
[![License](https://img.shields.io/github/license/yuzen9622/auto-ticket)](LICENSE)

[繁體中文](README.zh-TW.md)

A local-first ticketing workflow application that installs as a global command and runs an API, a worker, and a browser dashboard on your computer. It helps you prepare and monitor ticketing tasks while keeping your data, credentials, browser automation, and logs local.

## What it does

- Starts the API, worker, and web dashboard together from a single command.
- Lets you create, monitor, and review ticketing tasks in a local dashboard.
- Stores data, screenshots, timelines, and encrypted credentials under `~/.auto-ticket/`.
- Uses a dedicated Chrome profile; it never reads or writes your everyday Chrome profile.
- Uses mock payment only. It does not submit an order or make a real payment.

## Quick start

Install the `auto-ticket` command globally with the package manager you use:

```bash
# npm
npm install -g @yuzen9622/auto-ticket

# pnpm
pnpm add -g @yuzen9622/auto-ticket

# Yarn (v1 classic)
yarn global add @yuzen9622/auto-ticket
```

Then start it:

```bash
auto-ticket
```

If pnpm reports that no global bin directory is configured, run `pnpm setup` once and open a new terminal. Yarn 2 and later have no global install; use npm or pnpm instead.

The first run downloads the platform runtime and Playwright Chromium, then opens the dashboard at <http://127.0.0.1:3000>. Later launches reuse the installed runtime.

## Requirements

| Requirement | Details |
| --- | --- |
| Operating system | macOS 13 or later on Apple Silicon, or Windows 10 version 1803 or later on x64 |
| Node.js | 20.10 or later |
| Browser | Google Chrome |
| System tool | `tar` |
| Disk space | At least 1.5 GB free for the runtime, Chromium, and local data |

Intel Macs, Linux, and Windows on ARM are not supported. On Apple Silicon, use a native arm64 Node.js installation rather than Node.js through Rosetta.

Run the read-only diagnostic command if setup does not start as expected:

```bash
auto-ticket doctor
```

For platform notes, port-conflict guidance, and macOS Gatekeeper instructions, see [Installation](docs/INSTALL.md).

## Commands

| Command | Description |
| --- | --- |
| `auto-ticket` or `auto-ticket start` | Start the API, worker, and dashboard. Press `Ctrl+C` to stop them. |
| `auto-ticket doctor` | Check the platform, Node.js, `tar`, Chrome, ports, runtime, disk space, and OCR configuration without writing data. |
| `auto-ticket version` | Print CLI, runtime, Python, and ONNX Runtime versions. |
| `auto-ticket migrate --dry-run` | Preview legacy-data migration without changing files. |
| `auto-ticket logs api -f` | Follow an application log. Replace `api` with `worker` or `web` as needed. |
| `auto-ticket runtime list` | List local runtime versions and reclaimable size. |

The API uses `127.0.0.1:8000` and the dashboard uses `127.0.0.1:3000`. These ports are fixed in the first release. If either port is busy, stop the process using it and run the command again.

## Local data and privacy

Everything created by the application is stored in `~/.auto-ticket/`, including the database, screenshots, timelines, logs, browser profiles, and runtime files. On Windows, this is still under the home directory rather than `%APPDATA%`.

The API and dashboard listen only on loopback addresses. The application does not collect telemetry, upload logs, or use cloud storage for your data. Downloading release artifacts, Playwright Chromium, and visiting ticketing websites are the only external network operations.

Credentials are kept in a local encrypted vault. Do not delete `~/.auto-ticket/data/credentials/.vault_key`: doing so permanently makes the vault contents unrecoverable.

Existing repository `data/` is copied, never moved or overwritten, during its first migration. Review the plan first with:

```bash
auto-ticket migrate --dry-run
```

See [Migration](docs/MIGRATION.md), [Runtime operations](docs/RUNTIME.md), and [Security](docs/SECURITY.md) for complete details.

## Safety boundaries

- Payment is always mock-only; the application does not create an order, submit an order, or make a real payment.
- The automation uses a dedicated Chrome profile at `~/.auto-ticket/chrome-profile/`; it does not access your normal Chrome profile.
- On macOS, the first launch may require a manual Gatekeeper approval. The application never removes quarantine attributes or weakens system security settings for you.
- Runtime downloads are verified against a SHA-256 digest bundled with the installed package.

## Development

The packaged application and repository development workflow are separate. To work on the repository, install Python 3.12 or later, [uv](https://docs.astral.sh/uv/), Node.js 20 or later, and pnpm.

```bash
pnpm run setup
pnpm run dev
```

Open <http://localhost:3000> after the services start. Run the full repository verification suite before submitting a change:

```bash
pnpm run check
```

See [Contributing](CONTRIBUTING.md) for commit and security rules.

## Documentation

- [Installation](docs/INSTALL.md)
- [Runtime operations](docs/RUNTIME.md)
- [Data migration](docs/MIGRATION.md)
- [Security](docs/SECURITY.md)
- [Legal notice and terms](LEGAL_NOTICE.md)
- [Release process](docs/RELEASING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
