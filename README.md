# Migrating HOS 5 block storage to SES


## Assumptions

- The HOS environment is running HOS 5.0.3
- The SES cluster has already been deployed and it does not contain preexisting
  data


## Steps

### Enable ceph backward compatibility to hammer

On the SES cluster:

```sh
ceph osd crush tunables hammer
ceph osd set-require-min-compat-client hammer
```


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


### Create keyrings

```sh
ceph auth get-or-create client.cinder mon 'allow r' osd 'allow class-read object_prefix rbd_children, allow rwx pool=volumes, allow rwx pool=vms, allow rwx pool=images'
ceph auth get-or-create client.glance mon 'allow r' osd 'allow class-read object_prefix rbd_children, allow rwx pool=images'
ceph auth get-or-create client.cinder-backup mon 'allow r' osd 'allow class-read object_prefix rbd_children, allow rwx pool=backups'
```

The keys generated here will be used in the next step.


### Deploy cinder client packages

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

libvirt_secret_uuid: 457eb676-33da-42ec-9a8c-9293d545c337
```

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


### Modify cinder and nova configuration teplates

#### Cinder

Edit `~/helion/my_cloud/config/cinder/cinder.conf.j2` and add a new section
for the ceph backend, for example:

```ini
[ses]
volume_driver = cinder.volume.drivers.rbd.RBDDriver
volume_backend_name = ceph
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
enabled_backends=vsa-1,ses
```

#### Nova

Edit `~/helion/my_cloud/config/nova/kvm-hypervisor.conf.j2` and add the
following lines to the `libvirt` section:

```ini
rbd_user = cinder
rbd_secret_uuid = 457eb676-33da-42ec-9a8c-9293d545c337
```

Again, make sure to replace the secret UUID.


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
ansible-playbook nova-reconfigure.yml
```


### Create a volume-type for the SES backend

```sh
. ~/service.osrc
cinder type-create ceph
cinder type-key ceph set volume_backend_name=ceph
```

Make sure `volume_backend_name` matches the `volume_backend_name` in the SES
section added to `cinder.conf.j2`.


### Migrate existing volumes

Before migrating a volume, it must be detached from its instance.

To migrate a volume to the SES backend, the cinder retype command can be used:

```sh
cinder retype --migration-policy on-demand 3b7cd205-ddbb-42c5-87db-20ce306a2c57 ceph
```

Where `3b7cd205-ddbb-42c5-87db-20ce306a2c57` is the ID of the volume and `ceph` is
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
