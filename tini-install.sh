#!/bin/bash
# Script for installing tini binary for the target architecture.


if [[ "$TARGETPLATFORM" == "linux/arm64" ]];
  then
    curl https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-arm64  --output /tini --location
    curl https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-arm64.asc  --output  /tini.asc --location
elif [[ "$TARGETPLATFORM" == "linux/amd64" ]];
  then
    curl https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini --output /tini --location
    curl https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini.asc --output /tini.asc --location
else
  echo "Cannot install tini for architecture $TARGETPLATFORM"
  # Maybe just modify the conditions? E.g. The target linux/arm/v8 seems compatible
  # with linux/arm64
  exit 10
fi

gpg -v --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 595E85A6B1B4779EA4DAAEC70B588DFF0527A9B7 && \
gpg --verify /tini.asc && \
chmod +x /tini