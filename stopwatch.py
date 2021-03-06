# coding=utf-8
import sys
import time

import adafruit_ads1x15.ads1115 as ads
import csv
import json
import logging
import queue
import threading
import tkinter as tk
from PIL import ImageTk
from adafruit_ads1x15.ads1x15 import Mode
from adafruit_ads1x15.analog_in import AnalogIn
from collections import deque
from datetime import datetime as dtime
from pathlib import Path
from tkinter import ttk

import gettext
t = gettext.translation('stopwatch', 'l10n')
_ = t.gettext

try:
    import busio, board
except (NotImplementedError, FileNotFoundError):
    logging.warning(_('Bussio: Unsupported hardware. Disabling I2C feature.'))

import gpiozero

# Default file path if not specified in config file
CSV_FILE_PATH = 'stopwatch_log.csv'
CONFIG_PATH = 'config.json'
RPM_K_DEFAULT_VALUE = 1  # It should be in range 1..4
FLOW_K_DEFAULT_VALUE = 8.34
FLOW_Q_DEFAULT_VALUE = 0.229
PRESSURE_K_DEFAULT_VALUE = 20
PRESSURE_Q_DEFAULT_VALUE = 0
MANUAL_MEASUREMENT_DATA_DISPLAY_SECONDS = 2

LOG_LEVEL = logging.WARNING


