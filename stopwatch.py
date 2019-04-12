import time
import queue
import tkinter as tk
from tkinter import ttk


class StopWatch(object):
    SCREEN_REFRESH_MS = 40
    TEXT_WRAP_PADDING_PX = 200

    def __init__(self, parent):
        # Store time points from which we'll calculate delta values
        self._times = []

        self._parent = parent
        self._parent.title('Raspberry Stage Display')
        self._parent.columnconfigure(0, weight=1)
        self._parent.rowconfigure(0, weight=1)

        # Make UI full screen
        self._parent.protocol("WM_DELETE_WINDOW", self.close)
        self._parent.focus_set()
        self._parent.attributes('-fullscreen', True)
        self._parent.bind('<KeyPress>', self.close)
        self._parent.config(cursor='none')

        # Default styles
        self._style = ttk.Style()
        self._style.configure('Black.TFrame', background='black')
        self._style.configure('Customized.Main.TLabel', background='black', font=('Microsoft Sans Serif', 100),
                              foreground='white')

        # Define themed main frame
        self._main_frame = ttk.Frame(self._parent, style='Black.TFrame')
        self._main_frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self._main_frame.columnconfigure(0, weight=1)
        self._main_frame.rowconfigure(0, weight=1)

        self._content_frame = ttk.Frame(self._main_frame, style='Black.TFrame')
        self._content_frame.grid(column=0, row=0)

        # Main text label
        self._text_label = ttk.Label(self._content_frame, style='Customized.Main.TLabel',
                                     wraplength=self._parent.winfo_screenwidth() - self.TEXT_WRAP_PADDING_PX,
                                     justify='center')
        self._text_label.grid(column=0, row=0)
        self._text_label['text'] = '00:00.000'

        # Queue for UI thread to update components
        self._thread_queue = queue.Queue()
        self._parent.after(self.SCREEN_REFRESH_MS, self._listen_for_result)

    # noinspection PyUnusedLocal
    def close(self, *args):
        self._parent.quit()

    @property
    def is_running(self):
        return len(self._times) > 0

    def start_watch(self):
        self._times.append(time.time())

    def reset_watch(self):
        self._times = []

    def measure_split_time(self):
        self._times.append(time.time())

    # def print_times(self):
    #     if len(self._times) <= 1:
    #         return
    #
    #     start = self._times[0]
    #     for idx in range(1, len(self._times)):
    #         val = self._times[idx]
    #
    #         print('{}. mezicas: {}'.format(idx, val - start))

    def _listen_for_result(self):
        """ Check if there is something in the queue. """

        def _format_time(timedelta):
            minutes = int(int(timedelta) / 60)

            return "{0:02d}:{1:06.3f}".format(minutes, float(timedelta - (minutes * 60)))

        def clear_data():
            self._text_label['text'] = ''

        # stop watch is running
        if self.is_running:
            self._text_label['text'] = _format_time(time.time() - self._times[0])
        else:
            try:
                pass

            except queue.Empty:
                pass

        self._parent.after(self.SCREEN_REFRESH_MS, self._listen_for_result)


if __name__ == "__main__":
    root = tk.Tk()
    stopwatch = StopWatch(root)
    stopwatch.start_watch()
    root.mainloop()

