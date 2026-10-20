#!/bin/sh
# Constrói o site publicado em _site/ (usado pelo Cloudflare Pages a cada alteração no repositório).
set -e
rm -rf _site && mkdir -p _site
cp *.html _site/
cp -r data _site/
python3 scripts/gerar_ics.py _site
touch _site/.nojekyll
echo "Site construído em _site/"
