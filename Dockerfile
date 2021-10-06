FROM ubuntu:xenial-20210804

RUN groupadd -g 5000 sync-engine \
  && useradd -d /home/sync-engine -m -u 5000 -g 5000 sync-engine

RUN DEBIAN_FRONTEND=noninteractive && apt-get update && apt-get dist-upgrade -y && apt-get install -y \
  build-essential \
  curl \
  dnsutils \
  gcc \
  g++ \
  git \
  python-dev \
  python-pip \
  python-virtualenv \
  wget \
  gettext-base \
  language-pack-en \
  libcurl4-openssl-dev \
  liblua5.2-dev \
  libmysqlclient-dev \
  libxml2-dev \
  libxslt-dev \
  libxslt1-dev \
  mysql-client \
  pkg-config \
  lsof \
  net-tools \
  shared-mime-info \
  telnet \
  tzdata \
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
RUN \
  virtualenv /opt/venv && \
  pip install setuptools==44.0.0 && \
  pip install pip==20.3.4 && \
  pip install -r requirements_frozen.txt && \
  pip install -e .

RUN ln -s /opt/app/bin/wait-for-it.sh /opt/venv/bin/

ENV \
  LANG="en_US.UTF-8" \
  LC_ALL="en_US.UTF-8"
