# @yuzen9622/auto-ticket

[繁體中文](https://github.com/yuzen9622/auto-ticket/blob/main/README.zh-TW.md) | [Project documentation](https://github.com/yuzen9622/auto-ticket)

A local-first launcher for auto-ticket. Install it globally to start the API, worker, and browser dashboard on your computer. Data, credentials, browser automation, and logs stay local.

## Install

Install the `autix` command globally with the package manager you use:

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
autix
```

If pnpm reports that no global bin directory is configured, run `pnpm setup` once and open a new terminal. Yarn 2 and later have no global install; use npm or pnpm instead.

The first run downloads the required runtime and Playwright Chromium, then opens <http://127.0.0.1:3000>. Later launches reuse the installed runtime.

## Requirements

- macOS 13 or later on Apple Silicon, or Windows 10 version 1803 or later on x64
- Node.js 20.10 or later
- Google Chrome
- `tar`
- At least 1.5 GB of free disk space

Intel Macs, Linux, and Windows on ARM are not supported. On Apple Silicon, use a native arm64 Node.js installation rather than Node.js through Rosetta.

## Commands

| Command | Description |
| --- | --- |
| `autix` or `autix start` | Start the API, worker, and dashboard. |
| `autix update` | Update the CLI to the latest release. |
| `autix doctor` | Run a read-only environment diagnostic. |
| `autix version` | Print CLI, runtime, Python, and ONNX Runtime versions. |
| `autix migrate --dry-run` | Preview legacy-data migration without changing files. |
| `autix logs api -f` | Follow logs; use `worker` or `web` for the other services. |
| `autix runtime list` | List locally installed runtime versions. |
| `autix --help` / `autix --version` | Show all commands, or print the CLI version. |

`auto-ticket` still works as an alias of `autix`.

The API and dashboard use fixed loopback ports `8000` and `3000` respectively. If a port is already in use, stop the process that owns it and try again.

## Safety and privacy

- All application data is stored under `~/.auto-ticket/`.
- The API and dashboard listen only on loopback addresses.
- The launcher uses a dedicated Chrome profile and never accesses your regular Chrome profile.
- Payment is mock-only; it does not submit an order or make a real payment.
- Runtime downloads are verified against the SHA-256 digest bundled in the package.

For installation help, migration behavior, runtime operations, and security details, read the [project documentation](https://github.com/yuzen9622/auto-ticket).

## License

[MIT](https://github.com/yuzen9622/auto-ticket/blob/main/LICENSE)
