# --- Stage 0 --- #
# This first stage is responsible for installing any dependencies the app needs
# to run, and updating any base dependencies.
FROM ubuntu:20.04 AS stage_0

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
    ca-certificates \
    curl \
    gpg \
    gpg-agent \
    dirmngr \
    python3.9 \
    gettext-base \
    libmysqlclient21 \
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


# --- Stage 1 --- #
# This stage is responsible for installing the build time dependencies for
# Python packages, building those packages, and then installing them
# into the virtual environment.
FROM stage_0 AS stage_1

RUN apt-get update \
  && apt-get install --no-install-recommends -y \
    make \
    gcc \
    git \
    python3.9-dev \
    python3-pip \
    libmysqlclient-dev \
  && rm -rf /var/lib/apt/lists/*

COPY /requirements/ /requirements/
RUN python3.9 -m pip install pip==24.0 virtualenv==20.25.1 && \
  python3.9 -m virtualenv /opt/venv && \
  /opt/venv/bin/python3.9 -m pip install --no-cache --no-deps -r /requirements/prod.txt -r /requirements/test.txt && \
  /opt/venv/bin/python3.9 -m pip check


# --- Stage 2 --- #
# This stage is responsible for copying the virtual environment from the
# previous stage, and then copying the application code into the image.
FROM stage_0

USER sync-engine

WORKDIR /opt/app

COPY --from=stage_1 --chown=sync-engine:sync-engine /opt/venv /opt/venv
RUN ln -s /opt/app/bin/wait-for-it.sh /opt/venv/bin/
COPY --chown=sync-engine:sync-engine ./ /opt/app/
