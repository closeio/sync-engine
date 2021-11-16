FROM ubuntu:focal-20211006
ARG PYTHON_VERSION=2.7

RUN groupadd -g 5000 sync-engine \
  && useradd -d /home/sync-engine -m -u 5000 -g 5000 sync-engine

ENV TZ="Etc/GMT"
RUN DEBIAN_FRONTEND=noninteractive && apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get dist-upgrade -y && apt-get install -y \
  build-essential \
  curl \
  dnsutils \
  gcc \
  g++ \
  git \
  python-dev \
  wget \
  gettext-base \
  language-pack-en \
  libcurl4-openssl-dev \
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
  vim \
  libffi-dev \
  software-properties-common \
  && rm -rf /var/lib/apt/lists/*

RUN if [ "${PYTHON_VERSION}" != "3.8" ] ; \
  then \
    add-apt-repository ppa:deadsnakes/ppa; \
    DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y python"${PYTHON_VERSION}" python"${PYTHON_VERSION}"-dev; \
  fi; \
  if [ "${PYTHON_VERSION}" = "3.8" ] ; then DEBIAN_FRONTEND=noninteractive apt-get install -y python"${PYTHON_VERSION}"-distutils; fi; \
  rm -rf /var/lib/apt/lists/*

RUN curl -O https://bootstrap.pypa.io/pip/2.7/get-pip.py && \
  python"${PYTHON_VERSION}" get-pip.py && \
  python"${PYTHON_VERSION}" -m pip install --upgrade pip==20.3.4 && \
  python"${PYTHON_VERSION}" -m pip install virtualenv==20.8.1

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
  python"${PYTHON_VERSION}" -m virtualenv /opt/venv && \
  /opt/venv/bin/python"${PYTHON_VERSION}" -m pip install setuptools==44.0.0 pip==20.3.4 && \
  /opt/venv/bin/python"${PYTHON_VERSION}" -m pip install --no-deps -r requirements_frozen.txt && \
  /opt/venv/bin/python"${PYTHON_VERSION}" -m pip install -e .

RUN /opt/venv/bin/python"${PYTHON_VERSION}" -m pip check

RUN ln -s /opt/app/bin/wait-for-it.sh /opt/venv/bin/

ENV \
  LANG="en_US.UTF-8" \
  LC_ALL="en_US.UTF-8"
