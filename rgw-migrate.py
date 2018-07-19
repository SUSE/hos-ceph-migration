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
### END Workaround radosgw client API bug

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

def migrate_object(src_s3, dst_s3, bucket, key):
    def progress(a, b):
        if b > 0:
            logger.info("Progress: %s/%s : %4.1f%%" % (bucket, key, a * 100.0 / b))
    logger.info("Uploading %s/%s" % (bucket, key))
    try:
        t1 = time.time()
        s3_from = make_boto_connection(src_s3)
        s3_to = make_boto_connection(dst_s3)
        key_from = s3_from.get_bucket(bucket).get_key(key)
        extra = s3_from.make_request('HEAD', bucket, key)
        headers = {k[2:]: v for k, v in extra.getheaders() if k.startswith('x-amz-meta') or k == 'x-object-manifest'}
        slo = headers.get('object-manifest')
        key_to = s3_to.get_bucket(bucket).new_key(key)
        if key_from.size > 0 and slo is None:
            key_to.set_contents_from_file(KeyFile(key_from), headers=headers, cb=progress)
        else:
            key_to.set_contents_from_string('', headers=headers)
        key_to.close()
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
                    logger.info("Migrating user: %s (%s)" % (user.display_name, user.uid))
                    user_to = admin_to.create_user(user.uid, user.display_name, generate_key=False)
                    user_to._update_from_user(user.__dict__)
                known_users[b.owner] = user
            bucket_from = s3_from.get_bucket(b.name)
            try:
                bucket_to = s3_to.get_bucket(b.name)
            except boto.exception.S3ResponseError:
                bucket_to = s3_to.create_bucket(b.name)
                admin_bucket_to = admin_to.get_bucket(b.name)
                if admin_bucket_to.owner != b.owner:
                    logger.info("Setting owner of bucket %s to %s" % (b.name, b.owner))
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
                yield (src_s3, dst_s3, b.name, key_from.name)

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
