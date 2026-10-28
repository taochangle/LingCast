import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type RelayConfig = {
  server: string
  hasKey: boolean
  enabled: boolean
  status?: { state: string; updatedAt: number } | null
}

export function RelaySettings({ avatarId, started }: { avatarId: number; started: boolean }) {
  const { i18n } = useTranslation()
  const en = i18n.language === 'en'
  const [server, setServer] = useState('')
  const [streamKey, setStreamKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [config, setConfig] = useState<RelayConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState(false)
  useEffect(() => {
    if (!Number.isFinite(avatarId) || avatarId <= 0) return
    let cancelled = false
    setConfig(null)
    setServer('')
    setStreamKey('')
    setShowKey(false)
    const load = async (initial = false) => {
      try {
        const { data } = await api.get<RelayConfig>(`/live/${avatarId}/relay`)
        if (cancelled) return
        setConfig(data)
        if (initial) setServer(data.server || 'rtmp://live-push.xhscdn.com/live')
        setLoadError(false)
      } catch {
        if (!cancelled) setLoadError(true)
      }
    }
    void load(true)
    const timer = window.setInterval(() => void load(), 3000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [avatarId])

  const save = async (enabled: boolean, stopOnly = false) => {
    if (busy) return
    setBusy(true)
    try {
      const { data } = await api.put<RelayConfig>(`/live/${avatarId}/relay`, {
        server: stopOnly ? config?.server : server.trim(),
        streamKey: stopOnly ? '' : streamKey.trim(),
        enabled,
      })
      setConfig(data)
      setStreamKey('')
      toast.success(en ? 'Stream settings saved' : '推流配置已保存')
    } catch (error) {
      const message = (error as { response?: { data?: { error?: string } } }).response?.data?.error
      toast.error(message || (en ? 'Unable to save' : '保存失败，请重试'))
    } finally { setBusy(false) }
  }
  const fresh = config?.status && Date.now() / 1000 - config.status.updatedAt < 10
  const state = fresh ? config.status!.state : config?.enabled ? 'unknown' : 'stopped'
  const labels: Record<string, string> = en
    ? { running: 'Relay process running', waiting: 'Waiting for avatar stream', error: 'Connection failed. Check the current stream key and retry.', stopped: 'Not streaming externally', unknown: 'Waiting for worker status' }
    : { running: '推流进程运行中', waiting: '等待数字人直播画面', error: '推流连接失败，请检查当前直播的地址和密钥，然后重新开始推流。', stopped: '未向外部平台推流', unknown: '等待播放服务上报状态' }
  return (
    <Card>
      <CardHeader>
        <CardTitle>{en ? 'External live stream' : '平台推流配置'}</CardTitle>
        <CardDescription>{en ? 'Update the server and stream key for each new platform session.' : '每场直播都可以更新服务器地址和串流密钥；密钥留空则沿用已保存的值。'}</CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-4'>
        <form className='flex flex-col gap-3' onSubmit={(event) => { event.preventDefault(); void save(false) }}>
          <Label htmlFor='relay-server'>{en ? 'RTMP server' : '推流服务器地址'}</Label>
          <Input id='relay-server' value={server} onChange={(event) => setServer(event.target.value)} placeholder='rtmp://live-push.xhscdn.com/live' disabled={busy || !config} autoComplete='off' />
          <Label htmlFor='relay-key'>{en ? 'Stream key' : '串流密钥'}</Label>
          <div className='flex gap-2'>
            <Input id='relay-key' type={showKey ? 'text' : 'password'} value={streamKey} onChange={(event) => setStreamKey(event.target.value)} placeholder={config?.hasKey ? (en ? 'Saved; enter a new key to replace' : '已保存；输入新密钥即可替换') : (en ? 'Paste the full stream key' : '粘贴完整串流密钥，包含 ? 后的参数')} disabled={busy || !config} autoComplete='new-password' />
            <Button type='button' variant='outline' onClick={() => setShowKey(!showKey)}>{en ? (showKey ? 'Hide' : 'Show') : (showKey ? '隐藏' : '显示')}</Button>
          </div>
          <div className='flex flex-wrap gap-2'>
            <Button type='submit' variant='outline' disabled={busy || !config || !server.trim() || (!streamKey.trim() && !config.hasKey)}>{en ? 'Save only' : '仅保存配置'}</Button>
            <Button type='button' onClick={() => void save(true)} disabled={busy || !config || !started || !server.trim() || (!streamKey.trim() && !config.hasKey)}>{en ? 'Save and start streaming' : '保存并开始推流'}</Button>
            <Button type='button' variant='destructive' onClick={() => void save(false, true)} disabled={busy || !config?.enabled}>{en ? 'Stop external stream' : '停止对外推流'}</Button>
          </div>
        </form>
        <p role='status' className='text-sm text-muted-foreground'>{loadError ? (en ? 'Unable to load settings' : '配置读取失败，请刷新重试') : labels[state] || state}</p>
        <p className='text-xs text-muted-foreground'>{en ? '720×1280 · 30 FPS · 3000 kbps · 1-second keyframe interval. Start the avatar stream first; verify reception on the platform.' : '720×1280 · 30 FPS · 3000 kbps · 关键帧间隔 1 秒。先打开数字人直播，再开始对外推流；最终画面请在平台直播间确认。'}</p>
      </CardContent>
    </Card>
  )
}
