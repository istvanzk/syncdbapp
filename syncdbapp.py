#
# A GUI/wxPython application to sync files from local directories to Dropbox/iCloud cloud storage.
# It scans source directories for new/modified files since the last sync,
# copies them to the target cloud-synced directories, and optionally evicts them from local storage.
# The application uses wxPython for the GUI and threading for background processing.
# Logging is implemented to track operations and errors. Each run of the app is logged to a separate timestamped log file. 
# The sync tasks are configured via a YAML file, each one with its own label, source, target, name, and ignore filters.
#
# NOTE on the file eviction: 
# To evict the files from local storage after copying to cloud, the local `cloudfile` command line tool is used.
# See https://github.com/istvanzk/cloudfile for more details.
# Make sure to have it in the same directory as this script, or adjust the path in the code accordingly.
# The evict process is started by the `cloudfile` but it runs in the background and managed by the app of the cloud provider (e.g., Dropbox, iCloud).
# The code here introduces delays to ensure the files are ccopied and sync, and also implements a 2nd retry attempt for evictions that fail the first time.
# Depending on the cloud provider and internet bandwidth, the eviction command and process may need to be further adjusted.
# For other OSs than MacOS, the `cloudfile` application needs to be replaced with a similar application built for the corresponding OS.

# Version: 1.0
# Author: Istvan Z. Kovacs, 2026
#
#    Copyright 2026, Istvan Z. Kovacs.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
#
import random
from click import Tuple
import wx
import threading
import time
import yaml
import os
import subprocess
import shutil
import logging
from stat import S_ISDIR, S_ISREG
from typing import Any, Callable, Dict, List

# Constants
LOG_FILE_PATH  = "history_syncdbapp"
TASKS_CONFIG_FILE = "config.yaml"

