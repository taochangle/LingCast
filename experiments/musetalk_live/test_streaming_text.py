import unittest,json
from unittest.mock import patch
from streaming_text import Sentences,qwen_sentences
class Response:
 status_code=200
 def __init__(self,lines):self.lines=lines
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def iter_lines(self,**kwargs):return iter(self.lines)
def line(text='',finish=None,reasoning=None):
 return 'data: '+json.dumps({'choices':[{'delta':{'content':text,'reasoning_content':reasoning},'finish_reason':finish}]})
class Tests(unittest.TestCase):
 def test_chunk_boundaries_and_long_text(self):
  s=Sentences();self.assertEqual(s.feed('欢迎你'),[]);self.assertEqual(s.feed('来到这里。今天很开心！'),['欢迎你来到这里。','今天很开心！']);self.assertEqual(s.feed('长'*125),['长'*60,'长'*60]);self.assertEqual(s.finish(),['长'*5])
 def test_first_sentence_is_yielded_before_final_response(self):
  tokens=[];finished=[]
  with patch('streaming_text.requests.post',return_value=Response([line('欢迎'),line('来到这里。'),line('请坐一会儿。','stop'),'data: [DONE]'])) as post:
   stream=qwen_sentences('http://local/v1','test','qwen','你好',tokens.append,finished.append)
   self.assertEqual(next(stream),'欢迎来到这里。');self.assertEqual(finished,[])
   self.assertEqual(list(stream),['请坐一会儿。']);self.assertEqual(len(finished),1);self.assertTrue(post.call_args.kwargs['stream']);self.assertTrue(post.call_args.kwargs['json']['stream'])
 def test_reasoning_field_ignored_and_incomplete_tail_discarded(self):
  with patch('streaming_text.requests.post',return_value=Response([line(reasoning='private thinking'),line('正常回答。'),line('未完成','length'),'data: [DONE]'])):
   stream=qwen_sentences('http://local','key','qwen','问',lambda _:None,lambda _:None)
   self.assertEqual(next(stream),'正常回答。')
   with self.assertRaises(RuntimeError):list(stream)
 def test_split_thinking_tag_rejected(self):
  with patch('streaming_text.requests.post',return_value=Response([line('<thi'),line('nk>不能读')])):
   with self.assertRaises(ValueError):list(qwen_sentences('http://local','key','qwen','问',lambda _:None,lambda _:None))
if __name__=='__main__':unittest.main()
