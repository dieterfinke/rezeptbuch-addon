ARG BUILD_FROM
FROM $BUILD_FROM

# Alles aus den Alpine-Paketquellen – nichts muss auf dem Raspberry Pi kompiliert werden
RUN apk add --no-cache python3 py3-aiohttp py3-pillow

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
