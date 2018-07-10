#!/bin/bash

RBD_CMD=rbd

FROM_CFG=$1
FROM_KEY=$2
FROM_POOL=$3

TO_CFG=$4
TO_KEY=$5
TO_POOL=$6

FROM_RBD="${RBD_CMD} -c ${FROM_CFG} --keyring ${FROM_KEY} -p ${FROM_POOL}"
TO_RBD="${RBD_CMD} -c ${TO_CFG} --keyring ${TO_KEY} -p ${TO_POOL}"

for volume in $($FROM_RBD ls)
do
    echo "Migrating volume ${volume}"
    ${TO_RBD} rm ${volume} 2>/dev/null || true
    ${FROM_RBD} export ${volume} - | ${TO_RBD} import - ${volume}
done
