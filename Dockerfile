FROM circleci/python:2.7-buster

USER root

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y liblua5.2-dev default-mysql-client

WORKDIR /src
COPY ./ ./
RUN pip install setuptools==44.0.0
RUN pip install -r requirements_frozen.txt -e .