class MainApp(object):
    _SCREEN_REFRESH_MS = 40
    _MEASURE_ORDER_PADDING = (50, 0)

    def __init__(self, parent):
        self._logger = logging.getLogger('MainApp')
        self._logger.setLevel(LOG_LEVEL)
        self._load_config()

        self._parent = parent
        self._parent.title(_('Firefighter Stopwatch'))
        self._parent.columnconfigure(0, weight=1)
        self._parent.rowconfigure(0, weight=1)

        # Make UI full screen
        self._parent.protocol("WM_DELETE_WINDOW", self.close)
        self._parent.focus_set()
        # self._parent.attributes('-fullscreen', True)
        self._parent.bind('<KeyPress>', self.close)
        self._parent.config(cursor='none')

        # Default styles
        ttk.Style().configure('Background.TFrame', background='#EEEEEE')
        ttk.Style().configure('Customized.Stopwatch.TLabel', background='#EEEEEE', font=('Microsoft Sans Serif', 60),
                              foreground='black')
        ttk.Style().configure('Customized.Main.TLabel', background='#EEEEEE', font=('Microsoft Sans Serif', 30),
                              foreground='black')

        # Define themed main frame
        main_frame = ttk.Frame(self._parent, style='Background.TFrame')
        main_frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(0, weight=1)

        content_frame = ttk.Frame(main_frame, style='Background.TFrame')
        content_frame.grid(column=0, row=0)

        # Arduino Development logo
        self._arduino_logo = ImageTk.PhotoImage(file='gfx/arduino_dev_logo.png')
        arduino_logo_label = ttk.Label(content_frame, style='Customized.Main.TLabel',
                                       image=self._arduino_logo, padding=(30, 0))
        arduino_logo_label.grid(column=0, row=0, columnspan=2)

        # Stopwatch
        self._stopwatch_label = ttk.Label(content_frame, style='Customized.Stopwatch.TLabel')
        self._stopwatch_label.grid(column=2, row=0, columnspan=3)
        self._stopwatch_label['text'] = '00:00.000'

        # Automatic measurement label
        auto_measurement_label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=20)
        auto_measurement_label.grid(column=0, row=1, columnspan=5)
        auto_measurement_label['text'] = _('Auto measurement')

        # Icons
        icon_images = ['gfx/clock_icon.png', 'gfx/rpm_icon.png', 'gfx/flow_icon.png', 'gfx/pressure_icon.png']
        self._icon_refs = []  # Necessary to keep the reference in order to avoid being garbage-collected
        icon_col = 1

        for icon in icon_images:
            self._icon_refs.append(ImageTk.PhotoImage(file=icon))
            label = ttk.Label(content_frame, style='Customized.Main.TLabel', image=self._icon_refs[-1])
            label.grid(column=icon_col, row=2)
            icon_col += 1

        icon_units = ['', _('(RPM)'), _('(l/min)'), _('(bar)')]
        icon_col = 1
        for unit in icon_units:
            label = ttk.Label(content_frame, style='Customized.Main.TLabel', text=unit)
            label.grid(column=icon_col, row=3)
            icon_col += 1

        # Auto-measurement rows
        initial_row = 4
        self._auto_measurement_labels = {'split_times': [], 'rpm': [], 'flow': [], 'pressure': []}

        for row in range(4):
            label = ttk.Label(content_frame, style='Customized.Main.TLabel',
                              padding=self._MEASURE_ORDER_PADDING)
            label.grid(column=0, row=initial_row + row)
            label['text'] = str(row + 1)

            label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=10,
                              anchor='center')
            label.grid(column=1, row=initial_row + row)
            self._auto_measurement_labels['split_times'].append(label)

            label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=6, anchor='center')
            label.grid(column=2, row=initial_row + row)
            self._auto_measurement_labels['rpm'].append(label)

            label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=6, anchor='center')
            label.grid(column=3, row=initial_row + row)
            self._auto_measurement_labels['flow'].append(label)

            label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=8, anchor='center')
            label.grid(column=4, row=initial_row + row)
            self._auto_measurement_labels['pressure'].append(label)

        # Manual measurement label
        label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=20)
        label.grid(column=0, row=8, columnspan=5)
        label['text'] = _('Manual measurement')

        self._manual_measurement_labels = {'split_times': [], 'rpm': [], 'flow': [], 'pressure': [],
                                           'symbol_label': None}

        label = ttk.Label(content_frame, style='Customized.Main.TLabel',
                          padding=self._MEASURE_ORDER_PADDING)
        label.grid(column=0, row=9)
        label['text'] = 'M'
        self._manual_measurement_labels['symbol_label'] = label
        label.grid_remove()

        label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=10, anchor='center')
        label.grid(column=1, row=9)
        self._manual_measurement_labels['split_times'].append(label)
        label.grid_remove()

        label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=6, anchor='center')
        label.grid(column=2, row=9)
        self._manual_measurement_labels['rpm'].append(label)

        label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=6, anchor='center')
        label.grid(column=3, row=9)
        self._manual_measurement_labels['flow'].append(label)

        label = ttk.Label(content_frame, style='Customized.Main.TLabel', padding=(30, 10), width=8, anchor='center')
        label.grid(column=4, row=9)
        self._manual_measurement_labels['pressure'].append(label)
        self._manual_measurement_running = False

        # Queue for UI thread to update components
        self._thread_queue = queue.Queue()
        self._parent.after(self._SCREEN_REFRESH_MS, self._update_ui)

        self._stopwatch = StopWatch(self)
        self._rpmmeter = RpmMeter(self)
        self._flowmeter = FlowMeter(self)
        self._pressure = PressureTransducer(self)

    # noinspection PyUnusedLocal
    def close(self, *args):
        self._parent.quit()

    def post_on_ui_thread(self, value):
        self._thread_queue.put(value)

    def _load_config(self, path=CONFIG_PATH):
        """
        Load configuration from local JSON file. For all mandatory parameters
        there are defaults on top of this file.
        """
        self.configuration = None
        with open(path, 'r') as f:
            self.configuration = json.loads(f.read())

    def _update_ui(self):
        """ Refresh UI """

        def update_ui_stopwatch_time(stopwatch_time: str):
            self._stopwatch_label['text'] = stopwatch_time

        def update_ui_set_current_measurement_data():
            if not self._manual_measurement_running:
                pressure = self._pressure.get_sliding_avg_pressure()
                self._manual_measurement_labels['rpm'][0]['text'] = str(self._rpmmeter.get_current_rpm())
                self._manual_measurement_labels['flow'][0]['text'] = str(self._flowmeter.get_current_flow())
                self._manual_measurement_labels['pressure'][0]['text'] = '{}/{}'.format(pressure[0], pressure[1])

        def set_measurement_data(row=0, split_time='', rpm='', flow='', pressure='', is_manual_measure=False):
            def runnable():
                time.sleep(MANUAL_MEASUREMENT_DATA_DISPLAY_SECONDS)
                self.post_on_ui_thread(StopWatch.MANUAL_MEASURE_ENDED)

            if is_manual_measure:
                self._manual_measurement_running = True
                self._manual_measurement_labels['symbol_label'].grid()
                self._manual_measurement_labels['split_times'][0].grid()

                self._manual_measurement_labels['split_times'][0]['text'] = split_time
                self._manual_measurement_labels['rpm'][0]['text'] = rpm
                self._manual_measurement_labels['flow'][0]['text'] = flow
                self._manual_measurement_labels['pressure'][0]['text'] = pressure

                worker = threading.Thread(target=runnable)
                worker.daemon = True
                worker.start()

            else:
                if row is None or row < 0 or row > 3:
                    # raise ValueError("Automatic measurements have at most 4 rows!")
                    return

                self._auto_measurement_labels['split_times'][row]['text'] = split_time
                self._auto_measurement_labels['rpm'][row]['text'] = rpm
                self._auto_measurement_labels['flow'][row]['text'] = flow
                self._auto_measurement_labels['pressure'][row]['text'] = pressure

        def clear_measurement_data():
            for idx in range(4):
                set_measurement_data(row=idx, split_time='', rpm='',
                                     flow='', pressure='',
                                     is_manual_measure=False)

            set_measurement_data(row=0, split_time='', rpm='',
                                 flow='', pressure='',
                                 is_manual_measure=True)

        def write_log_to_csv(checkpoint='', split_time='', flow='', rpm='',
                             pressure_1='', pressure_2='', is_manual_measure=False):
            write_header = False
            header = [_('Measurement date and time'), _('Checkpoint'), _('Time'), _('Flow (l/min)'),
                      _('Engine revs (1/min)'), _('Pressure #1 (bar)'), _('Pressure #2 (bar)'),
                      _('Flag for auto/manual measurement {A, M}')]

            data = [dtime.now().isoformat(), checkpoint, split_time, flow, rpm, pressure_1, pressure_2,
                    'A' if not is_manual_measure else 'M']

            csv_file = None

            if self.configuration is not None:
                try:
                    csv_file = self.configuration['logging']['location']
                except KeyError or AttributeError:
                    csv_file = CSV_FILE_PATH

            if not Path(csv_file).exists():
                write_header = True

            try:
                with open(csv_file, 'a', newline='') as f:
                    writer = csv.writer(f)

                    if write_header:
                        writer.writerow(header)

                    writer.writerow(data)
            except FileNotFoundError:
                self._logger.error(_("Unable to create log file. Check path in \'config.json\'."))

        def get_row_for_checkpoint(checkpoint: int):
            # checkpoint -> row mapping
            mapping = {4: 0,
                       3: 1,
                       2: 2,
                       1: 3}

            return mapping.get(checkpoint)

        update_ui_stopwatch_time(self._stopwatch.get_current_time())
        update_ui_set_current_measurement_data()

        try:
            event = self._thread_queue.get(False)

            # Events without data
            if type(event) == str:
                if event == StopWatch.STOPWATCH_RESET:
                    clear_measurement_data()
                if event == StopWatch.MANUAL_MEASURE_ENDED:
                    self._manual_measurement_labels['symbol_label'].grid_remove()
                    self._manual_measurement_labels['split_times'][0].grid_remove()
                    self._manual_measurement_running = False

            # Events with data as dicts (key = value)
            elif type(event) == dict:
                checkpoint = None

                for eventKey, eventValue in event.items():
                    if eventKey == StopWatch.SPLIT_TIME_MEASURED:
                        checkpoint = event.get(StopWatch.CHECKPOINT)
                        flow = str(self._flowmeter.get_current_flow())
                        pressure = self._pressure.get_sliding_avg_pressure()
                        rpm = str(self._rpmmeter.get_current_rpm())

                        set_measurement_data(row=get_row_for_checkpoint(checkpoint), split_time=eventValue,
                                             rpm=rpm, flow=flow,
                                             pressure='{}/{}'.format(pressure[0], pressure[1]))

                        if checkpoint:
                            write_log_to_csv(checkpoint=checkpoint, split_time=eventValue,
                                             flow=flow, rpm=rpm, pressure_1=str(pressure[0]),
                                             pressure_2=str(pressure[1]))

                    elif eventKey == StopWatch.MANUAL_MEASURE_STARTED:
                        checkpoint = event.get(StopWatch.CHECKPOINT)
                        flow = str(self._flowmeter.get_current_flow())
                        pressure = self._pressure.get_sliding_avg_pressure()
                        rpm = str(self._rpmmeter.get_current_rpm())

                        set_measurement_data(is_manual_measure=True, split_time=eventValue,
                                             rpm=rpm, flow=flow,
                                             pressure='{}/{}'.format(pressure[0], pressure[1]))

                        write_log_to_csv(split_time=eventValue,
                                         flow=flow, rpm=rpm, pressure_1=str(pressure[0]),
                                         pressure_2=str(pressure[1]), is_manual_measure=True)

                if checkpoint is not None:
                    self._logger.info(_("Split time measured on checkpoint {}").format(checkpoint))

        except queue.Empty:
            pass

        self._parent.after(self._SCREEN_REFRESH_MS, self._update_ui)