# Setup Logging configuration
# Log file name with timestamp to avoid overwriting
logging.basicConfig(
    filename=LOG_FILE_PATH + "_" + time.strftime("%Y%m%d_%H%M%S", time.localtime()) + ".log",
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - (%(threadName)-10s) - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
        
class SyncDBFrame(wx.Frame):
    """Main application window for syncing files to Dropbox."""
    def __init__(self):
        super().__init__(parent=None, title='Sync files to Cloud (copy, evict) - V1.0', size=(400, 300),
                         style=(wx.DEFAULT_FRAME_STYLE & ~wx.RESIZE_BORDER) | wx.STAY_ON_TOP)

        # Internal parameters
        self.taskConfigs: List[Dict[str, Any]] = []
        self.taskSizers: List[wx.StaticBoxSizer] = []
        self.taskLabels: List[str]   = []
        self.taskBtns: List[wx.Button]   = []
        self.taskGauges: List[wx.Gauge]  = []
        self.taskStatus: List[wx.StaticText]  = []
        self.taskFiles: List[List[tuple]] = []

        # Initial states
        self.stop_requested = False
        self.run_scan  = True
        self.run_copy  = True
        self.run_evict = False

        # Load task configurations from file
        self.load_tasks_from_config()

        # Main panel
        self.panel = wx.Panel(self)
        
        # Main vertical sizer to stack rows from top to bottom
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # 1. Welcome text message line
        welcome_txt = wx.StaticText(self.panel, label=f"Found {len(self.taskConfigs)} sync tasks to run")
        main_sizer.Add(welcome_txt, 0, wx.ALL | wx.CENTER, 10)

        # 2. Three checkboxes in a row
        cb_group_sizer = wx.StaticBoxSizer(wx.HORIZONTAL, self.panel, "Task options")
        self.cbScan  = wx.CheckBox(self.panel, label="Scan")
        self.cbScan.SetValue(self.run_scan)
        self.cbCopy  = wx.CheckBox(self.panel, label="Copy")
        self.cbCopy.SetValue(self.run_copy)
        self.cbEvict = wx.CheckBox(self.panel, label="Evict")
        self.cbEvict.SetValue(self.run_evict)

        # Bind checkboxes to event handlers
        self.cbScan.Bind(wx.EVT_CHECKBOX, self.on_checkbox)
        self.cbCopy.Bind(wx.EVT_CHECKBOX, self.on_checkbox)
        self.cbEvict.Bind(wx.EVT_CHECKBOX, self.on_checkbox)
        cb_group_sizer.Add(self.cbScan, 0, wx.ALL, 5)
        cb_group_sizer.Add(self.cbCopy, 0, wx.ALL, 5)
        cb_group_sizer.Add(self.cbEvict, 0, wx.ALL, 5)

        #cb_sizer.AddMany([(self.cb1, 0, wx.RIGHT, 10), (self.cb2, 0, wx.RIGHT, 10), (self.cb3, 0, 0)])
        #main_sizer.Add(cb_sizer, 0, wx.CENTER | wx.BOTTOM, 15)
        main_sizer.Add(cb_group_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        # 3. Create rows from config file (name + run button + two lines of text + progress bar)
        for task in self.taskConfigs:
            main_sizer.Add(
                self.create_run_task_row(task),
                0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 15
            )


        # 4. Three buttons with labels
        btn_row5_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btnRunAll = wx.Button(self.panel, label="Run All")
        self.btnRunAll.Bind(wx.EVT_BUTTON, self.on_launch_all_tasks)
        self.btnRunAll.Enable()
        btn_row5_sizer.Add(self.btnRunAll, 1, wx.EXPAND | wx.RIGHT, 10)

        self.btnStop = wx.Button(self.panel, label="Stop")
        self.btnStop.Bind(wx.EVT_BUTTON, self.on_stop_button_click)
        self.btnStop.Disable()
        btn_row5_sizer.Add(self.btnStop, 1, wx.EXPAND | wx.RIGHT,10)

        self.btnQuit = wx.Button(self.panel, label="Quit")
        self.btnQuit.Bind(wx.EVT_BUTTON, self.on_quit_button_click)
        self.btnQuit.Enable()
        btn_row5_sizer.Add(self.btnQuit, 1, wx.EXPAND)

        main_sizer.Add(btn_row5_sizer, 0, wx.EXPAND | wx.ALL, 10)

        # 5. Progress bar with short message text above it
        # self.progress_label = wx.StaticText(self.panel, label="Progress")
        # self.gauge = wx.Gauge(self.panel, range=100, size=(250, 15))
        # self.gauge.SetValue(45) # Example value
        # main_sizer.Add(self.progress_label, 0, wx.LEFT, 10)
        # main_sizer.Add(self.gauge, 0, wx.EXPAND | wx.ALL, 10)

        # 6. Status message text line (Footer)
        #self.status_text = wx.StaticText(self.panel, label="Status: System Ready")
        #self.status_text.SetForegroundColour(wx.WHITE)
        #main_sizer.Add(self.status_text, 0, wx.LEFT | wx.TOP, 10)


        # This timer will fire every 100 milliseconds 
        # when  started with self.timer.Start(100)
        #self.timer = wx.Timer(self)
        #self.Bind(wx.EVT_TIMER, self.on_timer_tick, self.timer)

        # Finalize layout
        self.panel.SetSizer(main_sizer)
        
        # Dynamically resize based on number of task rows
        self.adjust_window_size()
        
        # Bind close event handler
        self.Bind(wx.EVT_CLOSE, self.on_close)
        
        self.Show()


    # --- Helper functions ---
    def load_tasks_from_config(self):
        """Load tasks configurations from YAML file."""
        config_path = os.path.join(os.path.dirname(__file__), TASKS_CONFIG_FILE)
        if not os.path.exists(config_path):
            logging.warning("Tasks config file not found: %s", config_path)
            self.tasksConfigs = []
        
        try:
            with open(config_path, 'r') as file:
                config = yaml.safe_load(file)
                self.taskConfigs = config.get('tasks', []) if config else []
                if len(self.taskConfigs) == 0:
                    logging.warning("No tasks found in config file %s", config_path)
                else:
                    logging.info("Loaded %s tasks from config file %s", str(self.taskConfigs), config_path)
        except Exception as e:
            logging.error("Error reading tasks config file: %s", str(e))
            self.taskConfigs = []

    
    def save_tasks_to_config(self):
        """Save tasks configurations to YAML file."""
        config_path = os.path.join(os.path.dirname(__file__), TASKS_CONFIG_FILE)
        if not os.path.exists(config_path):
            logging.error("Tasks config file not found: %s", config_path)
            raise FileNotFoundError(f"Tasks config file not found: {config_path}")
        
        try:
            with open(config_path, 'w') as file:
                yaml.safe_dump({'tasks': self.taskConfigs}, file, sort_keys=False)
            logging.info("Saved %s tasks to config file %s", str(self.taskConfigs), config_path)
        except Exception as e:
            logging.error("Error writing Tasks config file: %s", str(e))
            raise e

    def adjust_window_size(self):
        """Dynamically resize the window based on the number of task rows."""
        # Calculate height: base height + (height per task row × number of tasks)
        base_height = 180  # Welcome text, checkboxes, buttons, and margins
        height_per_row = 140  # Approximate height per task row
        total_height = base_height + (len(self.taskBtns) * height_per_row)
        
        self.SetSize((600, total_height))
        self.SetMinSize(self.GetSize())
        self.SetMaxSize(self.GetSize())

    def create_run_task_row(self, task: Dict[str, Any]) -> wx.StaticBoxSizer:
        """Creates a row in the GUI for a single sync task."""
        # Get  configs
        btn_label   = task.get('label', 'Task')
        txt_source  = task.get('source', 'Source:')
        txt_target  = task.get('target', 'Target:')
        txt_name    = task.get('name', 'Task name')
        last_synced = task.get('synced', '')

        # Static box sizer to enclose the entire row
        task_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.panel, txt_name + (f" (Last synced: {last_synced})" if last_synced else ""))

        # Horizontal sizer for button and text lines
        row_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Button on the left, with unique ID
        btn_id = len(self.taskBtns) + 1
        btn = wx.Button(self.panel, id=btn_id, label="Run " + btn_label)
        #btn.SetMinSize((80, 40))
        #btn.SetBackgroundColour(wx.GREEN)
        #btn.SetForegroundColour(wx.BLACK)
        btn.Bind(wx.EVT_BUTTON, self.on_launch_task)

        # Vertical sizer for the two lines of text
        text_v_sizer = wx.BoxSizer(wx.VERTICAL)
        #text_header = wx.StaticText(self.panel, label=th)
        #text_header.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_BOLD))
        txt1 = wx.StaticText(self.panel, label="Source: " + txt_source)
        txt1.SetForegroundColour((50, 200, 50))
        txt2 = wx.StaticText(self.panel, label="Target: " + txt_target)
        txt2.SetForegroundColour((200, 50, 50))
        text_v_sizer.AddMany([txt1, txt2])

        row_sizer.Add(btn, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 10)
        row_sizer.Add(text_v_sizer, 0, wx.ALIGN_CENTER_VERTICAL)
        task_sizer.Add(row_sizer, 1, wx.EXPAND | wx.ALL, 5)

        # Progress bar with short message text above it
        gauge_sizer = wx.BoxSizer(wx.VERTICAL)
        gauge = wx.Gauge(self.panel, range=100, size=(250, 15))
        gauge.SetValue(0) 
        st = wx.StaticText(self.panel, label="Status: Ready")
        gauge_sizer.Add(st, 0, wx.BOTTOM, 2)
        gauge_sizer.Add(gauge, 0, wx.EXPAND)

        task_sizer.Add(gauge_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Store sizers, labels, buttons, gauges and status references for each task
        self.taskSizers.append(task_sizer)
        self.taskLabels.append(btn_label)
        self.taskBtns.append(btn)
        self.taskGauges.append(gauge)
        self.taskStatus.append(st)
        self.taskFiles.append([])
                    
        return task_sizer


    # --- Main Task Event Handlers ---
    def on_launch_all_tasks(self, event):
        """Main Thread: Triggered by user click on RunAll button."""

        self.stop_requested = False

        # Validate event_id (=button ID) and map to task
        for task_id in range(1, len(self.taskBtns) + 1):

            # Create and start the worker thread
            worker = threading.Thread(
                target=self.run_task_logic,
                args=(task_id,)
            )
            worker.daemon = True  # Thread closes if the app is closed
            worker.start()

            self.taskBtns[task_id-1].Disable()
            self.taskStatus[task_id-1].SetLabel(f"Status: Starting {self.taskLabels[task_id-1]}...")

        self.btnRunAll.Disable()
        self.btnStop.Enable()
        #self.status_text.SetLabel(f"Status: Starting All Tasks...")

    def on_launch_task(self, event):
        """Main Thread: Triggered by user click on Run Task X button."""
        btn = event.GetEventObject()
        task_id = event.GetId()
    
        # Validate event_id (=button ID) and map to task
        if task_id in range(1, len(self.taskBtns) + 1):

            self.stop_requested = False

            # Create and start the worker thread
            worker = threading.Thread(
                target=self.run_task_logic,
                args=(task_id,)
            )
            worker.daemon = True  # Thread closes if the app is closed
            worker.start()

            btn.Disable()
            self.btnRunAll.Disable()
            self.btnStop.Enable()
            self.taskStatus[task_id-1].SetLabel(f"Status: Starting Task{task_id}...")
            #self.status_text.SetLabel(f"Status: Starting Task{task_id}...")

    def run_task_logic(self, task_id):
        """Worker Thread: Does the actual processing."""

        status_str = f"Status {self.taskLabels[task_id-1]}:"

        if self.run_scan:
            # Start scanning source for files to sync (randomize start time a bit)
            wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} Scanning...")
            time.sleep(random.uniform(0.5, 1.5))
            files_to_sync = self.scan_source_for_sync(task_id)

            if files_to_sync == 0:
                wx.CallAfter(self.on_task_stopped, task_id)
                if self.stop_requested:
                    wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} Scan cancelled.")
                else:
                    wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} No files to sync.")
                return

            if self.run_copy:
                # Set progress bar range
                wx.CallAfter(self.taskGauges[task_id-1].SetRange, files_to_sync)

                # Start syncing files to target directory (randomize start time a bit)
                if not self.run_evict:
                    wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} Copying ({files_to_sync} files)...")
                else:
                    wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} Copying and Evicting ({files_to_sync} files)...")

                time.sleep(random.uniform(0.5, 1.5))
                files_synced, files_evicted = self.sync_to_target_and_evict(task_id)

                if files_synced == 0 or self.stop_requested:
                    wx.CallAfter(self.on_task_stopped,task_id)
                    if self.stop_requested:
                        wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} Sync cancelled.")
                    else:
                        wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} No files were copied/synced. Likely, all files are already up-to-date in the cloud.")
                    return

                # Store task run timestamp
                self.taskConfigs[task_id-1]['synced'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                self.taskSizers[task_id-1].GetStaticBox().SetLabel(
                    self.taskConfigs[task_id-1]['name'] + \
                    f" (Last synced: {self.taskConfigs[task_id-1]['synced']})")

                wx.CallAfter(self.on_task_complete, task_id)
                wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} Scan and Sync completed. Copied {files_synced}/{files_to_sync}, Evicted {files_evicted}/{files_to_sync}.")
            
            else:
                wx.CallAfter(self.on_task_complete, task_id)
                wx.CallAfter(self.taskStatus[task_id-1].SetLabel, f"{status_str} Scan completed ({files_to_sync} files).")


    def scan_source_for_sync(self, task_id: int) -> int:
        """
        Scans the source directory tree for files and folders to be synced. 
        Ignore items (file and folder names) based on the specified filters.
        The function is called from the worker thread run_task_logic().
        The function populates self.taskFiles[task_id-1] with (filepath, modification_time) tuples
        and it returns the total number of files to sync.

        Args:
            task_id: The ID of the task to scan.
        Returns: 
            the total number of files to sync.
        """
        # Reset list of files to proccesss (filepath, modification_time) tuples
        self.taskFiles[task_id-1] = []  
        
        # Get source directory and last synced time
        source_dir = self.taskConfigs[task_id-1]['source']
        time_tuple = time.strptime(self.taskConfigs[task_id-1]['synced'], "%Y-%m-%d %H:%M:%S")
        last_run   = time.mktime(time_tuple)

        #  Get ignore filters from config
        ignore_config = self.taskConfigs[task_id-1].get('ignore', [])
        
        def should_ignore(filename: str) -> bool:
            """Check if filename matches any ignore patterns."""
            _fl = filename.lower()
            for ignore_rule in ignore_config:
                # Check 'startswith' patterns
                for pattern in ignore_rule.get('startswith', []):
                    if _fl.startswith(pattern.lower()):
                        return True
                # Check 'endswith' patterns
                for pattern in ignore_rule.get('endswith', []):
                    if _fl.endswith(pattern.lower()):
                        return True
            return False
    
        def walktree(topdir: str, callback: Callable) -> bool:
            """
            Recursively descend the directory tree rooted at top,
            calling the callback function for each regular file.
            """
            try:
                for f in os.listdir(topdir):

                    # Check for stop request
                    if self.stop_requested:
                        return False

                    # Send Pulse command to UI thread
                    wx.CallAfter(self.taskGauges[task_id-1].Pulse)  

                    # Process/check each file/directory
                    pathname = os.path.join(topdir, f)
                    mode = os.lstat(pathname).st_mode
                    _fl = f.lower()
                    if S_ISDIR(mode):
                        # Apply filtering logic here if needed
                        if should_ignore(f):
                            continue
                        # It's a directory, recurse into it
                        if not walktree(pathname, callback):
                            return False

                    elif S_ISREG(mode):
                        # Apply filtering logic here if needed
                        if should_ignore(f):
                            continue
                        # It's a file, call the callback function
                        callback(pathname)
                   
                    else:
                        # Unknown file type, print a message
                        logging.warning("Task %s :: Skipping %s: not a file or directory", self.taskLabels[task_id-1], pathname)
            
            except Exception as e:
                logging.error(f"Task %s :: Error accessing %s: %s", self.taskLabels[task_id-1], topdir, str(e))

            return True

        def file_to_sync(filepath: str):
            """
            Callback function to process each file.
            """
            stats = os.stat(filepath)
            if stats.st_mtime > last_run or stats.st_ctime > last_run:
                self.taskFiles[task_id-1].append((filepath, stats.st_mtime))
                logging.debug("Task %s :: File to sync: %s", self.taskLabels[task_id-1], filepath)

        # Start walking recursively the source directory tree
        if walktree(source_dir, file_to_sync):
            total_files = len(self.taskFiles[task_id-1])
            logging.info("Task %s :: Found %d files to (potentially) sync in %s since last run at %s", self.taskLabels[task_id-1], total_files, source_dir, self.taskConfigs[task_id-1]['synced'])
            return total_files
        else:
            self.taskFiles[task_id-1] = []
            logging.info("Task %s :: Scan cancelled by user.", self.taskLabels[task_id-1])
            return 0

    def sync_to_target_and_evict(self, task_id: int, simsync: bool = False) -> tuple[int, int]:
        """
        Sync files to target directory = copy file if it doesn't exist in target or if it is newer than the last copied version
        Evict copied files from Cloud target if configured.
        The function is called from the worker thread run_task_logic().

        The function processes self.taskFiles[task_id-1] which contains (filepath, modification_time) tuples.

        Args:
            task_id: The ID of the task to sync.
            simsync: If True, no actual file operations are performed (for testing).
        Returns: 
            A tuple with the number of files copied and the number of files evicted.
        """
        # Just in case
        if not self.run_copy:
            return (0,0)

        task_str = f"Task {self.taskLabels[task_id-1]} ::"

        # The configured  source and target directories for this task
        source_dir = self.taskConfigs[task_id-1]['source']
        target_dir = self.taskConfigs[task_id-1]['target']

        # Process each selected source file
        copied_count  = 0
        evicted_count = 0
        evict_retry   = []
        for index, (source_path, source_mtime) in enumerate(self.taskFiles[task_id-1], start=1):
                
            # Check for stop request
            if self.stop_requested:
                logging.info("%s Sync cancelled by user: %d files copied, %d files evicted.", task_str, copied_count, evicted_count)
                return (copied_count, evicted_count)
                    
            # Update GUI from main thread
            wx.CallAfter(self.taskGauges[task_id-1].SetValue, index)  

            # Calculate the relative path to recreate structure in Target
            rel_path = os.path.relpath(path=source_path, start=source_dir)
            target_path = os.path.join(target_dir, rel_path)

            try:
                # Ensure the destination directory exists
                if not simsync:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                else:
                    logging.info("%s SIMULATED MKDIR for %s", task_str, os.path.dirname(target_path))

                # Copy the file if it doesn't exist in target or if it is newer than the last copied version
                if not os.path.exists(target_path) or source_mtime > os.path.getmtime(target_path):
                    if not simsync:
                        shutil.copy2(source_path, target_path) # copy2 preserves metadata
                        logging.info("%s Copied %s to %s", task_str, source_path, target_path)
                    else:
                        logging.info("%s SIMULATED COPY for %s to %s", task_str, source_path, target_path)
                    copied_count += 1

                    # Evict from Cloud Target (frees up space in the local Cloud folder)
                    # We wait a moment to ensure the Cloud provider "sees" the new file
                    if self.run_evict:
                        if not simsync:
                            time.sleep(3.0) 
                            # Using the orginal 'fileproviderctl' does not work in latest MacOS versions (2024+)!
                            # Use custom local code from https://github.com/istvanzk/cloudfile/tree/main
                            subprocess.run(['./cloudfile', 'evict', target_path], capture_output=True, timeout=5, check=True)
                            time.sleep(3.0) 
                            logging.info("%s Evicted %s", task_str, target_path)
                        else:
                            logging.info("%s SIMULATED EVICT for %s", task_str, target_path)
                        evicted_count += 1

            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                logging.error("%s Evict error (1st attempt): %s", task_str, str(e))
                evict_retry.append(target_path)
            except Exception as e:
                logging.error("%s Error processing %s: %s ", task_str, source_path, str(e))

        # Retry evictions that failed the first time
        for target_path in evict_retry:
            # Send Pulse command to UI thread
            wx.CallAfter(self.taskGauges[task_id-1].Pulse)

            # Retry eviction
            try:
                if not simsync:
                    subprocess.run(['./cloudfile', 'evict', target_path], capture_output=True, timeout=5, check=True)
                    time.sleep(2.0) 
                    logging.info("%s Evicted retry (2nd attempt) %s", task_str, target_path)
                else:
                    logging.info("%s SIMULATED EVICT retry (2nd attempt) for %s", task_str, target_path)
                evicted_count += 1
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                logging.error("%s Evict retry error (2nd attempt): %s", task_str, str(e))
            except Exception as e:
                logging.error("%s Error processing eviction retry (2nd attempt) for %s: %s", task_str, target_path, str(e))

        logging.info("%s Sync completed: %d files copied, %d files evicted.", task_str, copied_count, evicted_count)
        return (copied_count, evicted_count)


    def on_task_complete(self, task_id):
        """Main Thread: Called when the worker thread completes."""
        self.taskBtns[task_id-1].Enable()
        self.btnRunAll.Enable()
        self.btnStop.Disable()
        self.taskGauges[task_id-1].SetValue(0)

    def on_task_stopped(self, task_id):   
        """Main Thread cleanup logic."""
        self.on_task_complete(task_id)


    # --- Other Event Handlers ---
    #def on_timer_tick(self, event):
    #    """This function runs every 100ms when the timer is active."""
    #    self.gauge.Pulse()

    def on_checkbox(self, event):
        cb = event.GetEventObject()
        #state = "Checked" if cb.IsChecked() else "Unchecked"
        #self.status_text.SetLabel(f"Checkbox '{cb.GetLabel()}' is now {state}")
        if cb == self.cbScan:
            self.run_scan = cb.IsChecked()
            if not self.run_scan:
                self.run_copy = False
                self.cbCopy.SetValue(False)
                self.run_evict = False
                self.cbEvict.SetValue(False)

        elif cb == self.cbCopy:
            self.run_copy = cb.IsChecked()
            if self.run_copy:
                self.run_scan = True
                self.cbScan.SetValue(True)
            else:
                self.run_evict = False
                self.cbEvict.SetValue(False)

        elif cb == self.cbEvict:
            self.run_evict = cb.IsChecked()
            if self.run_evict:
                self.run_copy = True
                self.cbCopy.SetValue(True)

    def on_stop_button_click(self, event):
        self.stop_requested = True
        time.sleep(0.5)

    def on_quit_button_click(self, event):
        self.stop_requested = True
        time.sleep(0.5)
        self.Close()

    def on_close(self, event):
        """Handle the window close event."""
        self.stop_requested = True
        self.save_tasks_to_config()
        logging.info("Sync application closed.")
        self.Destroy()


if __name__ == '__main__':
    logging.info("Sync application started.")
    app = wx.App()
    frame = SyncDBFrame()
    app.MainLoop()