#!/usr/bin/env python3

"""Parses Mikrotik log with kid-control date lines and stores them in a DB

It is assumed that the following script runs on a Mikrotik router periodically:

    :global getBytesUp do={
      :local t [/system clock get time]
      :local bup [/ip kid-control device get [find name=$n] bytes-up]
      :local bdown [/ip kid-control device get [find name=$n] bytes-down]
      :return "kid-control: $n bytes-up=$bup bytes-down=$bdown"
    }

    :log info [$getBytesUp n="xiaomi-dalibor"]
    :log info [$getBytesUp n="xiaomi-david"]
    :log info [$getBytesUp n="samsung-dalibor"]
    :log info [$getBytesUp n="lenovo-wifi"]
    /ip kid-control device reset-counters

This script reads log lines produced by the Mikrotik router from standard input and stores them in an SQLite database.
"""

from datetime import datetime, timedelta
import logging.config
import argparse, sqlite3, sys, re

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

class D(datetime):
    "subclass of datetime which supplies a default year for strptime"
    @classmethod
    def strptime(cls, datestring, fmt):
        d = datetime.strptime(datestring, fmt)
        if d.year == 1900:
            now = datetime.now()
            d1 = d.replace(year=now.year)
            d2 = d.replace(year=now.year-1)
            td1 = d1 - now
            td2 = d2 - now
            if td1 < timedelta(0):
                td1 = -td1
            if td2 < timedelta(0):
                td2 = -td2
            if td1 > td2:
                return d2
            return d1
        return d

def parse_bytes(value):
    "parses bytes with units and returns bytes"
    if value.endswith('KiB'):
        return float(value[:-3]) * 1024
    if value.endswith('MiB'):
        return float(value[:-3]) * 1024 * 1024
    if value.endswith('GiB'):
        return float(value[:-3]) * 1024 * 1024 * 1024
    return float(value)

def parse_arguments():
    parser = argparse.ArgumentParser()
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
    for line in sys.stdin:
        match = re.search(
            '(\w\w\w \d\d \d\d:\d\d:\d\d) \S+ kid-control: (\S+) bytes-up=(\S+) bytes-down=(\S+)', line)
        if match:
            ts = D.strptime(match.group(1), '%b %d %H:%M:%S')
            name = match.group(2)
            bytes_up = parse_bytes(match.group(3))
            bytes_down = parse_bytes(match.group(4))
            cur.execute("""
                INSERT INTO mtkidcon VALUES(datetime(?), ?, ?, ?)
                ON CONFLICT(timestamp, name) DO
                UPDATE SET bytes_up = ?, bytes_down = ?
            """, (ts.isoformat(), name,
                  bytes_up, bytes_down, bytes_up, bytes_down))
    con.commit()
    logger.info('stopped')

logging.config.dictConfig(logconfig)
logger = logging.getLogger(__name__)
try:
    main()
except Exception:
    logger.exception("Fatal error")
