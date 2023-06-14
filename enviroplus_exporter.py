#!/usr/bin/env python3
from glob import glob
import os
import random
import requests
import time
import logging
import argparse
import subprocess
from threading import Thread
from math import log10

import ioexpander as io

from prometheus_client import start_http_server, Gauge, Histogram

from bme280 import BME280
from enviroplus import gas
from pms5003 import PMS5003, ReadTimeoutError as pmsReadTimeoutError, ChecksumMismatchError as pmsChecksumError, SerialTimeoutError as pmsSerialTimoutError
from scd4x import SCD4X

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559


o2Active = True
co2Active = True
tempActive = True
pressActive = True
humActive = True
gasActive = True
partActive = True
lightActive = True

MAXCOUNT = 2
MINCOUNT = 0
DEFAULT_READING = 0

o2Count = MINCOUNT
co2Count = MINCOUNT
tempCount = MINCOUNT
pressCount = MINCOUNT
humCount = MINCOUNT
gasCount = MINCOUNT
partCount = MINCOUNT
lightCount = MINCOUNT


try:
    # setting up IOexpander
    ioe = io.IOE(i2c_addr=0x18)
    o2_pin = 12
    ioe.set_adc_vref(5.0)  # Input voltage of IO Expander, this is 3.3 on Breakout Garden
    ioe.set_mode(o2_pin, io.ADC)
except:
    o2Active = False
    o2Count = MAXCOUNT
    logging.error("O2 sensor cannot be initialised")

logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler("enviroplus_exporter.log"),
              logging.StreamHandler()],
    datefmt='%Y-%m-%d %H:%M:%S')

logging.info("""enviroplus_exporter.py - Expose readings from the Enviro+ sensor by Pimoroni in Prometheus format

Press Ctrl+C to exit!

""")

DEBUG = os.getenv('DEBUG', 'false') == 'true'

bus = SMBus(1)

try:
    bme280 = BME280(i2c_dev=bus)
except:
    tempActive, pressActive, humActive = False
    tempCount, pressCount, humCount = MAXCOUNT
    logging.error('Temperature, Pressure, Humidity sensors not present or inactive')
    
try:
    pms5003 = PMS5003()
except:
    partActive = False
    partCount = MAXCOUNT
    logging.error('Particulate sensors not present or inactive')

#unit 1 gas base readings
_base_ox = 108706
_base_red = 396941
_base_nh3 = 365918

# tuning factors for unit 1
_ox_factor = 0.01
_red_factor = 2.0
_nh3_factor = 1.7

TEMPERATURE = Gauge('temperature','Temperature measured (*C)')
PRESSURE = Gauge('pressure','Pressure measured (hPa)')
HUMIDITY = Gauge('humidity','Relative humidity measured (%)')
OXIDISING = Gauge('oxidising','Mostly nitrogen dioxide but could include NO and Hydrogen (Ohms)')
REDUCING = Gauge('reducing', 'Mostly carbon monoxide but could include H2S, Ammonia, Ethanol, Hydrogen, Methane, Propane, Iso-butane (Ohms)')
NH3 = Gauge('NH3', 'mostly Ammonia but could also include Hydrogen, Ethanol, Propane, Iso-butane (Ohms)')
CO2 = Gauge('CO2', 'CO2 measured (PPM)')
O2 = Gauge('O2', 'O2 measured (%)')
LUX = Gauge('lux', 'current ambient light level (lux)')
PROXIMITY = Gauge('proximity', 'proximity, with larger numbers being closer proximity and vice versa')
PM1 = Gauge('PM1', 'Particulate Matter of diameter less than 1 micron. Measured in micrograms per cubic metre (ug/m3)')
PM25 = Gauge('PM25', 'Particulate Matter of diameter less than 2.5 microns. Measured in micrograms per cubic metre (ug/m3)')
PM10 = Gauge('PM10', 'Particulate Matter of diameter less than 10 microns. Measured in micrograms per cubic metre (ug/m3)')

