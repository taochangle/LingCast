import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'

type Config = {
  url: string
  enabled: boolean
  welcome: boolean
  reply: boolean
  revision: number
}
type Event = {
  id: string
  kind: string
  nickname: string
  text: string
  observed_ms: number
  action: string
}
type Result = {
  config: Config
  status: { state: string; message?: string; received?: number } | null
  events: Event[]
}
const states: Record<string, string> = {
  stopped: '已停止',
  connecting: '正在打开直播间',
  listening: '正在监听',
  blocked: '需要检查直播页',
  ended: '直播已结束',
  error: '监听异常',
}
function message(error: unknown) {
  return (
    (error as { response?: { data?: { error?: string } } }).response?.data
      ?.error || '弹幕监听请求失败，请稍后重试'
  )
}
export function XHSListener({ avatarId }: { avatarId: number }) {
  const [url, setUrl] = useState('')
  const [welcome, setWelcome] = useState(false)
  const [reply, setReply] = useState(false)
  const [data, setData] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  useEffect(() => {
    if (!Number.isFinite(avatarId) || avatarId <= 0) return
    const abort = new AbortController()
    let initial = true
    const refresh = async () => {
      try {
        const response = await api.get<Result>(`/live/${avatarId}/xhs`, {
          signal: abort.signal,
        })
        if (abort.signal.aborted) return
        setData(response.data)
        setPollError('')
        if (initial) {
          setUrl(response.data.config.url || '')
          setWelcome(response.data.config.welcome)
          setReply(response.data.config.reply)
          initial = false
        }
      } catch (error) {
        if (!abort.signal.aborted) setPollError(message(error))
      }
    }
    void refresh()
    const timer = setInterval(() => void refresh(), 2500)
    return () => {
      abort.abort()
      clearInterval(timer)
    }
  }, [avatarId])
  const save = async (enabled: boolean, stop = false) => {
    setBusy(true)
    setError('')
    try {
      const config = stop
        ? { ...data?.config, enabled: false }
        : { url, welcome, reply, enabled }
      const response = await api.put<Result>(`/live/${avatarId}/xhs`, config)
      setData(response.data)
      if (!stop) setUrl(response.data.config.url)
      toast.success(
        enabled
          ? '已请求开始监听，将跳过首屏历史消息'
          : stop
            ? '已停止监听'
            : '链接已保存'
      )
    } catch (error) {
      setError(message(error))
    } finally {
      setBusy(false)
    }
  }
  if (!Number.isFinite(avatarId) || avatarId <= 0) return null
  const state = data?.config.enabled
    ? states[data.status?.state || ''] || '本机采集端未连接'
    : '未开启监听'
  return (
    <Card>
      <CardHeader>
        <CardTitle>小红书弹幕监听</CardTitle>
        <CardDescription>
          开播后粘贴本场分享链接，本机可见浏览器会打开房间读取新弹幕和进场提示；换场时更新链接即可。
        </CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-4'>
        <Label htmlFor='xhs-room-url'>
          弹幕采集地址（也可以粘贴完整分享文案）
        </Label>
        <Textarea
          id='xhs-room-url'
          rows={3}
          maxLength={8192}
          value={url}
          disabled={!data || busy}
          onChange={(event) => setUrl(event.target.value)}
          placeholder='https://www.xiaohongshu.com/livestream/… 或 https://xhslink.com/…'
        />
        <div className='flex flex-wrap items-center gap-6'>
          <div className='flex items-center gap-2'>
            <Switch
              id='xhs-welcome'
              checked={welcome}
              onCheckedChange={setWelcome}
              disabled={!data || busy}
            />
            <Label htmlFor='xhs-welcome'>自动欢迎进场</Label>
          </div>
          <div className='flex items-center gap-2'>
            <Switch
              id='xhs-reply'
              checked={reply}
              onCheckedChange={setReply}
              disabled={!data || busy}
            />
            <Label htmlFor='xhs-reply'>使用 Qwen 回复弹幕</Label>
          </div>
        </div>
        <p className='text-xs text-muted-foreground'>
          开关修改后点击「保存并开始监听」生效。可先关闭两个开关验证采集；开启后需要数字人处于直播状态才会口播。
        </p>
        <div className='flex flex-wrap items-center gap-2'>
          <Button
            variant='outline'
            disabled={busy || !data}
            onClick={() => void save(false)}
          >
            仅保存，不监听
          </Button>
          <Button
            disabled={busy || !data || !url.trim()}
            onClick={() => void save(true)}
          >
            保存并开始监听
          </Button>
          <Button
            variant='outline'
            disabled={busy || !data?.config.enabled}
            onClick={() => void save(false, true)}
          >
            停止监听
          </Button>
          <Badge variant='secondary'>{state}</Badge>
          {data?.config.url && (
            <a
              className='text-sm underline'
              href={data.config.url}
              target='_blank'
              rel='noopener noreferrer'
            >
              查看已保存的直播间
            </a>
          )}
        </div>
        {data?.status?.message && (
          <p role='status' className='text-sm text-muted-foreground'>
            {data.status.message}
          </p>
        )}
        {(error || pollError) && (
          <Alert variant='destructive'>
            <AlertDescription>{error || pollError}</AlertDescription>
          </Alert>
        )}
        <p className='text-xs text-muted-foreground'>
          本机需保持运行，登录或验证可在弹出的独立浏览器中完成。跳过历史消息、主播发言、点赞和礼物；重复欢迎去重，同房间最多每分钟欢迎一次、每
          12
          秒回复一条。手动口播优先，互动完成后继续自主主持。遇到登录或验证码会显示异常，不会自动绕过。
        </p>
        <details open>
          <summary className='cursor-pointer text-sm'>
            最近采集消息（当前房间最多显示 40 条）
          </summary>
          {data?.events.length ? (
            <ul className='mt-3 flex max-h-64 flex-col gap-2 overflow-y-auto text-sm'>
              {data.events.map((event) => (
                <li key={event.id} className='break-words'>
                  <span className='text-muted-foreground'>
                    {new Date(event.observed_ms).toLocaleTimeString()} ·{' '}
                  </span>
                  <strong>{event.nickname}</strong>
                  {event.kind === 'enter' ? ' 来了' : `：${event.text}`}
                </li>
              ))}
            </ul>
          ) : (
            <p className='mt-3 text-sm text-muted-foreground'>
              尚未采集到新消息。开始监听时已有的消息不会补播。
            </p>
          )}
        </details>
      </CardContent>
    </Card>
  )
}
