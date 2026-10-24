#!/usr/bin/env python3
"""Explicit schema setup. Production provisioning belongs to Athena's agent."""

import argparse
import os
from pathlib import Path

import psycopg2
from byteforge_mcp_file_friend_core.database import connection_options
from dotenv import load_dotenv
from psycopg2 import sql


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-database", action="store_true", help="Explicitly provision role and database"
    )
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--pg-password", default=os.environ.get("FF_DB_ADMIN_PASSWORD"))
    args = parser.parse_args()
    options = connection_options()
    if args.create_database:
        if not args.pg_password:
            parser.error("Provide FF_DB_ADMIN_PASSWORD or --pg-password for provisioning")
        admin = {**options, "dbname": "postgres", "user": args.pg_user, "password": args.pg_password}
        connection = psycopg2.connect(**admin)
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (options["user"],))
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(options["user"])),
                        (options["password"],),
                    )
                cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (options["dbname"],))
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(options["dbname"]), sql.Identifier(options["user"])
                        )
                    )
        finally:
            connection.close()
    connection = psycopg2.connect(**options)
    try:
        with connection, connection.cursor() as cursor:
            schema = os.environ.get("FF_DB_SCHEMA", "public")
            cursor.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
            cursor.execute((Path(__file__).resolve().parents[1] / "database/schema.sql").read_text())
    finally:
        connection.close()
    print("File Friend schema applied successfully")


if __name__ == "__main__":
    main()
