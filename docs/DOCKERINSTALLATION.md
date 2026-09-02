# Docker Installation

This guide is the Docker installation path for RoboRSI.

If you do not want Docker, use [INSTALLATION.md](./INSTALLATION.md).

## 1. Prerequisites

Start from a clean clone:

```bash
git clone https://github.com/nssmd/robo-rsi.git
cd RoboRSI
```

## 2. Build the Docker Image

```bash
docker build -t roborsi .
```

## 3. Initialize RoboRSI

```bash
docker run -v ~/.roborsi:/root/.roborsi --rm roborsi onboard
```

## 4. Configure the Model Provider

Edit `~/.roborsi/config.json` on your host to add API keys or provider settings. See [INSTALLATION.md](./INSTALLATION.md#5-configure-the-model-provider) for provider details.

## 5. Verify Inside Docker

```bash
docker run -v ~/.roborsi:/root/.roborsi --rm roborsi status
```

Check that:

- `Config` is shown as `✓`
- `Workspace` is shown as `✓`
- the current `Model` is correct

## 6. Run the Agent

```bash
docker run -v ~/.roborsi:/root/.roborsi --rm roborsi agent -m "hello"
```

## 7. Run the Gateway

```bash
docker run -v ~/.roborsi:/root/.roborsi -p 18790:18790 roborsi gateway
```

## 8. Docker Compose

You can also use Docker Compose:

```bash
docker compose run --rm roborsi-cli onboard     # first-time setup
docker compose up -d roborsi-gateway             # start gateway
docker compose run --rm roborsi-cli agent -m "Hello!"
docker compose logs -f roborsi-gateway           # view logs
```
