'''
This script manages and monitors network targets using SQLite databases to track their status. 
It includes functionality to sample targets, log responses, and manage monitoring processes 
across multiple nodes. Designed for testing and production, the script supports running in 
test mode or as a monitoring process.

Main Features:
    - SQLite-based tracking of network targets.
    - Sampling network targets using `ping`.
    - Rotating target sampling to distribute load across nodes.
    - Monitoring status and logging responses.

Usage:
    python script_name.py test
        Runs the script in test mode with predefined data.

    python script_name.py monitor <master_db> <working_db> <src_address>
        Monitors targets listed in the master database and logs results in the working database.

Dependencies:
    - SQLite
    - Mininet
    - Logging
'''
from typing import Tuple
import os
import sys
import sqlite3
import shutil
import time
import subprocess
import mininet.net
import logging


def open_db(file_path: str) -> sqlite3.Connection:
    '''
Open a connection to an SQLite database.

Args:
    file_path (str): Path to the SQLite database file.

Returns:
    sqlite3.Connection: A connection object to interact with the database.
'''
    
    db = sqlite3.connect(file_path)
    return db


def create_db(file_path: str):
    '''
Create a new SQLite database and initialize it with the schema.

Args:
    file_path (str): Path where the SQLite database will be created.

Raises:
    IOError: If the schema file cannot be read.
'''

    data_dir = os.path.dirname(file_path)
    if len(data_dir) > 0 and not os.path.exists(data_dir):
        os.makedirs(data_dir)

    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    db = sqlite3.connect(file_path)
    with open(path) as f:
        db.executescript(f.read())
    db.close()


def is_running(db, address: str) -> bool:
    '''
Check if a target is marked as running in the database.

Args:
    db (sqlite3.Connection): Database connection.
    address (str): Address of the target.

Returns:
    bool: True if the target is running, False otherwise.
'''

    c = db.cursor()
    q = c.execute("SELECT running FROM targets WHERE address = ?", (address,))
    entry = q.fetchone()
    return entry[0]


def set_running(db, address: str, running: bool):
    '''
Update the running status of a target in the database.

Args:
    db (sqlite3.Connection): Database connection.
    address (str): Address of the target.
    running (bool): Running status to set (True or False).
'''

    c = db.cursor()
    q = c.execute(
        "UPDATE targets SET running = ? WHERE address = ?",
        (
            running,
            address,
        ),
    )
    db.commit()


def can_run(db, address: str) -> bool:
    '''
Check if a target can be sampled based on the database.

Args:
    db (sqlite3.Connection): Database connection.
    address (str): Address of the target.

Returns:
    bool: True if the target can be sampled, False otherwise.
'''

    c = db.cursor()
    q = c.execute("SELECT run FROM targets WHERE address = ?", (address,))
    entry = q.fetchone()
    return entry[0]


def set_can_run(db, address: str, can_run):
    '''
Update the 'can_run' status of a target in the database.

Args:
    db (sqlite3.Connection): Database connection.
    address (str): Address of the target.
    can_run (bool): New value for the 'can_run' status.
'''

    c = db.cursor()
    q = c.execute(
        "UPDATE targets SET run = ? WHERE address = ?",
        (
            can_run,
            address,
        ),
    )
    db.commit()


def get_status_count(db, stable: bool) -> Tuple[int, int]:
    '''
Retrieve the count of responding and total targets.

Args:
    db (sqlite3.Connection): Database connection.
    stable (bool): If True, filter by stable targets only.

Returns:
    tuple[int, int]: A tuple containing the count of responding targets and total targets.
'''

    c = db.cursor()
    # May sample only stable node connections or all
    if stable:
        q = c.execute("SELECT COUNT(*) FROM targets WHERE stable = TRUE AND responded = TRUE")
        good_targets = q.fetchone()[0]
        q = c.execute("SELECT COUNT(*) FROM targets WHERE stable = TRUE AND total_count > 0")
        total_targets = q.fetchone()[0]
    else:
        q = c.execute("SELECT COUNT(*) FROM targets WHERE responded = TRUE")
        good_targets = q.fetchone()[0]
        q = c.execute("SELECT COUNT(*) FROM targets WHERE total_count > 0")
        total_targets = q.fetchone()[0]
    c.close()
    return good_targets, total_targets

def get_last_five(db) ->list[tuple[str,bool]]:
    '''
Get the last five targets sampled, along with their response status.

Args:
    db (sqlite3.Connection): Database connection.

Returns:
    list[tuple[str, bool]]: List of tuples with target names and response status.
'''

    c = db.cursor()
    q = c.execute("SELECT name, responded FROM targets ORDER BY sample_time DESC LIMIT 5")
    result = []
    for name, responded in q.fetchall():
        result.append((name, responded))
    return result

def get_status_list(db) -> dict[str, bool]:
    '''
Get the response status of all targets that have been sampled at least once.

Args:
    db (sqlite3.Connection): Database connection.

Returns:
    dict[str, bool]: A dictionary mapping target names to their response status.
'''

    c = db.cursor()
    q = c.execute("SELECT name, responded FROM targets WHERE total_count > 0")
    result = {}
    for e in q.fetchall():
        result[e[0]] = e[1]
    return result


TEST = False


