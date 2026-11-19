#session.py
#the file brain. one jsonl per session, one message per line.
#sids are born here now — openai_lib should only know how to talk to the wire.

import json
import os
from datetime import datetime, timezone

base_dir = "/home/rayan/Documents/agent/sessions/"


class Session:
    def __init__(self, sid=None):
        #sid None -> new one, named right now. else adopt the file that exists.
        #file only appears on first append, so dead sessions leave no corpses.
        self.sid = sid if sid else self._new_sid()
        os.makedirs(base_dir, exist_ok=True)

    def _new_sid(self):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"session_{ts}"

    def _path(self):
        return os.path.join(base_dir, self.sid + ".jsonl")

    def load(self):
        #transcript as a list of dicts, oldest first. no file yet -> fresh []
        if not os.path.exists(self._path()):
            return []
        with open(self._path()) as f:
            return [json.loads(line) for line in f if line.strip()]

    def append(self, message):
        #message = {"role": ..., "content": ...}, exactly the shape responses.create eats
        with open(self._path(), "a") as f:
            f.write(json.dumps(message) + "\n")

    def log(self, role, content):
        #shortcut, the dict shape isn't worth typing twice per turn
        self.append({"role": role, "content": content})

    @staticmethod
    def list_sessions():
        #newest first. session_YYYYMMDD... names sort themselves correctly.
        if not os.path.exists(base_dir):
            return []
        return sorted(
            (f.removesuffix(".jsonl") for f in os.listdir(base_dir) if f.endswith(".jsonl")),
            reverse=True,
        )

    @classmethod
    def latest(cls):
        #continue where you left off. nothing saved yet -> new session anyway.
        sids = cls.list_sessions()
        return cls(sids[0]) if sids else cls()
