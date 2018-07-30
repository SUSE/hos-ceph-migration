#!/usr/bin/env python

import os, sys
from pprint import pprint
from keystoneauth1 import loading, session
from cinderclient import client as c_client
from novaclient import client as n_client


def info(msg):
    print("# "+msg)

def warn(msg):
    print("# WARNING: "+msg)

def detach_volume(srvid, volid):
    print("openstack server remove volume %s %s" % (srvid, volid))

def attach_volume(srvid, volid):
    print("openstack server add volume %s %s" % (srvid, volid))

def remove_snapshot(snapid):
    print("openstack snapshot delete %s" % snapid)
    
def retype_volume(volid, newtype):
    print("cinder retype --migration-policy on-demand %s %s" % (volid, newtype))
    info("Monitor migration process using: openstack volume show %s | grep migration_status" % volid)

def patch_volume_boot_index(vol, enable):
    if enable:
        warn("patching boot volume info for volume %s" % vol)
        query = "update block_device_mapping set boot_index=999 where deleted=0 and volume_id='%s' and boot_index=0"
    else:
        query = "update block_device_mapping set boot_index=0 where deleted=0 and volume_id='%s' and boot_index is NULL"
    print("echo \""+(query % vol)+"\" | sudo mysql nova")

def instance_power(srv, power):
    pcmd = 'start' if power else 'stop'
    if not power:
        warn("instance %s is running: shutting down" % srv)
    print("openstack server %s %s" % (pcmd, srv))


loader = loading.get_plugin_loader('password')
auth = loader.load_from_options(
    auth_url             = os.getenv('OS_AUTH_URL'),
    username             = os.getenv('OS_USERNAME'),
    user_domain_name     = os.getenv('OS_USER_DOMAIN_NAME'),
    user_id              = os.getenv('OS_USER_ID'),
    project_name         = os.getenv('OS_PROJECT_NAME'),
    project_domain_name  = os.getenv('OS_PROJECT_DOMAIN_NAME'),
    project_id           = os.getenv('OS_PROJECT_ID'),
    password             = os.getenv('OS_PASSWORD'),
)

sess = session.Session(auth=auth, verify=os.getenv('OS_CACERT', True))
cinder = c_client.Client(os.getenv('OS_VOLUME_API_VERSION', '2'),
                         session=sess,
                         endpoint_type=os.getenv('OS_ENDPOINT_TYPE', 'public'))
nova = n_client.Client(os.getenv('OS_COMPUTE_API_VERSION', '2'),
                       session=sess,
                       endpoint_type=os.getenv('OS_ENDPOINT_TYPE', 'public'))

NO_ROLLING_FLAG = '--no-rolling'

if len(sys.argv) < 2:
    print("Syntax: %s [%s] <fromtype>=<totype>..." % (sys.argv[0], NO_ROLLING_FLAG))
    sys.exit(0)

no_rolling_migration = False
restart_instances = []

available_volume_types = set([t.name for t in cinder.volume_types.list()])

voltype_map = {}
for arg in sys.argv[1:]:
    if arg == NO_ROLLING_FLAG:
        no_rolling_migration = True
    elif '=' in arg:
        vtfrom, vtto = (x.strip() for x in arg.split('=', 1))
        for t in (vtfrom, vtto):
            if t not in available_volume_types:
                warn("unknown volume type: %s" % t)
                continue
        voltype_map[vtfrom] = vtto

backlog = {}
srvs_with_volumes = set()

info("Migration plan for the following volume types:")
for f, t in sorted(voltype_map.items()):
    info("%s -> %s" % (f.rjust(15), t))

snapshots = {x.id: x.volume_id
    for x in cinder.volume_snapshots.list(search_opts=dict(all_tenants=1))}
vols_with_snapshots = {
    x: set([k for k, v in snapshots.items() if v == x])
        for x in snapshots.values()
}

vol_count = vol_total_size = 0

# First migrate all detached volumes
info("Migrating detached volumes")
for v in cinder.volumes.list(search_opts=dict(all_tenants=1)):
    if v.volume_type in voltype_map:
        if v.id in vols_with_snapshots:
            warn("volume %s has snapshot(s): removing" % v.id)
            for snapid in vols_with_snapshots[v.id]:
                remove_snapshot(snapid)
        srvs = [x['server_id'] for x in v.attachments]
        if len(srvs) > 1:
            # This should not happen since multi-attach is not supported yet
            warn("Volume %s is attached to multiple instances: ignoring" % v.id)
        else:
            vol_count += 1
            vol_total_size += v.size
            if srvs:
                srvs_with_volumes.add(srvs[0])
                backlog[v.id] = v.volume_type
            else:
                retype_volume(v.id, voltype_map[v.volume_type])

# Then, for each idle server, detach all volumes, migrate if needed and re-attach
info("Migrating attached volumes")
for srv in srvs_with_volumes:
    s = nova.servers.get(srv)
    info("Migrating volumes attached to instance %s (%s)" % (srv, s.name))
    if s.status not in ('ACTIVE', 'SHUTOFF'):
        warn("Instance %s is in an invalid state (%s): skipping" % (srv, s.status))
        continue
    srv_running = s.status == 'ACTIVE'
    if srv_running:
        instance_power(srv, False)
        if no_rolling_migration:
            restart_instances.append(srv)
    vols = [x['id'] for x in s.to_dict().get('os-extended-volumes:volumes_attached')]
    workaround_required = not s.image and vols[0] in backlog
    if workaround_required:
        # This instance boots from a volume hosted on a legacy volume type:
        # apply workaround
        patch_volume_boot_index(vols[0], True)
    for v in vols:
        detach_volume(srv, v)
    for v in vols:
        if v in backlog:
            # We only want to retype if the volume has one of the legacy
            # volume types but we still want to detach and reattach the
            # volume to keep the right attachment order
            retype_volume(v, voltype_map[backlog[v]])
    for v in vols:
        attach_volume(srv, v)
    if workaround_required:
        # Undo workaround
        patch_volume_boot_index(vols[0], False)
    if srv_running and not no_rolling_migration:
        instance_power(srv, True)

# In case we are not doing a rolling migration, power back on the instances
# at the end of the migration
if restart_instances:
    info("Before proceding, make sure nova is correctly configured to connect to the new backend")
    for srv in restart_instances:
            instance_power(srv, True)
    
info("Migration completed: %d volumes, %dGB data, %d instances" %
     (vol_count, vol_total_size, len(srvs_with_volumes)))