class StopWatch(object):
    # Events broadcast by StopWatch
    STOPWATCH_STARTED = 'stopwatch_started'
    STOPWATCH_STOPPED = 'stopwatch_stopped'
    STOPWATCH_RESET = 'stopwatch_reset'
    SPLIT_TIME_MEASURED = 'split_time_measured'
    MANUAL_MEASURE_STARTED = 'manual_measure_started'
    MANUAL_MEASURE_ENDED = 'manual_measure_ended'
    CHECKPOINT = 'checkpoint'

    # GPIO input pins
    _STOPWATCH_TRIGGER_PIN = 7
    _STOPWATCH_SPLIT_TIME_TRIGGER_PIN = 8

    # Whichever pin is triggered last will stop the watch.
    # Each triggering will record data at a given moment.
    _STOPWATCH_STOP_TRIGGER_PINS = [11, 25]

    _STOPWATCH_RESET_PIN = 21
    _MANUAL_MEASURE_PIN = 20

    def __init__(self, parent: MainApp):
        self._logger = logging.getLogger('StopWatch')
        self._logger.setLevel(LOG_LEVEL)

        # Store time points from which we'll calculate delta values
        self._times = []
        self._cleared = True
        self._is_running = False
        self._should_stop_clock = False
        self._parent = parent

        # To control the order of inputs we'll track them here
        self._first_split_time_measured = False
        self._checkpoint_1_measured = False
        self._checkpoint_2_measured = False

        try:
            # FIXME: All buttons except the first one cause 'when_pressed' to be triggered right after init.
            # Suspecting a bug in gpiozero library. Order of buttons is not relevant to reproduce this issue.
            # Apparently this bug does occur only on a PC, not RPi.

            start_button = gpiozero.Button(self._STOPWATCH_TRIGGER_PIN, pull_up=True, bounce_time=0.01)
            start_button.when_pressed = lambda: self._start_watch()

            split_time_button = gpiozero.Button(self._STOPWATCH_SPLIT_TIME_TRIGGER_PIN, pull_up=True, bounce_time=0.01)
            split_time_button.when_pressed = lambda: self._measure_first_split_time()

            stop_button_1 = gpiozero.Button(self._STOPWATCH_STOP_TRIGGER_PINS[0], pull_up=True, bounce_time=0.01)
            stop_button_1.when_pressed = lambda button: self._stop_watch(button)

            stop_button_2 = gpiozero.Button(self._STOPWATCH_STOP_TRIGGER_PINS[1], pull_up=True, bounce_time=0.01)
            stop_button_2.when_pressed = lambda button: self._stop_watch(button)

            manual_measure_button = gpiozero.Button(self._MANUAL_MEASURE_PIN, pull_up=True, bounce_time=0.01)
            manual_measure_button.when_pressed = lambda: self._run_manual_measurement()

            reset_button = gpiozero.Button(self._STOPWATCH_RESET_PIN, pull_up=True, bounce_time=0.01)
            reset_button.when_pressed = lambda: self._reset_watch()

            self._buttons = {'start_button': start_button, 'split_time_button': split_time_button,
                             'stop_button_1': stop_button_1, 'stop_button_2': stop_button_2,
                             'manual_measure_button': manual_measure_button, 'reset_button': reset_button}
        except:
            logging.warning(
                _('Gpiozero: Unable to load pin factory. Most probably, you\'re running this application on a PC. '
                  'In this case, you can setup remote GPIO. See the docs.'))
            self._buttons = {}

    @property
    def is_running(self):
        return self._is_running

    def _start_watch(self):
        if self._cleared and not self.is_running:
            self._measure_split_time(checkpoint=4)
            self._cleared = False
            self._is_running = True
            self._parent.post_on_ui_thread(self.STOPWATCH_STARTED)

    def _measure_first_split_time(self):
        if self.is_running:
            if self._first_split_time_measured:
                self._logger.warning(_("Repeated measure on checkpoint 3"))
                return

            self._measure_split_time(checkpoint=3)
            self._first_split_time_measured = True

    def _stop_watch(self, button_id):
        if self._first_split_time_measured and self.is_running:
            if button_id == self._buttons['stop_button_1']:
                if self._checkpoint_1_measured:
                    self._logger.warning(_("Repeated measure on checkpoint 1"))
                    return

                self._measure_split_time(checkpoint=1)
                self._checkpoint_1_measured = True
            elif button_id == self._buttons['stop_button_2']:
                if self._checkpoint_2_measured:
                    self._logger.warning(_("Repeated measure on checkpoint 2"))
                    return

                self._measure_split_time(checkpoint=2)
                self._checkpoint_2_measured = True

            # This method will be triggered by two sensors.
            # The one which triggers last will stop the clock.
            if self._should_stop_clock:
                self._is_running = False
                self._parent.post_on_ui_thread(self.STOPWATCH_STOPPED)
            else:
                self._should_stop_clock = True

    def _reset_watch(self):
        self._cleared = True
        self._is_running = False
        self._should_stop_clock = False
        self._first_split_time_measured = False
        self._checkpoint_1_measured = False
        self._checkpoint_2_measured = False
        self._times = []
        self._manual_measurement_running = False
        self._parent.post_on_ui_thread(self.STOPWATCH_RESET)

    def _measure_split_time(self, checkpoint: int):
        split_time = time.time()
        self._times.append(split_time)
        self._parent.post_on_ui_thread({self.SPLIT_TIME_MEASURED: self._format_time(split_time - self._times[0]),
                                        self.CHECKPOINT: checkpoint})

    def _run_manual_measurement(self):
        self._parent.post_on_ui_thread({self.MANUAL_MEASURE_STARTED: self.get_current_time()})

    @staticmethod
    def _format_time(timedelta):
        minutes = int(int(timedelta) / 60)

        return "{0:02d}:{1:06.3f}".format(minutes, float(timedelta - (minutes * 60)))

    def get_current_time(self):
        """
        Get stopwatch time formatted as string.
        If the watch is not running and it was never started, it will return 00:00.000.
        If the watch is not running but it was started, it will return last split time.
        Otherwise it will return the time since the watch was triggered.
        """
        if self._is_running:
            return self._format_time(time.time() - self._times[0])
        elif len(self._times) > 1:
            # Do not reset stopwatch time yet. Instead show last split time.
            return self._format_time(self._times[-1] - self._times[0])
        else:
            return self._format_time(0)


