FROM ubuntu:20.04

RUN groupadd -g 5000 sync-engine \
  && useradd -d /home/sync-engine -m -u 5000 -g 5000 sync-engine

ENV TZ="Etc/GMT"
ENV DEBIAN_FRONTEND=noninteractive
ARG BUILD_WEEK=0
RUN echo $BUILD_WEEK && apt-get update \
  && apt-get dist-upgrade -y \
  && apt-get install -y \
    tzdata \
    build-essential \
    curl \
    dnsutils \
    gcc \
    g++ \
    git \
    python3-dev \
    python3-pip \
    python3-distutils \
    wget \
    gettext-base \
    language-pack-en \
    libcurl4-openssl-dev \
    libmysqlclient-dev \
    libxml2-dev \
    libxslt-dev \
    libxslt1-dev \
    mysql-client \
    postgresql-client \
    pkg-config \
    lsof \
    net-tools \
    shared-mime-info \
    telnet \
    vim \
    libffi-dev \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir /etc/inboxapp && \
  chown sync-engine:sync-engine /etc/inboxapp && \
  mkdir /var/lib/inboxapp && \
  chown sync-engine:sync-engine /var/lib/inboxapp && \
  mkdir /opt/app && \
  chown sync-engine:sync-engine /opt/app && \
  mkdir /opt/venv && \
  chown sync-engine:sync-engine /opt/venv

USER sync-engine

WORKDIR /opt/app

ENV PATH="/opt/venv/bin:$PATH"

COPY --chown=sync-engine:sync-engine ./ ./
RUN python3 -m pip install pip==22.3.1 virtualenv==20.17.1 && \
  python3 -m virtualenv /opt/venv && \
  /opt/venv/bin/python3 -m pip install setuptools==57.5.0 && \
  /opt/venv/bin/python3 -m pip install --no-deps -r requirements/prod.txt -r requirements/test.txt && \
  /opt/venv/bin/python3 -m pip install -e . && \
  /opt/venv/bin/python3 -m pip check

RUN ln -s /opt/app/bin/wait-for-it.sh /opt/venv/bin/

ENV \
  LANG="en_US.UTF-8" \
  LC_ALL="en_US.UTF-8"
