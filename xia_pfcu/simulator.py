# -*- coding: utf-8 -*-
#
# This file is part of the xia-pfcu project
#
# Copyright (c) 2020 Tiago Coutinho
# Distributed under the LGPLv3. See LICENSE for more info.

"""
.. code-block:: yaml

    devices:
    - class: PFCU
      package: xia_pfcu.simulator
      module_id: 15
      shutter_mode: true       # start in shutter mode
      shutter_open: false      # start with shutter closed
      exposure_decimation: 1   # initial decimation
      lock: false              # initial lock status
      transports:
      - type: serial
        url: /tmp/pfcu-1
"""

import time

import gevent
from sinstruments.simulator import BaseDevice

STATUS = """\
%PFCU{addr} OK PFCU v1.0 (c) XIA 1999 All Rights Reserved\r
CHANNEL IN/OUT (FPanel   TTL  RS232) Shorted? Open? \r
    1     OUT     OUT    OUT   OUT      NO      NO\r
    2     OUT     OUT    OUT   OUT      NO      NO\r
    3      IN     OUT    OUT    IN      NO      NO\r
    4     OUT     OUT    OUT   OUT      NO      NO\r
RS232 Control Enabled: YES\r
RS232 Control Only: {rs232only}\r
Shutter Mode Enabled: {mode}\r
Exposure Decimation: {decimation:5d}\r
DONE;\r
"""

class PFCU(BaseDevice):

    newline = b"\r"

    DEFAULT = {
        "module_id": 15,
        "shutter_mode": False,
        "shutter_open": False,
        "exposure_decimation": 1,
        "lock": False
    }

    def __init__(self, name, **opts):
        kwargs = {}
        if "newline" in opts:
            kwargs["newline"] = opts.pop("newline")
        self._config = dict(self.DEFAULT, **opts)
        self._open = False
        super().__init__(name, **kwargs)

    @property
    def module_id(self):
        return self._config["module_id"]

    @property
    def shutter_mode(self):
        return self._config["shutter_mode"]

    @shutter_mode.setter
    def shutter_mode(self, enable):
        self._config["shutter_mode"] = enable

    @property
    def shutter_open(self):
        return self._config["shutter_open"]

    @shutter_open.setter
    def shutter_open(self, value):
        self._config["shutter_open"] = value

    @property
    def shutter_status(self):
        return "Open" if self.shutter_open else "Closed"

    @property
    def exposure_decimation(self):
        return self._config["exposure_decimation"]

    @exposure_decimation.setter
    def exposure_decimation(self, value):
        self._config["exposure_decimation"] = int(value)

    @property
    def lock(self):
        return self._config["lock"]

    @lock.setter
    def lock(self, value):
        self._config["lock"] = value

    def handle_message(self, line):
        self._log.debug("request: %r", line)
        line = line.decode().strip().upper()
        assert line.startswith("!PFCU")
        addr, cmd, *args = line[5:].split()
        if cmd == "C":  # Close shutter
            if self.shutter_mode:
                self.shutter_open = False
                result = "%PFCU{} OK Shutter Closed DONE;".format(self.module_id)
            else:
                result = "%PFCU{} ERROR: Shutter mode disabled;".format(self.module_id)
        elif cmd == "O":  # Open shutter
            if self.shutter_mode:
                self.shutter_open = True
                result = "%PFCU{} OK Shutter Open DONE;".format(self.module_id)
            else:
                result = "%PFCU{} ERROR: Shutter mode disabled;".format(self.module_id)
        elif cmd == "P":  # Position inquiry
            result = "%PFCU{} OK 0000 DONE;".format(self.module_id)
        elif cmd == "S":  # Status report
            if self.shutter_mode:
                mode = "YES Shutter is {}".format(self.shutter_status)
            else:
                mode = "NO"
            rs232only = "YES" if self.lock else "NO"
            result = STATUS.format(
                addr=self.module_id,
                mode=mode,
                decimation=self.exposure_decimation,
                rs232only=rs232only
            )
        elif cmd == "H":  # Shutter status
            if self.shutter_mode:
                result = "%PFCU{} OK Shutter {} DONE;".format(
                    self.module_id, self.shutter_status)
            else:
                result = "%PFCU{} ERROR: Shutter mode disabled;".format(self.module_id)
        elif cmd == "D":  # Decimation
            try:
                self.exposure_decimation = args[0]
            except ValueError:
                result = "%PFCU{} ERROR: Invalid Decimation Value;".format(self.module_id)
            else:
                result = "%PFCU{} OK Decimation = {} DONE;".format(self.module_id, args[0])
        elif cmd == "F":  # Fault status
            result = "%PFCU{} OK 0123 DONE;".format(self.module_id)
        elif cmd == "2":  # Enable shutter mode
            self.shutter_mode = True
            result = "%PFCU{} OK Shutter mode Enabled DONE;".format(self.module_id)
        elif cmd == "L":  # Lock (RS232 control only)
            self.lock = True
            result = "%PFCU{} OK Locked DONE;".format(self.module_id)
        elif cmd == "Z":  # Clear short error condition
            result = "%PFCU{} OK 0123 DONE;".format(self.module_id)
        elif cmd == "U":  # Unlock (enable all control sources)
            self.lock = False
            result = "%PFCU{} OK Unlocked DONE;".format(self.module_id)
        elif cmd == "4":  # Disable shutter mode
            self.shutter_mode = False
            result = "%PFCU{} OK Shutter mode Disabled DONE;".format(self.module_id)
        result = result.encode() + b"\r"
        self._log.debug("reply: %r", result)
        return result

