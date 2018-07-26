#!/usr/bin/env python

import json, logging, os, re, sys, time, traceback
from collections import namedtuple
from multiprocessing import Pool
import click
import radosgw
import swiftclient

### BEGIN Workaround radosgw client API bug
class Stats(object):
    """RADOS Gateway User stats"""
    def __init__(self, stats_dict):
        d = dict(num_objects=0, size=0, size_actual=0, size_utilized=0,
                 size_kb=0, size_kb_actual=0, size_kb_utilized=0)
        stats_dict = stats_dict or d
        self._object = stats_dict
        for key in stats_dict:
            setattr(self, key.lower(), stats_dict[key])

    def __repr__(self):
        # pylint: disable=E1101
        return "<Usage: num_objects={} size={} size_actual={} size_kb={} " \
               "size_kb_actual={} size_kb_utilized={} size_utilized={}>".format(self.num_objects,
                                                                                self.size,
                                                                                self.size_actual,
                                                                                self.size_kb,
                                                                                self.size_kb_actual,
                                                                                self.size_kb_utilized,
                                                                                self.size_utilized)

radosgw.user.Stats = Stats

def _update_from_user(self, user):
    if type(user) is dict:
        user_dict = user
    else:
        user_dict = user.__dict__
    self.user_id = user_dict['user_id']
    self.tenant = user_dict.get('tenant')
    self.display_name = user_dict['display_name']
    self.email = user_dict['email']
    self.suspended = user_dict['suspended']
    self.max_buckets = user_dict['max_buckets']
    # subusers
    self.subusers = []
    subusers = user_dict['subusers']
    for subuser in subusers:
        # TODO
        pass
    # keys (s3)
    self.keys = []
    keys = user_dict['keys']
    for key in keys:
        if type(key) is dict:
            key_dict = key
        else:
            key_dict = key.__dict__
        s3key = radosgw.user.Key(key_dict['user'],
                                 key_dict['access_key'], key_dict['secret_key'],
                                 's3')
        self.keys.append(s3key)
    # swift_keys
    self.swift_keys = []
    keys = user_dict['swift_keys']
    for key in keys:
        if type(key) is dict:
            key_dict = key
        else:
            key_dict = key.__dict__
        swiftkey = radosgw.user.Key(key_dict['user'],
                                    key_dict['secret_key'],
                                    'swift')
        self.swift_keys.append(swiftkey)
    # caps
    self.caps = []
    caps = user_dict['caps']
    for cap in caps:
        if type(cap) is dict:
            cap_dict = cap
        else:
            cap_dict = cap.__dict__
        ucap = radosgw.user.Cap(cap_dict['type'], cap_dict['perm'])
        self.caps.append(ucap)
    if 'stats' in user_dict:
        self.stats = Stats(user_dict['stats'])
    else:
        self.stats = None

radosgw.user.UserInfo._update_from_user = _update_from_user

_kwargs_get = radosgw.connection._kwargs_get

def create_subuser(self, uid, subuser, **kwargs):
    params = {'uid': uid, 'subuser': subuser}
    _kwargs_get('generate_secret', kwargs, params)
    _kwargs_get('secret', kwargs, params)
    _kwargs_get('key_type', kwargs, params, 's3')
    _kwargs_get('access', kwargs, params)
    _kwargs_get('format', kwargs, params, 'json')
    response = self.make_request('PUT', path='/user?subuser', query_params=params)
    body = self._process_response(response)
    subuser_dict = json.loads(body)
    return subuser_dict

radosgw.connection.RadosGWAdminConnection.create_subuser = create_subuser

def get_quota(self, uid, qtype='user'):
    params = {'uid': uid, 'quota-type': qtype, 'format': 'json'}
    response = self.make_request('GET', path='/user?quota', query_params=params)
    body = self._process_response(response)
    quota_dict = json.loads(body)
    return quota_dict

radosgw.connection.RadosGWAdminConnection.get_quota = get_quota

def set_quota(self, uid, qtype='user', **kwargs):
    params = {'uid': uid, 'quota-type': qtype}
    newargs = {k.replace('_', '-'): v for k, v in kwargs.items()}
    params.update(newargs)
    response = self.make_request('PUT', path='/user?quota', query_params=params)
    self._process_response(response)

radosgw.connection.RadosGWAdminConnection.set_quota = set_quota

### END Workaround radosgw client API bugs

logging.basicConfig(level=logging.INFO, format='%(asctime)-15s %(name)-14s %(levelname)-9s %(message)s')
logger = logging.getLogger('rgw-migrate')
for mute in ('swiftclient', 'boto', 'requests'):
    logging.getLogger(mute).setLevel(logging.CRITICAL)

OS_UID_RE = re.compile('^[0-9a-fA-F]{32}$')

s3_account = namedtuple('s3_account', ['host', 'port', 'access_key', 'secret_key'])
swift_account = namedtuple('swift_account', ['host', 'port', 'user', 'key'])

def decode_s3_account(account):
    host, port, access, secret = account.split(':')
    return s3_account(host, int(port), access, secret)

def make_admin_connection(account):
    return radosgw.connection.RadosGWAdminConnection(
        access_key=account.access_key,
        secret_key=account.secret_key,
        host=account.host, port=account.port,
        aws_signature='AWS2',
        is_secure=False
    )

def make_swift_connection(account):
    return swiftclient.Connection(authurl='http://{}:{}/auth'.format(account.host, account.port),
        user=account.user, key=account.key)

