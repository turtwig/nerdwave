#!/usr/bin/env python

import argparse
import sys

import libs.config
import libs.db
import libs.log

parser = argparse.ArgumentParser(description="Nerdwave DB table creator.")
parser.add_argument("--config", default=None)
args = parser.parse_args()
libs.config.load(args.config)

libs.log.init()
libs.db.connect()

libs.db.create_tables()
libs.db.add_custom_fields()

print("Done")
print()

sys.exit(0)