def sample_target(db, name: str, address: str, stable: bool, src_address: str):
    '''
Sample a target by sending a ping and update the database with the results.

Args:
    db (sqlite3.Connection): Database connection.
    name (str): Name of the target.
    address (str): IP address of the target.
    stable (bool): Whether the target is stable.
    src_address (str): Source IP address to use for the ping.

Logs:
    - Ping results and process output.
    - Database updates for target status.
'''

    logging.info("sample target: %s", address)
    process = subprocess.run(
        ["ping", "-I", src_address, "-c1", "-W3", f"{address}"], capture_output=True, text=True
    )
    logging.info("%s", process.stdout)
    sent, received = mininet.net.Mininet._parsePing(process.stdout)
    result = sent == received

    prev_responded = False
    now = time.time()
    c = db.cursor()
    q = c.execute("SELECT responded FROM targets WHERE address = ?", (address,))
    qr = q.fetchall()
    if len(qr) == 0:
        c.execute("INSERT INTO targets (name, address, sample_time, stable) VALUES (?, ?, ?, ?)", (name, address, now, stable))
    else:
        prev_responded = qr[0][0]

    if result:
        c.execute(
            "UPDATE targets SET responded = TRUE, "
            + "sample_time = ?,"
            + "total_count = total_count + 1, "
            + "total_success = total_success + 1 "
            + "WHERE address = ?",
            (
                now,
                address,
            ),
        )
    elif prev_responded:
        c.execute(
            "UPDATE targets SET responded = FALSE, "
            + "sample_time = ?,"
            + "total_count = total_count + 1 WHERE address = ?",
            (
                now,
                address,
            ),
        )
    db.commit()


def monitor_targets(db_path_master: str, db_path_local: str, address: str):
    '''
Monitor targets listed in the master database and log results in the local database.

Args:
    db_path_master (str): Path to the master database file.
    db_path_local (str): Path to the local database file.
    address (str): Address of the current monitoring node.

Raises:
    Exception: If the monitoring process encounters an error.
'''

    logging.info("Monitoring targets from %s to %s", address, db_path_local)
    create_db(db_path_local)
    db_master = open_db(db_path_master)
    db_local = open_db(db_path_local)

    # Make an entry for the current monitoring process
    c = db_master.cursor()
    q = c.execute("SELECT name, stable FROM targets WHERE address = ?", (address,))
    name, stable = q.fetchone()
    c.close()
    c = db_local.cursor()
    c.execute("INSERT INTO targets (name, address, stable, me) VALUES (?, ?, ?, TRUE)", (name,address,stable))
    db_local.commit()
    c.close()

    running = True
    while running:
        targets = []
        logging.info("reload target list")
        c = db_master.cursor()
        q = c.execute("SELECT name, address, stable FROM targets")
        for entry in q.fetchall():
            targets.append(entry)

        index = -1
        for i in range(len(targets)):
            if targets[i][1] == address:
                index = i
                break

        if index != -1:
            # Rotate list so all elements are sampling different targets
            tmp = targets[index + 1 :]
            tmp.extend(targets[:index])
            targets = tmp

        for target in targets:
            if not TEST:
                time.sleep(5)
            running = can_run(db_master, address)
            if running:
                sample_target(db_local, target[0], target[1], target[2], address)
        if TEST:
            set_can_run(db_master, address, False)
        running = can_run(db_master, address)


def init_targets(db_file_path: str, data: list[tuple[str,str,bool]]):
    '''
Initialize the targets table in the database with the provided data.

Args:
    db_file_path (str): Path to the SQLite database file.
    data (list[tuple[str, str, bool]]): List of target tuples (name, address, stable).

Creates:
    - A clean database with the specified target entries.
'''

    create_db(db_file_path)

    db = open_db(db_file_path)
    c = db.cursor()
    q = c.execute("DELETE FROM targets")
    db.commit()

    for entry in data:
        target = entry[0]
        address = entry[1]
        stable = entry[2]

        c.execute(
            "INSERT INTO targets (name, address, stable) VALUES (?, ?, ?)", (target, address, stable)
        )
    db.commit()
    db.close()


def test() -> bool:
    '''
Run the script in test mode with predefined target data.

Returns:
    bool: True when the test completes successfully.

Logs:
    - Initialization of the test databases.
    - Sampling of test targets and their statuses.
    - Results of the test.
'''

    data = [
        ("host1", "192.168.33.1", True),
        ("host2", "192.168.44.2", True),
        ("host3", "192.168.55.2", True),
        ("host3", "192.168.55.3", False),
    ]
    db_master = "master.sqlite"
    db_working = "work.sqlite"

    init_targets(db_master, data)
    global TEST
    TEST = True
    set_running(open_db(db_master), data[1][1], True)
    monitor_targets(db_master, db_working, data[0][1])
    monitor_targets(db_master, db_working, data[3][1])
    good, total = get_status_count(open_db(db_working))
    results = get_status_list(open_db(db_working))
    results = get_last_five(open_db(db_working))
    print(f"status {good} / {total}")
    return True


if __name__ == "__main__":
    # Arguments:
    # test
    # monitor tmp_file_master tmp_file_working src_address
    logging.basicConfig(filename=f"/tmp/error_msg.{os.getpid()}", level=logging.INFO)

    if len(sys.argv) == 2:
        if sys.argv[1] == "test":
            logging.info("Starting test")
            test()
            sys.exit(0)
    elif len(sys.argv) == 5:
        if sys.argv[1] == "monitor":
            try:
                monitor_targets(sys.argv[2], sys.argv[3], sys.argv[4])
                sys.exit(0)
            except Exception as e:
                logging.error(str(e))
                sys.exit(-1)
    print("usage:")
    print("\tmonitor <master_db> <working_db> <src_address>")
    print("\ttest")
    sys.exit(-1)