OXIDISING_HIST = Histogram('oxidising_measurements', 'Histogram of oxidising measurements', buckets=(0, 10000, 15000, 20000, 25000, 30000, 35000, 40000, 45000, 50000, 55000, 60000, 65000, 70000, 75000, 80000, 85000, 90000, 100000))
REDUCING_HIST = Histogram('reducing_measurements', 'Histogram of reducing measurements', buckets=(0, 100000, 200000, 300000, 400000, 500000, 600000, 700000, 800000, 900000, 1000000, 1100000, 1200000, 1300000, 1400000, 1500000))
NH3_HIST = Histogram('nh3_measurements', 'Histogram of nh3 measurements', buckets=(0, 10000, 110000, 210000, 310000, 410000, 510000, 610000, 710000, 810000, 910000, 1010000, 1110000, 1210000, 1310000, 1410000, 1510000, 1610000, 1710000, 1810000, 1910000, 2000000))
CO2_HIST = Histogram('co2_measurements', 'Histogram of co2 measurements', buckets=(0, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 5000, 5500, 6000, 6500, 7000, 7500, 8000, 8500, 9000, 9500, 10000))
O2_HIST = Histogram('o2_measurements', 'Histogram of o2 measurements', buckets=(0, 2, 4, 6, 8, 10, 12, 16, 18, 20, 22, 24, 26))

PM1_HIST = Histogram('pm1_measurements', 'Histogram of Particulate Matter of diameter less than 1 micron measurements', buckets=(0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100))
PM25_HIST = Histogram('pm25_measurements', 'Histogram of Particulate Matter of diameter less than 2.5 micron measurements', buckets=(0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100))
PM10_HIST = Histogram('pm10_measurements', 'Histogram of Particulate Matter of diameter less than 10 micron measurements', buckets=(0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100))


# Sometimes the sensors can't be read. Resetting the i2c 
def reset_i2c():
    subprocess.run(['i2cdetect', '-y', '1'])
    time.sleep(2)


# Get the temperature of the CPU for compensation
def get_cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp = f.read()
        temp = int(temp) / 1000.0
    return temp


def get_temperature(factor):
    """Get temperature from the weather sensor"""
    # Tuning factor for compensation. Decrease this number to adjust the
    # temperature down, and increase to adjust up
    global tempActive
    global tempCount
    if tempActive:
        try:
            raw_temp = bme280.get_temperature()
        except (IOError, RuntimeError, OSError, ValueError):
            logging.error("Could not get temperature readings. Resetting i2c")
            reset_i2c()
            TEMPERATURE.set(0)
            tempCount += 1
            
            if tempCount >= MAXCOUNT:
                tempCount = MAXCOUNT
                tempActive = False

        if factor:
            cpu_temps = [get_cpu_temperature()] * 5
            cpu_temp = get_cpu_temperature()
            # Smooth out with some averaging to decrease jitter
            cpu_temps = cpu_temps[1:] + [cpu_temp]
            avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))
            temperature = raw_temp - ((avg_cpu_temp - raw_temp) / factor)
        else:
            temperature = raw_temp

        TEMPERATURE.set(temperature)   # Set to a given value
    else:
        TEMPERATURE.set(0)
        tempCount += 1
        logging.error("Temperature sensor not present or inactive")
        
        if tempCount > MINCOUNT:
            tempCount -= 1
        else:
            tempCount = MINCOUNT
            tempActive = True

def get_pressure():
    """Get pressure from the weather sensor"""
    global pressActive
    global pressCount
    if pressActive:
        try:
            pressure = bme280.get_pressure()
            PRESSURE.set(pressure)
        except (IOError, RuntimeError, OSError, ValueError):
            logging.error("Could not get pressure readings. Resetting i2c.")
            reset_i2c()
            PRESSURE.set(0)
            pressCount += 1
            
            if pressCount >= MAXCOUNT:
                pressCount = MAXCOUNT
                pressActive = False
    else:
        PRESSURE.set(0)
        logging.error("Pressure sensor not present or inactive")

        if pressCount > MINCOUNT:
            pressCount -= 1
        else:
            pressCount = MINCOUNT
            pressActive = True


def get_humidity():
    """Get humidity from the weather sensor"""
    global humActive
    global humCount
    if humActive:
        try:
            humidity = bme280.get_humidity()
            HUMIDITY.set(humidity)
        except (IOError, RuntimeError, OSError, ValueError):
            logging.error("Could not get humidity readings. Resetting i2c.")
            reset_i2c()
            HUMIDITY.set(0)

            humCount += 1

            if humCount >= MAXCOUNT:
                humCount = MAXCOUNT
                humActive = False
    else:
        HUMIDITY.set(0)
        logging.error("Humidity sensor not present or inactive")

        if humCount > MINCOUNT:
            humCount -= 1
        else:
            humCount = MINCOUNT
            humActive = True

def calc_ppm(ox, red, nh3):
    global _base_ox
    global _base_red
    global _base_nh3
    global _ox_factor
    global _red_factor
    global _nh3_factor
    
    try: # N02 PPM
        oxidising = round(((ox/_base_ox) * _ox_factor),2)
    except ZeroDivisionError:
        oxidising = 0.0
        
    try: # CO PPM
        reducing = round((10**(log10(red/_base_red)) * _red_factor),2)
    except ZeroDivisionError:
        reducing = 0.0

    try: # NH3 PPM
        ammonia = round((10**(log10(nh3/_base_nh3)) * _nh3_factor),2)
    except ZeroDivisionError:
        ammonia = 0.0

    return oxidising, reducing, ammonia

