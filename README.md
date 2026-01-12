# dashboard-156

API Flask (gunicorn) para resumo do canal **PMPA 156** (token fixo no código).

## Endpoints
- `GET /` status
- `GET /healthz` healthcheck
- `GET /resumo-hoje` resumo do dia

## Deploy via Docker Compose (no servidor)

1) Copie `docker-compose.example.yml` para `docker-compose.yml`
2) Suba:
```bash
docker compose pull
docker compose up -d
docker logs -f dashboard-156
```

A API ficará em `http://127.0.0.1:5022/` (ajuste a porta no compose).

COMANDO PARA PUXAR A IMG NO DOCKER

docker rm -f dashboard-156 2>/dev/null || true

docker run -d \
  --name dashboard-156 \
  --restart always \
  -p 5022:5000 \
  ghcr.io/devs-wescctech/dashboard-156:latest
