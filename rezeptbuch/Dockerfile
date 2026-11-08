# Neuere Home-Assistant-Versionen übergeben BUILD_FROM nicht mehr automatisch.
# Darum steht das Basis-Image hier direkt drin; BUILD_ARCH setzt der Supervisor.
ARG BUILD_ARCH=amd64
ARG BUILD_FROM=ghcr.io/home-assistant/${BUILD_ARCH}-base:3.21
FROM ${BUILD_FROM}

# Alles aus den Alpine-Paketquellen – nichts muss auf dem Raspberry Pi kompiliert werden
RUN apk add --no-cache python3 py3-aiohttp py3-pillow

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