def get_gas():
    """Get all gas readings"""
    global gasActive
    global gasCount
    global MAXCOUNT
    global MINCOUNT
    
    if gasActive:
        try:
            gas.enable_adc()
            readings = gas.read_all()
            ox = readings.oxidising
            red = readings.reducing
            nh3 = readings.nh3
            ppm = calc_ppm(ox, red, nh3)
            
        except (IOError, RuntimeError, OSError, ValueError):
            logging.error("Could not get gas reading. Resetting i2c.")
            reset_i2c()
            
            OXIDISING.set(0)
            OXIDISING_HIST.observe(0)

            REDUCING.set(0)
            REDUCING_HIST.observe(0)

            NH3.set(0)
            NH3_HIST.observe(0)

            gasCount += 1

            if gasCount >= MAXCOUNT:
                gasCount = MAXCOUNT
                gasActive = False
        else:
            OXIDISING.set(ppm[0])
            OXIDISING_HIST.observe(ppm[0])

            REDUCING.set(ppm[1])
            REDUCING_HIST.observe(ppm[1])

            NH3.set(ppm[2])
            NH3_HIST.observe(ppm[2])
    else:
        OXIDISING.set(0)
        OXIDISING_HIST.observe(0)

        REDUCING.set(0)
        REDUCING_HIST.observe(0)

        NH3.set(0)
        NH3_HIST.observe(0)
        logging.error("GAS sensor not present or inactive")

        if gasCount > MINCOUNT:
            gasCount -= 1
        else:
            gasCount = MINCOUNT
            gasActive = True


def get_o2():
    """Get O2 reading via ioexpander"""
    global o2Active
    global o2Count

    if o2Active:
        try:
            adc = ioe.input(o2_pin)
            adc = round(adc,2)     
        except (IOError, RuntimeError, OSError, ValueError):
            logging.error("Could not get o2 reading. Resetting i2c.")
            reset_i2c()
            o2Count += 1
            O2.set(0)
            O2_HIST.observe(0)

            if o2Count >= MAXCOUNT:
                o2Count = MAXCOUNT
                o2Active = False
        else:
            O2.set(((adc*0.212)/2.0)*100)
            O2_HIST.observe(((adc*0.212)/2.0)*100)
    else:
        O2.set(0)
        O2_HIST.observe(0)
        logging.error("O2 sensor not present or inactive")

        if o2Count > MINCOUNT:
            o2Count -= 1
        else:
            o2Count = MINCOUNT
            o2Active = True


def get_co2():
    """Get CO2 readings plus additional sensor readings"""
    global co2Active
    global co2Count

    if co2Active: 
        try:
            co2, temperature, relative_humidity, timestamp = device.measure()
        except (IOError, RuntimeError, OSError, ValueError):
            logging.error("Could not get CO2 reading. Resetting i2c.")
            reset_i2c()
            co2Count += 1
            CO2.set(0)
            CO2_HIST.observe(0)

            if co2Count >= MAXCOUNT:
                co2Count = MAXCOUNT
                co2Active = False
        else:
            CO2.set(co2)
            CO2_HIST.observe(co2)
    else:
        CO2.set(0)
        CO2_HIST.observe(0)
        logging.error("CO2 sensor not present or inactive")

        if co2Count > MINCOUNT:
            co2Count -= 1
        else:
            co2Count = MINCOUNT
            co2Active = True


def get_light():
    """Get all light readings"""
    global lightActive
    global lightCount

    if lightActive:
        try:
           lux = ltr559.get_lux()
           prox = ltr559.get_proximity()

           LUX.set(lux)
           PROXIMITY.set(prox)
        except (IOError, RuntimeError, OSError, ValueError):
            logging.error("Could not get lux and proximity readings. Resetting i2c.")
            reset_i2c()
            LUX.set(0)
            PROXIMITY.set(0)

            lightCount += 1

            if lightCount >= MAXCOUNT:
                lightCount = MAXCOUNT
                lightActive = False
    else:
        LUX.set(0)
        PROXIMITY.set(0)
        logging.error("Light and Proximity sensors not present or inactive")

        if lightCount > MINCOUNT:
            lightCount -= 1
        else:
            lightCount = MINCOUNT
            lightActive = True


