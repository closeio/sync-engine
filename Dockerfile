FROM ubuntu:22.04

RUN groupadd -g 5000 sync-engine \
  && useradd -d /home/sync-engine -m -u 5000 -g 5000 sync-engine

ENV TZ="Etc/GMT"
ENV DEBIAN_FRONTEND=noninteractive
ARG BUILD_WEEK=0
RUN echo $BUILD_WEEK \
  && apt-get update \
  && apt-get install -y software-properties-common \
  && add-apt-repository ppa:deadsnakes/ppa \
  && apt-get dist-upgrade -y \
  && apt-get install -y \
    tzdata \
    build-essential \
    curl \
    dnsutils \
    gcc \
    g++ \
    git \
    python3.11-dev \
    python3.11-venv \
    python3.11-distutils \
    wget \
    gettext-base \
    language-pack-en \
    libcurl4-openssl-dev \
    # libmysqlclient-dev \
    libmariadb-dev-compat \
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
RUN python3.11 -m venv /opt/venv && \
  /opt/venv/bin/python3.11 -m pip install setuptools==57.5.0 && \
  /opt/venv/bin/python3.11 -m pip install pymongo==2.9.5 && \
  /opt/venv/bin/python3.11 -m pip install pip==22.3.1 wheel==0.38.4 setuptools==65.6.3 && \
  /opt/venv/bin/python3.11 -m pip install --no-deps -r requirements/prod.txt -r requirements/test.txt && \
  /opt/venv/bin/python3.11 -m pip install -e . && \
  /opt/venv/bin/python3.11 -m pip check

RUN ln -s /opt/app/bin/wait-for-it.sh /opt/venv/bin/

ENV \
  LANG="en_US.UTF-8" \
  LC_ALL="en_US.UTF-8"
