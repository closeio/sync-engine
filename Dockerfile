FROM ubuntu:16.04

USER root

RUN DEBIAN_FRONTEND=noninteractive && apt-get update && apt-get dist-upgrade -y && apt-get install -y \
  build-essential \
  curl \
  dnsutils \
  gcc \
  g++ \
  git \
  python-dev \
  python-pip \
  wget \
  gettext-base \
  language-pack-en \
  libcurl4-openssl-dev \
  liblua5.2-dev \
  libmysqlclient-dev \
  libxml2-dev \
  libxslt-dev \
  libxslt1-dev \
  libffi-dev \
  mysql-client \
  pkg-config \
  lsof \
  net-tools \
  shared-mime-info \
  telnet \
  tzdata \
  vim \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY ./ ./
RUN pip install setuptools==44.0.0
RUN pip install pip==20.3.4
RUN pip install -r requirements_frozen.txt -e .

ENV \
  LANG="en_US.UTF-8" \
  LC_ALL="en_US.UTF-8"
