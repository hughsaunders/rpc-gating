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


    Influx structure:
        Measurement (eg maas_nova)
            Series (per tag eg job_reference)
            Point (per timestamp)
                Fields (Eg keystone_user_count)

    Fields is a list of key, value pairs.
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


class InfluxTimestampParseException(Exception):
    pass


class SubunitContext():
    """Context manager for writing subunit results."""

    def __init__(self, output_path):
        self.output_path = output_path

    def __enter__(self):
        self.output_stream = open(self.output_path, 'wb')
        self.output = StreamResultToBytes(self.output_stream)
        self.output.startTestRun()
        return self.output

    def __exit__(self, *args, **kwargs):
        self.output.stopTestRun()
        self.output_stream.close()

    def status(self, *args, **kwargs):
        self.output.status(*args, **kwargs)


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
        raise InfluxTimestampParseException(
            "Error parsing a timestamp from influx: {}".format(alle))
    return fpt


def calculate_measurement_uptime(client, measurement, job_reference,
                                 measurement_period_seconds,
                                 resolution=60):
    """ For a certain build (job_reference), estimate the amount
    of seconds a component (column of table) was up.
    For that, we slice the job by resolution.
    If I have no data in the slice, I assume downtime. If I have
    at least one data per slice, I assume up, even if degraded.
    The count of these positive events multiplied by resolution
    is an average idea of the uptime.
    """
    per_field_downtime_seconds = defaultdict(int)
    per_field_downtime_percent = {}

    query = (
        "select max(/.*_status/) from {measurement} "
        "where time < now() and job_reference='{job_reference}' "
        "group by time({resolution}s) fill(-1)"
    ).format(measurement=measurement,
             job_reference=job_reference,
             resolution=resolution)

    all_data = client.query(query)
    for time_slice in all_data.get_points():
        for element in time_slice:
            if element != 'time' and time_slice[element] == 1:
                key = element.replace('max_', '')
                per_field_downtime_seconds[key] += resolution
                per_field_downtime_percent[key] = \
                    (float(per_field_downtime_seconds[key]) /
                        measurement_period_seconds) * 100

    # total_seconds is the measurement period multiplied by the number of
    # series. This is used to create an average percent for the measurement.
    total_seconds = len(per_field_downtime_seconds) \
        * measurement_period_seconds
    total_downtime = sum(per_field_downtime_seconds.values())
    measurement_downtime_seconds = total_downtime \
        / len(per_field_downtime_seconds)
    measurement_downtime_percent = \
        (float(total_downtime)/total_seconds) * 100

    return dict(
        measurement_downtime_seconds=measurement_downtime_seconds,
        measurement_downtime_percent=measurement_downtime_percent,
        per_field_downtime_percent=per_field_downtime_percent,
        per_field_downtime_seconds=dict(per_field_downtime_seconds)
    )


def build_report_dict(client, measurements, job_reference,
                      measurement_period_seconds):
    report = defaultdict(dict)
    for measurement in measurements:
        measurement_data = calculate_measurement_uptime(
            client, measurement, job_reference, measurement_period_seconds)
        report['measurement_downtime_seconds'][measurement] = \
            measurement_data['measurement_downtime_seconds']
        report['measurement_downtime_percent'][measurement] = \
            measurement_data['measurement_downtime_percent']
        report['per_field_downtime_seconds'][measurement] = \
            measurement_data['per_field_downtime_seconds']
        report['per_field_downtime_percent'][measurement] = \
            measurement_data['per_field_downtime_percent']
    return dict(report)


def main(args=None):
    client = InfluxDBClient(args.influx_ip, args.influx_port,
                            database='telegraf')

    # First find the first and last timestamp from telegraf.
    # This way querying maas_* data will always
    # be accurate (if no data is reported by maas plugins,
    # this should be a failure metric)
    find_first_timestamp_query = (
        "select first(total) from processes "
        "where job_reference = '{}';".format(args.job_reference)
    )
    first_ts = return_time(client, find_first_timestamp_query, delta_seconds=5)
    find_last_timestamp_query = (
        "select last(total) from processes "
        "where job_reference = '{}';".format(args.job_reference))
    last_ts = return_time(client, find_last_timestamp_query, delta_seconds=-5)

    logging.info(
        "Metrics were gathered between {first} and {last}".format(
            first=first_ts,
            last=last_ts))

    measurement_period_seconds = (last_ts - first_ts).seconds

    measurements = ['maas_glance', 'maas_cinder', 'maas_keystone',
                    'maas_heat', 'maas_neutron', 'maas_nova', 'maas_horizon']

    report = build_report_dict(
        client, measurements, args.job_reference, measurement_period_seconds)

    if args.ymlreport:
        yml_report = yaml.safe_dump(report, default_flow_style=False)
        logging.info(yml_report)
        with open(args.ymlreport, 'w+') as output_file:
            output_file.write("---\n")
            output_file.write(yml_report)

    # Subunit is a unit test result format. Here we output a stream of
    # test results, one per measurement.
    # Each measurement is a successfull test if its overall downtime
    # is less than args.max_downtime
    if args.subunitreport:
        with SubunitContext(args.subunitreport) as output:
            for measurement_name, downtime_seconds in \
                    report['measurement_downtime_seconds'].items():
                status = "success"
                if downtime_seconds > args.max_downtime:
                    status = "fail"
                # Record test start
                output.status(
                    test_id=measurement_name,
                    timestamp=first_ts
                )

                # Record end of test
                output.status(
                    test_id=measurement_name,
                    # TODO(hughsaunders): Be more intelligent about thresholds
                    test_status=status,
                    test_tags=None,
                    runnable=False,
                    # file_name=measure,
                    # file_bytes=str(measurement_data[measure]).encode('utf-8'),
                    timestamp=last_ts,
                    eof=True,
                    mime_type='text/plain; charset=UTF8'
                )


if __name__ == "__main__":
    """ Args parser and logic router """
    logging.getLogger().setLevel(logging.INFO)
    parser = argparse.ArgumentParser(
        description='Fetch maas_ metrics, and report downtime data.')
    parser.add_argument("--ymlreport", help="Yaml report filename")
    parser.add_argument("--subunitreport", help="Subunit report filename")
    parser.add_argument(
        "--max-downtime",
        help="Maximum allowable downtime per service (seconds)",
        default=60*60,
        type=int)
    arguments = parser.parse_args()
    try:
        arguments.influx_ip = os.environ['INFLUX_IP']
    except KeyError:
        logging.error("Please set INFLUX_IP")
        sys.exit(1)

    try:
        arguments.influx_port = os.environ['INFLUX_PORT']
    except KeyError:
        logging.error("Please set INFLUX_PORT")
        sys.exit(2)

    try:
        arguments.job_reference = os.environ['BUILD_TAG']
    except KeyError:
        logging.error("Please set BUILD_TAG for its usage as job ref")
        sys.exit(3)
    main(arguments)