def migrate_object(src_swift, dst_swift, bucket, key):
    logger.info("Uploading %s/%s", bucket, key)
    try:
        t1 = time.time()
        swift_from = make_swift_connection(src_swift)
        swift_to = make_swift_connection(dst_swift)
        hdr_from = swift_from.head_object(bucket, key)
        headers = {
            k: v for k, v in hdr_from.items() if k in (
                'x-object-manifest', 'content-type', 'last-modified', 'x-timestamp'
                ) or k.startswith('x-object-meta-')}
        lo = headers.get('x-object-manifest')
        size = int(hdr_from.get('content-length'))
        if size > 0 and lo is None:
            hdr_from, key_from = swift_from.get_object(bucket, key)
            logger.info("Uploading %s/%s as regular object (%d bytes)", bucket, key, size)
            swift_to.put_object(bucket, key, key_from, headers=headers)
        else:
            logger.info("Uploading %s/%s as large object (%d bytes)", bucket, key, size)
            swift_to.put_object(bucket, key, '', headers=headers)
        t2 = time.time()
        elapsed = t2 - t1
        return (bucket, key, size, elapsed)
    except:
        logger.exception('Uploading %s/%s FAILED!', bucket, key)
        return (bucket, key, -1, traceback.extract_stack())

def migrate_object_job(migration):
    return migrate_object(*migration)

def ensure_swift_subuser(admin, uid):
    user = admin.get_user(uid)
    if len(user.swift_keys) < 1:
        admin.create_subuser(user.uid, user.uid+':migration', key_type='swift', generate_secret=True, access='full')
        user = admin.get_user(uid)
    return user

@click.command()
@click.option('--jobs', '-j', type=click.IntRange(min=1, max=None), default=20, metavar="JOBS",
    help='Number of parallel trasfers (default=20)')
@click.argument('src', type=str, nargs=1)
@click.argument('dst', type=str, nargs=1)
def migrate(src, dst, jobs):
    """
        Migrate radosgw data between two Ceph clusters

        SRC and DST represent radosgw admin credentials for source
        and destination clusters in the following format:

            host:port:access_key:secret_key
    """

    def iter_objects(src_cred, dst_cred):
        src_s3 = decode_s3_account(src_cred)
        dst_s3 = decode_s3_account(dst_cred)
        admin_from = make_admin_connection(src_s3)
        admin_to = make_admin_connection(dst_s3)

        known_users_to = {}
        known_users_from = {}

        buckets = admin_from.get_buckets()

        for b in buckets:
            if OS_UID_RE.match(b.owner) is None:
                # Ignore buckets not owned by openstack users
                continue
            logger.info("Migrating bucket "+b.name)
            try:
                user_from = known_users_from[b.owner]
            except KeyError:
                user_from = ensure_swift_subuser(admin_from, b.owner)
                known_users_from[b.owner] = user_from
            try:
                user_to = known_users_to[b.owner]
            except KeyError:
                try:
                    user_to = admin_to.get_user(b.owner)
                except radosgw.exception.NoSuchUser:
                    logger.info("Migrating user: %s (%s)" % (user_from.display_name, user_from.uid), exc_info=0)
                    user_to = admin_to.create_user(user_from.uid, user_from.display_name, generate_key=False)
                    user_quota = admin_from.get_quota(b.owner, 'user')
                    admin_to.set_quota(b.owner, 'user', **user_quota)
                    bucket_quota = admin_from.get_quota(b.owner, 'bucket')
                    admin_to.set_quota(b.owner, 'bucket', **bucket_quota)
                    logger.info("Updated user quota for %s: user=%s bucket=%s", b.owner, user_quota, bucket_quota)
                user_to = ensure_swift_subuser(admin_to, b.owner)
                known_users_to[b.owner] = user_to

            swift_account_from = swift_account(src_s3.host, src_s3.port, user_from.swift_keys[0].user, user_from.swift_keys[0].access_key)
            swift_account_to = swift_account(dst_s3.host, dst_s3.port, user_to.swift_keys[0].user, user_to.swift_keys[0].access_key)

            swift_from = make_swift_connection(swift_account_from)
            swift_to = make_swift_connection(swift_account_to)

            container_from_hdrs, container_from = swift_from.get_container(b.name, full_listing=True)
            filtered_hdrs = {
                k: v for k, v in container_from_hdrs.items() if k in (
                    'x-storage-policy', 'default-placement', 'x-timestamp', 'x-container-read', 'x-container-write'
                    ) or k.startswith('x-container-meta-')}
            try:
                swift_to.head_container(b.name)
            except swiftclient.ClientException:
                swift_to.put_container(b.name, headers=filtered_hdrs)
            for obj in container_from:
                logger.info("Checking %s/%s (%d bytes, etag=%s)", b.name, obj['name'], obj['bytes'], obj['hash'])
                try:
                    obj_to_hdrs = swift_to.head_object(b.name, obj['name'])
                except swiftclient.ClientException:
                    pass
                else:
                    if int(obj_to_hdrs['content-length']) != obj['bytes'] or obj_to_hdrs['etag'] != obj['hash']:
                        logger.info("Key already present but out of date")
                        swift_to.delete_object(b.name, obj['name'])
                    else:
                        logger.info("Key already present and up to date")
                        continue
                logger.info("Submitting for upload")
                yield (swift_account_from, swift_account_to, b.name, obj['name'])

    pool = Pool(processes=jobs)
    for bucket, key, size, elapsed in pool.imap_unordered(migrate_object_job, iter_objects(src, dst)):
        if size < 0:
            logger.info("Upload of %s/%s FAILED!" % (bucket, key))
        else:
            logger.info("Upload of %s/%s completed in %ds" % (bucket, key, elapsed))
    pool.close()
    pool.join()


if __name__ == '__main__':
    # pylint: disable=E1120
    migrate()
