实现cf 故障转移
```
docker run --rm \
  --env-file .env \
  -v "$PWD":/app \
  aiastia/cloudflare-dnsswitch \
  python check_once.py
```