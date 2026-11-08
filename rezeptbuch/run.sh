#!/usr/bin/with-contenv bashio
export ANTHROPIC_API_KEY="$(bashio::config 'anthropic_api_key')"
export KI_MODELL="$(bashio::config 'ki_modell')"
export DATA_DIR=/data
export PORT=8099
bashio::log.info "Rezeptbuch wird gestartet …"
exec python3 /app/main.py