def get_particulates():
    """Get the particulate matter readings"""
    global partActive
    global partCount
    if partActive:
        try:
            pms_data = pms5003.read()
        except (IOError, pmsReadTimeoutError, pmsChecksumError, pmsSerialTimoutError, RuntimeError, OSError, ValueError):
            logging.error("Could not get particulate matter readings.")
            PM1.set(0)
            PM25.set(0)
            PM10.set(0)
            PM1_HIST.observe(0)
            PM25_HIST.observe(0)
            PM10_HIST.observe(0)
            
            partCount += 1

            if partCount >= MAXCOUNT:
                partCount = MAXCOUNT
                partActive = False
        else:
            PM1.set(pms_data.pm_ug_per_m3(1.0))
            PM25.set(pms_data.pm_ug_per_m3(2.5))
            PM10.set(pms_data.pm_ug_per_m3(10))

            PM1_HIST.observe(pms_data.pm_ug_per_m3(1.0))
            PM25_HIST.observe(pms_data.pm_ug_per_m3(2.5) - pms_data.pm_ug_per_m3(1.0))
            PM10_HIST.observe(pms_data.pm_ug_per_m3(10) - pms_data.pm_ug_per_m3(2.5))
    else:
        PM1.set(0)
        PM25.set(0)
        PM10.set(0)

        PM1_HIST.observe(0)
        PM25_HIST.observe(0)
        PM10_HIST.observe(0)
        logging.error("Particulate sensors not present or inactive")

        if partCount > MINCOUNT:
            partCount -= 1
        else:
            partCount = MINCOUNT
            partActive = True


def collect_all_data():
    """Collects all the data currently set"""
    sensor_data = {}
    sensor_data['temperature'] = TEMPERATURE.collect()[0].samples[0].value
    sensor_data['humidity'] = HUMIDITY.collect()[0].samples[0].value
    sensor_data['pressure'] = PRESSURE.collect()[0].samples[0].value
    sensor_data['oxidising'] = OXIDISING.collect()[0].samples[0].value
    sensor_data['reducing'] = REDUCING.collect()[0].samples[0].value
    sensor_data['nh3'] = NH3.collect()[0].samples[0].value
    sensor_data['lux'] = LUX.collect()[0].samples[0].value
    sensor_data['proximity'] = PROXIMITY.collect()[0].samples[0].value
    sensor_data['pm1'] = PM1.collect()[0].samples[0].value
    sensor_data['pm25'] = PM25.collect()[0].samples[0].value
    sensor_data['pm10'] = PM10.collect()[0].samples[0].value
    sensor_data['o2'] = O2.collect()[0].samples[0].value
    sensor_data['co2'] = CO2.collect()[0].samples[0].value
    return sensor_data


def str_to_bool(value):
    if value.lower() in {'false', 'f', '0', 'no', 'n'}:
        return False
    elif value.lower() in {'true', 't', '1', 'yes', 'y'}:
        return True
    raise ValueError('{} is not a valid boolean value'.format(value))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-b", "--bind", metavar='ADDRESS', default='0.0.0.0', help="Specify alternate bind address [default: 0.0.0.0]")
    parser.add_argument("-p", "--port", metavar='PORT', default=8000, type=int, help="Specify alternate port [default: 8000]")
    parser.add_argument("-f", "--factor", metavar='FACTOR', type=float, help="The compensation factor to get better temperature results when the Enviro+ pHAT is too close to the Raspberry Pi board")
    parser.add_argument("-e", "--enviro", metavar='ENVIRO', type=str_to_bool, help="Device is an Enviro (not Enviro+) so don't fetch data from gas and particulate sensors as they don't exist")
    parser.add_argument("-d", "--debug", metavar='DEBUG', type=str_to_bool, help="Turns on more verbose logging, showing sensor output and post responses [default: false]")
    args = parser.parse_args()

    # Start up the server to expose the metrics.
    start_http_server(addr=args.bind, port=args.port)
    # Generate some requests.
    
    try:
        # Enable CO2 sensor
        device = SCD4X(quiet=False)
        device.start_periodic_measurement()
    except:
        co2Active = False
        logging.error("CO2 sensor cannot be initialised")

    if args.debug:
        DEBUG = True

    if args.factor:
        logging.info("Using compensating algorithm (factor={}) to account for heat leakage from Raspberry Pi board".format(args.factor))

    logging.info("Listening on http://{}:{}".format(args.bind, args.port))

    while True:
        get_temperature(args.factor)
        get_pressure()
        get_humidity()
        get_light()
        if not args.enviro:
            get_gas()
            get_o2()
            get_particulates()
            get_co2()
        if DEBUG:
            logging.info('Sensor data: {}'.format(collect_all_data()))
