#!/usr/bin/env python3

"""Retrieves kid-control data from Mikrotik router and stores them in a DB

It is assumed that the following script runs on a Mikrotik router periodically:

    :local time [/system clock get time]
    :local hh [ :pick $time 0 2 ]
    :local mm [ :pick $time 3 5 ]
    :local fname "/sdcard/$hh-$mm"
    /execute {
    /ip kid-control device remove [find where dynamic]
    /ip kid-control device print detail
    /ip kid-control device reset-counters
    } file=$fname

It is further assumed that the user mtkidcon exists on the router and the user
can SSH into the router using an SSH key. This script downloads all files
produced by the aforementioned Mikrotik script, parses the files, and stores
them in an SQLite database.
"""

from datetime import datetime
import logging.config
import argparse
import sqlite3
import tempfile
import os
import subprocess

logconfig = {
    'version': 1,
    'disable_existing_loggers': 'no',
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(process)s] %(levelname)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        }
    },
    'handlers': {
        'file_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'filename': 'info.log',
            'maxBytes': 10485760,
            'backupCount': 5,
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['file_handler'],
        'propagate': 'no'
    }
}

def parse_contents(contents):
    "returns output of 'print detail' parsed into a dictionary"
    h = {}
    name = None
    for l in contents.split():
        if '=' in l:
            (key, value) = l.split('=')
            value = value.strip('"')
            if key in ('bytes-down', 'bytes-up'):
                if value.endswith('KiB'):
                    value = float(value[:-3]) * 1024
                elif value.endswith('MiB'):
                    value = float(value[:-3]) * 1024 * 1024
                elif value.endswith('GiB'):
                    value = float(value[:-3]) * 1024 * 1024 * 1024
                else:
                    value = float(value)
            if key == 'name':
                name = value
                h[name] = {}
            else:
                h[name][key] = value
    return h

def process_file(cur, dirname, filename):
    with open(os.path.join(dirname, filename), 'r') as fd:
        contents = fd.read()
    kc_dict = parse_contents(contents)
    hh = filename[0:2]
    mm = filename[3:5]
    now = datetime.now()
    dt = datetime(now.year, now.month, now.day, int(hh), int(mm))
    if dt > now:
        dt = datetime(now.year, now.month, now.day - 1, int(hh), int(mm))
    for name in kc_dict.keys():
        bytes_up, bytes_down = (0, 0)
        if 'bytes-down' in kc_dict[name]:
            bytes_down = kc_dict[name]['bytes-down']
        if 'bytes-up' in kc_dict[name]:
            bytes_up = kc_dict[name]['bytes-up']
        cur.execute("""
            INSERT INTO mtkidcon VALUES(datetime(?), ?, ?, ?)
            ON CONFLICT(timestamp, name) DO
            UPDATE SET bytes_up = ?, bytes_down = ?
        """, (dt.isoformat(), name, bytes_up, bytes_down, bytes_up, bytes_down))

def parse_arguments():
    parser = argparse.ArgumentParser()

    parser.add_argument("--router", default='router',
                        help="Mikrotik router hostname")
    parser.add_argument("--router-dir", default='sdcard',
                        help="directory on router where kid-control data is")
    parser.add_argument("--local-dir", default='disk1',
                        help="directory in tmpdir where kid-control data is")
    parser.add_argument("--ssh-key", default='ssh/mtkidcon',
                        help="SSH key pathname for scp")
    parser.add_argument("--ssh-user", default='mtkidcon',
                        help="SSH key pathname for scp")
    parser.add_argument("--sqlite-db", default='mtkidcon.db',
                        help="SQLite database where to store retrieved data")
    parser.add_argument("--print",
                        help="print DB data for the specified user")

    return parser.parse_args()

def main():
    logger.info('started')
    args = parse_arguments()
    con = sqlite3.connect(args.sqlite_db)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mtkidcon(
            timestamp  DATETIME,
            name       TEXT,
            bytes_up   NUMERIC,
            bytes_down NUMERIC,
            PRIMARY KEY(timestamp, name))
        """)
    if args.print:
        for row in cur.execute("""
            SELECT timestamp, bytes_up, bytes_down
            FROM mtkidcon WHERE name = ? ORDER BY 1
        """, (args.print,)):
            print('{} {} {}'.format(row[0], row[1], row[2]))
        return
    with tempfile.TemporaryDirectory() as td:
        proc = subprocess.Popen(
            ['scp', '-i', args.ssh_key, '-rq',
             f'{args.ssh_user}@{args.router}:{args.router_dir}', td],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        (out, err) = proc.communicate()
        if err:
            logger.info("scp error: {}".format(err.decode('utf-8')))
        dirname = os.path.join(td, args.local_dir)
        for filename in os.listdir(dirname):
            process_file(cur, dirname, filename)
    con.commit()
    logger.info('stopped')

logging.config.dictConfig(logconfig)
logger = logging.getLogger(__name__)
try:
    main()
except Exception:
    logger.exception("Fatal error")
