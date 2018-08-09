# Migrating HOS 5 storage to SES

- [Migrating HOS 5 storage to SES](#migrating-hos-5-storage-to-ses)
  - [Assumptions](#assumptions)
  - [Preparations](#preparations)
    - [Enable SES backward compatibility to hammer](#enable-ses-backward-compatibility-to-hammer)
    - [Upgrade Ceph client packages for RHEL and SLES](#upgrade-ceph-client-packages-for-rhel-and-sles)
    - [Create required pools](#create-required-pools)
  - [Block storage](#block-storage)
    - [Preparing a migration from an HLM-deployed Ceph cluster](#preparing-a-migration-from-an-hlm-deployed-ceph-cluster)
      - [Create keyrings](#create-keyrings)
      - [Deploy Ceph client packages](#deploy-ceph-client-packages)
      - [Modify configuration templates](#modify-configuration-templates)
        - [Cinder](#cinder)
        - [Nova](#nova)
    - [Preparing a migration from non-Ceph backends](#preparing-a-migration-from-non-ceph-backends)
      - [Create keyrings](#create-keyrings-1)
      - [Deploy Ceph client packages](#deploy-ceph-client-packages-1)
      - [Modify configuration templates](#modify-configuration-templates-1)
        - [Cinder](#cinder-1)
    - [Apply the changes to the services](#apply-the-changes-to-the-services)
    - [Create a volume-type for the SES backend](#create-a-volume-type-for-the-ses-backend)
    - [Migrate existing volumes](#migrate-existing-volumes)
    - [Migrate cinder-backed instances](#migrate-cinder-backed-instances)
    - [Using the migration planning script](#using-the-migration-planning-script)
      - [Example](#example)
  - [Image storage](#image-storage)
  - [Backup storage](#backup-storage)
  - [Object storage](#object-storage)
    - [Create radosgw system users](#create-radosgw-system-users)
    - [Enable access to radosgw admin API on HOS](#enable-access-to-radosgw-admin-api-on-hos)
    - [Migrate data](#migrate-data)
    - [Configure keystone authentication on SES](#configure-keystone-authentication-on-ses)
    - [Change catalog endpoints for radosgw](#change-catalog-endpoints-for-radosgw)
    - [Remove radosgw system users](#remove-radosgw-system-users)
    - [Disable access to radosgw admin API on HOS](#disable-access-to-radosgw-admin-api-on-hos)
  - [Decommission the old storage nodes](#decommission-the-old-storage-nodes)

## Assumptions

- The HOS environment is running HOS 5.0.3 in a supported configuration
- The SES cluster has already been deployed and it does not contain preexisting
  data


## Preparations

### Enable SES backward compatibility to hammer

On the SES cluster:

```sh
ceph osd crush tunables hammer
ceph osd set-require-min-compat-client hammer
```

### Upgrade Ceph client packages for RHEL and SLES

If HOS is using RHEL or SLES compute nodes, make sure the Ceph packages in
the HOS repos are up to date: follow the HOS5 documentation in the section
"*Setting up a yum repo on Lifecycle Manager Node for Hosting the Ceph Client
Packages for RHEL 7.2*" on how to add packages to the HOS repos using the
third party integration framework. For SLES, the packages required are:

- ceph-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- ceph-base-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- ceph-common-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- ceph-mds-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- ceph-mgr-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- ceph-mon-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- ceph-osd-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- ceph-radosgw-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- libcephfs2-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- librados2-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- libradosstriper1-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- librbd1-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- librgw2-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- python-cephfs-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- python-rados-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- python-rbd-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm
- python-rgw-12.2.5+git.1530082629.8cbf63d997-2.16.1.x86_64.rpm

This step is not required when migrating object storage only.


### Create required pools

```sh
ceph osd pool create volumes 256
ceph osd pool create images 256
ceph osd pool create backups 256
ceph osd pool create vms 256
```

The number of PGs in the commands above (256) are just an example and should be
changed depending on the environment: see the official ceph
[docs around placement gorups](http://docs.ceph.com/docs/luminous/rados/operations/placement-groups/).


## Block storage

### Preparing a migration from an HLM-deployed Ceph cluster

#### Create keyrings

On the SES cluster:

```sh
ceph auth get-or-create client.glance-ses mon 'profile rbd' osd 'profile rbd pool=images'
ceph auth get-or-create client.cinder-ses mon 'profile rbd' osd 'profile rbd pool=volumes, profile rbd pool=vms, profile rbd pool=images'
ceph auth get-or-create client.cinder-backup-ses mon 'profile rbd' osd 'profile rbd pool=backups'
```

The keys generated here will be used in the next step.


#### Deploy Ceph client packages

Log into the HOS deployer node as the `stack` user and clone/copy this repo
to the user's home directory.

In the `ceph-client-setup` directory, create a `vars.yml` file using
`example-vars.yml` as a template and fill in the keys from the previous step:

```yml
---

keyring:
  client.cinder: AQB21QtbHwznAxAAz7tS3m6SHzGWppCBpYgjGg==
  client.glance: AQCQ1Qtb2OW0DhAAQSztXBeWfwdx/qXOvdO5bA==
  client.cinder-backup: AQCg1Qtb16TzKRAAM76vnJIjWSl5hbFfi+oqpw==

libvirt_secret_uuid: 993b98b1-7c4f-4c23-95ff-1c14fb3e5e1c
cluster_name: ses
```

The variable `libvirt_secret_uuid` should be set to a random UUID: it can be
generated using the `uuidgen` command.

Copy the `/etc/ceph/ceph.conf` file from the SES cluster in the same directory
as the `vars.yml` file, rename it to `ceph.conf.j2` and make any required change.

Make sure to set `cluster_name` to `ses` to avoid clashes with the existing
Ceph configuration files.

Run the playbook:

```sh
ansible-playbook ceph-client-deploy.yml
```

This will make sure the ceph  configuration file in place and enable the ceph
libvirt backend on the compute nodes.

From a controller or compute node, check that the client can talk to the ceph
cluster:

```sh
sudo ceph -c /etc/ceph/ses.conf --id cinder-ses -s
```


#### Modify configuration templates

##### Cinder

Edit `~/helion/my_cloud/config/cinder/cinder.conf.j2` and add a new section
for the ceph backend, for example:

```ini
[ses_ceph]
volume_driver = cinder.volume.drivers.rbd.RBDDriver
volume_backend_name = ses_ceph
rbd_pool = volumes
rbd_ceph_conf = /etc/ceph/ses.conf
rbd_flatten_volume_from_snapshot = false
rbd_max_clone_depth = 5
rbd_store_chunk_size = 4
rados_connect_timeout = -1
rbd_user = cinder-ses
rbd_secret_uuid = 457eb676-33da-42ec-9a8c-9293d545c337
```

Make sure to replace `457eb676-33da-42ec-9a8c-9293d545c337` in
`rbd_secret_uuid` with the value used for `libvirt_secret_uuid` in `vars.yml`
and that the new backend is listed in `enabled_backends` in the
`DEFAULTS` section, for example:

```ini
enabled_backends=ceph1,ses_ceph
```

##### Nova

Edit `~/helion/my_cloud/config/nova/kvm-hypervisor.conf.j2` and make sure
the `libvirt` section does not contain `rbd_user` or `rbd_secret_uid`.

This is needed because of
[nova bug #1635008](https://bugs.launchpad.net/nova/+bug/1635008).

`rbd_user` and `rbd_secret_uid` are strictly required only when using Ceph
for ephemeral disk, which is not a supported configuration in HOS5: for
regular cinder volumes, Ceph details from cinder will be used when attaching
volumes.


### Preparing a migration from non-Ceph backends

#### Create keyrings

On the SES cluster:

```sh
ceph auth get-or-create client.glance mon 'profile rbd' osd 'profile rbd pool=images'
ceph auth get-or-create client.cinder mon 'profile rbd' osd 'profile rbd pool=volumes, profile rbd pool=vms, profile rbd pool=images'
ceph auth get-or-create client.cinder-backup mon 'profile rbd' osd 'profile rbd pool=backups'
```

The keys generated here will be used in the next step.


#### Deploy Ceph client packages

Log into the HOS deployer node as the `stack` user and clone/copy this repo
to the user's home directory.

In the `ceph-client-setup` directory, create a `vars.yml` file using
`example-vars.yml` as a template and fill in the keys from the previous step:

```yml
---

keyring:
  client.cinder: AQB21QtbHwznAxAAz7tS3m6SHzGWppCBpYgjGg==
  client.glance: AQCQ1Qtb2OW0DhAAQSztXBeWfwdx/qXOvdO5bA==
  client.cinder-backup: AQCg1Qtb16TzKRAAM76vnJIjWSl5hbFfi+oqpw==

libvirt_secret_uuid: 993b98b1-7c4f-4c23-95ff-1c14fb3e5e1c
cluster_name: ceph
```

The variable `libvirt_secret_uuid` should be set to a random UUID: it can be
generated using the `uuidgen` command.

Copy the `/etc/ceph/ceph.conf` file from the SES cluster in the same directory
as the `vars.yml` file, rename it to `ceph.conf.j2` and make any required change.

Run the playbook:

```sh
ansible-playbook ceph-client-deploy.yml
```

This will make sure the ceph client packages are installed on the nodes, put
the configuration file in place and enable the ceph libvirt backend on the
compute nodes.

From a controller or compute node, check that the client can talk to the ceph
cluster:

```sh
sudo ceph --id cinder -s
```


#### Modify configuration templates

##### Cinder

Edit `~/helion/my_cloud/config/cinder/cinder.conf.j2` and add a new section
for the ceph backend, for example:

```ini
[ses_ceph]
volume_driver = cinder.volume.drivers.rbd.RBDDriver
volume_backend_name = ses_ceph
rbd_pool = volumes
rbd_ceph_conf = /etc/ceph/ceph.conf
rbd_flatten_volume_from_snapshot = false
rbd_max_clone_depth = 5
rbd_store_chunk_size = 4
rados_connect_timeout = -1
rbd_user = cinder
rbd_secret_uuid = 457eb676-33da-42ec-9a8c-9293d545c337
```

Make sure to replace `457eb676-33da-42ec-9a8c-9293d545c337` in
`rbd_secret_uuid` with the value used for `libvirt_secret_uuid` in `vars.yml`
and that the new backend is listed in `enabled_backends` in the
`DEFAULTS` section, for example:

```ini
enabled_backends=vsa-1,ses_ceph
```


### Apply the changes to the services

Commit the configuration changes and verify the input model is still
consistent:

```sh
cd ~/helion/hos/ansible/
git commit -am "Enable SES"
ansible-playbook -i hosts/localhost config-processor-run.yml
ansible-playbook -i hosts/localhost ready-deployment.yml
```

Reconfigure the services:

```sh
cd ~/scratch/ansible/next/hos/ansible/
ansible-playbook cinder-reconfigure.yml
```


### Create a volume-type for the SES backend

```sh
. ~/service.osrc
cinder type-create ses
cinder type-key ses set volume_backend_name=ses_ceph
```

Make sure `volume_backend_name` matches the `volume_backend_name` in the SES
section added to `cinder.conf.j2`.


### Migrate existing volumes

Before migrating a volume, it must be detached from its instance and all of
its snapshots must be removed.

To migrate a volume to the SES backend, the cinder retype command can be used:

```sh
cinder retype --migration-policy on-demand 3b7cd205-ddbb-42c5-87db-20ce306a2c57 ses
```

Where `3b7cd205-ddbb-42c5-87db-20ce306a2c57` is the ID of the volume and `ses` is
the name of the volume-type we created in the previous step.

The status of the migration can be checked with the `cinder show` command.

The volume can be re-attached to the instance after the migration is complete.


### Migrate cinder-backed instances

Currently cinder does not support detaching the boot volume from its instance.

A workaround involves manipulating the nova `block_device_mapping` table
contents to trick cinder into thinking the volume is not the boot volume:

```sql
update block_device_mapping set boot_index=999 where deleted=0 and volume_id='ca166060-ba4b-4950-8308-1cf0395c934f' and boot_index=0;
```

Now the volume can be detached and migrated. After reattaching the volumes to
the instance, the database entry can be changed back:

```sql
update block_device_mapping set boot_index=0 where deleted=0 and volume_id='ca166060-ba4b-4950-8308-1cf0395c934f' and boot_index is NULL;
```


### Using the migration planning script

The `migration_planner.py` script can be used to build a detailed list of
steps to execute in order to migrate the existing volumes. The output can
be executed verbatim in a shell but it is not meant to be used as a shell
script.

The script expects one or more arguments in the form `oldtype=newtype` where
`oldtype` is the original volume type and `newtype` is the volume type we want
to migrate to.

It is meant to be run on a controller node in the openstack-client virtualenv.

#### Example

This is the state of the environment we want to migrate:

```
stack@hos5to8-cp1-c1-m1-mgmt:~$ . ~/service.osrc 
stack@hos5to8-cp1-c1-m1-mgmt:~$ openstack volume list --all-projects --long
+--------------------------------------+--------------+-----------+------+--------+----------+------------------------------+--------------------------------------+
| ID                                   | Display Name | Status    | Size | Type   | Bootable | Attached to                  | Properties                           |
+--------------------------------------+--------------+-----------+------+--------+----------+------------------------------+--------------------------------------+
| 1b199df7-3273-40eb-b705-8902e8cca727 | unused       | available |    2 | VSA-r1 | false    |                              |                                      |
| 3fb9d86b-cea7-4508-ba44-6099e6c088e0 | vm2-disk1    | in-use    |    2 | VSA-r5 | false    | Attached to vm2 on /dev/vdb  | attached_mode='rw', readonly='False' |
| a5de12db-7bef-469c-aac6-d1b2c109191e | vm1-disk3    | in-use    |    2 | VSA-r5 | false    | Attached to vm1 on /dev/vdd  | attached_mode='rw', readonly='False' |
| 4986d4a8-e4ae-4691-b522-16cf34458d33 | vm1-disk2    | in-use    |    2 | ceph   | false    | Attached to vm1 on /dev/vdc  | attached_mode='rw', readonly='False' |
| a554a4ed-4893-4f7c-b19d-e6feff7683b7 | vm1-disk1    | in-use    |    2 | VSA-r5 | false    | Attached to vm1 on /dev/vdb  | attached_mode='rw', readonly='False' |
| 83b0dad4-fd8d-4847-a50c-3efc310384d9 | vm1-boot     | in-use    |    8 | VSA-r1 | true     | Attached to vm1 on /dev/vda  | attached_mode='rw', readonly='False' |
+--------------------------------------+--------------+-----------+------+--------+----------+------------------------------+--------------------------------------+
stack@hos5to8-cp1-c1-m1-mgmt:~$ openstack snapshot list --all-projects --long
+--------------------------------------+-----------------+-------------+-----------+------+----------------------------+-----------+------------+
| ID                                   | Name            | Description | Status    | Size | Created At                 | Volume    | Properties |
+--------------------------------------+-----------------+-------------+-----------+------+----------------------------+-----------+------------+
| 222cb290-3a2e-4f94-97ec-f18d814b9944 | vm1-disk3-snap1 | None        | available |    2 | 2018-05-31T15:28:13.000000 | vm1-disk3 |            |
+--------------------------------------+-----------------+-------------+-----------+------+----------------------------+-----------+------------+
stack@hos5to8-cp1-c1-m1-mgmt:~$ openstack server list --all-projects --long
+--------------------------------------+------+--------+------------+-------------+------------------------------+---------------------+--------------------------------------+-------------------+---------------------------+------------+
| ID                                   | Name | Status | Task State | Power State | Networks                     | Image Name          | Image ID                             | Availability Zone | Host                      | Properties |
+--------------------------------------+------+--------+------------+-------------+------------------------------+---------------------+--------------------------------------+-------------------+---------------------------+------------+
| 1c9b3acb-58f5-4973-8f5d-b088c726fd6b | vm2  | ACTIVE | None       | Running     | lan=10.10.10.5, 172.16.122.4 | cirros-0.3.4-x86_64 | 033e6158-7813-4af8-9796-3f954b001303 | nova              | hos5to8-cp1-comp0001-mgmt |            |
| baff0a89-995c-4fcf-88ee-d39206a36035 | vm1  | ACTIVE | None       | Running     | lan=10.10.10.8, 172.16.122.3 |                     |                                      | nova              | hos5to8-cp1-comp0001-mgmt |            |
+--------------------------------------+------+--------+------------+-------------+------------------------------+---------------------+--------------------------------------+-------------------+---------------------------+------------+
stack@hos5to8-cp1-c1-m1-mgmt:~$ 
```

We activate the openstackclient virtualenv and run the planner:

```
stack@hos5to8-cp1-c1-m1-mgmt:~$ . /opt/stack/service/openstackclient/venv/bin/activate
(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$ python ~/hos-ceph-migration/migration_planner.py VSA-r1=ses VSA-r5=ses
# Migration plan for the following volume types:
#          VSA-r1 -> ses
#          VSA-r5 -> ses
# Migrating detached volumes
cinder retype --migration-policy on-demand 1b199df7-3273-40eb-b705-8902e8cca727 ses
# Monitor migration process using: openstack volume show 1b199df7-3273-40eb-b705-8902e8cca727 | grep migration_status
# WARNING: volume a5de12db-7bef-469c-aac6-d1b2c109191e has snapshot(s): removing
openstack snapshot delete 222cb290-3a2e-4f94-97ec-f18d814b9944
# Migrating attached volumes
# Migrating volumes attached to instance baff0a89-995c-4fcf-88ee-d39206a36035 (vm1)
# WARNING: instance baff0a89-995c-4fcf-88ee-d39206a36035 is running: shutting down
openstack server stop baff0a89-995c-4fcf-88ee-d39206a36035
# WARNING: patching boot volume info for volume 83b0dad4-fd8d-4847-a50c-3efc310384d9
echo "update block_device_mapping set boot_index=999 where deleted=0 and volume_id='83b0dad4-fd8d-4847-a50c-3efc310384d9' and boot_index=0" | sudo mysql nova
openstack server remove volume baff0a89-995c-4fcf-88ee-d39206a36035 83b0dad4-fd8d-4847-a50c-3efc310384d9
openstack server remove volume baff0a89-995c-4fcf-88ee-d39206a36035 a554a4ed-4893-4f7c-b19d-e6feff7683b7
openstack server remove volume baff0a89-995c-4fcf-88ee-d39206a36035 4986d4a8-e4ae-4691-b522-16cf34458d33
openstack server remove volume baff0a89-995c-4fcf-88ee-d39206a36035 a5de12db-7bef-469c-aac6-d1b2c109191e
cinder retype --migration-policy on-demand 83b0dad4-fd8d-4847-a50c-3efc310384d9 ses
# Monitor migration process using: openstack volume show 83b0dad4-fd8d-4847-a50c-3efc310384d9 | grep migration_status
cinder retype --migration-policy on-demand a554a4ed-4893-4f7c-b19d-e6feff7683b7 ses
# Monitor migration process using: openstack volume show a554a4ed-4893-4f7c-b19d-e6feff7683b7 | grep migration_status
cinder retype --migration-policy on-demand a5de12db-7bef-469c-aac6-d1b2c109191e ses
# Monitor migration process using: openstack volume show a5de12db-7bef-469c-aac6-d1b2c109191e | grep migration_status
openstack server add volume baff0a89-995c-4fcf-88ee-d39206a36035 83b0dad4-fd8d-4847-a50c-3efc310384d9
openstack server add volume baff0a89-995c-4fcf-88ee-d39206a36035 a554a4ed-4893-4f7c-b19d-e6feff7683b7
openstack server add volume baff0a89-995c-4fcf-88ee-d39206a36035 4986d4a8-e4ae-4691-b522-16cf34458d33
openstack server add volume baff0a89-995c-4fcf-88ee-d39206a36035 a5de12db-7bef-469c-aac6-d1b2c109191e
echo "update block_device_mapping set boot_index=0 where deleted=0 and volume_id='83b0dad4-fd8d-4847-a50c-3efc310384d9' and boot_index is NULL" | sudo mysql nova
openstack server start baff0a89-995c-4fcf-88ee-d39206a36035
# Migrating volumes attached to instance 1c9b3acb-58f5-4973-8f5d-b088c726fd6b (vm2)
# WARNING: instance 1c9b3acb-58f5-4973-8f5d-b088c726fd6b is running: shutting down
openstack server stop 1c9b3acb-58f5-4973-8f5d-b088c726fd6b
openstack server remove volume 1c9b3acb-58f5-4973-8f5d-b088c726fd6b 3fb9d86b-cea7-4508-ba44-6099e6c088e0
cinder retype --migration-policy on-demand 3fb9d86b-cea7-4508-ba44-6099e6c088e0 ses
# Monitor migration process using: openstack volume show 3fb9d86b-cea7-4508-ba44-6099e6c088e0 | grep migration_status
openstack server add volume 1c9b3acb-58f5-4973-8f5d-b088c726fd6b 3fb9d86b-cea7-4508-ba44-6099e6c088e0
openstack server start 1c9b3acb-58f5-4973-8f5d-b088c726fd6b
# Migration completed: 5 volumes, 16GB data, 2 instances
(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$
```

After following the plan, this is the state of the environment:

```
(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$ openstack volume list --all-projects --long
+--------------------------------------+--------------+-----------+------+------+----------+------------------------------+--------------------------------------+
| ID                                   | Display Name | Status    | Size | Type | Bootable | Attached to                  | Properties                           |
+--------------------------------------+--------------+-----------+------+------+----------+------------------------------+--------------------------------------+
| 1b199df7-3273-40eb-b705-8902e8cca727 | unused       | available |    2 | ses  | false    |                              |                                      |
| 3fb9d86b-cea7-4508-ba44-6099e6c088e0 | vm2-disk1    | in-use    |    2 | ses  | false    | Attached to vm2 on /dev/vdb  | attached_mode='rw', readonly='False' |
| a5de12db-7bef-469c-aac6-d1b2c109191e | vm1-disk3    | in-use    |    2 | ses  | false    | Attached to vm1 on /dev/vde  | attached_mode='rw', readonly='False' |
| 4986d4a8-e4ae-4691-b522-16cf34458d33 | vm1-disk2    | in-use    |    2 | ceph | false    | Attached to vm1 on /dev/vdd  | attached_mode='rw', readonly='False' |
| a554a4ed-4893-4f7c-b19d-e6feff7683b7 | vm1-disk1    | in-use    |    2 | ses  | false    | Attached to vm1 on /dev/vdc  | attached_mode='rw', readonly='False' |
| 83b0dad4-fd8d-4847-a50c-3efc310384d9 | vm1-boot     | in-use    |    8 | ses  | true     | Attached to vm1 on /dev/vdb  | attached_mode='rw', readonly='False' |
+--------------------------------------+--------------+-----------+------+------+----------+------------------------------+--------------------------------------+
(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$ openstack snapshot list --all-projects --long

(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$ openstack server list --all-projects --long
+--------------------------------------+------+--------+------------+-------------+------------------------------+---------------------+--------------------------------------+-------------------+---------------------------+------------+
| ID                                   | Name | Status | Task State | Power State | Networks                     | Image Name          | Image ID                             | Availability Zone | Host                      | Properties |
+--------------------------------------+------+--------+------------+-------------+------------------------------+---------------------+--------------------------------------+-------------------+---------------------------+------------+
| 1c9b3acb-58f5-4973-8f5d-b088c726fd6b | vm2  | ACTIVE | None       | Running     | lan=10.10.10.5, 172.16.122.4 | cirros-0.3.4-x86_64 | 033e6158-7813-4af8-9796-3f954b001303 | nova              | hos5to8-cp1-comp0001-mgmt |            |
| baff0a89-995c-4fcf-88ee-d39206a36035 | vm1  | ACTIVE | None       | Running     | lan=10.10.10.8, 172.16.122.3 |                     |                                      | nova              | hos5to8-cp1-comp0001-mgmt |            |
+--------------------------------------+------+--------+------------+-------------+------------------------------+---------------------+--------------------------------------+-------------------+---------------------------+------------+
(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$
```

Running the script again, will confirm the migration was successful:

```
(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$ python ~/hos-ceph-migration/migration_planner.py VSA-r1=ses VSA-r5=ses
# Migration plan for the following volume types:
#          VSA-r1 -> ses
#          VSA-r5 -> ses
# Migrating detached volumes
# Migrating attached volumes
# Migration completed: 0 volumes, 0GB data, 0 instances
(openstackclient-20180403T122416Z) stack@hos5to8-cp1-c1-m1-mgmt:~$ 
```

## Image storage

Glance images stored on Ceph can be migrated to SES by exporting the contents
of the `images` pool and reimporting in the same pool in the SES cluster.
A simple script is provided. It takes source and destination cluster and moves
the rbd images across, for example:

```
sudo ./migrate-images.sh ceph ses
```

The script requires root privileges to access keyring files and to manipulate
the glance mysql database.

## Backup storage

Cinder volume backups stored on Ceph can be migrated to SES by exporting the
contents of the `backups` pool and reimporting in the same pool in the SES
cluster. A simple script is provided. It takes source and destination cluster
and moves the rbd images across, for example:

```
sudo ./migrate-backups.sh ceph ses
```

The script requires root privileges to access keyring files.

## Object storage

### Create radosgw system users

On both the HOS5 ceph and SES clusters, run:

```sh
radosgw-admin user create --system --uid=migration --display-name="HOS5-HOS8 migration"
```

It will print a JSON structure containing the details of the newly created
user. Take note of the `access_key` and `secret_key` values from the `keys`
field.

### Enable access to radosgw admin API on HOS

On one of the HOS5 radosgw nodes run:

```sh
iptables -I INPUT -m tcp -p tcp --dport 7480 -j ACCEPT
```

### Migrate data

The object migration script depends on a python module which is not included
in any of the HOS5 virtualenvs. To create a suitable virtualenv:

```sh
virtualenv ~/radosgw-venv
. ~/radosgw-venv/bin/activate
pip install radosgw-admin python-swiftclient boto click
```

From the same HOS5 radosgw node run the migration script:

```sh
./rgw-migrate.py ip_from:port_from:access_key_from:secret_key_from ip_to:port_to:access_key_to:secret_key_to
```

Where:
 - `ip_from` is the IP of the management interface of the HOS5 radosgw node
 - `port_from` is the TCP port where the source radosgw is listening (7480)
 - `access_key_from` and `secret_key_from` are the credentials for the radosgw
   admin user on the HOS5 Ceph cluster
 - `ip_to` is the IP of the SES radosgw node
 - `port_to` is the TCP port where the destination radosgw is listening (80 by
   default)
 - `access_key_to` and `secret_key_to` are the credentials for the radosgw
   admin user on the SES cluster

### Configure keystone authentication on SES

Copy the rgw lines from the HOS5's ceph config file and paste them in the SES'
ceph config file in each of the sections reserved for the radosgw daemon.
This is an example of the fields:

```ini
rgw keystone url = https://hos5to8-cp1-vip-KEY-API-mgmt:5000
rgw keystone accepted roles = admin, _member_
rgw keystone admin user = radosgw
rgw keystone admin password = I18xNEJD
rgw keystone admin tenant = services
rgw s3 auth use keystone = False
```

Make sure the SES nodes can resolve the keystone endpoint address, for example
by adding a new entry in `/etc/hosts`.

TLS certificates from HOS5 must also be configured on the SES cluster for
keystone authentication to work: copy `/etc/ssl/certs/helion*.pem` from one of
the HOS5 controllers to `/etc/pki/trust/anchors/` on the SES nodes and, as
root, run:

```sh
update-ca-certificates
```

### Change catalog endpoints for radosgw

```sh
. ~/keystone.osrc
openstack endpoint list|grep -i ceph-object-store | awk '{print $2}' | xargs openstack endpoint delete
for ep in admin internal public ; do openstack endpoint create --region region1 ceph-rgw $ep http://172.16.110.11:80/swift/v1 ; done
```

Where 172.16.110.11:80 must be replaced with the IP and port where the object
gateway service from the SES cluster is exposed.

### Remove radosgw system users

On both the HOS5 ceph and SES clusters, run:

```sh
radosgw-admin user rm --uid=migration
```

### Disable access to radosgw admin API on HOS

On the HOS5 radosgw node we used for the migration run:

```sh
iptables -D INPUT -m tcp -p tcp --dport 7480 -j ACCEPT
```

## Decommission the old storage nodes

Once all volumes have been successfully migrated, the old backend can be
removed from the `cinder.conf.j2`. Make sure `default_volume_type` is
not pointing to the old backend.

When migrating from a HLM-deployed backend like VSA or Ceph, the storage
nodes must be removed from the control plane before starting the HOS upgrade
process.

Also make sure to remove the `ceph-monitor` and `ceph-radosgw` service
components from the control plane and disable and stop the monitor and gateway
services if they are running on controller nodes.

Once the input model has been updated, the final configuration can
be deployed:

```sh
cd ~/helion/hos/ansible/
git commit -am "Remove old cinder backend"
ansible-playbook -i hosts/localhost config-processor-run.yml
ansible-playbook -i hosts/localhost ready-deployment.yml
cd ~/scratch/ansible/next/hos/ansible/
ansible-playbook cinder-reconfigure.yml # or hlm-reconfigure.yml if the control plane was changed
```
