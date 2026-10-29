"""Build a private listening comparison from completed experiment job IDs."""
import html
import json
from pathlib import Path
import sys
from statistics import median

root = Path('/data1/taoxin/lingcast-musetalk-bench/live-test')
rows = []
cards = []
counts = {}
for job_id in sys.argv[1:]:
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('invalid job id')
    folder = root/job_id
    server = json.loads((folder/'metrics.json').read_text())
    browser = json.loads((folder/'browser.json').read_text())
    rows.append(dict(id=job_id, server=server, browser=browser))
    name = {'zhixia': '知夏', 'ruotang': '若棠'}[server['avatar']]
    key = (server['avatar'], server['engine'])
    counts[key] = counts.get(key, 0)+1
    text = server['segments'][0]['text']
    cards.append(f'''<article><h2>{name} · {html.escape(server['engine'])} · 第{counts[key]}次</h2>
<p>{html.escape(text)}</p><p>监看首帧 {browser['browser_first_frame_ms']/1000:.2f} 秒 ·
语音起点估计 {browser['browser_first_speech_ms']/1000:.2f} 秒 ·
音频排程空隙累计 {browser.get('audio_gap_ms', 0):.0f} 毫秒</p>
<audio controls preload="none" src="/full/{job_id}"></audio>
<details><summary>原始计时</summary><pre>{html.escape(json.dumps(rows[-1],ensure_ascii=False,indent=2))}</pre></details></article>''')
(root/'cosyvoice-comparison.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
summary = '<table><tr><th>引擎</th><th>次数</th><th>首帧中位数</th><th>首帧范围</th><th>语音起点中位数（估计）</th></tr>'
for engine in ['indextts', 'cosyvoice']:
    selected = [r['browser'] for r in rows if r['server']['engine'] == engine]
    if not selected:
        continue
    frames = [r['browser_first_frame_ms']/1000 for r in selected]
    speech = [r['browser_first_speech_ms']/1000 for r in selected]
    summary += f'<tr><td>{engine}</td><td>{len(selected)}</td><td>{median(frames):.2f}秒</td><td>{min(frames):.2f}～{max(frames):.2f}秒</td><td>{median(speech):.2f}秒</td></tr>'
summary += '</table>'
(root/'comparison.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>LingCast · 配音延迟与音色对比</title><style>
body{font:16px system-ui;background:#101725;color:#eef3ff;max-width:1100px;margin:40px auto;padding:0 24px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px}
article{background:#192438;border:1px solid #34455e;border-radius:12px;padding:20px}p{line-height:1.7;color:#bdd0e7}
table{width:100%;border-collapse:collapse;margin:24px 0}th,td{border:1px solid #34455e;padding:12px;text-align:left}audio{width:100%}pre{white-space:pre-wrap;font-size:12px}a{color:#9cd7ff}</style>
<h1>IndexTTS / CosyVoice3 对比</h1>
<p>相同台词、相同参考声音，逐条新合成，模型常驻预热。知夏与若棠是不同画面，现有参考声音相同。
语音起点为 WebAudio 排程加首个非静音样本的估计，非麦克风实录；测试不经过线上推流。</p>
<p>试听重点：声音像不像参考、吐字和语气是否自然。完整音频供音色对比；分块播放连续性请在测试页观察。
仅少量样本，不能视为稳定延迟保证。未做人工盲听评分。<a href="/">进入互动测试</a></p>
<p>统计包含下面全部样本和慢请求，不只取最快值。CosyVoice 为原生 PyTorch FP16、固定25-token流式块。</p>'''+summary+'<main>'+''.join(cards)+'</main></html>')
print(root/'comparison.html')
