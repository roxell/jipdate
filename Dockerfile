FROM python:3.8-slim-bullseye

COPY . /build

ENV DEBIAN_FRONTEND=noninteractive

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
      mailutils \
      vim \
      && apt-get clean \
      && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN pip3 install --no-cache-dir --quiet -r requirements.txt && \
    python3 setup.py sdist bdist_wheel && \
    pip3 install --no-cache-dir --quiet ./dist/*.whl
RUN rm -rf /build

CMD ["jipdate", "--help"]
