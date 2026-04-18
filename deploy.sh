#!/bin/bash
set -e
cd /oriself-next-app
git pull origin main
git submodule update --remote --merge

# rebuild web
cd web
NEXT_PUBLIC_API_URL=https://next.oriself.com/api npm run build
cd ..

# reinstall server deps
cd server && .venv/bin/pip install -e . -q && cd ..

pm2 restart oriself-next-web oriself-next-server
echo "Deploy done: $(date)"
