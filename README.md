# A wxPython application for MacOS to sync files from local directories to Dropbox/iCloud cloud storage

![Exp](https://img.shields.io/badge/Dev-Experimental-orange.svg)
[![Lic](https://img.shields.io/badge/License-Apache2.0-green)](http://www.apache.org/licenses/LICENSE-2.0)
![Py](https://img.shields.io/badge/Python-3.12+-green)
![Ver](https://img.shields.io/badge/Version-1.0-blue)

## Description
The app scans source directories for new/modified files since the last sync,
copies them to the target cloud-synced directories, and optionally evicts them from local storage.

The application uses wxPython for the GUI and threading for background processing.

The sync tasks are configured via the [config.yaml](./config.yaml) YAML file, each task with its own _label_, _source_, _target_, _name_ and _ignore_ filters.

Logging is implemented to track operations and errors. Each run of the app is logged to a separate timestamped log file. The results of the sync operations are logged to the output `history_syncdbapp_YYYYMMDD_HHMMSS.log` file, where the `YYYYMMDD_HHMMSS` indicates the date/time when the application has been started.

### Notes on the file eviction
To evict the files from local storage after copying to cloud, a local [cloudfile command line tool](https://github.com/istvanzk/cloudfile) is used which needs to be compiled first and runs only on MacOS.
Make sure to have the compiled binary copied to the same directory as the `syncdbapp.py` script, or adjust the path in the code accordingly.

The evict process is started by the `cloudfile` but it runs in the background and managed by the app of the cloud provider (e.g., Dropbox, iCloud).
The code here introduces delays to ensure the files are ccopied and sync, and also implements a 2nd retry attempt for evictions that fail the first time.
When the 2nd eviction attempt also fails, the corresponding file is not evicted (remains copied to the Cloud), and this is logged in the `history_syncdbapp_*.log` file.
Depending on the cloud provider and internet bandwidth, the eviction command and process may need to be further adjusted in the pyhtin code. 

For other OSs than MacOS, the `cloudfile` application needs to be replaced with a similar application built for the corresponding OS.

## Usage

0. Clone this repo with the cloudfile_cli submodule:
```
git clone --recurse-submodules https://github.com/istvanzk/syncdbapp
```

1. Configure the sync tasks in `config.yaml`

2. Run the python app with:
```
python3 syncdbapp.py
```
3. In the app GUI select with the checkboxes what operations you would like to perform for each task to be run: 

a) **Scan**: It scans recursively the Source folder for files modified after the last synced date given in the `config.yaml` for the task. The Scan datetime is _not_ recorded in the `config.yaml` file.

b) **Copy**: Copies recursively the files found during scanning to the Target folder. Files which are already found in the Target are not overwritten only if the Source constains newer (modified data) versions of the same files. A Copy a operation can be run only after a Scan, and the Copy date/time is recorded in the `config.yaml` file.

c) **Evict**: Evicts the copied files to Target, such that thhese are available only in the Cloud Storage. An Evit operation can be run only after a Copy, and the Evict date/time is recorded in the `config.yaml` file.

4. Run the desired task with the corresponding buttons **Run \<Task label\>**, or run all tasks with the button **RunAll**. The status and progress of each run are displayed inside the corresponding task box.

NOTEs: 
- Each task run operation is executed in a separate python thread.
- The evict operation is actually executed in the background by the corresponding Cloud Storage app e.g., Dropbox or iCloud, management. Therefore this python code only triggers the evict action, and returns, while the eviction is executed.
