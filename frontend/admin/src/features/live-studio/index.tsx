import axios from 'axios'
import { RelaySettings } from './relay-settings'
import { XHSListener } from './xhs-listener'
import { ProjectReader } from './project-reader'
import { HostPanel } from './host-panel'
import Player from 'xgplayer'
import 'xgplayer/dist/index.min.css'
import FlvPlugin from 'xgplayer-flv'
import { Copy, Eye, EyeOff, LoaderCircle, Power, PowerOff, Video, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  api,
  type Avatar,
  type ChatMessage,
  type LiveSessionItem,
  type LiveSettings,
  type LiveStatus,
  type Scene,
} from '@/lib/api'

function showApiError(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    const message = (error.response?.data as { error?: string } | undefined)?.error
    toast.error(message ?? fallback)
    return
  }
  toast.error(fallback)
}

const DEFAULT_IDLE_MOTION = {
  enabled: true,
  breatheAmplitude: 0.006,
  breathePeriod: 4,
  driftAmplitude: 0.0015,
}

export function LiveStudio({ avatarId }: { avatarId: string }) {
  const { t, i18n } = useTranslation()
  const id = Number(avatarId)
  const [hostMaterial, setHostMaterial] = useState<{ topic: string; text: string; version: number } | null>(null)
  const navigate = useNavigate()
  const playerHostRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<Player | null>(null)
  const [status, setStatus] = useState<LiveStatus | null>(null)
  const [started, setStarted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [monitorOpen, setMonitorOpen] = useState(false)
  const [monitorPos, setMonitorPos] = useState<{ x: number; y: number } | null>(null)
  const winRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [messageText, setMessageText] = useState('')
  const [sendingMessage, setSendingMessage] = useState(false)

  const sendMessage = async () => {
    const text = messageText.trim()
    if (!text || Array.from(text).length > 220 || !id || !started || sendingMessage) return
    setSendingMessage(true)
    try {
      await api.post(`/live/${id}/narrate`, { text })
      setMessageText('')
      toast.success(i18n.language === 'en' ? 'Speech queued. Your text will be spoken as written.' : '口播已提交，数字人将按原文说出')
    } catch (error) {
      showApiError(error, t('common.requestFailed'))
    } finally {
      setSendingMessage(false)
    }
  }
  const [liveSettings, setLiveSettings] = useState<LiveSettings>({
    subtitleEnabled: true,
    subtitleFont: '',
    subtitlePosition: 'bottom',
    subtitleBorder: 2,
    subtitleSize: 46,
    idleSceneId: 0,
    idleSwitchMode: 'interval',
    idleSwitchSeconds: 15,
    idleFadeSeconds: 0.4,
    idleMotion: DEFAULT_IDLE_MOTION,
  })
  const [settingsLoading, setSettingsLoading] = useState(true)
  const [savingSettings, setSavingSettings] = useState(false)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [avatars, setAvatars] = useState<Avatar[]>([])
  const [liveSessions, setLiveSessions] = useState<LiveSessionItem[]>([])
  // Full pull URL: dev uses VITE_API_BASE_URL, dockerized app falls back to
  // the current origin (e.g. http://localhost:8080).
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const streamUrl = `${import.meta.env.VITE_API_BASE_URL || origin}/live/avatar_${id}.flv`
  const locale = i18n.language === 'en' ? 'en-US' : 'zh-CN'

  // Check whether the live session already exists on load.
  useEffect(() => {
    if (!id) return
    api
      .get(`/live/${id}/status`)
      .then(() => setStarted(true))
      .catch(() => setStarted(false))
  }, [id])

  // Load the avatar's persisted live settings (subtitles).
  useEffect(() => {
    if (!id) return
    api
      .get<Avatar>(`/avatars/${id}`)
      .then(({ data }) => {
        const s = data.liveSettings
        if (s) {
          setLiveSettings({
            subtitleEnabled: s.subtitleEnabled,
            subtitleFont: s.subtitleFont ?? '',
            subtitlePosition: s.subtitlePosition === 'top' ? 'top' : 'bottom',
            subtitleBorder: s.subtitleBorder ?? 2,
            subtitleSize: s.subtitleSize || 46,
            idleSceneId: s.idleSceneId ?? 0,
            idleSwitchMode: s.idleSwitchMode === 'random' ? 'random' : 'interval',
            idleSwitchSeconds: s.idleSwitchSeconds || 15,
            idleFadeSeconds: s.idleFadeSeconds ?? 0.4,
            idleMotion: s.idleMotion ?? DEFAULT_IDLE_MOTION,
          })
        }
      })
      .finally(() => setSettingsLoading(false))
  }, [id])

  // Scenes of the avatar, to pick the idle stream default videos.
  useEffect(() => {
    if (!id) {
      setScenes([])
      return
    }
    api
      .get<{ data: Scene[] }>(`/avatars/${id}/scenes`)
      .then(({ data }) => setScenes(data.data))
      .catch(() => setScenes([]))
  }, [id])

  const saveSettings = async () => {
    if (!id || savingSettings) return
    setSavingSettings(true)
    try {
      await api.put(`/avatars/${id}/live-settings`, liveSettings)
      toast.success(t('live.toastSettingsSaved'))
      if (started) {
        // Re-open the stream so the new subtitle settings take effect now.
        await api.post(`/live/${id}/stop`).catch(() => {})
        await api.post(`/live/${id}/start`)
        setStarted(true)
        toast.info(t('live.toastRestarted'))
      }
    } catch (error) {
      showApiError(error, t('common.requestFailed'))
    } finally {
      setSavingSettings(false)
    }
  }

  // Avatar switcher data: ready avatars + which ones are live, every 3s.
  // Without ?avatarId= we still load the list and auto-select the first
  // ready avatar so the page never shows an empty/NaN state.
  useEffect(() => {
    let stopped = false
    const refresh = async () => {
      try {
        const [avatarResp, liveResp] = await Promise.all([
          api.get<{ data: Avatar[] }>('/avatars'),
          api.get<{ data: LiveSessionItem[] }>('/live'),
        ])
        if (stopped) return
        setAvatars(avatarResp.data.data)
        setLiveSessions(liveResp.data.data)
        if (!Number.isFinite(id)) {
          const ready = avatarResp.data.data.find((a) => Boolean(a.defaultVideoS3Url))
          if (ready) {
            void navigate({
              to: '/live-studio',
              search: { avatarId: String(ready.id) },
              replace: true,
            })
          }
        }
      } catch {
        // keep previous data
      }
    }
    void refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [id, navigate])

  // HTTP-FLV player (xgplayer + flv plugin) — only while the floating monitor
  // is open AND the stream is on (default closed to save resources).
  useEffect(() => {
    if (!id || !started || !monitorOpen || !playerHostRef.current) return
    const player = new Player({
      el: playerHostRef.current,
      url: streamUrl,
      type: 'flv',
      isLive: true,
      autoplay: true,
      width: '100%',
      height: '100%',
      plugins: [FlvPlugin],
    })
    playerRef.current = player
    return () => {
      player.destroy()
      playerRef.current = null
    }
  }, [id, started, monitorOpen, streamUrl])

  // Open the floating window at a sensible default spot the first time.
  const openMonitor = () => {
    if (!monitorPos) {
      setMonitorPos({
        x: Math.max(8, window.innerWidth - 340 - 24),
        y: Math.max(8, window.innerHeight - 340 * (16 / 9) - 96),
      })
    }
    setMonitorOpen(true)
  }

  // Drag the floating window by its header bar.
  const onHeaderMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return
    e.preventDefault()
    const rect = winRef.current?.getBoundingClientRect()
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      origX: rect?.left ?? 0,
      origY: rect?.top ?? 0,
    }
    window.addEventListener('mousemove', onDragMove)
    window.addEventListener('mouseup', onDragEnd)
  }

  const onDragMove = (e: MouseEvent) => {
    const d = dragRef.current
    if (!d) return
    setMonitorPos({
      x: Math.min(window.innerWidth - 60, Math.max(-340 + 40, d.origX + e.clientX - d.startX)),
      y: Math.min(window.innerHeight - 40, Math.max(0, d.origY + e.clientY - d.startY)),
    })
  }

  const onDragEnd = () => {
    dragRef.current = null
    window.removeEventListener('mousemove', onDragMove)
    window.removeEventListener('mouseup', onDragEnd)
  }

  // Persisted chat feed (viewer id/username + bot replies), refreshed every 3s.
  useEffect(() => {
    if (!id) return
    let stopped = false
    const load = async () => {
      try {
        const { data } = await api.get<{ data: ChatMessage[] }>('/chat/history', {
          params: { avatarId: id },
        })
        if (!stopped) setChatMessages(data.data)
      } catch {
        // keep previous state
      }
    }
    void load()
    const timer = window.setInterval(load, 3000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [id])

  // Queue monitor: refresh every second.
  useEffect(() => {
    if (!id) return
    let stopped = false
    const timer = window.setInterval(async () => {
      try {
        const { data } = await api.get<LiveStatus>(`/live/${id}/status`)
        if (!stopped) {
          setStatus(data)
          setStarted(true)
        }
      } catch (error) {
        // Only a 404 means the session is gone; other errors keep polling.
        const notFound = axios.isAxiosError(error) && error.response?.status === 404
        if (!stopped && notFound) {
          setStatus(null)
          setStarted(false)
        }
      }
    }, 1000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [id])

  const handleStart = async () => {
    if (!id || busy) return
    setBusy(true)
    try {
      await api.post(`/live/${id}/start`)
      setStarted(true)
      toast.success(t('live.toastStarted'))
    } catch (error) {
      showApiError(error, t('common.requestFailed'))
    } finally {
      setBusy(false)
    }
  }

  const handleStop = async () => {
    if (!id || busy) return
    setBusy(true)
    try {
      await api.post(`/live/${id}/stop`)
      setStarted(false)
      setStatus(null)
      toast.success(t('live.toastStopped'))
    } catch (error) {
      showApiError(error, t('common.requestFailed'))
    } finally {
      setBusy(false)
    }
  }

  const copyStreamUrl = async () => {
    try {
      await navigator.clipboard.writeText(streamUrl)
      toast.success(t('live.toastUrlCopied'))
    } catch {
      toast.error(t('live.toastCopyFailed'))
    }
  }

  const statusVariant =
    status?.status === 'active'
      ? 'default'
      : status?.status === 'pending'
        ? 'secondary'
        : 'outline'

  return (
    <>
      <Header fixed>
        <div className='me-auto' />
        <ThemeSwitch />
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-center justify-between gap-3'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>{t('live.title')}</h2>
            <p className='text-muted-foreground'>
              {t('live.subtitle', { id })}
            </p>
          </div>
          <div className='flex items-center gap-2'>
            {status && (
              <Badge variant={statusVariant}>
                {status.status === 'active'
                  ? t('live.speaking')
                  : status.status === 'pending'
                    ? t('live.starting')
                    : t('live.idle')}
              </Badge>
            )}
            {started ? (
              <Button variant='destructive' size='sm' onClick={() => void handleStop()} disabled={busy}>
                {busy ? <LoaderCircle className='size-4 animate-spin' /> : <PowerOff className='size-4' />}
                {t('live.stop')}
              </Button>
            ) : (
              <Button size='sm' onClick={() => void handleStart()} disabled={busy}>
                {busy ? <LoaderCircle className='size-4 animate-spin' /> : <Power className='size-4' />}
                {t('live.start')}
              </Button>
            )}
          </div>
        </div>

        <Card>
          <CardContent className='flex flex-wrap items-center gap-2 py-3 text-sm'>
            <span className='text-muted-foreground'>{t('live.streamUrl')}</span>
            <code className='break-all rounded bg-muted px-2 py-1'>{streamUrl}</code>
            <Button variant='ghost' size='sm' onClick={() => void copyStreamUrl()}>
              <Copy className='size-3.5' />
              {t('common.copy')}
            </Button>
            <span className='text-xs text-muted-foreground'>
              {t('live.multiHint')}
            </span>
          </CardContent>
        </Card>

        <RelaySettings key={id} avatarId={id} started={started} />
        <XHSListener key={`xhs-${id}`} avatarId={id} />
        <ProjectReader key={`project-${id}`} avatarId={id} started={started} onMaterial={(topic, text) => setHostMaterial({ topic, text, version: Date.now() })} />
        <HostPanel key={`host-${id}-${hostMaterial?.version || 0}`} avatarId={id} started={started} material={hostMaterial} />

        <div className='grid gap-4 lg:grid-cols-4'>
          {/* Left: avatar switcher */}
          <Card className='lg:col-span-1'>
            <CardHeader className='gap-1'>
              <CardTitle>{t('live.avatarLiveTitle')}</CardTitle>
              <CardDescription>{t('live.avatarLiveDesc')}</CardDescription>
            </CardHeader>
            <CardContent className='flex flex-col gap-2'>
              {avatars
                .filter((avatar) => Boolean(avatar.defaultVideoS3Url))
                .map((avatar) => {
                  const live = liveSessions.some((s) => s.avatarId === avatar.id)
                  const active = avatar.id === id
                  return (
                    <Link
                      key={avatar.id}
                      to='/live-studio'
                      search={{ avatarId: String(avatar.id) }}
                      className={`flex items-center gap-3 rounded-lg border p-2 transition-colors hover:bg-muted ${
                        active
                          ? 'border-primary/70 bg-primary/10 ring-1 ring-primary/60'
                          : ''
                      }`}
                    >
                      {avatar.imageS3Url && (
                        <img
                          src={avatar.imageS3Url}
                          alt={avatar.name}
                          className='h-9 w-9 rounded-md border object-cover'
                        />
                      )}
                      <div className='min-w-0 flex-1'>
                        <p
                          className={`truncate text-sm ${
                            active ? 'font-semibold text-primary' : 'font-medium'
                          }`}
                        >
                          {avatar.name}
                        </p>
                        <p className='text-xs text-muted-foreground'>#{avatar.id}</p>
                      </div>
                      <Badge variant={live ? 'default' : 'outline'}>
                        {live ? t('live.live') : t('live.offline')}
                      </Badge>
                    </Link>
                  )
                })}
            </CardContent>
          </Card>

          {/* Right: queue / chat / settings / input (2x2 on wide screens) */}
          <div className='grid gap-4 lg:col-span-3 lg:grid-cols-2'>
            <Card>
              <CardHeader className='gap-1'>
                <CardTitle>{t('live.queueTitle')}</CardTitle>
                <CardDescription>
                  {status
                    ? t('live.queueCount', { count: status.queueLength })
                    : t('common.loading')}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {status && status.pending.length === 0 ? (
                  <p className='py-4 text-center text-sm text-muted-foreground'>
                    {t('live.queueEmpty')}
                  </p>
                ) : (
                  <ol className='max-h-56 list-decimal space-y-1 overflow-y-auto ps-5 text-sm'>
                    {status?.pending.map((item, i) => (
                      <li key={`${i}-${item}`} className='line-clamp-2 text-muted-foreground'>
                        {item}
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='gap-1'>
                <CardTitle>{i18n.language === 'en' ? 'Direct speech' : '直接口播'}</CardTitle>
                <CardDescription>{i18n.language === 'en' ? 'Enter the words you want the avatar to say to the audience.' : '输入你希望数字人对观众说的话，按原文播放。'}</CardDescription>
              </CardHeader>
              <CardContent className='flex flex-col gap-4'>
                <form onSubmit={(event) => { event.preventDefault(); void sendMessage() }} className='flex flex-col gap-2'>
                  <Label htmlFor='studio-message'>{i18n.language === 'en' ? 'Words to speak' : '数字人要说的话'}</Label>
                  <div className='flex gap-2'>
                    <Input id='studio-message' value={messageText} onChange={(event) => setMessageText(event.target.value)} disabled={sendingMessage || !started} placeholder={i18n.language === 'en' ? 'Type the words to speak, then press Enter…' : '输入口播内容，按回车开始…'} />
                    <Button type='submit' disabled={!started || sendingMessage || !messageText.trim() || Array.from(messageText.trim()).length > 220}>
                      {sendingMessage && <LoaderCircle className='animate-spin' data-icon='inline-start' />}
                      {i18n.language === 'en' ? 'Speak' : '开始口播'}
                    </Button>
                  </div>
                  <p className='text-xs text-muted-foreground'>
                    {i18n.language === 'en'
                      ? (started ? 'Up to 220 characters. One narration, split at natural pauses. The first ready segment plays while later segments generate; idle video fills any gaps.' : 'Start the live session before submitting speech.')
                      : (started ? '最多 220 字，按原文整段配音，在自然停顿处切段。首段就绪即播，后续后台生成；未就绪时播放待机画面。' : '请先打开直播，再提交口播内容。')}
                  </p>
                  <p className='text-xs text-muted-foreground'>{Array.from(messageText.trim()).length}/220</p>
                </form>
                <p className='text-sm font-medium'>{i18n.language === 'en' ? 'Audience conversation history' : '观众互动记录'}</p>
                {chatMessages.length === 0 ? (
                  <p className='py-4 text-center text-sm text-muted-foreground'>
                    {t('live.chatEmpty')}
                  </p>
                ) : (
                  <ol className='max-h-72 space-y-2 overflow-y-auto'>
                    {chatMessages.map((m) => (
                      <li
                        key={m.id}
                        className={`rounded-lg p-2 ${
                          m.role === 'bot' ? 'bg-muted/70' : 'bg-transparent'
                        }`}
                      >
                        <p className='text-xs text-muted-foreground'>
                          {m.role === 'bot'
                            ? `🤖 ${m.username}`
                            : `${m.username} #${m.userId}`}
                          {' · '}
                          {new Date(m.createdAt).toLocaleTimeString(locale, {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </p>
                        <p className='mt-0.5 break-words text-sm'>{m.content}</p>
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='gap-1'>
                <CardTitle>{t('live.subtitleTitle')}</CardTitle>
                <CardDescription>{t('live.subtitleDesc')}</CardDescription>
              </CardHeader>
              <CardContent className='flex flex-col gap-3'>
                <div className='flex items-center justify-between gap-2'>
                  <Label htmlFor='subtitle-enabled'>{t('live.showSubtitle')}</Label>
                  <Switch
                    id='subtitle-enabled'
                    checked={liveSettings.subtitleEnabled}
                    onCheckedChange={(v) =>
                      setLiveSettings((s) => ({ ...s, subtitleEnabled: v }))
                    }
                    disabled={settingsLoading}
                  />
                </div>

                <div className='flex flex-col gap-1.5'>
                  <Label htmlFor='subtitle-font'>{t('live.font')}</Label>
                  <Input
                    id='subtitle-font'
                    list='subtitle-fonts'
                    value={liveSettings.subtitleFont}
                    onChange={(e) =>
                      setLiveSettings((s) => ({ ...s, subtitleFont: e.target.value }))
                    }
                    placeholder={t('live.fontPlaceholder')}
                    disabled={settingsLoading}
                  />
                  <datalist id='subtitle-fonts'>
                    <option value='SourceHanSansSC-Regular.otf' />
                    <option value='SourceHanSansSC-Bold.otf' />
                    <option value='SourceHanSerifSC-Regular.otf' />
                    <option value='AlibabaPuHuiTi-3-55-Regular.ttf' />
                    <option value='HarmonyOS_Sans_SC_Regular.ttf' />
                    <option value='STHeiti Medium.ttc' />
                  </datalist>
                </div>

                <div className='flex flex-col gap-1.5'>
                  <Label>{t('live.position')}</Label>
                  <Select
                    value={liveSettings.subtitlePosition}
                    onValueChange={(v) =>
                      setLiveSettings((s) => ({
                        ...s,
                        subtitlePosition: v as 'bottom' | 'top',
                      }))
                    }
                    disabled={settingsLoading}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value='bottom'>{t('live.bottom')}</SelectItem>
                      <SelectItem value='top'>{t('live.top')}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className='flex gap-3'>
                  <div className='flex flex-1 flex-col gap-1.5'>
                    <Label htmlFor='subtitle-border'>{t('live.border')}</Label>
                    <Input
                      id='subtitle-border'
                      type='number'
                      min={0}
                      max={10}
                      value={liveSettings.subtitleBorder}
                      onChange={(e) =>
                        setLiveSettings((s) => ({
                          ...s,
                          subtitleBorder: Number(e.target.value) || 0,
                        }))
                      }
                      disabled={settingsLoading}
                    />
                  </div>
                  <div className='flex flex-1 flex-col gap-1.5'>
                    <Label htmlFor='subtitle-size'>{t('live.size')}</Label>
                    <Input
                      id='subtitle-size'
                      type='number'
                      min={24}
                      max={96}
                      value={liveSettings.subtitleSize}
                      onChange={(e) =>
                        setLiveSettings((s) => ({
                          ...s,
                          subtitleSize: Number(e.target.value) || 46,
                        }))
                      }
                      disabled={settingsLoading}
                    />
                  </div>
                </div>

                <Button
                  onClick={() => void saveSettings()}
                  disabled={savingSettings || settingsLoading}
                >
                  {savingSettings ? <LoaderCircle className='size-4 animate-spin' /> : null}
                  {t('live.save')}
                </Button>
                <p className='text-xs text-muted-foreground'>{t('live.fontHint')}</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='gap-1'>
                <CardTitle>{t('live.idleStreamTitle')}</CardTitle>
                <CardDescription>{t('live.idleStreamDesc')}</CardDescription>
              </CardHeader>
              <CardContent className='flex flex-col gap-3'>
                <div className='flex flex-col gap-1.5'>
                  <Label>{t('live.idleScene')}</Label>
                  <Select
                    value={String(liveSettings.idleSceneId ?? 0)}
                    onValueChange={(v) =>
                      setLiveSettings((s) => ({ ...s, idleSceneId: Number(v) }))
                    }
                    disabled={settingsLoading}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value='0'>{t('live.idleSceneDefault')}</SelectItem>
                      {scenes.map((scene) => (
                        <SelectItem key={scene.id} value={String(scene.id)}>
                          {scene.title}（{scene.videos.length}{' '}
                          {t('live.idleVideoCount')}）
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className='flex flex-col gap-1.5'>
                  <Label>{t('live.idleSwitchMode')}</Label>
                  <Select
                    value={liveSettings.idleSwitchMode ?? 'interval'}
                    onValueChange={(v) =>
                      setLiveSettings((s) => ({
                        ...s,
                        idleSwitchMode: v as 'interval' | 'random',
                      }))
                    }
                    disabled={settingsLoading}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value='interval'>{t('live.switchInterval')}</SelectItem>
                      <SelectItem value='random'>{t('live.switchRandom')}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {liveSettings.idleSwitchMode === 'interval' && (
                  <div className='flex flex-col gap-1.5'>
                    <Label htmlFor='idle-switch-seconds'>
                      {t('live.idleSwitchSeconds')}
                    </Label>
                    <Input
                      id='idle-switch-seconds'
                      type='number'
                      min={3}
                      max={600}
                      value={liveSettings.idleSwitchSeconds ?? 15}
                      onChange={(e) =>
                        setLiveSettings((s) => ({
                          ...s,
                          idleSwitchSeconds: Number(e.target.value) || 15,
                        }))
                      }
                      disabled={settingsLoading}
                    />
                  </div>
                )}

                <Button
                  onClick={() => void saveSettings()}
                  disabled={savingSettings || settingsLoading}
                >
                  {savingSettings ? <LoaderCircle className='size-4 animate-spin' /> : null}
                  {t('live.save')}
                </Button>
                <p className='text-xs text-muted-foreground'>{t('live.idleStreamHint')}</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='gap-1'>
                <CardTitle>{t('live.idleMotionTitle')}</CardTitle>
                <CardDescription>{t('live.idleMotionDesc')}</CardDescription>
              </CardHeader>
              <CardContent className='flex flex-col gap-3'>
                <div className='flex flex-col gap-1.5'>
                  <Label htmlFor='idle-fade-seconds'>
                    {t('live.idleFadeSeconds')}
                  </Label>
                  <Input
                    id='idle-fade-seconds'
                    type='number'
                    min={0}
                    max={5}
                    step={0.1}
                    value={liveSettings.idleFadeSeconds ?? 0.4}
                    onChange={(e) =>
                      setLiveSettings((s) => ({
                        ...s,
                        idleFadeSeconds: Number(e.target.value),
                      }))
                    }
                    disabled={settingsLoading}
                  />
                  <p className='text-xs text-muted-foreground'>
                    {t('live.idleFadeHint')}
                  </p>
                </div>

                <label className='flex cursor-pointer items-center justify-between gap-2 text-sm'>
                  <span className='flex flex-col'>
                    <span>{t('live.idleMotionEnabled')}</span>
                    <span className='text-xs text-muted-foreground'>
                      {t('live.idleMotionEnabledHint')}
                    </span>
                  </span>
                  <Switch
                    checked={Boolean(liveSettings.idleMotion?.enabled)}
                    onCheckedChange={(v) =>
                      setLiveSettings((s) => ({
                        ...s,
                        idleMotion: {
                          ...(s.idleMotion ?? DEFAULT_IDLE_MOTION),
                          enabled: v,
                        },
                      }))
                    }
                    disabled={settingsLoading}
                  />
                </label>

                <div className='grid grid-cols-2 gap-3'>
                  <div className='flex flex-col gap-1.5'>
                    <Label>{t('live.idleBreatheAmplitude')}</Label>
                    <Input
                      type='number'
                      min={0}
                      max={0.05}
                      step={0.001}
                      value={liveSettings.idleMotion?.breatheAmplitude ?? 0.006}
                      onChange={(e) =>
                        setLiveSettings((s) => ({
                          ...s,
                          idleMotion: {
                            ...(s.idleMotion ?? DEFAULT_IDLE_MOTION),
                            breatheAmplitude: Number(e.target.value),
                          },
                        }))
                      }
                      disabled={settingsLoading}
                    />
                  </div>
                  <div className='flex flex-col gap-1.5'>
                    <Label>{t('live.idleBreathePeriod')}</Label>
                    <Input
                      type='number'
                      min={0.5}
                      max={30}
                      step={0.5}
                      value={liveSettings.idleMotion?.breathePeriod ?? 4}
                      onChange={(e) =>
                        setLiveSettings((s) => ({
                          ...s,
                          idleMotion: {
                            ...(s.idleMotion ?? DEFAULT_IDLE_MOTION),
                            breathePeriod: Number(e.target.value),
                          },
                        }))
                      }
                      disabled={settingsLoading}
                    />
                  </div>
                  <div className='col-span-2 flex flex-col gap-1.5'>
                    <Label>{t('live.idleDriftAmplitude')}</Label>
                    <Input
                      type='number'
                      min={0}
                      max={0.02}
                      step={0.0005}
                      value={liveSettings.idleMotion?.driftAmplitude ?? 0.0015}
                      onChange={(e) =>
                        setLiveSettings((s) => ({
                          ...s,
                          idleMotion: {
                            ...(s.idleMotion ?? DEFAULT_IDLE_MOTION),
                            driftAmplitude: Number(e.target.value),
                          },
                        }))
                      }
                      disabled={settingsLoading}
                    />
                  </div>
                </div>

                <Button
                  onClick={() => void saveSettings()}
                  disabled={savingSettings || settingsLoading}
                >
                  {savingSettings ? <LoaderCircle className='size-4 animate-spin' /> : null}
                  {t('live.save')}
                </Button>
                <p className='text-xs text-muted-foreground'>
                  {t('live.idleMotionHint')}
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      </Main>

      {/* Floating monitor: round FAB bottom-right; click opens a draggable 9:16 window. */}
      <button
        onClick={() => (monitorOpen ? setMonitorOpen(false) : openMonitor())}
        className='fixed bottom-6 right-6 z-50 flex size-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition hover:bg-primary/90'
        title={monitorOpen ? t('live.closeMonitor') : t('live.openMonitor')}
      >
        {monitorOpen ? <EyeOff className='size-6' /> : <Eye className='size-6' />}
        {started && (
          <span className='absolute right-1 top-1 size-3 rounded-full bg-red-500 ring-2 ring-background' />
        )}
      </button>

      {monitorOpen && (
        <div
          ref={winRef}
          style={{
            left: monitorPos?.x ?? 0,
            top: monitorPos?.y ?? 0,
            width: 340,
          }}
          className='fixed z-50 overflow-hidden rounded-xl border bg-background shadow-2xl'
        >
          <div
            onMouseDown={onHeaderMouseDown}
            className='flex cursor-move items-center justify-between border-b bg-muted/60 px-3 py-2'
          >
            <span className='flex items-center gap-1.5 text-xs font-medium'>
              <Video className='size-3.5' />
              {t('live.monitor')}
              {started && <span className='size-1.5 animate-pulse rounded-full bg-red-500' />}
            </span>
            <button
              onClick={() => setMonitorOpen(false)}
              className='rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground'
              title={t('common.close')}
            >
              <X className='size-4' />
            </button>
          </div>
          <div className='p-2'>
            {started ? (
              <div className='aspect-[9/16] w-full overflow-hidden rounded-lg border bg-black'>
                <div ref={playerHostRef} className='h-full w-full' />
              </div>
            ) : (
              <div className='flex aspect-[9/16] w-full flex-col items-center justify-center gap-2 rounded-lg border bg-muted/40 text-sm text-muted-foreground'>
                <Eye className='size-6' />
                {t('live.notStarted')}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