class FlowMeter(object):
    _FLOW_SENSOR_PIN = 26
    _MAX_QUEUE_LENGTH = 5
    _MIN_LPM = 0
    _MAX_LPM = 99999

    def __init__(self, parent: MainApp):
        self._logger = logging.getLogger('FlowMeter')
        self._logger.setLevel(LOG_LEVEL)

        self._parent = parent
        self._samples = deque(maxlen=self._MAX_QUEUE_LENGTH)

        if parent.configuration is not None:
            try:
                self._k = parent.configuration['flow']['k']
                self._q = parent.configuration['flow']['q']
            except KeyError or AttributeError:
                self._logger.warning(_("Flow variables are not properly defined in a config!"))
                self._k = FLOW_K_DEFAULT_VALUE
                self._q = FLOW_Q_DEFAULT_VALUE

        try:
            self._flow_sensor = gpiozero.Button(self._FLOW_SENSOR_PIN, pull_up=True, bounce_time=0.001)
            self._flow_sensor.when_pressed = lambda: self._update_flow()
        except:
            self._flow_sensor = None

    def _update_flow(self):
        self._samples.append(time.time())

    def get_current_flow(self):
        # Don't bother computing flow if water pump is not running.
        if len(self._samples) < self._MAX_QUEUE_LENGTH:
            lpm = 0
        else:
            f = 1 / ((self._samples[-1] - self._samples[0]) / self._MAX_QUEUE_LENGTH)
            lpm = int(self._k * (f + self._q))

            if lpm not in range(self._MIN_LPM, self._MAX_LPM + 1):
                self._logger.debug(_("Flow is out of range! Value: {}").format(lpm))
                lpm = self._MAX_LPM

        return lpm


