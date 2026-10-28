"""Build a local review page from measured benchmark outputs (no external media)."""
import argparse
import html
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dir', type=Path, default=Path('../tmp/lipsync-comparison'))
    a = p.parse_args()
    w = json.loads((a.dir/'wav2lip-metrics.json').read_text())
    l = json.loads((a.dir/'latent-metrics.json').read_text())
    t = json.loads((a.dir/'tts-metrics.json').read_text())
    assert w['video_sha256'] == l['video_sha256'], 'Input videos differ'
    assert w['audio_sha256'] == l['audio_sha256'], 'Input audio differs'
    cold, warm = w['runs'][0], w['runs'][-1]
    rows = [
        ('Wav2Lip / M5 / 冷启动', cold['first_frame_seconds'], cold['total_seconds']),
        ('Wav2Lip / M5 / 同进程后续句', warm['first_frame_seconds'], warm['total_seconds']),
        ('LatentSync / 云端 4090 / 独立进程', None, l['total_seconds']),
    ]
    table = '<table><tr><th>实际运行方式</th><th>首个口型帧</th><th>完整 MP4</th></tr>'
    for name, first, total in rows:
        table += f'<tr><td>{name}</td><td>{"整片输出" if first is None else f"{first:.2f} 秒"}</td><td>{total:.2f} 秒</td></tr>'
    table += '</table>'
    text = html.escape(t['text'])
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>LingCast 口型对比</title>
<style>body{font:16px system-ui;background:#111923;color:#e7edf6;max-width:1120px;margin:32px auto;padding:0 24px}p{line-height:1.7;color:#bdcada}.videos{display:grid;grid-template-columns:1fr 1fr;gap:24px}video{width:100%;max-height:62vh;background:#000}table{border-collapse:collapse;width:100%;margin:24px 0}th,td{padding:12px;border-bottom:1px solid #3d4c60;text-align:left}button,a{background:#285ab9;color:white;border:0;padding:12px 16px;border-radius:6px;cursor:pointer;text-decoration:none}h2{font-size:20px}@media(max-width:650px){.videos{grid-template-columns:1fr}}</style>
<h1>LingCast · 同音频、同底片口型对比</h1>'''
    page += f'<p>测试台词：{text}<br>人物：IT 女主持人；输出 576×768、25fps。相同输入 SHA-256 已核对；Wav2Lip 关闭人脸增强。</p>'
    page += table
    page += '<p>这是部署方案对比：Wav2Lip 在当前 M5 上运行，LatentSync 在云端 4090 上运行，不能据此判定同硬件模型性能。口型时间不含 TTS、网络传输和 OBS/平台延迟。Wav2Lip 首帧是内存产帧时间，尚未计入直播缓冲和编码。</p>'
    page += f'<p>共用语音时长约 5.22 秒。IndexTTS 初始化 {t["model_load_seconds"]:.2f} 秒，实际合成 {t["synthesis_seconds"]:.2f} 秒。音频另加 0.4 秒前置与 1 秒尾部静音；LatentSync 输出约 6.4 秒，截去部分尾部静音，口播未据此认定截断。</p>'
    page += '<p>本次抽帧观察：Wav2Lip 嘴周和下半脸偏软，个别张嘴帧的牙齿和口腔形态较生硬；LatentSync 嘴周细节和脸部纹理更清楚。这是单句人工抽帧判断，未计算客观同步或画质评分。</p>'
    page += '''<button id="play">从头同步播放（只播放左侧声音）</button>
<div class="videos"><section><h2>Wav2Lip ONNX</h2><video id="w" controls playsinline src="wav2lip-2.mp4"></video></section><section><h2>LatentSync</h2><video id="l" controls playsinline muted src="latentsync.mp4"></video></section></div>
<p>重点观察嘴周清晰度、牙齿、脸型稳定、闭嘴段，以及声音与嘴型的对应。两侧也可独立暂停和拖动。单句样本不能代替长播测试或客观口型同步评分。</p>
<p><a href="wav2lip-2.mp4" download>下载 Wav2Lip</a> <a href="latentsync.mp4" download>下载 LatentSync</a></p>
<script>document.getElementById('play').onclick=async()=>{const w=document.getElementById('w'),l=document.getElementById('l');w.pause();l.pause();w.currentTime=0;l.currentTime=0;l.muted=true;await Promise.all([w.play(),l.play()])}</script></html>'''
    (a.dir/'index.html').write_text(page)
    (a.dir/'comparison-metrics.json').write_text(json.dumps(dict(wav2lip=w, latentsync=l, tts=t), ensure_ascii=False, indent=2))
    print('Report ready; matching input hashes verified')


if __name__ == '__main__':
    main()
