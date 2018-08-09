#!/bin/bash

RBD_CMD=rbd

FROM_CLUSTER=$1
FROM_USER=$2
FROM_POOL=$3

TO_CLUSTER=$4
TO_USER=$5
TO_POOL=$6

FROM_RBD="${RBD_CMD} -c /etc/ceph/${FROM_CLUSTER}.conf --id ${FROM_USER} -p ${FROM_POOL}"
TO_RBD="${RBD_CMD} -c /etc/ceph/${TO_CLUSTER}.conf --id ${TO_USER} -p ${TO_POOL}"

for volume in $($FROM_RBD ls)
do
    echo "Migrating volume ${volume}"
    ${TO_RBD} rm ${volume} 2>/dev/null || true
    ${FROM_RBD} export ${volume} - | ${TO_RBD} import --image-format 2 --stripe-unit 8388608 --stripe-count 1 --order 23 --image-features 3 - ${volume}
    ${TO_RBD} snap create ${volume}@snap
done

FROM_FSID=$(crudini --get /etc/ceph/${FROM_CLUSTER}.conf global fsid)
TO_FSID=$(crudini --get /etc/ceph/${TO_CLUSTER}.conf global fsid)

echo "update image_locations set value=REPLACE(value, 'rbd://${FROM_FSID}/${FROM_POOL}/', 'rbd://${TO_FSID}/${TO_POOL}/')" | mysql glance
