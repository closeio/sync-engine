FROM ubuntu:20.04

RUN groupadd -g 5000 sync-engine \
  && useradd -d /home/sync-engine -m -u 5000 -g 5000 sync-engine

ENV \
  TZ="Etc/GMT" \
  LANG="en_US.UTF-8" \
  LC_ALL="en_US.UTF-8" \
  DEBIAN_FRONTEND=noninteractive \
  PATH="/opt/venv/bin:$PATH"

ARG BUILD_WEEK=0
RUN echo $BUILD_WEEK && apt-get update \
  && apt-get dist-upgrade -y \
  && apt-get install --no-install-recommends -y \
    tzdata \
    locales \
    make \
    curl \
    gpg \
    gpg-agent \
    dirmngr \
    gcc \
    git \
    python3.9-dev \
    python3-pip \
    gettext-base \
    libmysqlclient-dev \
    mysql-client \
    vim \
  && locale-gen en_US.UTF-8 \
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

COPY --chown=sync-engine:sync-engine ./ ./
RUN python3.9 -m pip install pip==24.2 virtualenv==20.26.3 && \
    python3.9 -m virtualenv /opt/venv && \
   /opt/venv/bin/python3.9 -m pip install --no-cache --no-deps -r requirements/prod.txt -r requirements/test.txt && \
   /opt/venv/bin/python3.9 -m pip check

RUN ln -s /opt/app/bin/wait-for-it.sh /opt/venv/bin/
