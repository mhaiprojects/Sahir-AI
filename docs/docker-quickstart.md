# Docker quick start

Full reference: [DOCKER.md](DOCKER.md).

## Requirements

- Docker Engine 24+ and Compose v2
- ~8 GB disk (Ollama chat model + Whisper + Piper)

## Steps

```bash
git clone https://github.com/mhaiprojects/Sahir-AI.git
cd Sahir-AI
cp .env.docker.example .env
# Optional: WEB_CERT_SAN=DNS:localhost,IP:127.0.0.1,IP:192.0.2.10
bash setup.sh docker
```

Or without `setup.sh`:

```bash
docker compose up -d --build
docker compose logs -f voice-assistant
```

Open `https://<host-ip>:8765`, accept the self-signed certificate, log in with your token file.

Debian/Ubuntu hosts: install Docker from Docker's official repo, add your user to the `docker` group, then follow the same steps (no `pacman` / Arch-only `install.sh` required).