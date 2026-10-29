"""Small, independently testable Qwen SSE and sentence adapter."""
import json,re,time
import requests
class Sentences:
 def __init__(self):self.buffer=''
 def feed(self,text):
  self.buffer+=text;result=[]
  while True:
   match=re.search(r'[。！？!?\n]',self.buffer)
   end=match.end() if match else 0
   if not end and len(self.buffer)>60:
    points=[m.end() for m in re.finditer(r'[，,；; ]',self.buffer[:60])]
    end=points[-1] if points and points[-1]>=12 else 60
   if not end:break
   value=self.buffer[:end].strip();self.buffer=self.buffer[end:]
   if value:result.append(value)
  return result
 def finish(self):
  value=self.buffer.strip();self.buffer='';return [value] if value else []
def qwen_sentences(base,key,model,prompt,on_token,on_done):
 payload={'model':model,'stream':True,'max_tokens':240,'chat_template_kwargs':{'enable_thinking':False},
 'messages':[{'role':'system','content':'你是温柔自然的中文数字人主播。回答用户的问题，用2至3句口语，每句12至24字，总计不超过80字。第一句直接回应，句末用中文句号。不要标题、Markdown、思考过程或编造观众发言。'}, {'role':'user','content':prompt}]}
 parser=Sentences();all_text='';finish=None;started=time.monotonic()
 with requests.post(base.rstrip('/')+'/chat/completions',json=payload,headers={'Authorization':'Bearer '+key},stream=True,timeout=(8,30)) as response:
  if response.status_code!=200:raise RuntimeError(f'Qwen HTTP {response.status_code}')
  response.encoding='utf-8'
  for line in response.iter_lines(chunk_size=1,decode_unicode=True):
   if time.monotonic()-started>60:raise TimeoutError('Qwen stream deadline')
   if not line or not line.startswith('data:'):continue
   value=line[5:].strip()
   if value=='[DONE]':break
   data=json.loads(value);choices=data.get('choices') or []
   if not choices:continue
   choice=choices[0];delta=choice.get('delta') or {};text=delta.get('content') or ''
   if not isinstance(text,str):raise ValueError('Invalid Qwen content')
   if text:
    all_text+=text
    if len(all_text)>1000 or any(tag in all_text.lower() for tag in ['<think','</think','thinking process:']):raise ValueError('Unexpected Qwen reasoning/oversized output')
    on_token(text)
    yield from parser.feed(text)
   if choice.get('finish_reason') is not None:finish=choice['finish_reason']
 if finish!='stop':raise RuntimeError('Qwen stream incomplete; unfinished sentence discarded')
 yield from parser.finish()
 on_done(all_text)
