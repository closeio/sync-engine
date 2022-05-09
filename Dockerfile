FROM ubuntu:20.04 AS base_python_image
ARG PYTHON_VERSION=3.8

RUN groupadd -g 5000 sync-engine \
  && useradd -d /home/sync-engine -m -u 5000 -g 5000 sync-engine


ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
  && apt-get dist-upgrade -y \
  && apt-get install -y \
    curl \
    python3 \
    python3-distutils \
    software-properties-common \
  && rm -rf /var/lib/apt/lists/*

RUN if [ "${PYTHON_VERSION}" != "3.8" ] ; \
  then \
    add-apt-repository ppa:deadsnakes/ppa; \
  fi \
  && apt-get update \
  && apt-get install -y \
       python"${PYTHON_VERSION}" \
       python"${PYTHON_VERSION}"-distutils \
  && rm -rf /var/lib/apt/lists/*

RUN curl -O https://bootstrap.pypa.io/pip/get-pip.py && \
  python"${PYTHON_VERSION}" get-pip.py && \
  python"${PYTHON_VERSION}" -m pip install --upgrade pip==21.3.1 && \
  python"${PYTHON_VERSION}" -m pip install virtualenv==20.8.1


# Image used to build the virtualenv.

FROM base_python_image AS dependency_image
ARG PYTHON_VERSION=3.8

ENV TZ="Etc/GMT"
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
  && apt-get dist-upgrade -y \
  && apt-get install -y \
    tzdata \
    build-essential \
    curl \
    dnsutils \
    gcc \
    g++ \
    git \
    python${PYTHON_VERSION}-dev \
    python${PYTHON_VERSION}-distutils \
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
  && rm -rf /var/lib/apt/lists/*

# Set the virtual environment up.
COPY --chown=sync-engine:sync-engine requirements/prod.txt /tmp/prod-requirements.txt
COPY --chown=sync-engine:sync-engine requirements/test.txt /tmp/test-requirements.txt

USER sync-engine

RUN \
  python"${PYTHON_VERSION}" -m virtualenv /home/sync-engine/venv && \
  /home/sync-engine/venv/bin/python -m pip install setuptools==44.0.0 pip==21.3.1 && \
  /home/sync-engine/venv/bin/python -m pip install --no-deps -r /tmp/prod-requirements.txt -r tmp/test-requirements.txt


# Final production image.
FROM base_python_image

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
  && apt-get dist-upgrade -y \
  && apt-get install -y \
    libmysqlclient \
  && rm -rf /var/lib/apt/lists/*

COPY --chown=sync-engine:sync-engine --from=dependency_image /home/sync-engine/venv /home/sync-engine/venv

RUN mkdir /etc/inboxapp && \
  chown sync-engine:sync-engine /etc/inboxapp && \
  mkdir /var/lib/inboxapp && \
  chown sync-engine:sync-engine /var/lib/inboxapp && \
  mkdir /opt/app && \
  chown sync-engine:sync-engine /opt/app

USER sync-engine

ENV PATH="/home/sync-engine/venv/bin:$PATH"

WORKDIR /opt/app

COPY --chown=sync-engine:sync-engine ./ ./
RUN \
  /home/sync-engine/venv/bin/python -m pip install -e .

RUN /home/sync-engine/venv/bin/python -m pip check

RUN ln -s /opt/app/bin/wait-for-it.sh /home/sync-engine/venv/bin/

ENV \
  LANG="en_US.UTF-8" \
  LC_ALL="en_US.UTF-8"
