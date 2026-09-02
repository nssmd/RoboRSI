# RoboRSI Installation Guide

This guide is the native host installation path. If you want Docker-based workflows, use:

- [Docker Installation](./DOCKERINSTALLATION.md)

## 1. Prerequisites

Start from a clean clone:

```bash
git clone https://github.com/nssmd/robo-rsi.git
cd RoboRSI
```

## 2. Install RoboRSI

Install the package in editable mode:

```bash
pip install -e ".[dev]"
```

After installation, the `roborsi` command should be available:

```bash
roborsi --help
```

Expected result:

- commands such as `onboard`, `status`, `agent`, and `provider` are listed

## 3. Initialize RoboRSI

Run:

```bash
roborsi onboard
```

This should create `~/.roborsi/config.json`, `~/.roborsi/workspace/`, and the initial workspace scaffold. You can verify it with:

```bash
find ~/.roborsi -maxdepth 4 -type f | sort
```

You should see at least:

```text
~/.roborsi/config.json
~/.roborsi/workspace/AGENTS.md
~/.roborsi/workspace/SOUL.md
~/.roborsi/workspace/TOOLS.md
~/.roborsi/workspace/USER.md
~/.roborsi/workspace/memory/MEMORY.md
```

## 4. Verify Status Output

Run:

```bash
roborsi status
```

Check that:

- `Config` is shown as `✓`
- `Workspace` is shown as `✓`
- the current `Model` looks correct
- provider status matches the actual state of your machine

## 5. Configure the Model Provider

Before testing `roborsi agent`, make sure the model provider is configured.

First run:

```bash
roborsi status
```

This tells you which providers are already available on the current machine.

Two common cases:

### 5.1 OAuth provider

If you are using an OAuth-based provider, log in directly.

The current codebase supports:

```bash
roborsi provider login openai-codex
roborsi provider login github-copilot
```

### 5.2 API key provider

If you are using an API-key-based provider, edit:

```bash
~/.roborsi/config.json
```

Fill in the provider key and default model there.

Common API key providers include:

- `openai`
- `anthropic`
- `openrouter`
- `deepseek`
- `gemini`
- `zhipu`
- `dashscope`
- `moonshot`
- `minimax`
- `siliconflow`
- `volcengine`
- `azureOpenai`
- `custom`
- `vllm`

Then run:

```bash
roborsi status
```

Check that:

- the current `Model` is correct
- the provider you want to use is no longer `not set`

## 6. Verify the Basic Model Path

Run one minimal message to confirm that RoboRSI can respond:

```bash
roborsi agent -m "hello"
```

Check that:

- the agent starts successfully
- the agent returns a normal reply
- failures point clearly to model configuration, provider setup, network, or permissions

## 7. Launch the Web Dashboard

The web dashboard provides a browser-based UI for chatting with RoboRSI.

### Prerequisites

Install the web optional dependency (if not already included in `.[dev]`):

```bash
pip install -e ".[web]"
```

Install the frontend dependencies:

```bash
cd ui
npm install
```

### Production Mode

Build the frontend and start the server:

```bash
cd ui && npm run build && cd ..
roborsi web start
```

Open **http://127.0.0.1:8765** in your browser.

### Development Mode (with hot reload)

```bash
# Terminal 1: start backend
roborsi web start

# Terminal 2: start frontend dev server
cd ui
npm run dev
```

Open **http://localhost:5173** in your browser. The Vite dev server proxies `/api` and `/ws` to the backend automatically.

### Options

```bash
roborsi web start --host 0.0.0.0 --port 9000
```

| Flag          | Default       | Description                |
|---------------|---------------|----------------------------|
| `--host`      | `127.0.0.1`  | Bind address               |
| `--port`      | `8765`        | Port number                |
| `--workspace` | `~/.roborsi/workspace` | Workspace directory |
| `--verbose`   | off           | Enable debug logging       |
