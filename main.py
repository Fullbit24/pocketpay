from capri.openai_lib import ModelHouse

mh = ModelHouse()

message=[]
message.append({
	"role":"user",
	"content": "hi",
	})

for i in mh.send(sid=123, messages=message):
	print(i, end="", flush=True)
print()