import Player from 'xgplayer'
import FlvPlugin from 'xgplayer-flv'
import 'xgplayer/dist/index.min.css'

const params = new URLSearchParams(window.location.search)
const avatar = params.get('avatar') || '1'
if (/^[1-9]\d*$/.test(avatar)) {
  const player = new Player({
    el: document.getElementById('player')!,
    url: `/live/avatar_${avatar}.flv`,
    type: 'flv',
    isLive: true,
    autoplay: true,
    muted: params.get('muted') === '1',
    controls: false,
    width: '100%',
    height: '100%',
    plugins: [FlvPlugin],
  })
  window.addEventListener('pagehide', () => player.destroy(), { once: true })
} else {
  document.getElementById('error')!.textContent = '请选择有效的数字人编号。'
}
