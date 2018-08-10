#!/bin/bash

RBD_CMD=rbd

FROM_CLUSTER=$1
TO_CLUSTER=$2

BACKUP_USER=cinder-backup
FROM_POOL=backups
TO_POOL=backups

[ "$FROM_CLUSTER" == "$TO_CLUSTER" ] && exit

if [ $FROM_CLUSTER == ceph ] ; then
    FROM_USER=$BACKUP_USER
else
    FROM_USER=$BACKUP_USER-$FROM_CLUSTER
fi

if [ $TO_CLUSTER == ceph ] ; then
    TO_USER=$BACKUP_USER
else
    TO_USER=$BACKUP_USER-$TO_CLUSTER
fi

FROM_RBD="${RBD_CMD} -c /etc/ceph/${FROM_CLUSTER}.conf --id ${FROM_USER} -p ${FROM_POOL}"
TO_RBD="${RBD_CMD} -c /etc/ceph/${TO_CLUSTER}.conf --id ${TO_USER} -p ${TO_POOL}"

for backup in $($FROM_RBD ls)
do
    echo "Migrating backup ${backup}"
    ${TO_RBD} rm ${backup} 2>/dev/null || true
    ${FROM_RBD} export ${backup} - | ${TO_RBD} import --image-format 2 --stripe-unit 8388608 --stripe-count 1 --order 23 --image-features 3 - ${backup}
done
