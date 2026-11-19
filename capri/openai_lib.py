import re
from openai import OpenAI
import os
import json
from datetime import datetime, timezone


client = OpenAI(base_url="http://127.0.0.1:8080/v1/", api_key="hi", )

base_dir = "/home/rayan/Documents/agent/sessions/"


class ModelHouse:
    def _sid_gen(self):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"session_{ts}"

    def send(self, messages, rea="none", sid=None):
        #stream only. full reply also piles up in self.last_reply as deltas
        #go by, so a caller that botches handling the stream can still grab it.
        if sid is None:
            raise ValueError("gib session id pls")

        self.last_reply = ""
        response = client.responses.create(
            model="hi",
            input=messages,
            stream=True,
            reasoning=({"effort": rea})
            )
        for event in response:
            if event.type == "response.output_text.delta":
                self.last_reply += event.delta
                yield event.delta