class PressureTransducer(object):
    # Pressure transducer parameters:
    # - brand:              BD sensors
    # - type:               26.600G
    # - pressure range:     0–100 bar
    # - voltage output:     0–10 V DC

    _SAMPLES_FOR_SLIDING_AVG = 25
    _MIN_PRESSURE = 0
    _MAX_PRESSURE = 100

    def __init__(self, parent: MainApp, avg_samples_no=None):
        self._logger = logging.getLogger('PressureTransducer')
        self._logger.setLevel(LOG_LEVEL)

        self._parent = parent
        self._i2c_initialized = False
        self._avg_samples_no = self._SAMPLES_FOR_SLIDING_AVG if avg_samples_no is None \
            else avg_samples_no

        def runnable():
            while True:
                if not self._is_measuring:
                    self._update_sliding_avg_pressure_thread()
                time.sleep(1 / self._avg_samples_no)

        if parent.configuration is not None:
            try:
                self._k = parent.configuration['pressure']['k']
                self._q = parent.configuration['pressure']['q']
            except KeyError or AttributeError:
                self._logger.warning(_("Pressure variables are not properly defined in a config!"))
                self._k = PRESSURE_K_DEFAULT_VALUE
                self._q = PRESSURE_Q_DEFAULT_VALUE

        try:
            # Init I2C bus
            if 'busio' in sys.modules and 'board' in sys.modules:
                self._i2c = busio.I2C(board.SCL, board.SDA)

                # Create instance of AD converter module
                self._adc = ads.ADS1115(self._i2c)
                self._i2c_initialized = True

                # Set gain to measure in range +/-6.144V
                self._adc.gain = 2 / 3

                # Set data rate
                self._adc.data_rate = 128

                # Set continuous mode
                self._adc.mode = Mode.SINGLE

                # Channels to read values from
                self._adc_channels = [AnalogIn(self._adc, ads.P0), AnalogIn(self._adc, ads.P1)]

                # Lists of voltages to compute sliding average from
                self._voltage_1_samples = deque(maxlen=self._avg_samples_no)
                self._voltage_2_samples = deque(maxlen=self._avg_samples_no)

                self._is_measuring = False

                # Init thread to poll for data
                self._worker = threading.Thread(target=runnable)
                self._worker.daemon = True
                self._worker.start()
            else:
                self._i2c_initialized = False

        except ValueError:
            self._i2c_initialized = False

    def _update_sliding_avg_pressure_thread(self):
        if self._i2c_initialized:
            self._is_measuring = True
            self._voltage_1_samples.append(self._adc_channels[0].voltage)
            self._voltage_2_samples.append(self._adc_channels[1].voltage)
            self._is_measuring = False

    def get_current_pressure(self):
        return 0, 0 if not self._i2c_initialized else tuple(
            map(self._calculate_pressure_from_input_value,
                [self._adc_channels[0].value, self._adc_channels[1].value])
        )

    def _calculate_pressure_from_input_value(self, voltage):
        # There is voltage divider on the input, so:
        # 5 V DC = 100 bar (full scale)
        # 1 V DC = 20 bar
        # 1 bar = 0.05 V DC
        pressure = int(self._k * voltage + self._q)

        if pressure not in range(self._MIN_PRESSURE, self._MAX_PRESSURE + 1):
            self._logger.debug(_("Pressure is out of range! Value: {}").format(pressure))
            pressure = self._MAX_PRESSURE

        return pressure

    def get_sliding_avg_pressure(self):
        if not self._i2c_initialized:
            return 0, 0
        else:
            # Sliding average is computed from _MAX_QUEUE_LENGTH samples
            avg_p1 = sum(self._voltage_1_samples) / self._avg_samples_no
            avg_p2 = sum(self._voltage_2_samples) / self._avg_samples_no
            return tuple(map(self._calculate_pressure_from_input_value, [avg_p1, avg_p2]))


