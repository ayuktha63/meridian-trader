FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MERIDIAN_DATA_DIR=/data
WORKDIR /srv

COPY requirements.lock .
RUN pip install -r requirements.lock

COPY app ./app
RUN useradd --create-home meridian && mkdir /data && chown meridian /data
USER meridian
VOLUME /data
EXPOSE 8000

# A container is reached over the network by definition, so it binds 0.0.0.0 —
# and therefore will not start without APP_ACCESS_TOKEN (app/security.py).
ENV API_HOST=0.0.0.0
CMD ["python", "-m", "app.serve"]
