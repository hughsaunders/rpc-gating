#!/usr/bin/env python3
""" Get all measurements for an influx job, and
outputs a report of uptime for maas_ components.
Inputs:
    - env.INFLUX_IP (mandatory)
    - env.INFLUX_PORT (mandatory)
    - env.BUILD_TAG (mandatory)
    - ymlreport <ymlreportfile> (optional)
    - subunitreport <subunitreport> (optional)
Outputs:
    - stdout
    - yaml file with uptime (when ymlreport is used)
    - subunit binary file with passing/failure criteria
    (when subunitreport is used)
    """
import argparse
from collections import defaultdict
import datetime
import logging
import os
import sys
import yaml

import dateutil.parser
from influxdb import InfluxDBClient
from subunit.v2 import StreamResultToBytes


def return_time(client, query, delta_seconds=0):
    """ From an InfluxDB query, fetch
    the first point time, and return a
    python time object. Shift it from
    a few seconds (delta_seconds) if necessary.
    """
    timestamp_query = client.query(query)
    # Get points is generator, we should just get first
    # point time (string type).
    fpt_str = next(timestamp_query.get_points())['time']
    try:
        fpt = dateutil.parser.parse(
            fpt_str) + datetime.timedelta(seconds=delta_seconds)
    except Exception as alle:
        raise SystemExit(alle)
    return fpt


def return_uptime(client, table, job_reference, resolution=60):
    """ For a certain build (job_reference), estimate the amount
    of seconds a component (column of table) was up.
    For that, we slice the job by resolution.
    If I have no data in the slice, I assume downtime. If I have
    at least one data per slice, I assume up, even if degraded.
    The count of these positive events multiplied by resolution
    is an average idea of the uptime.
    """
    uptimes = defaultdict(int)

    query = ("select max(/.*_status/) from {} where time < now() and "
             "job_reference='{}' group by time({}s) fill(-1)"
             ).format(table, job_reference, resolution)
    all_data = client.query(query)
    for time_slice in all_data.get_points():
        for element in time_slice:
            if element != 'time' and time_slice[element] == 1:
                uptimes[element.replace('max_', '')] += resolution
    return uptimes


def main(args=None):
    """ Main """

    try:
        influx_ip = os.environ['INFLUX_IP']
    except KeyError:
        logging.error("Please set INFLUX_IP")
        sys.exit(1)

    try:
        influx_port = os.environ['INFLUX_PORT']
    except KeyError:
        logging.error("Please set INFLUX_PORT")
        sys.exit(2)

    try:
        job_reference = os.environ['BUILD_TAG']
    except KeyError:
        logging.error("Please set BUILD_TAG for its usage as job ref")
        sys.exit(3)

    client = InfluxDBClient(influx_ip, influx_port, database='telegraf')

    # First find the first and last timestamp from telegraf.
    # This way querying maas_* data will always
    # be accurate (if no data is reported by maas plugins,
    # this should be a failure metric)
    find_first_timestamp_query = ("select first(total) from processes "
                                  "where job_reference = '{}';".format(job_reference))
    first_ts = return_time(client, find_first_timestamp_query, delta_seconds=5)
    find_last_timestamp_query = ("select last(total) from processes "
                                 "where job_reference = '{}';".format(job_reference))
    last_ts = return_time(client, find_last_timestamp_query, delta_seconds=-5)

    logging.info(
        "Metrics were gathered between {} and {}".format(first_ts, last_ts))

    measurements = ['maas_glance', 'maas_cinder', 'maas_keystone', 'maas_heat', 'maas_neutron',
                    'maas_nova', 'maas_horizon']

    yml_report = False
    subunit_report = False

    if args.ymlreport:
        yml_report = True
        output_file = open(args.ymlreport, 'w+')
        output_file.write("---\n")
    if args.subunitreport:
        subunit_report = True
        output_stream = open(args.subunitreport, 'wb')
        output = StreamResultToBytes(output_stream)
        output.startTestRun()

    for measurement in measurements:
        measurement_data = return_uptime(client, measurement, job_reference)
        yml_output = yaml.safe_dump(
            {measurement: dict(measurement_data)}, default_flow_style=False)
        logging.info(yml_output)
        if yml_report:
            output_file.write(yml_output)
        if subunit_report:
            for measure in measurement_data:
                # Input status
                output.status(
                    test_id=measure,
                    timestamp=first_ts
                )

                # Output the end of the event
                output.status(
                    test_id=measure,
                    # TODO(evrardjp): Define a Threshold to compare with
                    # and use it as a success criteria.
                    test_status="success",
                    test_tags=None,
                    runnable=False,
                    # file_name=measure,
                    # file_bytes=str(measurement_data[measure]).encode('utf-8'),
                    timestamp=last_ts,
                    eof=True,
                    mime_type='text/plain; charset=UTF8'
                )
    if subunit_report:
        output.stopTestRun()
        output_stream.close()
    if yml_report:
        output_file.close()


if __name__ == "__main__":
    """ Args parser and logic router """
    logging.getLogger().setLevel(logging.INFO)
    parser = argparse.ArgumentParser(
        description='Fetch maas_ metrics, and report downtime data.')
    parser.add_argument("--ymlreport", help="Yaml report filename")
    parser.add_argument("--subunitreport", help="Subunit report filename")
    arguments = parser.parse_args()
    main(arguments)
