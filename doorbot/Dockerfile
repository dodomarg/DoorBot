ARG BUILD_FROM
FROM ${BUILD_FROM}

# DoorBot deliberately has zero third-party Python dependencies: the whole
# add-on runs on the standard library, so there is nothing to pip install.
RUN apk add --no-cache python3

COPY rootfs /

RUN chmod a+x /usr/bin/doorbot-run

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOORBOT_DATA=/data \
    DOORBOT_PORT=8099

WORKDIR /opt/doorbot

LABEL io.hass.name="DoorBot" \
      io.hass.description="DIY servo deadbolt calibration and PIN management" \
      io.hass.type="addon"

CMD [ "/usr/bin/doorbot-run" ]
