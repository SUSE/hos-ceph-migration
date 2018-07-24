#!/usr/bin/env python

import os, re, sys, time, traceback
from collections import namedtuple
import radosgw
import boto
import boto.s3.connection
from boto.s3.keyfile import KeyFile
from multiprocessing import Pool
import click
import logging
import swiftclient
import json

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
    logger.info("CREATING SUBUSER: %s", params)
    response = self.make_request('PUT', path='/user?subuser', query_params=params)
    body = self._process_response(response)
    subuser_dict = json.loads(body)
    return subuser_dict

radosgw.connection.RadosGWAdminConnection.create_subuser = create_subuser

### END Workaround radosgw client API bugs

logging.basicConfig(level=logging.INFO, format='%(asctime)-15s %(levelname)-9s %(message)s')
logger = logging.getLogger(__name__)

OS_UID_RE = re.compile('^[0-9a-fA-F]{32}$')

s3_account = namedtuple('s3_account', ['host', 'port', 'access_key', 'secret_key'])

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

def make_boto_connection(account):
    return boto.connect_s3(
        aws_access_key_id=account.access_key,
        aws_secret_access_key=account.secret_key,
        host=account.host, port=account.port,
        calling_format=boto.s3.connection.OrdinaryCallingFormat(),
        is_secure=False
    )

def make_swift_connection(account, user, password):
    return swiftclient.Connection(authurl='http://{}:{}/auth'.format(account.host, account.port),
        user=user, key=password)

def migrate_object(src_s3, dst_s3, owner, bucket, key):
    logger.info("Uploading %s/%s as %s", bucket, key, owner.uid)
    try:
        t1 = time.time()
        s3_from = make_boto_connection(src_s3)
        swift_cred = owner.swift_keys[0]
        swift_to = make_swift_connection(dst_s3, swift_cred.user, swift_cred.access_key)
        key_from = s3_from.get_bucket(bucket).get_key(key)
        orig_headers = s3_from.make_request('HEAD', bucket, key).getheaders()
        headers = {k: v for k, v in orig_headers if k in ('x-object-manifest', 'content-type')}
        headers.update({k.replace('x-amz-meta-', 'x-object-meta-'): v for k, v in orig_headers if k.startswith('x-amz-meta-')})
        lo = headers.get('x-object-manifest')
        if key_from.size > 0 and lo is None:
            swift_to.put_object(bucket, key, KeyFile(key_from), headers=headers)
        else:
            swift_to.put_object(bucket, key, '', headers=headers)
        key_from.close()
        t2 = time.time()
        elapsed = t2 - t1
        return (bucket, key, key_from.size, elapsed)
    except:
        logger.exception('Uploading %s/%s FAILED!', bucket, key)
        return (bucket, key, -1, traceback.extract_stack())

def migrate_object_job(migration):
    return migrate_object(*migration)

@click.command()
@click.option('--jobs', '-j', type=click.IntRange(min=1, max=None), default=10,
    help='Number of parallel trasfers (default=10)')
@click.argument('src', type=str, nargs=1)
@click.argument('dst', type=str, nargs=1)
def migrate(src, dst, jobs):
    src_s3 = decode_s3_account(src)
    dst_s3 = decode_s3_account(dst)
    admin_from = make_admin_connection(src_s3)
    s3_from = make_boto_connection(src_s3)
    admin_to = make_admin_connection(dst_s3)
    s3_to = make_boto_connection(dst_s3)

    def iter_objects(admin_from, s3_from, admin_to, s3_to):
        known_users = {}

        buckets = admin_from.get_buckets()

        for b in buckets:
            if OS_UID_RE.match(b.owner) is None:
                # Ignore buckets not owned by openstack users
                continue
            logger.info("Migrating bucket "+b.name)
            if b.owner not in known_users:
                user = admin_from.get_user(b.owner)
                try:
                    user_to = admin_to.get_user(b.owner)
                except radosgw.exception.NoSuchUser:
                    logger.info("Migrating user: %s (%s)" % (user.display_name, user.uid), exc_info=0)
                    user_to = admin_to.create_user(user.uid, user.display_name, generate_key=False)
                if len(user_to.swift_keys) < 1:
                    admin_to.create_subuser(user.uid, user.uid+':migration', key_type='swift', generate_secret=True, access='full')
                    user_to = admin_to.get_user(b.owner)
                known_users[b.owner] = user_to
            bucket_from = s3_from.get_bucket(b.name)
            logger.info("CONTAINER ACL: %s", bucket_from.get_policy())
            try:
                bucket_to = s3_to.get_bucket(b.name)
            except boto.exception.S3ResponseError:
                bucket_to = s3_to.create_bucket(b.name)
                admin_bucket_to = admin_to.get_bucket(b.name)
                if admin_bucket_to.owner != b.owner:
                    logger.info("Setting owner of bucket %s to %s" % (b.name, b.owner), exc_info=0)
                    admin_bucket_to.unlink()
                    admin_bucket_to.link(b.owner)
            for key_from in bucket_from.list():
                logger.info("Checking %s/%s (%d bytes, etag=%s)" % (b.name, key_from.name, key_from.size, key_from.etag))
                key_to = bucket_to.get_key(key_from.name)
                if key_to is not None:
                    if key_to.size != key_from.size or key_to.etag != key_from.etag:
                        logger.info("Key already present but out of date")
                        key_to.delete()
                    else:
                        logger.info("Key already present and up to date")
                        continue
                logger.info("Submitting for upload")
                yield (src_s3, dst_s3, known_users[key_from.owner.id], b.name, key_from.name)

    pool = Pool(processes=jobs)
    for bucket, key, size, elapsed in pool.imap_unordered(migrate_object_job, iter_objects(admin_from, s3_from, admin_to, s3_to)):
        if size < 0:
            logger.info("Upload of %s/%s FAILED!" % (bucket, key))
        else:
            logger.info("Upload of %s/%s completed in %ds" % (bucket, key, elapsed))
    pool.close()
    pool.join()


if __name__ == '__main__':
    # pylint: disable=E1120
    migrate()
