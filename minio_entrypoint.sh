#!/bin/bash

set -e

# First start minio provisionally, check if the bucket exists
# and optionally create it with right policy
bash /usr/bin/docker-entrypoint.sh server /data &>/dev/null &
MINIO_PID=$!

while ! timeout 1 bash -c "echo > /dev/tcp/localhost/9000" &>/dev/null; do sleep 1; done

mc config host add minio http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD &>/dev/null
if [[ -z "`mc ls minio | grep $MINIO_BUCKET_NAME`" ]]; then
    mc mb "minio/$MINIO_BUCKET_NAME"
    mc policy set public "minio/$MINIO_BUCKET_NAME"
else
    echo "Bucket minio/$MINIO_BUCKET_NAME exists"
fi

kill -TERM $MINIO_PID

# Now exec the actual Docker entrypoint once fully initialized
exec bash /usr/bin/docker-entrypoint.sh server /data