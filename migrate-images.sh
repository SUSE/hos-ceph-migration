#!/bin/bash

RBD_CMD=rbd

FROM_CLUSTER=$1
TO_CLUSTER=$2

CINDER_USER=cinder
FROM_POOL=images
TO_POOL=images

[ "$FROM_CLUSTER" == "$TO_CLUSTER" ] && exit

if [ $FROM_CLUSTER == ceph ] ; then
    FROM_USER=$CINDER_USER
else
    FROM_USER=$CINDER_USER-$FROM_CLUSTER
fi

if [ $TO_CLUSTER == ceph ] ; then
    TO_USER=$CINDER_USER
else
    TO_USER=$CINDER_USER-$TO_CLUSTER
fi

FROM_RBD="${RBD_CMD} -c /etc/ceph/${FROM_CLUSTER}.conf --id ${FROM_USER} -p ${FROM_POOL}"
TO_RBD="${RBD_CMD} -c /etc/ceph/${TO_CLUSTER}.conf --id ${TO_USER} -p ${TO_POOL}"

for image in $($FROM_RBD ls)
do
    echo "Migrating image ${image}"
    ${TO_RBD} rm ${image} 2>/dev/null || true
    ${FROM_RBD} export ${image} - | ${TO_RBD} import --image-format 2 --stripe-unit 8388608 --stripe-count 1 --order 23 --image-features 3 - ${image}
    ${TO_RBD} snap create ${image}@snap
done

FROM_FSID=$(crudini --get /etc/ceph/${FROM_CLUSTER}.conf global fsid)
TO_FSID=$(crudini --get /etc/ceph/${TO_CLUSTER}.conf global fsid)

echo "update image_locations set value=REPLACE(value, 'rbd://${FROM_FSID}/${FROM_POOL}/', 'rbd://${TO_FSID}/${TO_POOL}/')" | mysql glance