class RpmMeter(object):
    """
    Calculate engine rev speed (RPM).

    To get the value we evaluate pulses coming from the engine.

    We are not interested in the pulse value, it's always 1. However we need to store the time,
    when the pulse was triggered. From the time difference between first and last value in a circular buffer,
    we can calculate the RPM.

    f = 1 / T, where T is time period of one pulse. We average the value by computing the period of N samples
    and divide by N to get the value for 1 sample.

    """

    _RPM_SENSOR_PIN = 16
    _MAX_QUEUE_LENGTH = 10
    _MIN_RPM = 0
    _MAX_RPM = 99999
    _AVG_SAMPLES = 25

    def __init__(self, parent: MainApp):
        self._logger = logging.getLogger('RpmMeter')
        self._logger.setLevel(LOG_LEVEL)

        self._parent = parent
        self._samples = deque(maxlen=self._MAX_QUEUE_LENGTH)

        # variables for running/moving average
        self._avg = deque(maxlen=self._AVG_SAMPLES)

        # variables for exponentially weighted average
        self._alpha = 1.0 / self._AVG_SAMPLES  # or 2.0/(self._AVG_SAMPLES+1)
        self._expAVG = 0

        if parent.configuration is not None:
            try:
                self._k_multiplier = parent.configuration['revs']['k']
            except KeyError or AttributeError:
                self._logger.warning(_("RPM variables are not properly defined in a config!"))
                self._k_multiplier = RPM_K_DEFAULT_VALUE

        try:
            self._rpm_sensor = gpiozero.Button(self._RPM_SENSOR_PIN, pull_up=True)
            self._rpm_sensor.when_pressed = lambda: self._update_rpm()
        except:
            self._rpm_sensor = None

    def _update_rpm(self):
        self._samples.append(time.time())

    def get_current_rpm(self):
        # The engine wasn't started or was just started.
        # Don't bother computing the RPM.
        if len(self._samples) < self._MAX_QUEUE_LENGTH:
            return 0
        else:
            freq = 1 / ((self._samples[-1] - self._samples[0]) / self._MAX_QUEUE_LENGTH) / self._k_multiplier
            freq = self.get_exp_avg(self._expAVG, freq)  # or self.get_running_avg(freq)
            rpm = int(freq * 60)

            if rpm not in range(self._MIN_RPM, self._MAX_RPM + 1):
                self._logger.debug(_("RPM is out of range! Value: {}").format(rpm))
                rpm = self._MAX_RPM

            return rpm

    def get_running_avg(self, x):
        # if the queue is empty then fill it with values of x
        if (self._avg == deque([])):
            for i in range(self._AVG_SAMPLES):
                self._avg.append(x)
        self._avg.append(x)
        self._avg.popleft()
        avg = 0
        for i in self._avg:
            avg += i
        avg = avg / float(self._AVG_SAMPLES)
        return avg

    def get_exp_avg(self, current_exp_avg, new_sample):
        avg = (1 - self._alpha) * current_exp_avg + self._alpha * new_sample
        return avg


if __name__ == "__main__":
    root = tk.Tk()
    app = MainApp(root)
    root.mainloop()
