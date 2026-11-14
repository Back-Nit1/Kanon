#!/bin/sh
# usage: fetch.sh URL OUTPATH  -> prints http status, content-type, size
sleep 1
curl -sL --compressed -A "kanon-research/0.1" -o "$2" -w "%{http_code} %{content_type} %{size_download}\n" "$1"
